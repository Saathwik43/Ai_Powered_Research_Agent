import os
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
import httpx
from dotenv import load_dotenv

load_dotenv()

provider_semaphores = {
    "Groq": asyncio.Semaphore(5),
    "OpenRouter": asyncio.Semaphore(5),
    "Cerebras": asyncio.Semaphore(5),
    "HuggingFace": asyncio.Semaphore(3),
    "Mistral": asyncio.Semaphore(3),
    "Gemini": asyncio.Semaphore(3),
    "OpenAI": asyncio.Semaphore(2),   # Free tier: 3 RPM ceiling, keep concurrency low
}

global_llm_sem = asyncio.Semaphore(3)  # kept for relevance.py backward-compat import

from langchain_huggingface import HuggingFaceEndpoint
from google import genai
from google.genai import types as genai_types
from contextvars import ContextVar

current_provider: ContextVar[str | None] = ContextVar("current_provider", default=None)
current_model: ContextVar[str | None] = ContextVar("current_model", default=None)
from services import usage_tracker
import time

logger = logging.getLogger(__name__)

_gemini_caches = {}
# Remembers contexts the API refused to cache, so a context that sits just under
# the real minimum doesn't cost an extra failed round-trip on every request.
_gemini_cache_failures = {}

_GEMINI_CACHE_TTL_SECONDS = 1800
_GEMINI_CACHE_FAILURE_TTL = 300

# Minimum context size worth caching.
#
# This was 32768, which is the minimum for the *gemini-1.5* generation. The
# default model here is `gemini-flash-latest`, where the floor is ~1k tokens.
# At 32768 the gate never opened: PDF chat caps its context at 40 000 chars,
# which the old `words * 1.3` estimator scored at ~8.7k -- 3.8x below the gate --
# so the full context was re-sent on every single chat message.
_GEMINI_CACHE_MIN_TOKENS = 2048

# ~4 chars/token is the standard rule of thumb and holds up on dense academic
# prose. `len(text.split()) * 1.3` under-counts it by roughly a third, because
# academic tokens are longer than the ~0.75 words/token that ratio assumes.
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    return len(text or "") // _CHARS_PER_TOKEN


def _sweep_expired(now: float) -> None:
    """Drop lapsed entries; without this both dicts grow for process lifetime."""
    for name, expiry in list(_gemini_caches.items()):
        if now >= expiry[1]:
            del _gemini_caches[name]
    for name, failed_at in list(_gemini_cache_failures.items()):
        if now - failed_at >= _GEMINI_CACHE_FAILURE_TTL:
            del _gemini_cache_failures[name]


async def get_or_create_gemini_cache(cache_key: str, system_instruction: str, shared_context: str, model: str = None) -> str | None:
    global _gemini_client
    if not _gemini_client:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            return None
        _gemini_client = genai.Client(api_key=key)

    model_name = model or os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    # A cached content handle is bound to the model that created it, so the model
    # has to be part of the key -- otherwise a handle made for flash would be
    # handed to a pro request and rejected.
    scoped_key = f"{cache_key}|{model_name}"

    now = time.time()
    _sweep_expired(now)

    entry = _gemini_caches.get(scoped_key)
    if entry:
        cache_name, expiry = entry
        if now < expiry:
            return cache_name
        del _gemini_caches[scoped_key]

    if estimate_tokens(shared_context) < _GEMINI_CACHE_MIN_TOKENS:
        return None

    if scoped_key in _gemini_cache_failures:
        return None

    try:
        cache = await _gemini_client.aio.caches.create(
            model=model_name,
            config=genai_types.CreateCachedContentConfig(
                contents=[shared_context],
                system_instruction=system_instruction or None,
                ttl=f"{_GEMINI_CACHE_TTL_SECONDS}s"
            )
        )
        _gemini_caches[scoped_key] = (cache.name, now + _GEMINI_CACHE_TTL_SECONDS)
        logger.info(
            f"Gemini context cache created for {scoped_key} "
            f"(~{estimate_tokens(shared_context)} tokens)"
        )
        return cache.name
    except Exception as e:
        _gemini_cache_failures[scoped_key] = now
        logger.warning(f"Failed to create Gemini cache for {scoped_key}: {e}")
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
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-large-latest")
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY")
CEREBRAS_MODEL = os.getenv("CEREBRAS_MODEL", "llama-3.3-70b")

