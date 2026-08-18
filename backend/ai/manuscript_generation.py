import os
import asyncio
import hashlib
import logging
from typing import NamedTuple

import httpx
from langchain_core.prompts import PromptTemplate

from ai.llm_provider import generate_completion
from ai.guardrails import validate_input_layers_a_b
from ai.relevance import _filter_relevant_papers
from ai.citation_format import format_citation
from integrations.paper_search import search_all
from fastapi import HTTPException
from ai.numerical_validator import validate_numerical_claims
from ai.evidence_extraction import extract_evidence_for_paper
from ai.citation_grounding import check_citation_grounding
from core.database import db

logger = logging.getLogger(__name__)

import time
import re

# Module level so generate and edit share one definition -- a revision that
# doesn't carry this framing can undo the guard the generation applied.
_METHOD_RESULTS_FRAMING = {
    "methodology": "This is a PROPOSED methodology, not a description of an experiment that was actually run. Frame it explicitly as a suggested approach for future work (e.g. 'This study proposes...', 'The following approach is proposed to...'). Do not write as if this was executed.",
    "results": "These are PROJECTED/EXPECTED outcomes based on literature trends, not real experimental data. Frame explicitly as expectations (e.g. 'Based on trends in prior work, it is expected that...', 'Projected outcomes suggest...'). Do NOT state specific fabricated numbers as if they were measured; only cite ranges/trends directly attributable to the provided literature context.",
}

# Citation-style-specific inline instructions (minimal token cost)
_CITE_INSTRUCTIONS = {
    "ieee": "Cite using numbered markers [1], [2], etc.",
    "apa": "Cite using APA inline style (Author, Year) drawn from the reference list.",
    "chicago": "Cite using Chicago superscript footnote numbers.",
    "oxford": "Cite using Oxford footnote-style numbered references.",
}


def _prompt(topic: str, section: str, context: str, citation_style: str = "ieee") -> str:
    cite_instruction = _CITE_INSTRUCTIONS.get(citation_style, _CITE_INSTRUCTIONS["ieee"])
    section_framing = _METHOD_RESULTS_FRAMING.get(section.strip().lower(), "")

    base = f"""You are an expert, highly-cited academic researcher and writer.
You are writing a formal, peer-reviewed research paper on the topic: "{topic}".
Your current task is exclusively to write the "{section}" section of the paper.

CRITICAL INSTRUCTION: If the topic "{topic}" is complete gibberish, a random string of characters, a nonsensical combination of unrelated everyday words, or doesn't correspond to a coherent, recognizable academic research subject, you MUST immediately output EXACTLY the following JSON and nothing else:
{{"error": "topic_unclear"}}
"""
    if context:
        base += f"""
Here is the background context and literature survey information you MUST incorporate and synthesize:
<context>
{context}
</context>
"""
    base += f"""

    {section_framing}
Instructions:
1. Write a highly rigorous, well-structured, and formal academic "{section}" section.
2. CRITICAL: DO NOT include a title, section header, or '#' heading at the top of your output (e.g. DO NOT start with '# Abstract' or '# {topic}'). Start DIRECTLY with the body text paragraph.
3. Seamlessly weave the provided literature and context into your arguments. Do not just list them.
4. Keep all claims appropriately cautious and academically sound (e.g., use "suggests", "indicates", "may").
5. Format the output in clean Markdown, using paragraphs, lists, or bold text only where academically appropriate.
6. Make it comprehensive, detailed, and at least 3-4 paragraphs long.
7. CRITICAL: Use LaTeX formatting for any mathematical or chemical formulas, subscripts, and superscripts (e.g., $O_2$, $x^2$, $$ E = mc^2 $$) so they render correctly.
8. CRITICAL: {cite_instruction} If no numbered reference list is provided, you may generate without citations but ensure academic rigor.
9. IMPORTANT: If a provided reference doesn't directly support a claim, state the claim as general background without a citation marker rather than force-citing an irrelevant source.
10. CRITICAL: DO NOT include a "References", "Bibliography", or "Works Cited" list at the end of the section. The references are compiled and managed externally.
11. IMPORTANT: Present quantitative data, benchmarks, trends, process flows, or distributions using the appropriate Mermaid diagram block (`xychart-beta`, `pie`, `graph TD`, or `sequenceDiagram`).
SELECT THE DIAGRAM TYPE BASED ON CONTEXT:
- Line chart (`xychart-beta` with `line [...]`): For continuous trends over time, epochs, or scaling (e.g. Efficiency vs Year, Accuracy vs Epochs).
- Bar chart (`xychart-beta` with `bar [...]`): For discrete performance benchmarks, baseline comparisons, or ablation studies (e.g. Model A vs Model B vs Model C).
- Combined Line & Bar (`xychart-beta` with both `bar [...]` and `line [...]`): For dual metric comparisons (e.g. Accuracy bars + Loss line).
- Pie chart (`pie title "..." "Category A": 40 "Category B": 60`): For percentage distributions, dataset splits, or resource allocations.
- Flowchart (`graph TD` / `flowchart TD`): For pipeline architectures, system workflows, or methodology steps.

CRITICAL RULES for xychart-beta:
- ONLY use simple numerical arrays (e.g. [15.2, 21.0, 29.5, 33.1]).
- Keep `x-axis` string labels short (1-2 words maximum per label like ["Baseline", "SVM", "RF", "CNN-BLSTM", "Ensemble"]) so they remain clear without overlapping.
- If data includes error margins (like $\\pm 0.02$), simplify to mean values in arrays and explain deviations in text.
Example Bar & Line chart:
```mermaid
xychart-beta
    title "Model Performance Comparison"
    x-axis ["Baseline", "ResNet-50", "Transformer", "Proposed"]
    y-axis "Accuracy (%)" 0 --> 100
    bar [65.4, 78.2, 86.5, 92.8]
    line [65.4, 78.2, 86.5, 92.8]
```
NEVER use Markdown tables to simulate graphs."""
    return base






