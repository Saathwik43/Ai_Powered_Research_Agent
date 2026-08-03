"""
Magic-byte file signature validation.

Extension / declared content-type is attacker-controlled and easily spoofed
(rename a .exe to .pdf, or send Content-Type: application/pdf with garbage
bytes). This checks the actual leading bytes of the uploaded file against
known file-format signatures before the content is trusted downstream.
"""

from fastapi import HTTPException

_SIGNATURES = {
    "pdf": (b"%PDF-",),
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpeg": (b"\xff\xd8\xff",),
}


def verify_pdf_signature(contents: bytes) -> None:
    if not contents.startswith(_SIGNATURES["pdf"][0]):
        raise HTTPException(status_code=400, detail="File is not a valid PDF (signature mismatch).")


def verify_image_signature(contents: bytes) -> None:
    if not (contents.startswith(_SIGNATURES["png"][0]) or contents.startswith(_SIGNATURES["jpeg"][0])):
        raise HTTPException(status_code=400, detail="File is not a valid PNG/JPEG image (signature mismatch).")


def verify_upload_signature(contents: bytes, content_type: str, filename: str) -> None:
    """Dispatch based on what the caller *claims* the file is, then verify the
    real bytes back it up. Plain-text formats have no reliable magic number,
    so we just make sure they don't look like disguised binaries instead."""
    ct = (content_type or "").lower()
    fn = (filename or "").lower()

    if ct == "application/pdf" or fn.endswith(".pdf"):
        verify_pdf_signature(contents)
        return

    if ct.startswith("image/") or fn.endswith((".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp")):
        # tiff/bmp/webp don't share PDF/PNG/JPEG's simple fixed magic bytes here;
        # only enforce the strict check for the two we can verify cheaply.
        if fn.endswith((".png", ".jpg", ".jpeg")) or ct in ("image/png", "image/jpeg"):
            verify_image_signature(contents)
        return

    if ct in ("text/plain", "text/markdown", "text/csv", "application/json") or fn.endswith(
        (".txt", ".md", ".csv", ".json", ".tsv")
    ):
        # Reject if it looks like binary data pretending to be text (e.g. an
        # .exe renamed to .txt) — null bytes never appear in legit UTF-8 text.
        if b"\x00" in contents[:4096]:
            raise HTTPException(status_code=400, detail="File does not look like valid text content.")
        return
