import logging

logger = logging.getLogger(__name__)

async def extract_source_text(
    file_bytes: bytes, content_type: str, filename: str, allow_third_party: bool = False
) -> str:
    ct = (content_type or "").lower()
    fn = (filename or "").lower()

    if ct == "application/pdf" or fn.endswith(".pdf"):
        # Defaults to False: only a caller that has checked the uploader's
        # consent can send the file off-premises (1.16).
        from ai.pdf_analysis import extract_pdf_text
        return await extract_pdf_text(file_bytes, allow_third_party=allow_third_party)

    if ct.startswith("image/") or fn.endswith((".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp")):
        try:
            import importlib
            pytesseract = importlib.import_module("pytesseract")
            from PIL import Image
            import io
            image = Image.open(io.BytesIO(file_bytes))
            text = pytesseract.image_to_string(image)
            return text.strip()
        except Exception as e:
            logger.warning(f"OCR extraction failed for {filename}: {e}")
            return ""

    if ct in ("text/plain", "text/markdown", "text/csv", "application/json") or fn.endswith((".txt", ".md", ".csv", ".json", ".tsv")):
        return file_bytes.decode("utf-8", errors="ignore")

    # Default fallback: keep raw UTF-8 text verbatim
    try:
        return file_bytes.decode("utf-8", errors="ignore")
    except Exception:
        raise ValueError(f"Unsupported file type: {content_type}")