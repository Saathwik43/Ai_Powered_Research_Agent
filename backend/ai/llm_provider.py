import os
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
import httpx
from langchain_huggingface import HuggingFaceEndpoint
from google import genai
from google.genai import types as genai_types
from contextvars import ContextVar

current_provider: ContextVar[str | None] = ContextVar("current_provider", default=None)
current_model: ContextVar[str | None] = ContextVar("current_model", default=None)
import usage_tracker
import time

logger = logging.getLogger(__name__)

_gemini_caches = {}

async def get_or_create_gemini_cache(cache_key: str, system_instruction: str, shared_context: str, model: str = None) -> str | None:
    global _gemini_client
    if not _gemini_client:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            return None
        _gemini_client = genai.Client(api_key=key)

    now = time.time()
    if cache_key in _gemini_caches:
        cache_name, expiry = _gemini_caches[cache_key]
        if now < expiry:
            return cache_name
        else:
            del _gemini_caches[cache_key]

    token_est = len(shared_context.split()) * 1.3
    if token_est < 2048:
        return None

    try:
        model_name = model or os.getenv("GEMINI_MODEL", "gemini-flash-latest")
        cache = await _gemini_client.aio.caches.create(
            model=model_name,
            config=genai_types.CreateCachedContentConfig(
                contents=[shared_context],
                system_instruction=system_instruction or None,
                ttl="1800s"
            )
        )
        _gemini_caches[cache_key] = (cache.name, now + 1800)
        return cache.name
    except Exception as e:
        logger.warning(f"Failed to create Gemini cache for {cache_key}: {e}")
        return None

# Providers and configurations
LLM_PROVIDER = os.getenv("MANUSCRIPT_PROVIDER", "auto").lower()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "~anthropic/claude-sonnet-latest")
HUGGINGFACE_TOKEN = os.getenv("HUGGINGFACEHUB_API_TOKEN") or os.getenv("HF_TOKEN")
HF_MODEL = os.getenv("HUGGINGFACE_MANUSCRIPT_MODEL", "mistralai/Mixtral-8x7B-Instruct-v0.1")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

# google-genai Client — created once at module level if key is available.
# The old google-generativeai SDK used genai.configure() globally; the new SDK
# uses a per-client api_key instead.
_gemini_client: "genai.Client | None" = None
if GEMINI_API_KEY:
    _gemini_client = genai.Client(api_key=GEMINI_API_KEY)

_executor = ThreadPoolExecutor(max_workers=4)

async def _generate_gemini(system_prompt: str, user_prompt: str, max_tokens: int, temperature: float, model: str = None, cached_content: str = None) -> str:
    global _gemini_client
    if not _gemini_client:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not configured.")
        _gemini_client = genai.Client(api_key=key)

    config = genai_types.GenerateContentConfig(
        system_instruction=None if cached_content else (system_prompt or None),
        temperature=temperature,
        max_output_tokens=max_tokens,
        cached_content=cached_content
    )

    try:
        response = await _gemini_client.aio.models.generate_content(
            model=model or os.getenv("GEMINI_MODEL", "gemini-flash-latest"),
            contents=user_prompt,
            config=config,
        )
        usage = response.usage_metadata.total_token_count if hasattr(response, 'usage_metadata') and response.usage_metadata else 0
        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            logger.info(f"Gemini usage: {response.usage_metadata}")
        return response.text.strip(), usage
    except Exception as e:
        logger.error(f"Gemini API Error ({type(e).__name__}): {e}", exc_info=True)
        raise RuntimeError(f"Gemini generation failed: {type(e).__name__} - {e}") from e

