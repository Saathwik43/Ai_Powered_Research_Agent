"""Plain-language paper briefing for literature cards.

Search results only carry title + abstract. The evidence ladder that reads
full text runs at manuscript time and is too expensive for every survey card.

LLM briefing is preferred. If the model is slow or missing, `extractive_brief`
turns the abstract into labeled bullets so the card never falls back to a
truncated paragraph.

`/api/literature` briefs the head of its first page inline under a deadline so
a card can arrive complete, and warms the rest of that page afterwards. Both
paths read and write one durable cache, so the second search for a paper — by
this worker, another worker, or another user — costs nothing and produces the
same bullets. Only model output is stored; caching the extractive fallback
would freeze a degraded briefing in place for hours.
"""

from __future__ import annotations

import json
import logging
import re
import time

import anyio

from ai.llm_provider import generate_completion
from core import shared_store
from core.paper_identity import identity_keys, paper_identity
from services.usage_tracker import tagged

logger = logging.getLogger(__name__)

_CACHE_TTL = 600
_SHARED_TTL = 6 * 3600
_brief_cache: dict[str, tuple[list[str], float]] = {}
# Full-text briefings are keyed separately: same paper, different depth, and a
# different value shape (bullets plus which source they were read from).
_deep_cache: dict[str, tuple[dict, float]] = {}

# How many of the first page are briefed before the search response is sent,
# and how long search is willing to wait for them. The rest of the page is
# warmed after the response. 15 is the frontend's PAGE_SIZE.
INLINE_BRIEF_COUNT = 8
INLINE_BRIEF_DEADLINE = 2.5
WARM_BRIEF_COUNT = 15
_WARM_DEADLINE = 25.0

_enabled = True


def set_enabled(value: bool) -> None:
    """Turn search-time briefing off — used by the test suite, see conftest."""
    global _enabled
    _enabled = bool(value)


def enabled() -> bool:
    return _enabled

_MAX_BULLETS = 5
_MIN_BULLETS = 2
_ABSTRACT_CHARS = 2000
_BULLET_CHARS = 280
_SOFT_CHARS = 180
_HARD_CHARS = 260
_MIN_CLIP = 80
_ELLIPSIS = "…"
_WHITESPACE_RE = re.compile(r"\s+")
# A sentence end, ignoring the abbreviations that look like one.
_SENTENCE_END_RE = re.compile(
    r"(?<!\be\.g)(?<!\bi\.e)(?<!\bet al)(?<!\bvs)(?<!\bFig)(?<!\bEq)(?<!\bRef)"
    r"(?<!\bcf)(?<!\bapprox)[.!?][\"'”)\]]?(?=\s|$)"
)
_SPLIT_RE = re.compile(
    r"(?<=[.!?])\s+|(?<=;)\s+|(?=\bWe\b)|(?=\bThis paper\b)|(?=\bOur\b)",
    re.I,
)
_LABELS = ("About", "Method", "Finding", "Results", "Limit")
_FILL_LABELS = ("About", "Finding", "Method")
_PLACEHOLDER_ABSTRACTS = {
    "No abstract available",
    "No abstract available.",
    "Abstract not available via PubMed summary API.",
}
_LIMIT_RE = re.compile(
    r"\b(limit|however|cannot|do not|future work|only when|does not yet)\b",
    re.I,
)
# Results needs a stated outcome, not an evaluation setup ("we evaluate on three
# datasets") and not background prose that happens to contain an outcome word
# ("increasingly used"). Either a number, or a comparative verb owned by the work.
_RESULTS_NUM_RE = re.compile(
    r"(\d+(\.\d+)?\s*%|\b\d+(\.\d+)?\s*x\b|"
    r"\b(accuracy|auc|f1|bleu|rouge|precision|recall|error rate|mae|rmse)\b"
    r"[^.]{0,40}\d)",
    re.I,
)
_RESULTS_VERB_RE = re.compile(
    r"\b(outperform|surpass|achiev|attain|improv|reduc|boost|yield)\w*\b",
    re.I,
)
_RESULTS_SUBJECT_RE = re.compile(
    r"\b(our|we|proposed|baseline|state[- ]of[- ]the[- ]art|sota|results?|"
    r"experiments?|evaluation)\b",
    re.I,
)
_DIGIT_RE = re.compile(r"\d")
_DANGLING_RE = re.compile(
    r"[\s,;:]*\b(and|or|but|which|that|while|whereas|as well as)\s*$",
    re.I,
)
_METHOD_RE = re.compile(
    r"\b(we (propose|present|introduce|develop|use|apply|focus|train|design|"
    r"evaluate|benchmark|test)|"
    r"method|approach|algorithm|framework|evaluat|experiment|benchmark|"
    r"dataset|\busing\b)",
    re.I,
)
_FINDING_RE = re.compile(
    r"\b(we (show|find|demonstrate|observe|conclude|investigate|study)|"
    r"this paper (shows|presents|argues)|contribution)\b",
    re.I,
)

