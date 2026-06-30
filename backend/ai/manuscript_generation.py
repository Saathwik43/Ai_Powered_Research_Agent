import os
import asyncio
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
    providers = []
    if MANUSCRIPT_PROVIDER in ("auto", "openrouter"):
        providers.append(("OpenRouter", _generate_openrouter))
    if MANUSCRIPT_PROVIDER in ("auto", "huggingface"):
        providers.append(("Hugging Face", _generate_huggingface))

    for provider_name, provider in providers:
        try:
            return await provider(topic, section, context)
        except Exception as e:
            print(f"{provider_name} manuscript generation failed: {e}")
            if MANUSCRIPT_PROVIDER != "auto":
                break

    return _local_draft(topic, section, context)