async def _generate_openai(system_prompt: str, user_prompt: str, max_tokens: int, temperature: float) -> str:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    payload = {
        "model": os.getenv("OPENAI_MODEL", "gpt-4o"),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        usage = data.get("usage", {}).get("total_tokens", 0)
        return data["choices"][0]["message"]["content"].strip(), usage



async def _generate_groq(system_prompt: str, user_prompt: str, max_tokens: int, temperature: float) -> str:
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY is not configured.")
    payload = {
        "model": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
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
        usage = data.get("usage", {}).get("total_tokens", 0)
        return data["choices"][0]["message"]["content"].strip(), usage


async def _generate_nvidia(system_prompt: str, user_prompt: str, max_tokens: int, temperature: float) -> str:
    key = os.getenv("NVIDIA_API_KEY")
    if not key:
        raise RuntimeError("NVIDIA_API_KEY is not configured.")
    payload = {
        "model": os.getenv("NVIDIA_MODEL", "deepseek-ai/deepseek-r1"),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post("https://integrate.api.nvidia.com/v1/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        usage = data.get("usage", {}).get("total_tokens", 0)
        return data["choices"][0]["message"]["content"].strip(), usage


async def _generate_openrouter(system_prompt: str, user_prompt: str, max_tokens: int, temperature: float) -> str:
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured.")
    payload = {
        "model": os.getenv("OPENROUTER_MODEL", "~anthropic/claude-sonnet-latest"),
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
        usage = data.get("usage", {}).get("total_tokens", 0)
        return data["choices"][0]["message"]["content"].strip(), usage


def _run_huggingface(system_prompt: str, user_prompt: str, max_tokens: int, temperature: float) -> str:
    key = os.getenv("HUGGINGFACEHUB_API_TOKEN") or os.getenv("HF_TOKEN")
    if not key:
        raise RuntimeError("HUGGINGFACEHUB_API_TOKEN or HF_TOKEN is not configured.")
    
    llm = HuggingFaceEndpoint(
        repo_id=os.getenv("HUGGINGFACE_MANUSCRIPT_MODEL", "mistralai/Mixtral-8x7B-Instruct-v0.1"),
        task="text-generation",
        max_new_tokens=max_tokens,
        temperature=temperature,
        huggingfacehub_api_token=key,
    )
    prompt = f"{system_prompt}\n\n{user_prompt}" if system_prompt else user_prompt
    if "[INST]" not in prompt:
        prompt = f"[INST] {prompt} [/INST]"
        
    try:
        content = llm.invoke(prompt).strip()
        usage = (len(prompt) + len(content)) // 4
        return content, usage
    except StopIteration as e:
        raise RuntimeError(f"LangChain StopIteration: {e}") from e


async def _generate_huggingface(system_prompt: str, user_prompt: str, max_tokens: int, temperature: float) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _run_huggingface, system_prompt, user_prompt, max_tokens, temperature)


async def get_embedding(text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> list[float] | None:
    global _gemini_client
    if not _gemini_client:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            return None
        _gemini_client = genai.Client(api_key=key)
        
    try:
        response = await _gemini_client.aio.models.embed_content(
            model="models/gemini-embedding-2",
            contents=text,
            config=genai_types.EmbedContentConfig(task_type=task_type)
        )
        return response.embeddings[0].values
    except Exception as e:
        logger.warning(f"Failed to get embedding: {e}")
        return None

async def generate_completion(system_prompt: str, user_prompt: str, max_tokens: int = 1200, temperature: float = 0.45, provider_override: str = None, model: str = None, cached_content: str = None) -> str:
    """
    Attempts to generate a completion by cascading through configured AI providers.
    """
    effective_provider = provider_override or current_provider.get()
    effective_model = model or current_model.get()

    if effective_provider:
        effective_provider = effective_provider.lower()
        provider_fn = None
        if effective_provider == "openai":
            provider_fn = _generate_openai
        elif effective_provider == "gemini":
            provider_fn = _generate_gemini
        elif effective_provider == "groq":
            provider_fn = _generate_groq
        elif effective_provider == "openrouter":
            provider_fn = _generate_openrouter
        elif effective_provider == "nvidia":
            provider_fn = _generate_nvidia
        elif effective_provider == "huggingface":
            provider_fn = _generate_huggingface
            
        if not provider_fn:
            raise RuntimeError(f"Unknown provider '{effective_provider}'.")
            
        for attempt in range(2):
            try:
                user_id = usage_tracker.current_user_id.get()
                if user_id:
                    await usage_tracker.check_quota(user_id)
                # Note: passing effective_model to Gemini explicitly
                if effective_provider == "gemini":
                    result, tokens = await asyncio.wait_for(provider_fn(system_prompt, user_prompt, max_tokens, temperature, effective_model, cached_content), timeout=60)
                else:
                    # Others don't currently take model arg directly in their signature except Gemini, wait, _generate_groq takes kwargs?
                    # Let's inspect signature or just pass model if supported. 
                    # Currently the _generate_* functions only take system_prompt, user_prompt, max_tokens, temperature except gemini
                    # So for others we just call them normally, they fetch models from env.
                    result, tokens = await asyncio.wait_for(provider_fn(system_prompt, user_prompt, max_tokens, temperature), timeout=60)
                if user_id:
                    await usage_tracker.log_usage(user_id, tokens, effective_provider.title())
                return result
            except Exception as e:
                logger.error(f"{effective_provider.title()} generation failed (attempt {attempt + 1}): {e}")
                if attempt == 0:
                    await asyncio.sleep(2)
        raise RuntimeError(f"{effective_provider.title()} provider failed to generate a completion.")

    providers = []
    if LLM_PROVIDER in ("auto", "openai") and os.getenv("OPENAI_API_KEY"):
        providers.append(("OpenAI", _generate_openai))
    if LLM_PROVIDER in ("auto", "gemini") and os.getenv("GEMINI_API_KEY"):
        providers.append(("Gemini", _generate_gemini))
    if LLM_PROVIDER in ("auto", "groq"):
        providers.append(("Groq", _generate_groq))
    if LLM_PROVIDER in ("auto", "openrouter"):
        providers.append(("OpenRouter", _generate_openrouter))
    if LLM_PROVIDER in ("auto", "nvidia") and os.getenv("NVIDIA_API_KEY"):
        providers.append(("NVIDIA", _generate_nvidia))
    if LLM_PROVIDER in ("auto", "huggingface"):
        providers.append(("Hugging Face", _generate_huggingface))

    for provider_name, provider_func in providers:
        for attempt in range(2):
            try:
                user_id = usage_tracker.current_user_id.get()
                if user_id:
                    await usage_tracker.check_quota(user_id)
                if provider_name == "Gemini":
                    result, tokens = await asyncio.wait_for(provider_func(system_prompt, user_prompt, max_tokens, temperature, effective_model, cached_content), timeout=60)
                else:
                    result, tokens = await asyncio.wait_for(provider_func(system_prompt, user_prompt, max_tokens, temperature), timeout=60)
                if user_id:
                    await usage_tracker.log_usage(user_id, tokens, provider_name)
                return result
            except Exception as e:
                logger.error(f"{provider_name} generation failed (attempt {attempt + 1}): {e}")
                if attempt == 0:
                    await asyncio.sleep(2)
        if LLM_PROVIDER != "auto":
            break
            
    raise RuntimeError("All configured AI providers failed to generate a completion.")


import json

async def _stream_openai_compatible(url: str, headers: dict, payload: dict):
    payload["stream"] = True
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        delta = data.get("choices", [{}])[0].get("delta", {}).get("content")
                        if delta:
                            yield {"type": "chunk", "text": delta}
                    except json.JSONDecodeError:
                        pass
                yield {"type": "done"}
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            retry_after = e.response.headers.get("Retry-After")
            if not retry_after:
                retry_after = e.response.headers.get("x-ratelimit-reset-requests") or e.response.headers.get("x-ratelimit-reset-tokens")
            retry_val = None
            try:
                if retry_after:
                    retry_val = float(retry_after.replace('s',''))
            except:
                pass
            yield {"type": "stopped", "reason": "rate_limit", "retry_after_seconds": retry_val}
        else:
            yield {"type": "stopped", "reason": "error", "message": f"HTTP Error {e.response.status_code}"}
    except Exception as e:
        yield {"type": "stopped", "reason": "error", "message": str(e)}

async def stream_completion(system_prompt: str, user_prompt: str, max_tokens: int, temperature: float, provider: str, model: str = None, cached_content: str = None):
    effective_provider = provider or current_provider.get()
    effective_model = model or current_model.get()
    
    if not effective_provider:
        yield {"type": "stopped", "reason": "error", "message": "Provider not specified."}
        return
        
    effective_provider = effective_provider.lower()
    
    if effective_provider == "groq":
        key = os.getenv("GROQ_API_KEY")
        if not key:
            yield {"type": "stopped", "reason": "error", "message": "GROQ_API_KEY not configured."}
            return
        payload = {
            "model": effective_model or os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        async for chunk in _stream_openai_compatible("https://api.groq.com/openai/v1/chat/completions", headers, payload):
            yield chunk

    elif provider == "nvidia":
        key = os.getenv("NVIDIA_API_KEY")
        if not key:
            yield {"type": "stopped", "reason": "error", "message": "NVIDIA_API_KEY not configured."}
            return
        payload = {
            "model": effective_model or os.getenv("NVIDIA_MODEL", "deepseek-ai/deepseek-r1"),
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        async for chunk in _stream_openai_compatible("https://integrate.api.nvidia.com/v1/chat/completions", headers, payload):
            yield chunk

    elif provider == "openrouter":
        key = os.getenv("OPENROUTER_API_KEY")
        if not key:
            yield {"type": "stopped", "reason": "error", "message": "OPENROUTER_API_KEY not configured."}
            return
        payload = {
            "model": effective_model or os.getenv("OPENROUTER_MODEL", "anthropic/claude-3-haiku"),
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        async for chunk in _stream_openai_compatible("https://openrouter.ai/api/v1/chat/completions", headers, payload):
            yield chunk

    elif provider == "openai":
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            yield {"type": "stopped", "reason": "error", "message": "OPENAI_API_KEY not configured."}
            return
        payload = {
            "model": effective_model or os.getenv("OPENAI_MODEL", "gpt-4o"),
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        async for chunk in _stream_openai_compatible("https://api.openai.com/v1/chat/completions", headers, payload):
            yield chunk

    elif provider == "gemini":
        global _gemini_client
        if not _gemini_client:
            key = os.getenv("GEMINI_API_KEY")
            if not key:
                yield {"type": "stopped", "reason": "error", "message": "GEMINI_API_KEY not configured."}
                return
            _gemini_client = genai.Client(api_key=key)
            
        config = genai_types.GenerateContentConfig(
            system_instruction=None if cached_content else (system_prompt or None),
            temperature=temperature,
            max_output_tokens=max_tokens,
            cached_content=cached_content
        )

        try:
            response_stream = await _gemini_client.aio.models.generate_content_stream(
                model=effective_model or os.getenv("GEMINI_MODEL", "gemini-flash-latest"),
                contents=user_prompt,
                config=config,
            )
            async for chunk in response_stream:
                if chunk.text:
                    yield {"type": "chunk", "text": chunk.text}
                if hasattr(chunk, 'usage_metadata') and chunk.usage_metadata:
                    logger.info(f"Gemini stream usage: {chunk.usage_metadata}")
            yield {"type": "done"}
        except Exception as e:
            if "429" in str(e):
                yield {"type": "stopped", "reason": "rate_limit", "retry_after_seconds": None}
            else:
                yield {"type": "stopped", "reason": "error", "message": str(e)}
    else:
        yield {"type": "stopped", "reason": "error", "message": f"Unknown provider {effective_provider}"}