def _check_unverified_citations(content: str, context: str) -> dict:
    flags = {}
    if not context or len(context.strip()) < 50:
        if re.search(r'[A-Z][a-z]+ et al\.?\s*\(\d{4}\)', content):
            flags["unverified_citations"] = True
    return flags


async def _citation_flags(content: str, context: str, references_mapping: dict) -> dict:
    """
    Phase 3: sentence-level grounding when a numbered reference list (with
    Phase 2 evidence) exists for this section. Falls back to the old
    author-year regex heuristic only when there's no reference list to check
    against — e.g. very short/context-less generations, where the writer LLM
    occasionally invents an APA-style inline citation instead of using [N]
    markers at all.
    """
    if references_mapping:
        return await check_citation_grounding(content, references_mapping)
    return _check_unverified_citations(content, context)


# _filter_relevant_papers is imported from ai.relevance (shared module).
# The name is re-exported here so existing patch targets
# 'ai.manuscript_generation._filter_relevant_papers' continue to work.
import time

# Topic -> (retrieved literature, fetched_at).
#
# This cache holds ONLY public literature, which is genuinely shareable between
# users. Nothing user-specific may ever be written into it -- see the copy in
# _prepare_generation below for what happens when it is.
_research_cache: dict[str, tuple[list, float]] = {}
_RESEARCH_CACHE_TTL = 3600  # 1 hour
# A search that returned nothing is usually a transient upstream failure, not a
# fact about the topic. Cache it briefly so retries don't re-run the whole
# pipeline, but expire it fast enough that recovery is quick.
_RESEARCH_CACHE_EMPTY_TTL = 300


class _Prepared(NamedTuple):
    """
    Result of _prepare_generation. A NamedTuple so existing positional unpacking
    and index access keep working while named access stays readable.
    """
    user_prompt: str
    system_prompt: str
    references_mapping: dict
    gap_analysis_data: dict | None
    papers: list
    error: str | None
    cached_content: str | None
    cache_plan: dict | None = None
    user_prompt_cached: str | None = None