# google-genai Client — created once at module level if key is available.
# The old google-generativeai SDK used genai.configure() globally; the new SDK
# uses a per-client api_key instead.
_gemini_client: "genai.Client | None" = None
if GEMINI_API_KEY:
    _gemini_client = genai.Client(api_key=GEMINI_API_KEY)

_executor = ThreadPoolExecutor(max_workers=4)


def _gemini_thinking_config(max_tokens: int, is_pro: bool):
    """Flash models reject thinking_budget=0; 128 is the current floor."""
    if is_pro:
        return None
    budget = 128 if max_tokens < 2000 else 300
    return genai_types.ThinkingConfig(thinking_budget=budget)


async def _generate_gemini(system_prompt: str, user_prompt: str, max_tokens: int, temperature: float, model: str = None, cached_content: str = None) -> str:
    global _gemini_client
    if not _gemini_client:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not configured.")
        _gemini_client = genai.Client(api_key=key)

    model_name = model or os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    is_pro = "pro" in model_name.lower()
    
    config_kwargs = {
        "system_instruction": None if cached_content else (system_prompt or None),
        "temperature": temperature,
        "max_output_tokens": max_tokens,
        "cached_content": cached_content
    }
    
    thinking = _gemini_thinking_config(max_tokens, is_pro)
    if thinking is not None:
        config_kwargs["thinking_config"] = thinking
        
    config = genai_types.GenerateContentConfig(**config_kwargs)

    try:
        response = await _gemini_client.aio.models.generate_content(
            model=model_name,
            contents=user_prompt,
            config=config,
        )
        usage = response.usage_metadata.total_token_count if hasattr(response, 'usage_metadata') and response.usage_metadata else 0
        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            thoughts=getattr(response.usage_metadata, 'thoughts_token_count',0)
            output = getattr(response.usage_metadata, 'candidates_token_count',0)
            logger.info(f"Gemini usage: total = {usage}, thinking={thoughts},output={output}")
            if thoughts and output and thoughts > output : 
                logger.warning(f"Thinking Consumed more budget than output (thinking={thoughts},output={output}) - likely truncated")
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


