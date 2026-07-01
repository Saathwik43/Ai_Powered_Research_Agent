import os
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
import httpx
from langchain_huggingface import HuggingFaceEndpoint

logger = logging.getLogger(__name__)

# Providers and configurations
LLM_PROVIDER = os.getenv("MANUSCRIPT_PROVIDER", "auto").lower()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet")
HUGGINGFACE_TOKEN = os.getenv("HUGGINGFACEHUB_API_TOKEN") or os.getenv("HF_TOKEN")
HF_MODEL = os.getenv("HUGGINGFACE_MANUSCRIPT_MODEL", "mistralai/Mixtral-8x7B-Instruct-v0.1")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

_executor = ThreadPoolExecutor(max_workers=4)


async def _generate_groq(system_prompt: str, user_prompt: str, max_tokens: int, temperature: float) -> str:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not configured.")
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()


async def _generate_openrouter(system_prompt: str, user_prompt: str, max_tokens: int, temperature: float) -> str:
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not configured.")
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.getenv("APP_PUBLIC_URL", "http://localhost:5173"),
        "X-Title": "Research Agent",
    }
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()


def _run_huggingface(system_prompt: str, user_prompt: str, max_tokens: int, temperature: float) -> str:
    if not HUGGINGFACE_TOKEN:
        raise RuntimeError("HUGGINGFACEHUB_API_TOKEN or HF_TOKEN is not configured.")
    
    llm = HuggingFaceEndpoint(
        repo_id=HF_MODEL,
        task="text-generation",
        max_new_tokens=max_tokens,
        temperature=temperature,
        huggingfacehub_api_token=HUGGINGFACE_TOKEN,
    )
    prompt = f"{system_prompt}\n\n{user_prompt}" if system_prompt else user_prompt
    if "[INST]" not in prompt:
        prompt = f"[INST] {prompt} [/INST]"
        
    try:
        return llm.invoke(prompt).strip()
    except StopIteration as e:
        raise RuntimeError(f"LangChain StopIteration: {e}") from e


async def _generate_huggingface(system_prompt: str, user_prompt: str, max_tokens: int, temperature: float) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _run_huggingface, system_prompt, user_prompt, max_tokens, temperature)


async def generate_completion(system_prompt: str, user_prompt: str, max_tokens: int = 1200, temperature: float = 0.45) -> str:
    """
    Attempts to generate a completion by cascading through configured AI providers.
    """
    providers = []
    if LLM_PROVIDER in ("auto", "groq"):
        providers.append(("Groq", _generate_groq))
    if LLM_PROVIDER in ("auto", "openrouter"):
        providers.append(("OpenRouter", _generate_openrouter))
    if LLM_PROVIDER in ("auto", "huggingface"):
        providers.append(("Hugging Face", _generate_huggingface))

    for provider_name, provider in providers:
        for attempt in range(2):
            try:
                result = await asyncio.wait_for(provider(system_prompt, user_prompt, max_tokens, temperature), timeout=60)
                return result
            except Exception as e:
                logger.error(f"{provider_name} generation failed (attempt {attempt + 1}): {e}")
                if attempt == 0:
                    await asyncio.sleep(2)
        if LLM_PROVIDER != "auto":
            break
            
    raise RuntimeError("All configured AI providers failed to generate a completion.")