def _gemini_cache_plan(topic: str, context: str, system_prompt: str, model: str = None) -> dict:
    """
    Everything needed to materialise a Gemini context cache, without creating one.

    The key includes a digest of the context itself. It used to be
    ``md5(f"{topic}:{provider}:{model}")`` -- no context -- so every section of a
    topic shared one entry while the cached path deliberately omitted context
    from the prompt. Whichever section ran first therefore froze the context for
    all the others: if `lit_review` ran first its gap-analysis block leaked into
    the Abstract and Methodology, and if a section with fewer than two papers ran
    first, every later section silently lost its reference list while still being
    told to cite [N] markers.
    """
    shared_context = (
        "Here is the background context and literature survey information you "
        f"MUST incorporate and synthesize:\n<context>\n{context}\n</context>"
    )
    context_digest = hashlib.md5(shared_context.encode()).hexdigest()[:16]
    return {
        "cache_key": hashlib.md5(
            f"{topic}:{model or 'default'}:{context_digest}".encode()
        ).hexdigest(),
        "system_instruction": system_prompt,
        "shared_context": shared_context,
        "model": model,
    }


async def _resolve_gemini_cache(cache_plan: dict) -> str | None:
    """Materialise (or reuse) the cache described by *cache_plan*."""
    if not cache_plan:
        return None
    from ai.llm_provider import get_or_create_gemini_cache
    return await get_or_create_gemini_cache(**cache_plan)


EVIDENCE_FIELDS = ("objective", "method", "dataset", "results", "limitations", "future_work")

# Per-field and total caps for the grounding context handed to edit_section.
# It used to be 15 full abstracts with no bound at all.
_SNAPSHOT_FIELD_CHARS = 600
_SOURCE_CONTEXT_CHARS = 12000


def _reference_snapshot(references_mapping: dict) -> list:
    """
    Trim a reference mapping down to what grounding an edit actually needs.

    Persisted after generation so `edit_section` can ground a revision without
    re-running search, relevance classification and evidence extraction.
    """
    snapshot = []
    for idx, paper in sorted(
        (references_mapping or {}).items(),
        key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0,
    ):
        evidence = paper.get("evidence")
        fields = {}
        if isinstance(evidence, dict):
            fields = {
                k: str(evidence[k])[:_SNAPSHOT_FIELD_CHARS]
                for k in EVIDENCE_FIELDS
                if evidence.get(k)
            }
        snapshot.append({
            "index": str(idx),
            "title": paper.get("title") or "Unknown Title",
            "authors": paper.get("authors") or "",
            "year": paper.get("year") or "",
            "evidence": fields,
            # Only kept as a fallback for papers evidence extraction missed.
            "abstract": "" if fields else (paper.get("abstract") or "")[:_SNAPSHOT_FIELD_CHARS],
        })
    return snapshot


def _render_source_context(snapshot: list) -> str:
    """
    Render a snapshot as the <sources> block for the edit prompt.

    Evidence fields are written out by name. The previous expression was
    ``v.get('abstract','') or v.get('evidence','')`` which preferred the long
    raw abstract over the distilled evidence, and -- when abstract was null --
    interpolated the evidence **dict** straight into the prompt as a Python
    repr.
    """
    lines = []
    for entry in snapshot or []:
        header = f"[{entry['index']}] {entry.get('title', '')}"
        meta = " ".join(p for p in (entry.get("authors"), str(entry.get("year") or "")) if p)
        if meta:
            header += f" — {meta}"
        lines.append(header)
        evidence = entry.get("evidence") or {}
        if evidence:
            for field, value in evidence.items():
                lines.append(f"  {field}: {value}")
        elif entry.get("abstract"):
            # Fallback only. Distilled evidence is both shorter and more
            # directly checkable than the raw abstract, so it wins when present.
            lines.append(f"  abstract: {entry['abstract']}")
    return "\n".join(lines)[:_SOURCE_CONTEXT_CHARS]


