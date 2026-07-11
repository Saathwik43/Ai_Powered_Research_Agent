import os
import asyncio
import logging

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

logger = logging.getLogger(__name__)

import time
import re
def _prompt(topic: str, section: str, context: str) -> str:
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
Instructions:
1. Write a highly rigorous, well-structured, and formal academic "{section}" section.
2. DO NOT include a title or heading for the section. Start directly with the content.
3. Seamlessly weave the provided literature and context into your arguments. Do not just list them.
4. Keep all claims appropriately cautious and academically sound (e.g., use "suggests", "indicates", "may").
5. Format the output in clean Markdown, using paragraphs, lists, or bold text only where academically appropriate.
6. Make it comprehensive, detailed, and at least 3-4 paragraphs long.
7. CRITICAL: Use LaTeX formatting for any mathematical or chemical formulas, subscripts, and superscripts (e.g., $O_2$, $x^2$, $$ E = mc^2 $$) so they render correctly.
8. CRITICAL: Cite ONLY from the numbered reference list provided in the context, using [1], [2], etc. inline markers. If no numbered reference list is provided, you may generate without citations but ensure academic rigor.
9. IMPORTANT: If a provided reference doesn't directly support a claim, state the claim as general background without a citation marker rather than force-citing an irrelevant source."""
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


async def _prepare_generation(topic: str, section: str, context: str, citation_style: str, provider: str = None, model: str = None):
    if not validate_input_layers_a_b(topic):
        return None, None, None, None, None, '{"error": "topic_unclear"}', None
        
    papers = await search_all(topic, limit_per_source=15) or []
    if papers:
        papers = await _filter_relevant_papers(topic, papers)
        papers = papers[:15]
        
        async def fetch_evidence(p):
            p["evidence"], p["evidence_source"] = await extract_evidence_for_paper(p)
            return p
            
        await asyncio.gather(*(fetch_evidence(p) for p in papers), return_exceptions=True)

    references_mapping = {}
    if len(papers) >= 2:
        ref_text = "\n\nNumbered Reference List:\n"
        for idx, p in enumerate(papers, 1):
            title = p.get('title', 'Unknown Title')
            authors = p.get('authors', 'Unknown Authors')
            year = p.get('year', 'Unknown Year')
            doi = p.get('doi', p.get('url', ''))
            
            ev = p.get("evidence", {})
            has_evidence = any(ev.get(k) for k in ["objective", "method", "dataset", "results", "limitations", "future_work"])
            
            if has_evidence:
                content_text = ""
                for k in ["objective", "method", "dataset", "results", "limitations", "future_work"]:
                    if ev.get(k):
                        content_text += f"{k.capitalize()}: {ev[k]}. "
                content_text = content_text.strip()
            else:
                content_text = p.get('abstract', '')

            ref_text += f"[{idx}] {authors} ({year}). {title}. {content_text} {doi}\n"
            references_mapping[str(idx)] = p
        
        context = (context or "") + ref_text
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
    
    cached_content = None
    if provider == "gemini" and context:
        from ai.llm_provider import get_or_create_gemini_cache
        import hashlib
        cache_key = hashlib.md5(f"{topic}:{provider}:{model or 'default'}".encode()).hexdigest()
        
        shared_context = f"Here is the background context and literature survey information you MUST incorporate and synthesize:\n<context>\n{context}\n</context>"
        cached_content = await get_or_create_gemini_cache(cache_key, system_prompt, shared_context, model)
        
        if cached_content:
            # We omit the evidence block from the per-call user_prompt since it's cached.
            user_prompt = _prompt(topic, section, "")
        else:
            user_prompt = _prompt(topic, section, context)
    else:
        user_prompt = _prompt(topic, section, context)

    return user_prompt, system_prompt, references_mapping, gap_analysis_data, papers, None, cached_content


async def generate_section(topic: str, section: str, context: str, citation_style: str = "ieee"):
    provider_override = None
    max_tokens_limit = 1200
    if section.lower().replace(" ", "_") in ("lit_review", "literature_review"):
        provider_override = "gemini"
        max_tokens_limit = 2000

    from ai.llm_provider import LLM_PROVIDER
    active_provider = provider_override or (LLM_PROVIDER if LLM_PROVIDER != "auto" else "gemini")
    
    user_prompt, system_prompt, references_mapping, gap_analysis_data, papers, err, cached_content = await _prepare_generation(
        topic, section, context, citation_style, provider=active_provider
    )
    if err:
        return err, {}
    
    try:
        result = await generate_completion(system_prompt, user_prompt, max_tokens=max_tokens_limit, temperature=0.45, provider_override=provider_override, cached_content=cached_content)
        flags = await _citation_flags(result, context, references_mapping)
        flags.update(validate_numerical_claims(result, papers))
        if references_mapping:
            flags["references"] = references_mapping
            flags["formatted_references"] = {
                k: format_citation(v, style=citation_style) for k, v in references_mapping.items()
            }
        if gap_analysis_data:
            flags["gap_analysis"] = gap_analysis_data
        return result, flags
    except Exception as e:
        logger.error(f"manuscript generation failed (AI unavailable): {e}")
        raise HTTPException(
            status_code=503,
            detail={"verification_unavailable": True, "message": "AI generation is temporarily unavailable. Please try again in a moment."}
        )

from ai.llm_provider import stream_completion, stream_completion_auto

async def generate_section_stream(topic: str, section: str, context: str, citation_style: str, mode: str = "manual", provider: str = None, model: str = None):
    user_prompt, system_prompt, references_mapping, gap_analysis_data, papers, err, cached_content = await _prepare_generation(
        topic, section, context, citation_style, provider=provider, model=model
    )
    if err:
        yield {"type": "stopped", "reason": "error", "message": "Topic unclear"}
        return
        
    max_tokens_limit = 2000 if section.lower().replace(" ", "_") in ("lit_review", "literature_review") else 1200
    
    full_text = ""
    
    stream_gen = stream_completion_auto(system_prompt, user_prompt, max_tokens_limit, 0.45, cached_content) if mode == "auto" else stream_completion(system_prompt, user_prompt, max_tokens_limit, 0.45, provider, model, cached_content)
    
    async for chunk in stream_gen:
        if chunk.get("type") == "chunk":
            full_text += chunk.get("text", "")
            yield chunk
        elif chunk.get("type") == "done" or chunk.get("type") == "stopped":
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


async def edit_section(topic: str, section: str, current_content: str, instructions: str):
    system_prompt = "You are a meticulous academic editor."
    user_prompt = _edit_prompt_fn(topic, section, current_content, instructions)
    
    try:
        result = await generate_completion(system_prompt, user_prompt, max_tokens=1200, temperature=0.45)
        return result
    except Exception as e:
        logger.error(f"manuscript edit failed: {e}")

    # If all fail, return current content with a note
    return current_content + "\n\n_(Note: AI revision providers failed. Original content retained.)_"
