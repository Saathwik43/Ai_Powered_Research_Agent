import os
import asyncio
import logging

import httpx
from langchain_core.prompts import PromptTemplate

from ai.llm_provider import generate_completion
from ai.guardrails import validate_input_layers_a_b
from fastapi import HTTPException

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
6. Make it comprehensive, detailed, and at least 3-4 paragraphs long."""
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

async def generate_section(topic: str, section: str, context: str):
    if not validate_input_layers_a_b(topic):
        return '{"error": "topic_unclear"}', {}
        
    cache_key = None
    if context and context.strip():
        cache_key = hash(topic + section + context)
        if cache_key in _cache:
            cache_entry = _cache[cache_key]
            # TTL check (1 hour = 3600 seconds)
            if time.time() - cache_entry['time'] < 3600:
                flags = _check_unverified_citations(cache_entry['content'], context)
                return cache_entry['content'], flags

    system_prompt = "You write rigorous, concise academic manuscript sections."
    user_prompt = _prompt(topic, section, context)
    
    try:
        result = await generate_completion(system_prompt, user_prompt, max_tokens=1200, temperature=0.45)
        if cache_key is not None:
            _cache[cache_key] = {'content': result, 'time': time.time()}
        flags = _check_unverified_citations(result, context)
        return result, flags
    except Exception as e:
        logger.error(f"manuscript generation failed (AI unavailable): {e}")
        
    # Layer C failure (AI down) -> graceful fallback
    result = _local_draft(topic, section, context)
    result += "\n\n*(Note: AI generation is temporarily unavailable. This is a local template.)*"
    return result, {}


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