_SYSTEM_PROMPT = """You write a short briefing of a research paper from its title and abstract only.
A reader should understand the work without opening the paper.

Output ONLY valid JSON:
{"bullets": ["...", "...", "..."]}

Write 4 or 5 bullets, in this order, skipping any point the abstract does not support:
1. About: what the paper is about, in plain language
2. Method: how the authors did the work
3. Finding: the main claim or contribution
4. Results: numbers or outcomes only if the abstract states them. If the abstract
   only describes an evaluation setup and never reports an outcome, skip this bullet.
5. Limit: a limitation only if the abstract states one

Rules:
- Easy terms. If you must use a technical word, follow it with a short gloss in parentheses.
- Do not invent methods, findings, or numbers that are not in the title or abstract.
- You cannot see the full paper. Never claim to summarize a section you were not given.
- Each bullet starts with a label and a colon, e.g. "About: ..."
- One complete sentence per bullet, under 200 characters, so it is never cut short.
- If the abstract itself stops mid-sentence, do not guess how it ends; drop that point."""

_DEEP_SYSTEM_PROMPT = """You write a short briefing of a research paper from sections of its full text.
A reader should understand the work without opening the paper.

Output ONLY valid JSON:
{"bullets": ["...", "...", "..."]}

Write 4 or 5 bullets, in this order, skipping any point the sections do not support:
1. About: what the paper is about, in plain language
2. Method: how the authors did the work
3. Results: what actually came out of it — quote the numbers when they are given
4. Limit: a limitation the authors state
5. Next: what the authors say is still open, only if they say it

Rules:
- Easy terms. If you must use a technical word, follow it with a short gloss in parentheses.
- Do not invent methods, findings, or numbers. Every bullet comes from the sections below.
- You were given extracted sections, not the whole paper. Do not refer to figures,
  tables, or sections you cannot see.
- Each bullet starts with a label and a colon, e.g. "About: ..."
- One complete sentence per bullet, under 200 characters, so it is never cut short."""

_DEEP_LABELS = ("About", "Method", "Results", "Limit", "Next")
_DEEP_FIELD_LABELS = (
    ("objective", "About"),
    ("method", "Method"),
    ("dataset", "Method"),
    ("results", "Results"),
    ("limitations", "Limit"),
    ("future_work", "Next"),
)
# One section of context each. The ladder returns whole sections; a Results
# section can run for pages, and the point of this path is to send the model
# what it needs rather than the paper.
_SECTION_CHARS = 900
_DEEP_CACHE_PREFIX = "deep:"


def _cache_key(paper: dict) -> str | None:
    if identity_keys(paper) or (paper.get("abstract") or "").strip() or (paper.get("title") or "").strip():
        return paper_identity(paper)
    return None


def _safe_text(value, limit: int) -> str:
    text = str(value or "").replace("\x00", " ").replace("</context>", " ")
    return text.strip()[:limit]


def _normalize(text) -> str:
    return _WHITESPACE_RE.sub(" ", _safe_text(text, _ABSTRACT_CHARS)).strip()


def _clip_brief(text: str, soft: int = _SOFT_CHARS, hard: int = _HARD_CHARS) -> str:
    """Clip to whole sentences, never mid-thought.

    Below `soft` the text is returned untouched. Past it we keep the longest run
    of whole sentences that still fits inside `hard`, so a 200-character sentence
    survives intact instead of losing its last words. Only when the first
    sentence alone overruns `hard` do we cut, and then the cut is marked with an
    ellipsis — a full stop on a half sentence reads as a complete claim.
    """
    t = _normalize(text)
    if len(t) <= soft:
        return t
    window = t[:hard]
    cut = 0
    for match in _SENTENCE_END_RE.finditer(window):
        cut = match.end()
    if cut >= _MIN_CLIP:
        return window[:cut].strip()
    slice_ = t[:soft]
    sp = slice_.rfind(" ")
    stem = slice_[:sp] if sp > _MIN_CLIP else slice_
    return stem.rstrip(" ,;:.") + _ELLIPSIS


