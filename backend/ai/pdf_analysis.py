import io
import os
import json
import logging
import asyncio
import tempfile
import fitz
from llama_parse import LlamaParse
from pypdf import PdfReader
from fastapi import HTTPException
from ai.guardrails import validate_input_layers_a_b, validate_layer_b
from ai.evidence_extraction import extract_evidence
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

async def extract_pdf_text(file_bytes: bytes) -> str:
    """Extract text from PDF file bytes with tiered fallback: LlamaParse -> PyMuPDF -> pypdf."""
    text = ""
    
    # Tier 1: LlamaParse
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(file_bytes)
            temp_path = tmp.name
            
        parser = LlamaParse(result_type="markdown")
        # wait_for takes a coroutine
        documents = await asyncio.wait_for(parser.aload_data(temp_path), timeout=30.0)
        if documents:
            text = "\n".join(doc.text for doc in documents)
            logger.info("Successfully extracted PDF text using LlamaParse.")
    except asyncio.TimeoutError:
        logger.warning("LlamaParse extraction timed out. Falling back to PyMuPDF.")
    except Exception as e:
        logger.warning(f"LlamaParse extraction failed: {e}. Falling back to PyMuPDF.")
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
            
    # Tier 2: PyMuPDF
    if not text:
        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            for page in doc:
                text += page.get_text() + "\n"
            if text.strip():
                logger.info("Successfully extracted PDF text using PyMuPDF.")
            else:
                logger.warning("PyMuPDF returned empty text. Falling back to pypdf.")
        except Exception as e:
            logger.warning(f"PyMuPDF extraction failed: {e}. Falling back to pypdf.")
            
    # Tier 3: pypdf (fallback)
    if not text.strip():
        try:
            reader = PdfReader(io.BytesIO(file_bytes))
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
            if text.strip():
                logger.info("Successfully extracted PDF text using pypdf fallback.")
        except Exception as e:
            logger.error(f"Failed to extract PDF text with pypdf: {e}")
            if isinstance(e, HTTPException):
                raise
            raise HTTPException(status_code=400, detail="Could not read the PDF file. It might be corrupted or encrypted.")

    # Layer B validation only
    if not validate_layer_b(text):
        raise HTTPException(
            status_code=400, 
            detail="The extracted text is invalid, unclear, or contains potential injection attempts."
        )
        
    return text.strip()


async def analyze_uploaded_paper(text: str, custom_prompt: str = None) -> dict:
    """
    Run analysis on extracted PDF text. 
    If custom_prompt is provided, answers the prompt.
    Otherwise, returns the structured gap analysis shape.
    """
    # Just in case, validate again
    if not validate_layer_b(text):
        raise HTTPException(status_code=400, detail="Invalid text content.")
        
    evidence = await extract_evidence({"title": "", "abstract": text[:15000]})
    has_evidence = any(v.strip() for v in evidence.values())
    context_text = json.dumps(evidence, indent=2) if has_evidence else text[:30000]
        
    if custom_prompt:
        if not validate_input_layers_a_b(custom_prompt):
            raise HTTPException(status_code=400, detail="Invalid custom prompt.")
            
        prompt = _CUSTOM_PROMPT_TEMPLATE.replace("{text}", context_text).replace("{custom_prompt}", custom_prompt)
        try:
            raw = await generate_completion(
                system_prompt="You are a helpful academic research assistant.",
                user_prompt=prompt,
                max_tokens=1000,
                temperature=0.3
            )
            return {"type": "custom", "content": raw.strip()}
        except Exception as e:
            logger.error(f"Failed custom PDF analysis: {e}")
            raise HTTPException(status_code=500, detail="Analysis failed.")

    # Default structured analysis
    prompt = _PDF_ANALYSIS_USER_TEMPLATE.replace("{text}", context_text)
    try:
        raw = await generate_completion(
            system_prompt=_GAP_SYSTEM_PROMPT,
            user_prompt=prompt,
            max_tokens=1200,
            temperature=0.3
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