async def _store_reference_snapshot(topic: str, references_mapping: dict) -> None:
    """Persist the reference set for *topic* so edits can reuse it."""
    if not references_mapping:
        return
    try:
        from services import usage_tracker
        user_id = usage_tracker.current_user_id.get()
        if not user_id:
            return
        await db["manuscript_references"].update_one(
            {"user_id": user_id, "topic": topic},
            {"$set": {
                "user_id": user_id,
                "topic": topic,
                "references": _reference_snapshot(references_mapping),
                "updated_at": time.time(),
            }},
            upsert=True,
        )
    except Exception as e:
        logger.warning(f"Failed to store reference snapshot for '{topic}': {e}")


async def _load_reference_snapshot(topic: str) -> list:
    """Reference set saved by the last generation for *topic*, or []."""
    try:
        from services import usage_tracker
        user_id = usage_tracker.current_user_id.get()
        if not user_id:
            return []
        doc = await db["manuscript_references"].find_one({"user_id": user_id, "topic": topic})
        return (doc or {}).get("references") or []
    except Exception as e:
        logger.warning(f"Failed to load reference snapshot for '{topic}': {e}")
        return []


def _user_source_to_paper(source: dict) -> dict:
    """Render one uploaded source as a paper dict the reference builder accepts."""
    raw = source.get("raw_text") or ""
    return {
        "title": source.get("filename", "User Source"),
        "authors": "User-provided",
        "year": "",
        "abstract": raw[:4000],
        "evidence": {
            "results": raw[:4000],
            "dataset": raw[:2000],
            "objective": "", "method": "", "limitations": "", "future_work": ""
        },
        "evidence_source": "user_upload",
    }