async def _generate_mistral(system_prompt: str, user_prompt: str, max_tokens: int, temperature: float) -> str:
    key = os.getenv("MISTRAL_API_KEY")
    if not key:
        raise RuntimeError("MISTRAL_API_KEY is not configured.")
    payload = {
        "model": os.getenv("MISTRAL_MODEL", "mistral-large-2407"),
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
        response = await client.post("https://api.mistral.ai/v1/chat/completions", headers=headers, json=payload)
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
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        usage = data.get("usage", {}).get("total_tokens", 0)
        return data["choices"][0]["message"]["content"].strip(), usage


async def _generate_cerebras(system_prompt: str, user_prompt: str, max_tokens: int, temperature: float) -> str:
    key = os.getenv("CEREBRAS_API_KEY")
    if not key:
        raise RuntimeError("CEREBRAS_API_KEY is not configured.")
    payload = {
        "model": os.getenv("CEREBRAS_MODEL", "llama-3.3-70b"),
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
        response = await client.post("https://api.cerebras.ai/v1/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        usage = data.get("usage", {}).get("total_tokens", 0)
        return data["choices"][0]["message"]["content"].strip(), usage


async def _generate_nvidia(system_prompt: str, user_prompt: str, max_tokens: int, temperature: float) -> str:
    key = os.getenv("NVIDIA_API_KEY")
    if not key:
        raise RuntimeError("NVIDIA_API_KEY is not configured.")
    payload = {
        "model": os.getenv("NVIDIA_MODEL", "meta/llama-3.1-70b-instruct"),
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
        try:
            response = await client.post("https://integrate.api.nvidia.com/v1/chat/completions", headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            usage = data.get("usage", {}).get("total_tokens", 0)
            return data["choices"][0]["message"]["content"].strip(), usage
        except httpx.HTTPStatusError as e:
            logger.error(f"NVIDIA error {e.response.status_code}: {e.response.text}")
            raise


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
        "Authorization": f"Bearer {key}",
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


EMBEDDING_MODEL = "models/gemini-embedding-2"

# embed_content accepts a list of contents, so a whole rerank window costs one
# request instead of one per paper. Chunked to stay inside the per-request cap.
EMBEDDING_BATCH_SIZE = 50


def _ensure_gemini_client():
    """Return the shared client, or None when no key is configured."""
    global _gemini_client
    if not _gemini_client:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            return None
        _gemini_client = genai.Client(api_key=key)
    return _gemini_client


async def get_embeddings_batch(
    texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT"
) -> list[list[float] | None]:
    """
    Embed *texts* in as few requests as possible.

    Always returns one entry per input, positionally aligned, with None where
    the embedding could not be produced. A failing chunk yields None for its
    own texts only; other chunks still return values.
    """
    from services.api_telemetry import track_call

    if not texts:
        return []

    client = _ensure_gemini_client()
    if not client:
        return [None] * len(texts)

    out: list[list[float] | None] = []
    for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
        chunk = texts[start:start + EMBEDDING_BATCH_SIZE]
        async with track_call("Google Gemini", "embed") as rec:
            try:
                response = await client.aio.models.embed_content(
                    model=EMBEDDING_MODEL,
                    contents=chunk,
                    config=genai_types.EmbedContentConfig(task_type=task_type)
                )
                embeddings = list(response.embeddings or [])
                values = [getattr(e, "values", None) for e in embeddings]
                # Never let a short response shift the caller's alignment.
                if len(values) < len(chunk):
                    values.extend([None] * (len(chunk) - len(values)))
                out.extend(values[:len(chunk)])
                rec.succeed(http_status=200, items=sum(1 for v in values if v))
            except Exception as e:
                rec.fail(error=str(e))
                logger.warning(f"Failed to get embeddings for {len(chunk)} text(s): {e}")
                out.extend([None] * len(chunk))
    return out


async def get_embedding(text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> list[float] | None:
    results = await get_embeddings_batch([text], task_type)
    return results[0] if results else None

_TELEMETRY_NAMES = {
    "openai": "OpenAI",
    "gemini": "Google Gemini",
    "groq": "Groq",
    "openrouter": "OpenRouter",
    "cerebras": "Cerebras",
    "nvidia": "NVIDIA NIM",
    "huggingface": "Hugging Face Inference",
    "mistral": "Mistral",
}


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
        elif effective_provider == "cerebras":
            provider_fn = _generate_cerebras
        elif effective_provider == "nvidia":
            provider_fn = _generate_nvidia
        elif effective_provider == "huggingface":
            provider_fn = _generate_huggingface
        elif effective_provider == "mistral":
            provider_fn = _generate_mistral
            
        if not provider_fn:
            raise RuntimeError(f"Unknown provider '{effective_provider}'.")
            
        from services.api_telemetry import track_call
        tel_name = _TELEMETRY_NAMES.get(effective_provider, effective_provider)
        for attempt in range(2):
            try:
                user_id = usage_tracker.current_user_id.get()
                if user_id:
                    await usage_tracker.check_quota(user_id)
                async with track_call(tel_name, "generate") as rec:
                    if effective_provider == "gemini":
                        result, tokens = await asyncio.wait_for(provider_fn(system_prompt, user_prompt, max_tokens, temperature, effective_model, cached_content), timeout=60)
                    else:
                        result, tokens = await asyncio.wait_for(provider_fn(system_prompt, user_prompt, max_tokens, temperature), timeout=60)
                    rec.succeed(http_status=200, items=tokens)
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
    if LLM_PROVIDER == "openrouter":
        providers.append(("OpenRouter", _generate_openrouter))
    if LLM_PROVIDER in ("auto", "cerebras") and os.getenv("CEREBRAS_API_KEY"):
        providers.append(("Cerebras", _generate_cerebras))
    if LLM_PROVIDER in ("auto", "mistral") and os.getenv("MISTRAL_API_KEY"):
        providers.append(("Mistral", _generate_mistral))
    if LLM_PROVIDER in ("auto", "huggingface"):
        providers.append(("HuggingFace", _generate_huggingface))


    from services.api_telemetry import track_call
    for provider_name, provider_func in providers:
        for attempt in range(2):
            try:
                user_id = usage_tracker.current_user_id.get()
                if user_id:
                    await usage_tracker.check_quota(user_id)
                sem=provider_semaphores.get(provider_name, asyncio.Semaphore(3))
                tel_name = _TELEMETRY_NAMES.get(provider_name.lower(), provider_name)
                async with sem:
                    async with track_call(tel_name, "generate") as rec:
                        if provider_name== "Gemini":
                            result,tokens = await asyncio.wait_for(provider_func(system_prompt, user_prompt , max_tokens , temperature, effective_model, cached_content),timeout=60)
                        else:
                            result,tokens = await asyncio.wait_for(provider_func(system_prompt, user_prompt , max_tokens , temperature),timeout=60)
                        rec.succeed(http_status=200, items=tokens)
                if user_id:
                    await usage_tracker.log_usage(user_id, tokens, provider_name)
                    await usage_tracker.check_provider_rpd(provider_name)
                return result
            except Exception as e:
                import httpx
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 402:
                    logger.info(f"{provider_name} skipped: account out of credits (402).")
                    break
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429:
                    logger.info(f"{provider_name} skipped: rate limited(429), moving to nextt provider.")
                    break
                logger.error(f"{provider_name} generation failed (attempt {attempt + 1}): {e}")
                if attempt == 0:
                    await asyncio.sleep(2)
        if LLM_PROVIDER != "auto":
            break
            
    raise RuntimeError("All configured AI providers failed to generate a completion.")


import json

async def _stream_openai_compatible(url: str, headers: dict, payload: dict, provider_name: str = "LLM"):
    from services.api_telemetry import track_call

    payload = dict(payload)
    payload["stream"] = True
    if provider_name in ("Groq", "OpenAI", "OpenRouter"):
        payload["stream_options"] = {"include_usage": True}

    async with track_call(provider_name, "stream") as rec:
        try:
            total_chars = 0
            usage_tokens = 0
            timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
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
                            if data.get("usage"):
                                usage_tokens = data["usage"].get("total_tokens", 0) or 0
                            delta = data.get("choices", [{}])[0].get("delta", {}).get("content")
                            if delta:
                                total_chars += len(delta)
                                yield {"type": "chunk", "text": delta}
                        except json.JSONDecodeError as e:
                            logger.warning(f"Stream chunk parse failure, raw line: {data_str[:200]!r} — error: {e}")
                            continue
                    logger.info(f"Stream completed: {total_chars} chars from {url}")
                    if usage_tokens:
                        user_id = usage_tracker.current_user_id.get()
                        if user_id:
                            await usage_tracker.log_usage(user_id, usage_tokens, provider_name)
                    rec.succeed(http_status=200, items=usage_tokens or total_chars)
                    yield {"type": "done"}
        except httpx.HTTPStatusError as e:
            await e.response.aread()
            rec.fail(http_status=e.response.status_code, error=f"HTTP {e.response.status_code}")
            logger.error(f"Stream API HTTP Error {e.response.status_code}: {e.response.text}")
            if e.response.status_code == 429:
                retry_after = e.response.headers.get("Retry-After")
                if not retry_after:
                    retry_after = e.response.headers.get("x-ratelimit-reset-requests") or e.response.headers.get("x-ratelimit-reset-tokens")
                retry_val = None
                try:
                    if retry_after:
                        retry_val = float(retry_after.replace('s',''))
                except Exception:
                    pass
                yield {"type": "stopped", "reason": "rate_limit", "retry_after_seconds": retry_val}
            elif e.response.status_code == 402:
                yield {"type": "stopped", "reason": "payment_required", "message": "Payment required (out of credits)."}
            else:
                yield {"type": "stopped", "reason": "error", "message": f"HTTP Error {e.response.status_code}"}
        except Exception as e:
            rec.fail(error=str(e))
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
        async for chunk in _stream_openai_compatible("https://api.groq.com/openai/v1/chat/completions", headers, payload, "Groq"):
            yield chunk

    elif effective_provider == "nvidia":
        key = os.getenv("NVIDIA_API_KEY")
        if not key:
            yield {"type": "stopped", "reason": "error", "message": "NVIDIA_API_KEY not configured."}
            return
        payload = {
            "model": effective_model or os.getenv("NVIDIA_MODEL", "meta/llama-3.1-70b-instruct"),
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        async for chunk in _stream_openai_compatible("https://integrate.api.nvidia.com/v1/chat/completions", headers, payload, "NVIDIA NIM"):
            yield chunk

    elif effective_provider == "cerebras":
        key = os.getenv("CEREBRAS_API_KEY")
        if not key:
            yield {"type": "stopped", "reason": "error", "message": "CEREBRAS_API_KEY not configured."}
            return
        payload = {
            "model": effective_model or os.getenv("CEREBRAS_MODEL", "llama-3.3-70b"),
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        async for chunk in _stream_openai_compatible("https://api.cerebras.ai/v1/chat/completions", headers, payload, "Cerebras"):
            yield chunk

    elif effective_provider == "openrouter":
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
        async for chunk in _stream_openai_compatible("https://openrouter.ai/api/v1/chat/completions", headers, payload, "OpenRouter"):
            yield chunk

    elif effective_provider == "openai":
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
        async for chunk in _stream_openai_compatible("https://api.openai.com/v1/chat/completions", headers, payload, "OpenAI"):
            yield chunk

    elif effective_provider == "mistral":
        key = os.getenv("MISTRAL_API_KEY")
        if not key:
            yield {"type": "stopped", "reason": "error", "message": "MISTRAL_API_KEY not configured."}
            return
        payload = {
            "model": effective_model or os.getenv("MISTRAL_MODEL", "mistral-large-latest"),
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        async for chunk in _stream_openai_compatible("https://api.mistral.ai/v1/chat/completions", headers, payload, "Mistral"):
            yield chunk

    elif effective_provider == "huggingface":
        from services.api_telemetry import track_call
        async with track_call("Hugging Face Inference", "generate") as rec:
            try:
                text, _tokens = await _generate_huggingface(system_prompt, user_prompt, max_tokens, temperature)
                rec.succeed(http_status=200, items=_tokens)
                yield {"type": "chunk", "text": text}
                yield {"type": "done"}
            except Exception as e:
                rec.fail(error=str(e))
                yield {"type": "stopped", "reason": "error", "message": str(e)}

    elif effective_provider == "gemini":
        from services.api_telemetry import track_call
        global _gemini_client
        if not _gemini_client:
            key = os.getenv("GEMINI_API_KEY")
            if not key:
                yield {"type": "stopped", "reason": "error", "message": "GEMINI_API_KEY not configured."}
                return
            _gemini_client = genai.Client(api_key=key)
            
        model_name = effective_model or os.getenv("GEMINI_MODEL", "gemini-flash-latest")
        is_pro = "pro" in model_name.lower()
        
        config_kwargs = {
            "system_instruction": None if cached_content else (system_prompt or None),
            "temperature": temperature,
            "max_output_tokens": max_tokens,
            "cached_content": cached_content
        }
        
        thinking = _gemini_thinking_config(max_tokens, is_pro)
        if thinking is not None:
            config_kwargs["thinking_config"] = thinking
            
        config = genai_types.GenerateContentConfig(**config_kwargs)

        async with track_call("Google Gemini", "stream") as rec:
            try:
                response_stream = await _gemini_client.aio.models.generate_content_stream(
                    model=model_name,
                    contents=user_prompt,
                    config=config,
                )
                async for chunk in response_stream:
                    if chunk.text:
                        yield {"type": "chunk", "text": chunk.text}
                    if hasattr(chunk, 'usage_metadata') and chunk.usage_metadata:
                        logger.info(f"Gemini stream usage: {chunk.usage_metadata}")
                rec.succeed(http_status=200)
                yield {"type": "done"}
            except Exception as e:
                rec.fail(error=str(e))
                if "429" in str(e):
                    yield {"type": "stopped", "reason": "rate_limit", "retry_after_seconds": None}
                else:
                    yield {"type": "stopped", "reason": "error", "message": str(e)}
    else:
        yield {"type": "stopped", "reason": "error", "message": f"Unknown provider {effective_provider}"}


async def stream_completion_auto(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    temperature: float,
    cached_content: str = None,
    gemini_cache_resolver=None,
    user_prompt_cached: str = None,
):
    """
    Cascade across providers, resuming from partial output on failure.

    *gemini_cache_resolver* is an awaitable factory that materialises a Gemini
    context cache. It is invoked only when the cascade actually reaches Gemini,
    because creating a cache the run never uses costs an API round-trip for
    nothing. When it yields a handle, *user_prompt_cached* (the same prompt with
    the shared context removed) is used for that leg, so the context is sent
    once via the cache rather than twice.
    """
    fixed_order = ("openai", "gemini", "groq", "cerebras", "mistral", "huggingface")
    full_accumulated_text = ""

    for provider in fixed_order:
        leg_prompt = user_prompt
        leg_cache = cached_content if provider == "gemini" else None

        if provider == "gemini" and not leg_cache and gemini_cache_resolver and not full_accumulated_text:
            # Skipped once output exists: a resumed leg carries a continuation
            # note, so the context-free prompt would no longer be equivalent.
            try:
                leg_cache = await gemini_cache_resolver()
            except Exception as e:
                logger.warning(f"Gemini cache resolution failed, sending context inline: {e}")
                leg_cache = None
            if leg_cache and user_prompt_cached:
                leg_prompt = user_prompt_cached

        if full_accumulated_text:
            word_count = len(full_accumulated_text.split())
            estimated_tokens = word_count * 1.3
            if estimated_tokens > (max_tokens * 0.8):
                yield {"type": "stopped", "reason": "max_length_reached", "message": "Maximum text length reached during provider fallback."}
                return

            continuation_note = f"\n\n---\nA partial draft has already been written below. Continue seamlessly from exactly where it stops — do not repeat, rephrase, or restart any part of it, and match its existing tone/style:\n\n{full_accumulated_text}\n---\n"
            effective_user_prompt = leg_prompt + continuation_note
        else:
            effective_user_prompt = leg_prompt

        yield {"type": "provider_active", "provider": provider, "continuing": bool(full_accumulated_text)}

        try:
            async for chunk in stream_completion(system_prompt, effective_user_prompt, max_tokens, temperature, provider, None, leg_cache):
                if chunk.get("type") == "chunk":
                    full_accumulated_text += chunk.get("text", "")
                    yield chunk
                elif chunk.get("type") == "stopped":
                    raise RuntimeError(chunk.get("reason", "stopped"))
                elif chunk.get("type") == "done":
                    yield chunk
                    return
        except Exception as e:
            if "payment_required" in str(e):
                logger.info(f"Auto mode {provider} skipped: account out of credits (402).")
            else:
                logger.error(f"Auto mode {provider} failed: {e}")
            if full_accumulated_text:
                yield {"type": "provider_status", "message": "Switching provider, resuming draft..."}
            continue

    yield {"type": "stopped", "reason": "all_providers_failed", "message": "All AI providers failed to complete the generation."}
