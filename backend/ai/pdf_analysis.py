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
from ai.pdf_structure import extract_structure
from ai.grobid_client import extract_via_grobid

logger = logging.getLogger(__name__)

__all__ = ["extract_pdf_text", "extract_pdf_structure", "analyze_uploaded_paper"]

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

Please answer the following user prompt based ONLY on the paper text.

USER PROMPT:
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


async def extract_pdf_structure(file_bytes: bytes) -> dict:
    """Extract structural metadata from PDF file bytes with GROBID -> PyMuPDF fallback."""
    structure = {"title": "", "authors": [], "abstract": "", "sections": {}}
    if not file_bytes:
        return structure
        
    grobid_structure = await extract_via_grobid(file_bytes)
    low_fields = (
        sum(1 for v in grobid_structure.get("confidence", {}).values() if v == "low")
        if grobid_structure else 4
    )

    if grobid_structure is None:
        logger.warning("GROBID unreachable, falling back to PyMuPDF heuristic.")
        structure = extract_structure(file_bytes)
    elif low_fields == 0:
        structure = grobid_structure
    else:
        # GROBID partially low-confidence -- rescue only the weak fields
        # from the heuristic tier, don't discard GROBID's good fields.
        logger.info(f"GROBID low-confidence on {low_fields} field(s), rescuing from heuristic.")
        heuristic_structure = extract_structure(file_bytes)
        merged = dict(grobid_structure)
        merged_conf = dict(grobid_structure.get("confidence", {}))
        for field in ("title", "authors", "abstract", "sections"):
            if grobid_structure.get("confidence", {}).get(field) == "low":
                heur_conf = heuristic_structure.get("confidence", {}).get(field, "low")
                if heur_conf == "high" and heuristic_structure.get(field):
                    merged[field] = heuristic_structure[field]
                    merged_conf[field] = "high"
        merged["confidence"] = merged_conf
        structure = merged

    logger.info(f"Extracted PDF Structure: {json.dumps(structure, indent=2)}")
    return structure


async def analyze_uploaded_paper(text: str, custom_prompt: str = None, structure: dict = None) -> dict:
    """
    Run analysis on extracted PDF text. 
    If custom_prompt is provided, answers the prompt.
    Otherwise, returns the structured gap analysis shape.
    """
    # Just in case, validate again
    if not validate_layer_b(text):
        raise HTTPException(status_code=400, detail="Invalid text content.")
        
    if structure is None:
        structure = {"title": "", "authors": [], "abstract": "", "sections": {}}
        
    # Construct context for evidence extraction based on structure
    sections = structure.get("sections", {})
    method_text = sections.get("method", "")
    results_text = sections.get("results", "") or sections.get("results_and_discussion", "")
    structure_context = method_text + "\n" + results_text
    
    if not structure_context.strip():
        structure_context = structure.get("abstract", "")
    if not structure_context.strip():
        structure_context = text[:15000]
        
    evidence = await extract_evidence({"title": structure.get("title", ""), "abstract": structure_context})
    has_evidence = any(v.strip() for v in evidence.values())
    
    # Prepend essential metadata so the LLM can answer questions about it
    meta_title = structure.get("title", "Unknown")
    meta_authors = ", ".join(structure.get("authors", [])) if structure.get("authors") else "Unknown"
    metadata_prefix = f"Paper Title: {meta_title}\nAuthors: {meta_authors}\n\n"
    
    # Gap analysis uses just the evidence JSON to save tokens and stay focused
    gap_context_text = metadata_prefix + (json.dumps(evidence, indent=2) if has_evidence else text[:30000])
        
    if custom_prompt:
        if not validate_input_layers_a_b(custom_prompt):
            raise HTTPException(status_code=400, detail="Invalid custom prompt.")
            
        lower_prompt = custom_prompt.lower()
        
        # Direct structure match bypass for short, direct queries
        if len(lower_prompt.split()) < 10:
            if "author" in lower_prompt and structure.get("authors"):
                return {"type": "custom", "content": f"Based on the paper's structural metadata, the authors are: {meta_authors}."}
            title = structure.get("title", "")
            if title and title.lower() in lower_prompt:
                 return {"type": "custom", "content": f"The title of the paper is: {title}"}
             
        # For custom prompts, the LLM needs the full paper text (or structure) to answer arbitrary questions,
        # not just the 6-field evidence JSON. 
        context_data = {
            "abstract": structure.get("abstract", ""),
            "sections": structure.get("sections", {})
        }
        structured_sections = json.dumps(context_data, indent=2)
        custom_context = metadata_prefix + (structured_sections if len(structured_sections) > 20 else text)
        # Cap at ~40000 chars to avoid blowing up context window, but favor the end if asking for references
        if "reference" in lower_prompt and len(custom_context) > 40000:
            # If asking for references, keep the start (metadata) and the end (where references are)
            custom_context = custom_context[:5000] + "\n...[truncated]...\n" + custom_context[-35000:]
        else:
            custom_context = custom_context[:40000]

        # Fall back to LLM cascade
        prompt = _CUSTOM_PROMPT_TEMPLATE.replace("{text}", custom_context).replace("{custom_prompt}", custom_prompt)
        try:
            raw = await generate_completion(
                system_prompt="""You are a helpful academic research assistant.
CRITICAL FORMATTING RULES:
1. You MUST format your response using Markdown (use bolding, bullet points, and headers to make the text scannable).
2. For any mathematical equations, variables, or units with exponents (e.g. 10^3, Beff), you MUST wrap them in LaTeX syntax. Use single dollar signs ($x$) for inline math and double dollar signs ($$x$$) for block equations. Do NOT output raw unformatted math.
3. If providing code, use standard Markdown code blocks.
""",
                user_prompt=prompt,
                max_tokens=1000,
                temperature=0.3
            )
            return {"type": "custom", "content": raw.strip()}
        except Exception as e:
            logger.error(f"Failed custom PDF analysis: {e}")
            raise HTTPException(status_code=500, detail="Analysis failed.")

    # Default structured analysis
    prompt = _PDF_ANALYSIS_USER_TEMPLATE.replace("{text}", gap_context_text)
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