async def _prepare_generation(topic: str, section: str, context: str, citation_style: str, provider: str = None, model: str = None):
    if not validate_input_layers_a_b(topic):
        return _Prepared(None, None, None, None, None, '{"error": "topic_unclear"}', None)

    topic_key = topic.strip().lower()
    now = time.time()

    cached = _research_cache.get(topic_key)
    if cached and (now - cached[1]) < (_RESEARCH_CACHE_TTL if cached[0] else _RESEARCH_CACHE_EMPTY_TTL):
        logger.info(f"Using cached research for topic: '{topic_key}'")
        literature = cached[0]
    else:
        logger.info(f"No valid cache for '{topic_key}', running full research pipeline.")
        # Slice before filtering: search_all() returns ranked results and the
        # classifier costs one LLM call per paper, so papers past the window
        # would be paid for and then discarded. Matches gap_analysis.
        literature = (await search_all(topic, limit_per_source=15) or [])[:15]

        if literature:
            literature = await _filter_relevant_papers(topic, literature)

            sem = asyncio.Semaphore(3)
            async def fetch_evidence_throttled(p):
                async with sem:
                    if p.get("evidence_source") == "user_upload":
                        return p
                    p["evidence"], p["evidence_source"] = await extract_evidence_for_paper(p)
                    return p

            await asyncio.gather(*(fetch_evidence_throttled(p) for p in literature), return_exceptions=True)
        _research_cache[topic_key] = (literature, now)

    # Copy before appending. `literature` is the *shared cached list object*, so
    # appending this caller's private uploads to it used to:
    #   1. duplicate every user source once per section generated on the topic,
    #   2. shift every [N] marker between sections, so the compiled References
    #      list no longer matched the citations in the earlier sections, and
    #   3. hand one user's private document text to the next user who generated
    #      on the same topic (the cache is keyed on topic alone).
    papers = list(literature)

    try:
            from services import usage_tracker
            user_id = usage_tracker.current_user_id.get()
            if not user_id:
                user_sources = []
            else:
                # Sorted so the [N] numbering assigned below is stable across
                # sections instead of depending on Mongo's natural order.
                user_sources = await (
                    db["sources"]
                    .find({"topic": topic_key, "user_id": user_id})
                    .sort("created_at", 1)
                    .to_list(50)
                )
            papers.extend(_user_source_to_paper(s) for s in user_sources)
    except Exception as e:
        logger.warning(f"Failed to fetch user sources: {e}")

    references_mapping = {}
    if len(papers) >= 2:
        ref_text = "\n\nNumbered Reference List:\n"
        for idx, p in enumerate(papers, 1):
            title = p.get('title', 'Unknown Title')
            authors = p.get('authors', 'Unknown Authors')
            year = p.get('year', 'Unknown Year')
            doi = p.get('doi', p.get('url', ''))
            
            ev = p.get("evidence") or {}
            has_evidence = any(ev.get(k) for k in EVIDENCE_FIELDS)

            if has_evidence:
                content_text = ""
                for k in EVIDENCE_FIELDS:
                    if ev.get(k):
                        content_text += f"{k.capitalize()}: {ev[k]}. "
                content_text = content_text.strip()
            else:
                content_text = p.get('abstract') or ''

            ref_text += f"[{idx}] {authors} ({year}). {title}. {content_text} {doi}\n"
            references_mapping[str(idx)] = p
        
        context = (context or "") + ref_text
        # Persist for edit_section, which must not re-run this pipeline just to
        # learn what [N] refers to.
        await _store_reference_snapshot(topic, references_mapping)
    else:
        logger.info(f"Insufficient relevant papers ({len(papers) if papers else 0}) for '{topic}' — proceeding without forced reference list")
        
    gap_analysis_data = None
    if section.lower().replace(" ", "_") in ("lit_review", "literature_review"):
        from ai.gap_analysis import analyze_gaps
        try:
            gap_results = await analyze_gaps(topic, papers=papers)
            if gap_results.get("status") != "insufficient_literature":
                consensus_claims = [c.get("claim", "") for c in gap_results.get("consensus", [])]
                conflict_pairs = [f"{c.get('claim_a', '')} vs {c.get('claim_b', '')}" for c in gap_results.get("conflicts", [])]
                gap_descriptions = [g.get("description", "") for g in gap_results.get("gaps", [])]
                
                gap_context = (
                    "\n\nGap Analysis Findings to Incorporate:\n"
                    f"- Consensus: {'; '.join(consensus_claims)}\n"
                    f"- Conflicts: {'; '.join(conflict_pairs)}\n"
                    f"- Gaps Identified: {'; '.join(gap_descriptions)}\n"
                    f"- Suggested Direction: {gap_results.get('suggested_direction', '')}\n"
                )
                context = (context or "") + gap_context
                gap_analysis_data = {
                    "consensus": gap_results.get("consensus"),
                    "conflicts": gap_results.get("conflicts"),
                    "gaps": gap_results.get("gaps"),
                    "suggested_direction": gap_results.get("suggested_direction"),
                }
        except Exception as e:
            logger.warning(f"Internal gap analysis failed during lit_review generation: {e}")

    system_prompt = "You write rigorous, concise academic manuscript sections."
    system_prompt += "\nSources marked evidence_source='user_upload' are ground truth from the user's own experiments. Prefer their exact numbers over any inferred/generated figures. Never invent results not present in any source."
    
    # Cache plan, not a cache. In auto mode the provider is not known until the
    # cascade actually reaches one, so materialising a Gemini cache here would
    # pay for a cache the run may never touch. The plan is carried down and
    # materialised lazily by whoever reaches Gemini; see _resolve_gemini_cache.
    cache_plan = _gemini_cache_plan(topic, context, system_prompt, model) if context else None

    cached_content = None
    if provider and provider.lower() == "gemini" and cache_plan:
        cached_content = await _resolve_gemini_cache(cache_plan)

    # Context is omitted from the prompt only when it is genuinely already in a
    # cache; sending both would double the tokens rather than save any.
    user_prompt = _prompt(topic, section, "" if cached_content else context, citation_style)

    return _Prepared(
        user_prompt=user_prompt,
        system_prompt=system_prompt,
        references_mapping=references_mapping,
        gap_analysis_data=gap_analysis_data,
        papers=papers,
        error=None,
        cached_content=cached_content,
        cache_plan=cache_plan,
        # The context-free variant, ready for a cache materialised further down
        # the cascade. Built here so the lazy path never has to re-derive it.
        user_prompt_cached=_prompt(topic, section, "", citation_style),
    )


