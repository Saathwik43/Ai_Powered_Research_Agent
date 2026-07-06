import io
import json
import logging
from pypdf import PdfReader
from fastapi import HTTPException
from ai.guardrails import validate_input_layers_a_b, validate_layer_b
from ai.llm_provider import generate_completion
from ai.gap_analysis import _GAP_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

__all__ = ["extract_pdf_text", "analyze_uploaded_paper"]

_PDF_ANALYSIS_USER_TEMPLATE = """You are analyzing the text of an academic paper.

Below is the extracted text of the paper:
{text}

Based on ONLY the paper text above, produce a JSON object with exactly three fields:

1. "well_covered": an array of short strings (one sentence each) summarizing what aspects of the topic are well-established or thoroughly covered in this paper.

2. "gaps": an array of short strings identifying specific under-explored areas, limitations, or contradictions mentioned in this paper.

3. "suggested_direction": ONE concrete, specific, actionable research direction or follow-up work that addresses the most significant gap or limitation. This must be a detailed proposal (at least 20 words).

Output ONLY valid JSON with these three fields. No markdown, no explanation, no preamble."""

_CUSTOM_PROMPT_TEMPLATE = """You are an expert research assistant. 
Below is the extracted text of an academic paper:
{text}

Please answer the following user prompt based ONLY on the paper text:
{custom_prompt}
"""

def extract_pdf_text(file_bytes: bytes) -> str:
    """Extract text from PDF file bytes."""
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
        
        # Layer B validation only (Layer A fails on long PDF documents)
        if not validate_layer_b(text):
            raise HTTPException(
                status_code=400, 
                detail="The extracted text is invalid, unclear, or contains potential injection attempts."
            )
            
        return text.strip()
    except Exception as e:
        logger.error(f"Failed to extract PDF text: {e}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=400, detail="Could not read the PDF file. It might be corrupted or encrypted.")


async def analyze_uploaded_paper(text: str, custom_prompt: str = None) -> dict:
    """
    Run analysis on extracted PDF text. 
    If custom_prompt is provided, answers the prompt.
    Otherwise, returns the structured gap analysis shape.
    """
    # Just in case, validate again
    if not validate_layer_b(text):
        raise HTTPException(status_code=400, detail="Invalid text content.")
        
    if custom_prompt:
        if not validate_input_layers_a_b(custom_prompt):
            raise HTTPException(status_code=400, detail="Invalid custom prompt.")
            
        prompt = _CUSTOM_PROMPT_TEMPLATE.replace("{text}", text[:30000]).replace("{custom_prompt}", custom_prompt)
        try:
            raw = await generate_completion(
                system_prompt="You are a helpful academic research assistant.",
                user_prompt=prompt,
                max_tokens=1000,
                temperature=0.3,
                provider_override="gemini"
            )
            return {"type": "custom", "content": raw.strip()}
        except Exception as e:
            logger.error(f"Failed custom PDF analysis: {e}")
            raise HTTPException(status_code=500, detail="Analysis failed.")

    # Default structured analysis
    prompt = _PDF_ANALYSIS_USER_TEMPLATE.replace("{text}", text[:30000]) # Cap text to avoid massive context limits if needed
    try:
        raw = await generate_completion(
            system_prompt=_GAP_SYSTEM_PROMPT,
            user_prompt=prompt,
            max_tokens=1200,
            temperature=0.3,
            provider_override="gemini"
        )
        
        content = raw.strip()
        start = content.find("{")
        end = content.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError("No JSON object found")
            
        parsed = json.loads(content[start:end])
        return {"type": "structured", "data": parsed}
    except Exception as e:
        logger.error(f"Failed structured PDF analysis: {e}")
        raise HTTPException(status_code=500, detail="Analysis failed.")