def _ends_sentence(text: str) -> bool:
    stripped = text.rstrip()
    if not stripped:
        return False
    if stripped.endswith(_ELLIPSIS):
        return True
    return bool(_SENTENCE_END_RE.search(stripped[-3:]))


def _is_result(text: str) -> bool:
    if _RESULTS_NUM_RE.search(text):
        return True
    if not _RESULTS_VERB_RE.search(text):
        return False
    return bool(_RESULTS_SUBJECT_RE.search(text) or _DIGIT_RE.search(text))


def _classify_chunk(text: str) -> str | None:
    if _LIMIT_RE.search(text):
        return "Limit"
    if _is_result(text):
        return "Results"
    if _METHOD_RE.search(text):
        return "Method"
    if _FINDING_RE.search(text):
        return "Finding"
    return None


def _tidy_chunk(text: str) -> str:
    """Make a clause split read as a sentence, not as a severed middle."""
    cleaned = _normalize(text).strip(" ,;:-–—")
    cleaned = _DANGLING_RE.sub("", cleaned).rstrip(" ,;:")
    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    if cleaned and not _ends_sentence(cleaned):
        cleaned += "."
    return cleaned


def _explode_abstract(text: str) -> list[str]:
    text = _normalize(text)
    if not text or text in _PLACEHOLDER_ABSTRACTS:
        return []
    coarse = []
    for part in _SPLIT_RE.split(text):
        cleaned = _tidy_chunk(part)
        if len(cleaned) > 20:
            coarse.append(cleaned)
    parts: list[str] = []
    for part in coarse:
        if len(part) > 180 and ":" in part:
            left, right = part.split(":", 1)
            left, right = _tidy_chunk(left), _tidy_chunk(right)
            if len(left) > 25:
                parts.append(left)
            if len(right) > 25:
                parts.append(right)
        else:
            parts.append(part)
    # Source APIs often hand back an abstract that is itself cut short. The last
    # chunk is then a fragment; mark it so a card never presents it as finished.
    if parts and not _ends_sentence(text):
        parts[-1] = parts[-1].rstrip(" ,;:.") + _ELLIPSIS
    return parts


def extractive_brief(title: str, abstract: str) -> list[str]:
    """Turn title+abstract into labeled bullets so a card never shows a truncated paragraph."""
    unused = _explode_abstract(_safe_text(abstract, _ABSTRACT_CHARS))
    by_label: dict[str, str] = {}
    leftover: list[str] = []
    for chunk in unused:
        label = _classify_chunk(chunk)
        if label and label not in by_label:
            by_label[label] = chunk
        else:
            leftover.append(chunk)
    for label in _FILL_LABELS:
        if label not in by_label and leftover:
            by_label[label] = leftover.pop(0)
    if "About" not in by_label:
        titled = _normalize(title)[:_BULLET_CHARS]
        if titled:
            by_label["About"] = titled
    return [
        f"{label}: {_clip_brief(by_label[label])}"
        for label in _LABELS
        if label in by_label
    ]


def _get_cached(paper: dict) -> list[str] | None:
    key = _cache_key(paper)
    if not key:
        return None
    hit = _brief_cache.get(key)
    if not hit:
        return None
    bullets, cached_at = hit
    if time.time() - cached_at >= _CACHE_TTL:
        del _brief_cache[key]
        return None
    return list(bullets)


def _store(paper: dict, bullets: list[str]) -> list[str]:
    key = _cache_key(paper)
    if key:
        _brief_cache[key] = (list(bullets), time.time())
    return bullets


async def _prime_from_shared(papers: list[dict]) -> None:
    """Seed the process cache from the durable tier in one round trip."""
    if not shared_store.enabled():
        return
    wanted: dict[str, dict] = {}
    for paper in papers:
        key = _cache_key(paper)
        if key and key not in wanted and _get_cached(paper) is None:
            wanted[key] = paper
    if not wanted:
        return
    stored = await shared_store.get_many(shared_store.NS_BRIEF, list(wanted))
    now = time.time()
    for key, bullets in (stored or {}).items():
        if isinstance(bullets, list) and bullets:
            _brief_cache[key] = ([str(b) for b in bullets], now)