async def generate_section(topic: str, section: str, context: str, citation_style: str = "ieee"):
    provider_override = None
    max_tokens_limit = 1200
    if section.lower().replace(" ", "_") in ("lit_review", "literature_review"):
        provider_override = "gemini"
        max_tokens_limit = 2000

    from ai.llm_provider import LLM_PROVIDER
    active_provider = provider_override or (LLM_PROVIDER if LLM_PROVIDER != "auto" else None)
    
    effective_max_tokens = max(max_tokens_limit, 1800) if active_provider and active_provider.lower() == "gemini" else max_tokens_limit
    
    prep = await _prepare_generation(
        topic, section, context, citation_style, provider=active_provider
    )
    if prep.error:
        return prep.error, {}

    references_mapping = prep.references_mapping
    try:
        result = await generate_completion(prep.system_prompt, prep.user_prompt, max_tokens=effective_max_tokens, temperature=0.45, provider_override=provider_override, cached_content=prep.cached_content)
        flags = await _citation_flags(result, context, references_mapping)
        flags.update(validate_numerical_claims(result, prep.papers))
        if references_mapping:
            flags["references"] = references_mapping
            flags["formatted_references"] = {
                k: format_citation(v, style=citation_style) for k, v in references_mapping.items()
            }
        if prep.gap_analysis_data:
            flags["gap_analysis"] = prep.gap_analysis_data
        return result, flags
    except Exception as e:
        logger.error(f"manuscript generation failed (AI unavailable): {e}")
        raise HTTPException(
            status_code=503,
            detail={"verification_unavailable": True, "message": "AI generation is temporarily unavailable. Please try again in a moment."}
        )

from ai.llm_provider import stream_completion, stream_completion_auto

