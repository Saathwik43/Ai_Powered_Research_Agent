import os
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

import httpx
from dotenv import load_dotenv
from langchain_core.prompts import PromptTemplate
from langchain_huggingface import HuggingFaceEndpoint

load_dotenv()

MANUSCRIPT_PROVIDER = os.getenv("MANUSCRIPT_PROVIDER", "auto").lower()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet")
HUGGINGFACE_TOKEN = os.getenv("HUGGINGFACEHUB_API_TOKEN") or os.getenv("HF_TOKEN")
HF_MODEL = os.getenv("HUGGINGFACE_MANUSCRIPT_MODEL", "mistralai/Mixtral-8x7B-Instruct-v0.1")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

logger = logging.getLogger(__name__)

import time
_cache = {}

prompt_template = PromptTemplate(
    input_variables=["topic", "section", "context"],
    template="""You are an expert, highly-cited academic researcher and writer.
You are writing a formal, peer-reviewed research paper on the topic: "{topic}".
Your current task is exclusively to write the "{section}" section of the paper.

Here is the background context and literature survey information you MUST incorporate and synthesize:
<context>
{context}
</context>

Instructions:
1. Write a highly rigorous, well-structured, and formal academic "{section}" section.
2. DO NOT include a title or heading for the section. Start directly with the content.
3. Seamlessly weave the provided literature and context into your arguments. Do not just list them.
4. Keep all claims appropriately cautious and academically sound (e.g., use "suggests", "indicates", "may").
5. Format the output in clean Markdown, using paragraphs, lists, or bold text only where academically appropriate.
6. Make it comprehensive, detailed, and at least 3-4 paragraphs long."""
)

_executor = ThreadPoolExecutor(max_workers=4)


def _prompt(topic: str, section: str, context: str) -> str:
    return prompt_template.format(topic=topic, section=section, context=context)


async def _generate_groq(topic: str, section: str, context: str) -> str:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not configured.")

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "You write rigorous, concise academic manuscript sections.",
            },
            {"role": "user", "content": _prompt(topic, section, context)},
        ],
        "temperature": 0.45,
        "max_tokens": 1200,
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()


async def _generate_openrouter(topic: str, section: str, context: str) -> str:
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not configured.")

    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "You write rigorous, concise academic manuscript sections.",
            },
            {"role": "user", "content": _prompt(topic, section, context)},
        ],
        "temperature": 0.45,
        "max_tokens": 1200,
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.getenv("APP_PUBLIC_URL", "http://localhost:5173"),
        "X-Title": "Research Agent",
    }

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()


def _run_huggingface(topic: str, section: str, context: str) -> str:
    if not HUGGINGFACE_TOKEN:
        raise RuntimeError("HUGGINGFACEHUB_API_TOKEN or HF_TOKEN is not configured.")

    llm = HuggingFaceEndpoint(
        repo_id=HF_MODEL,
        task="text-generation",
        max_new_tokens=1024,
        temperature=0.5,
        huggingfacehub_api_token=HUGGINGFACE_TOKEN,
    )
    return llm.invoke(f"[INST] {_prompt(topic, section, context)} [/INST]").strip()


async def _generate_huggingface(topic: str, section: str, context: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _run_huggingface, topic, section, context)


def _local_draft(topic: str, section: str, context: str) -> str:
    section_name = section.replace("_", " ").title()
    context_line = context.strip() if context and context.strip() else "the available literature and current research trends"
    return (
        f"This {section_name.lower()} focuses on **{topic.strip() or 'the selected research topic'}** "
        f"using {context_line} as the guiding context.\n\n"
        "The discussion should establish the research problem, define the scope of inquiry, "
        "and connect the work to recent academic developments. It should also identify the "
        "main methodological or conceptual gap that motivates the study.\n\n"
        "Key points to expand:\n\n"
        "- State the central research objective clearly.\n"
        "- Summarize the most relevant prior work and its limitations.\n"
        "- Explain how this manuscript contributes new evidence, synthesis, or perspective.\n"
        "- Keep terminology consistent and add citations from the literature survey before submission.\n\n"
        "_External AI generation is not configured or is temporarily unavailable, so this structured "
        "draft was generated locally as a starting point._"
    )