async def _brief_into(results: list, papers: list[dict], durable: dict) -> None:
    """Fill *results* in place, recording model-derived bullets in *durable*.

    Writing through the caller's list rather than returning one is what lets
    the inline path keep the briefings that finished when its deadline
    cancels the rest.
    """
    await _prime_from_shared(papers)

    async def _one(index: int, paper: dict) -> None:
        cached = _get_cached(paper)
        if cached is not None:
            results[index] = cached
            return
        bullets = await brief_paper(paper)
        results[index] = bullets
        # `_store` runs only on an LLM success, so a hit here means these
        # bullets are model output rather than the extractive fallback.
        key = _cache_key(paper)
        if key and _get_cached(paper) is not None:
            durable[key] = bullets

    async with anyio.create_task_group() as tg:
        for index, paper in enumerate(papers):
            tg.start_soon(_one, index, paper)


async def _persist(durable: dict) -> None:
    """Write model output to the durable tier. Never inside a cancel scope."""
    if durable:
        await shared_store.set_many(shared_store.NS_BRIEF, durable, _SHARED_TTL)


def _parse_bullets(raw: str) -> list[str]:
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found")
    parsed = json.loads(cleaned[start:end])
    items = parsed.get("bullets") if isinstance(parsed, dict) else None
    if not isinstance(items, list):
        raise ValueError("bullets is not a list")
    bullets = []
    for item in items:
        text = _clip_brief(item, _BULLET_CHARS, _BULLET_CHARS + 80)
        if text:
            bullets.append(text)
        if len(bullets) >= _MAX_BULLETS:
            break
    if len(bullets) < _MIN_BULLETS:
        raise ValueError("too few bullets")
    return bullets


@tagged("literature")
async def brief_paper(paper: dict) -> list[str]:
    """Return 2–5 bullets. LLM first; extractive abstract split if that fails."""
    cached = _get_cached(paper)
    if cached is not None:
        return cached

    title = _safe_text(paper.get("title"), 300)
    abstract = _safe_text(paper.get("abstract"), _ABSTRACT_CHARS)
    fallback = extractive_brief(title, abstract)
    if abstract in _PLACEHOLDER_ABSTRACTS:
        abstract = ""
    if not title and not abstract:
        return fallback

    user_prompt = (
        f'Paper title: "{title}"\n'
        f'Paper abstract: "{abstract or "(none)"}"\n'
        "Output exactly the requested JSON."
    )

    try:
        from ai.llm_provider import global_llm_sem
        async with global_llm_sem:
            raw = await generate_completion(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=500,
                temperature=0.15,
                provider_override="groq",
            )
        bullets = _parse_bullets(raw or "")
        return _store(paper, bullets)
    except Exception as exc:
        logger.warning("Paper briefing failed for '%s': %s", title, exc)
        return fallback


def _sections_prompt(evidence: dict) -> str:
    """Render the extracted sections as prompt context, one bounded block each."""
    blocks = []
    seen: set[str] = set()
    for field, _label in _DEEP_FIELD_LABELS:
        text = _normalize(evidence.get(field))
        if not text or text in seen:
            continue
        seen.add(text)
        blocks.append(f"{field.replace('_', ' ').title()}: {text[:_SECTION_CHARS]}")
    return "\n".join(blocks)


def _evidence_bullets(evidence: dict) -> list[str]:
    """Label the extracted sections directly, for when the model is unavailable.

    Still worth more than the abstract split: this text came from the paper's
    own Results and Limitations sections, it is just not in plain language.
    """
    bullets: list[str] = []
    used: set[str] = set()
    for field, label in _DEEP_FIELD_LABELS:
        if label in used:
            continue
        text = _normalize(evidence.get(field))
        if len(text) < 30:
            continue
        used.add(label)
        bullets.append(f"{label}: {_clip_brief(text)}")
    return bullets