async def generate_section_stream(topic: str, section: str, context: str, citation_style: str, mode: str = "manual", provider: str = None, model: str = None):
    prep = await _prepare_generation(
        topic, section, context, citation_style, provider=provider, model=model
    )
    if prep.error:
        yield {"type": "stopped", "reason": "error", "message": "Topic unclear"}
        return

    user_prompt = prep.user_prompt
    system_prompt = prep.system_prompt
    references_mapping = prep.references_mapping
    gap_analysis_data = prep.gap_analysis_data
    papers = prep.papers
    cached_content = prep.cached_content

    # Emit all resolved sources upfront before LLM streaming starts
    sources_list = []
    if references_mapping:
        for idx_str, paper in sorted(references_mapping.items(), key=lambda x: int(x[0])):
            sources_list.append({
                "index": int(idx_str),
                "title": paper.get("title", "Unknown"),
                "authors": paper.get("authors", "Unknown"),
                "year": paper.get("year", ""),
                "url": paper.get("url") or paper.get("doi", ""),
            })
    yield {"type": "sources_list", "sources": sources_list}

    max_tokens_limit = 2000 if section.lower().replace(" ", "_") in ("lit_review", "literature_review") else 1200
    
    from ai.llm_provider import LLM_PROVIDER
    active_provider = provider or (LLM_PROVIDER if LLM_PROVIDER != "auto" else None)
    effective_max_tokens = max(max_tokens_limit, 1800) if active_provider and active_provider.lower() == "gemini" else max_tokens_limit
    
    full_text = ""
    
    if mode == "auto":
        # Pass the plan, not a cache: the cascade may never reach Gemini, and
        # creating a cache it never uses costs an API round-trip for nothing.
        # stream_completion_auto materialises it only on the Gemini leg, and
        # swaps in the context-free prompt at the same moment so the context is
        # never sent twice.
        stream_gen = stream_completion_auto(
            system_prompt, user_prompt, effective_max_tokens, 0.45, cached_content,
            gemini_cache_resolver=(lambda: _resolve_gemini_cache(prep.cache_plan)) if prep.cache_plan else None,
            user_prompt_cached=prep.user_prompt_cached,
        )
    else:
        stream_gen = stream_completion(system_prompt, user_prompt, effective_max_tokens, 0.45, provider, model, cached_content)
    
    async for chunk in stream_gen:
        if chunk.get("type") == "chunk":
            full_text += chunk.get("text", "")
            yield chunk
        elif chunk.get("type") == "done" or chunk.get("type") == "stopped":
            if chunk.get("type") == "done":
                try:
                    from services import usage_tracker
                    user_id = usage_tracker.current_user_id.get()
                    if user_id:
                        word_count = len((system_prompt + " " + user_prompt + " " + full_text).split())
                        tokens = int(word_count * 1.3)
                        used_provider = provider if mode == "manual" else "Auto (Cascade)"
                        await usage_tracker.log_usage(user_id, tokens, used_provider, "manuscript_stream")
                except Exception as e:
                    logger.error(f"Failed to log stream usage: {e}")

            # Run post-processing before sending the final signal
            flags = await _citation_flags(full_text, context, references_mapping)
            flags.update(validate_numerical_claims(full_text, papers))
            
            metadata = {"type": "metadata"}
            metadata.update(flags)
            if references_mapping:
                metadata["references"] = references_mapping
                metadata["formatted_references"] = {
                    k: format_citation(v, style=citation_style) for k, v in references_mapping.items()
                }
            if gap_analysis_data:
                metadata["gap_analysis"] = gap_analysis_data
                
            yield metadata
            yield chunk
            if chunk.get("type") == "stopped":
                break


edit_prompt_template = PromptTemplate(
    input_variables=["topic", "section", "current_content", "instructions", "source_context", "section_framing"],
    template="""You are an expert academic editor.
You are editing the "{section}" section of a research paper on the topic: "{topic}".

{section_framing}

Here is the source material and reference list you must stay grounded in - do not introduce claims or citations that aren't supported by it : 
<sources>
 {source_context}
</sources>

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
2. Stay grounded in <sources> - any new or changed claim must be traceable to it . Do not hallucinate citations
3. Maintain a highly rigorous, well-structured, and formal academic tone unless instructed otherwise.
4. DO NOT include a title or heading for the section. Start directly with the revised content.
5. Preserve the epistemic framing of the original — if a claim is written as proposed, projected or expected, it must stay that way. Never convert a proposal into a reported result.
6. Keep every [N] citation marker attached to the claim it supports. Do not add markers for claims the sources do not support, and do not renumber.
7. Output ONLY the revised text in clean Markdown, without any conversational filler or introductory remarks like "Here is the revised section."."""
)

def _edit_prompt_fn(topic: str, section: str, current_content: str, instructions: str, source_context: str = "") -> str:
    safe_content = current_content.replace("{","{{").replace("}","}}")
    safe_instructions=instructions.replace("{","{{").replace("}","}}")
    safe_context = (source_context or "").replace("{","{{").replace("}","}}")
    # Same proposed/projected framing the generator uses. Without it a revision
    # could quietly turn "it is expected that..." into "we observed that...",
    # undoing the fabrication guard _prompt() is careful to apply.
    framing = _METHOD_RESULTS_FRAMING.get(section.strip().lower(), "")
    return edit_prompt_template.format(
        topic=topic, section=section, current_content=safe_content, instructions=safe_instructions,
        source_context=safe_context or "No source context available",
        section_framing=framing.replace("{", "{{").replace("}", "}}"),
    )


