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
from ai.evidence_extraction import extract_evidence
from ai.citation_grounding import check_citation_grounding

logger = logging.getLogger(__name__)

import time
import re
_cache = {}

prompt_template = PromptTemplate(
    input_variables=["topic", "section", "context"],
    template="""You are an expert, highly-cited academic researcher and writer.
You are writing a formal, peer-reviewed research paper on the topic: "{topic}".
Your current task is exclusively to write the "{section}" section of the paper.

CRITICAL INSTRUCTION: If the topic "{topic}" is complete gibberish, a random string of characters, a nonsensical combination of unrelated everyday words, or doesn't correspond to a coherent, recognizable academic research subject, you MUST immediately output EXACTLY the following JSON and nothing else:
{{"error": "topic_unclear"}}

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
6. Make it comprehensive, detailed, and at least 3-4 paragraphs long.
7. CRITICAL: Cite ONLY from the numbered reference list provided in the context, using [1], [2], etc. inline markers. If no numbered reference list is provided, you may generate without citations but ensure academic rigor.
8. IMPORTANT: If a provided reference doesn't directly support a claim, state the claim as general background without a citation marker rather than force-citing an irrelevant source."""
)
def _prompt(topic: str, section: str, context: str) -> str:
    return prompt_template.format(topic=topic, section=section, context=context)




def _local_draft(topic: str, section: str, context: str):
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


async def generate_section(topic: str, section: str, context: str, citation_style: str = "ieee"):
    if not validate_input_layers_a_b(topic):
        return '{"error": "topic_unclear"}', {}
        
    papers = await search_all(topic, limit=15) or []
    if papers:
        papers = await _filter_relevant_papers(topic, papers)
        
        async def fetch_evidence(p):
            p["evidence"] = await extract_evidence(p)
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
            
            # Use structured evidence if available and not completely empty
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
            if gap_results.get("status") != "insufficient_literature" and "well_covered" in gap_results:
                gap_context = (
                    "\n\nGap Analysis Findings to Incorporate:\n"
                    f"- Well Covered: {'; '.join(gap_results.get('well_covered', []))}\n"
                    f"- Gaps Identified: {'; '.join(gap_results.get('gaps', []))}\n"
                    f"- Suggested Direction: {gap_results.get('suggested_direction', '')}\n"
                )
                context = (context or "") + gap_context
                gap_analysis_data = {
                    "well_covered": gap_results.get("well_covered"),
                    "gaps": gap_results.get("gaps"),
                    "suggested_direction": gap_results.get("suggested_direction"),
                }
        except Exception as e:
            logger.warning(f"Internal gap analysis failed during lit_review generation: {e}")

    cache_key = None
    if context and context.strip():
        cache_key = hash(topic + section + context)
        if cache_key in _cache:
            cache_entry = _cache[cache_key]
            # TTL check (1 hour = 3600 seconds)
            if time.time() - cache_entry['time'] < 3600:
                flags = await _citation_flags(cache_entry['content'], context, references_mapping)
                flags.update(validate_numerical_claims(cache_entry['content'], papers))
                if references_mapping:
                    flags["references"] = references_mapping
                    flags["formatted_references"] = {
                        k: format_citation(v, style=citation_style) for k, v in references_mapping.items()
                    }
                if gap_analysis_data:
                    flags["gap_analysis"] = gap_analysis_data
                return cache_entry['content'], flags

    system_prompt = "You write rigorous, concise academic manuscript sections."
    user_prompt = _prompt(topic, section, context)
    
    provider_override = None
    if section.lower().replace(" ", "_") in ("lit_review", "literature_review"):
        provider_override = "gemini"
    
    try:
        result = await generate_completion(system_prompt, user_prompt, max_tokens=1200, temperature=0.45, provider_override=provider_override)
        if cache_key is not None:
            _cache[cache_key] = {'content': result, 'time': time.time()}
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
        
    # Layer C failure (AI down) -> graceful fallback
    result = _local_draft(topic, section, context)
    result += "\n\n*(Note: AI generation is temporarily unavailable. This is a local template.)*"
    fallback_flags = {}
    if references_mapping:
        fallback_flags["references"] = references_mapping
        fallback_flags["formatted_references"] = {
            k: format_citation(v, style=citation_style) for k, v in references_mapping.items()
        }
    if gap_analysis_data:
        fallback_flags["gap_analysis"] = gap_analysis_data
    return result, fallback_flags


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