@tagged("literature")
async def deep_brief_paper(paper: dict) -> dict:
    """Brief one paper from its full text, for a card the reader opened.

    Runs the evidence ladder in `ai/evidence_extraction.py` — arXiv HTML, then
    arXiv LaTeX, then Europe PMC JATS, and only then the OA PDF — so the cheap
    structured sources are tried before anything is downloaded and parsed. What
    reaches the model is the extracted sections, capped, not the paper: roughly
    1.5k tokens rather than the 8–20k a full text would cost.

    Never call this for a page of results. It is one paper, on demand, because
    a fetch-and-parse is seconds of wall clock and megabytes of transfer.
    Returns `{"bullets": [...], "source": "arxiv-html"}`; `source` is what the
    card shows so the reader knows where the briefing came from.
    """
    key = _cache_key(paper)
    shared_key = f"{_DEEP_CACHE_PREFIX}{key}" if key else None
    if shared_key:
        hit = _deep_cache.get(shared_key)
        if hit and time.time() - hit[1] < _CACHE_TTL:
            return {"bullets": list(hit[0]["bullets"]), "source": hit[0]["source"]}
        if shared_store.enabled():
            stored = await shared_store.get(shared_store.NS_BRIEF, shared_key)
            if isinstance(stored, dict) and stored.get("bullets"):
                return {
                    "bullets": [str(b) for b in stored["bullets"]],
                    "source": str(stored.get("source") or "cache"),
                }

    from ai.evidence_extraction import extract_evidence_for_paper

    try:
        evidence, source = await extract_evidence_for_paper(paper)
    except Exception as exc:
        logger.warning("Full-text extraction failed for '%s': %s", paper.get("title"), exc)
        return {"bullets": await brief_paper(paper), "source": "abstract"}

    context = _sections_prompt(evidence or {})
    if not context:
        # Nothing was readable — the abstract briefing is still the honest answer.
        return {"bullets": await brief_paper(paper), "source": "abstract"}

    title = _safe_text(paper.get("title"), 300)
    user_prompt = (
        f'Paper title: "{title}"\n'
        "Sections extracted from the paper:\n"
        f"{context}\n"
        "Output exactly the requested JSON."
    )

    bullets = _evidence_bullets(evidence)
    try:
        from ai.llm_provider import global_llm_sem
        async with global_llm_sem:
            raw = await generate_completion(
                system_prompt=_DEEP_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=600,
                temperature=0.15,
                provider_override="groq",
            )
        bullets = _parse_bullets(raw or "")
    except Exception as exc:
        logger.warning("Full-text briefing failed for '%s': %s", title, exc)
        if not bullets:
            return {"bullets": await brief_paper(paper), "source": "abstract"}
        # Section text under its own label, unpolished but from the paper.
        return {"bullets": bullets, "source": source}

    payload = {"bullets": bullets, "source": source}
    if shared_key:
        _deep_cache[shared_key] = (payload, time.time())
        # Bullets only — never the PDF and never the extracted text. A parsed
        # paper is 50–150KB; this is about 500 bytes.
        await shared_store.set(shared_store.NS_BRIEF, shared_key, payload, _SHARED_TTL)
    return dict(payload)


@tagged("literature")
async def brief_papers(papers: list[dict]) -> list[list[str]]:
    """Brief each paper, preserving order. Caps at one page of results."""
    slice_ = list(papers or [])[:WARM_BRIEF_COUNT]
    if not slice_:
        return []
    results: list[list[str]] = [[] for _ in slice_]
    durable: dict[str, list[str]] = {}
    await _brief_into(results, slice_, durable)
    await _persist(durable)
    return results


@tagged("literature")
async def briefs_within(
    papers: list[dict], seconds: float = INLINE_BRIEF_DEADLINE
) -> list[list[str] | None]:
    """Bullets per paper, `None` for any that did not finish within *seconds*.

    Search must never wait on the model. Whatever is ready rides along in the
    response; the client asks for the rest through POST /api/literature/brief,
    which by then is often a cache hit anyway.
    """
    slice_ = list(papers or [])
    if not slice_ or not _enabled:
        return [None] * len(slice_)
    results: list[list[str] | None] = [None] * len(slice_)
    durable: dict[str, list[str]] = {}
    with anyio.move_on_after(seconds):
        await _brief_into(results, slice_, durable)
    await _persist(durable)
    return [bullets or None for bullets in results]


@tagged("literature")
async def warm_briefs(papers: list[dict], seconds: float = _WARM_DEADLINE) -> None:
    """Fill the durable cache for papers the reader has not scrolled to yet.

    Runs after the response is sent, so it costs the search nothing. Failures
    are logged and dropped — nothing is waiting on this.
    """
    slice_ = list(papers or [])[:WARM_BRIEF_COUNT]
    if not slice_ or not _enabled:
        return
    results: list[list[str] | None] = [None] * len(slice_)
    durable: dict[str, list[str]] = {}
    try:
        with anyio.move_on_after(seconds):
            await _brief_into(results, slice_, durable)
        await _persist(durable)
    except Exception as exc:
        logger.warning("Brief warming failed for %d paper(s): %s", len(slice_), exc)