async def _edit_reference_set(topic: str) -> list:
    """
    Reference set for a revision, without paying for research.

    This used to call _prepare_generation(), which meant every edit re-ran
    search_all across 11 sources, two batched relevance-classifier calls, up to
    15 evidence extractions and -- for lit_review -- an entire analyze_gaps run,
    then discarded the prompt it had built and kept only references_mapping.
    Roughly 20 upstream calls to answer "make paragraph two shorter".

    Order: the snapshot persisted at generation time, else the still-warm
    in-process research cache, else nothing. All three are free.
    """
    snapshot = await _load_reference_snapshot(topic)
    if snapshot:
        return snapshot

    cached = _research_cache.get(topic.strip().lower())
    if cached and cached[0] and (time.time() - cached[1]) < _RESEARCH_CACHE_TTL:
        return _reference_snapshot({str(i): p for i, p in enumerate(cached[0], 1)})

    return []


# Headroom over the current section so a revision is not truncated mid-sentence.
# The old flat 1200 was below what lit_review generates at (2000), so revising a
# long section silently cut it off -- and the diff view then offered the
# truncation for acceptance with no warning.
_EDIT_TOKEN_HEADROOM = 1.6
_EDIT_MIN_TOKENS = 1200
_EDIT_MAX_TOKENS = 4000


def _edit_token_budget(current_content: str) -> int:
    needed = int((len(current_content or "") / 4) * _EDIT_TOKEN_HEADROOM)
    return max(_EDIT_MIN_TOKENS, min(needed, _EDIT_MAX_TOKENS))


async def edit_section(topic: str, section: str, current_content: str, instructions: str, citation_style: str = "ieee"):
    """
    Revise *section* per *instructions*, returning ``(content, flags)``.

    Returns the same verification flags as generate_section. Revision used to be
    the only write path into the manuscript with no checks at all: a revision
    could introduce a hallucinated citation or an invented number and nothing
    looked, while the UI kept displaying the *previous* generation's flags -- so
    it read as verified about text that no longer existed.
    """
    if not validate_input_layers_a_b(instructions):
        raise HTTPException(status_code=400, detail="Revision instructions are unclear or invalid.")

    system_prompt = "You are a meticulous academic editor."

    snapshot = await _edit_reference_set(topic)
    source_context = _render_source_context(snapshot)
    references_mapping = {entry["index"]: entry for entry in snapshot}

    user_prompt = _edit_prompt_fn(topic, section, current_content, instructions, source_context)
    max_tokens = _edit_token_budget(current_content)

    try:
        result = await generate_completion(
            system_prompt, user_prompt, max_tokens=max_tokens, temperature=0.45
        )
    except Exception as e:
        # Deliberately an error, not a value. This used to return
        # current_content + "_(Note: AI revision providers failed...)_", which
        # the diff view rendered as an ordinary addition -- so accepting the
        # revision wrote that note into the manuscript.
        logger.error(f"manuscript edit failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail={
                "verification_unavailable": True,
                "message": "AI revision is temporarily unavailable. Please try again in a moment.",
            },
        )

    flags = await _citation_flags(result, source_context, references_mapping)
    flags.update(validate_numerical_claims(result, snapshot))
    if references_mapping:
        flags["formatted_references"] = {
            k: format_citation(v, style=citation_style) for k, v in references_mapping.items()
        }
    if _looks_truncated(result, max_tokens):
        flags["truncated"] = True

    return result, flags


def _looks_truncated(text: str, max_tokens: int) -> bool:
    """
    Heuristic: output that fills its budget and does not end on terminal
    punctuation was almost certainly cut off. generate_completion does not
    surface finish_reason (see C6), so this is the available signal.
    """
    if not text:
        return False
    if (len(text) / 4) < max_tokens * 0.95:
        return False
    return text.rstrip()[-1:] not in {".", "!", "?", '"', "'", ")", "`"}