async def generate_section(topic: str, section: str, context: str):
    cache_key = None
    if context and context.strip():
        cache_key = hash(topic + section + context)
        if cache_key in _cache:
            cache_entry = _cache[cache_key]
            # TTL check (1 hour = 3600 seconds)
            if time.time() - cache_entry['time'] < 3600:
                return cache_entry['content']

    providers = []
    if MANUSCRIPT_PROVIDER in ("auto", "groq"):
        providers.append(("Groq", _generate_groq))
    if MANUSCRIPT_PROVIDER in ("auto", "openrouter"):
        providers.append(("OpenRouter", _generate_openrouter))
    if MANUSCRIPT_PROVIDER in ("auto", "huggingface"):
        providers.append(("Hugging Face", _generate_huggingface))

    for provider_name, provider in providers:
        for attempt in range(2):
            try:
                result = await asyncio.wait_for(provider(topic, section, context), timeout=60)
                if cache_key is not None:
                    _cache[cache_key] = {'content': result, 'time': time.time()}
                return result
            except Exception as e:
                logger.error(f"{provider_name} manuscript generation failed (attempt {attempt + 1}): {e}")
                if attempt == 0:
                    await asyncio.sleep(2)
        if MANUSCRIPT_PROVIDER != "auto":
            break

    return _local_draft(topic, section, context)


edit_prompt_template = PromptTemplate(
    input_variables=["topic", "section", "current_content", "instructions"],
    template="""You are an expert academic editor.
You are editing the "{section}" section of a research paper on the topic: "{topic}".

Here is the current content of the section:
<current_content>
{current_content}
</current_content>

The user has requested the following specific changes or revisions:
<instructions>
{instructions}
</instructions>

Instructions:
1. Revise the current content strictly according to the user's instructions.
2. Maintain a highly rigorous, well-structured, and formal academic tone unless instructed otherwise.
3. DO NOT include a title or heading for the section. Start directly with the revised content.
4. Output ONLY the revised text in clean Markdown, without any conversational filler or introductory remarks like "Here is the revised section."."""
)

def _edit_prompt_fn(topic: str, section: str, current_content: str, instructions: str) -> str:
    return edit_prompt_template.format(
        topic=topic, section=section, current_content=current_content, instructions=instructions
    )

async def _edit_groq(topic: str, section: str, current_content: str, instructions: str) -> str:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not configured.")

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "You are a meticulous academic editor.",
            },
            {"role": "user", "content": _edit_prompt_fn(topic, section, current_content, instructions)},
        ],
        "temperature": 0.45,
        "max_tokens": 1200,
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()

async def _edit_openrouter(topic: str, section: str, current_content: str, instructions: str) -> str:
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not configured.")

    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "You are a meticulous academic editor.",
            },
            {"role": "user", "content": _edit_prompt_fn(topic, section, current_content, instructions)},
        ],
        "temperature": 0.45,
        "max_tokens": 1200,
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.getenv("APP_PUBLIC_URL", "http://localhost:5173"),
        "X-Title": "Research Agent",
    }

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()

def _run_edit_huggingface(topic: str, section: str, current_content: str, instructions: str) -> str:
    if not HUGGINGFACE_TOKEN:
        raise RuntimeError("HUGGINGFACEHUB_API_TOKEN or HF_TOKEN is not configured.")

    llm = HuggingFaceEndpoint(
        repo_id=HF_MODEL,
        task="text-generation",
        max_new_tokens=1024,
        temperature=0.5,
        huggingfacehub_api_token=HUGGINGFACE_TOKEN,
    )
    prompt = _edit_prompt_fn(topic, section, current_content, instructions)
    return llm.invoke(f"[INST] {prompt} [/INST]").strip()

async def _edit_huggingface(topic: str, section: str, current_content: str, instructions: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _run_edit_huggingface, topic, section, current_content, instructions)

async def edit_section(topic: str, section: str, current_content: str, instructions: str):
    providers = []
    if MANUSCRIPT_PROVIDER in ("auto", "groq"):
        providers.append(("Groq", _edit_groq))
    if MANUSCRIPT_PROVIDER in ("auto", "openrouter"):
        providers.append(("OpenRouter", _edit_openrouter))
    if MANUSCRIPT_PROVIDER in ("auto", "huggingface"):
        providers.append(("Hugging Face", _edit_huggingface))

    for provider_name, provider in providers:
        for attempt in range(2):
            try:
                result = await asyncio.wait_for(provider(topic, section, current_content, instructions), timeout=60)
                return result
            except Exception as e:
                logger.error(f"{provider_name} manuscript edit failed (attempt {attempt + 1}): {e}")
                if attempt == 0:
                    await asyncio.sleep(2)
        if MANUSCRIPT_PROVIDER != "auto":
            break

    # If all fail, return current content with a note
    return current_content + "\n\n_(Note: AI revision providers failed. Original content retained.)_"
