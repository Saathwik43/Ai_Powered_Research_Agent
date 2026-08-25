"""
Third-party document processing: disclosure and consent (1.16).

Uploaded PDFs were sent to LlamaCloud for parsing without the user being told.
That is a processor relationship: their document — often unpublished work, often
somebody else's copyrighted paper — leaves this system and is handled by a
company they never heard of, under that company's retention policy rather than
ours.

The scope shrank on 2026-08-22. Hosted GROBID was removed and structure parsing
is now in-process (`ai/pdf_structure.py`), so LlamaCloud is the only third party
left on the PDF path. That makes the honest version of this small: name the one
processor, say what it receives, and let the user decline.

**Declining is not a degraded product.** LlamaParse is tier 1 of four; tiers 2
and 3 (PyMuPDF, pypdf) run in this process on the same bytes and handle an
ordinary text PDF perfectly well. What is lost is layout-aware parsing of
scanned or heavily-formatted documents. So consent defaults to *off* and the
local tiers are used until the user turns it on — the opposite of the previous
behaviour, where the paid remote parser was tried first, always, silently.

What this module deliberately does **not** claim to do: a DPA is a contract
between the operator and LlamaIndex, signed out of band. Code can record that a
user was told and agreed; it cannot be the agreement.
"""

import logging
import os
from datetime import datetime, timezone

from bson import ObjectId

logger = logging.getLogger(__name__)

# Bumped whenever the disclosure below changes materially, which re-asks a user
# who agreed to an older wording rather than treating the old answer as covering
# the new terms.
DISCLOSURE_VERSION = "2026-08-25"

PROCESSORS = [
    {
        "name": "LlamaCloud (LlamaIndex, Inc.)",
        "purpose": "Layout-aware text extraction from uploaded PDFs (LlamaParse).",
        "receives": "The full bytes of the PDF you upload.",
        "when": "Only when a PDF's text cannot already be recovered from arXiv LaTeX source.",
        "url": "https://www.llamaindex.ai/privacy",
    },
]

DISCLOSURE = (
    "Better parsing of scanned or heavily formatted PDFs uses LlamaParse, run by "
    "LlamaIndex, Inc. Turning this on means the full file you upload is sent to "
    "their servers and processed under their privacy policy. "
    "Leave it off and your PDFs are parsed entirely on this server — that works "
    "for ordinary text PDFs and is the default. Nothing else on the PDF path "
    "leaves this system. You can change this at any time; it only affects "
    "uploads made after the change."
)

CONSENT_FIELD = "third_party_pdf_consent"


def _is_configured() -> bool:
    """Whether the remote parser could run at all. With no API key the consent
    question is moot, and asking it would be theatre."""
    return bool(os.getenv("LLAMA_CLOUD_API_KEY") or os.getenv("LLAMA_PARSE_API_KEY"))


def consent_state(user_doc: dict | None) -> dict:
    """The consent answer on record, in the shape the API returns."""
    stored = (user_doc or {}).get(CONSENT_FIELD) or {}
    granted = bool(stored.get("granted"))
    version = stored.get("version")
    return {
        "granted": granted,
        # An answer given against older wording is not an answer to this one.
        "current": granted and version == DISCLOSURE_VERSION,
        "version": version,
        "decided_at": stored.get("at"),
        "disclosure_version": DISCLOSURE_VERSION,
        "disclosure": DISCLOSURE,
        "processors": PROCESSORS,
        "available": _is_configured(),
    }


async def user_allows_third_party(user_id: str) -> bool:
    """Whether *user_id* has agreed to the **current** disclosure.

    Fails closed. An unreadable consent record means the document is parsed
    locally, which is a worse parse and not a disclosure the user did not make.
    """
    if not _is_configured():
        return False
    from core.auth import _load_user

    try:
        user = await _load_user(str(user_id))
    except Exception as e:
        logger.warning("Consent lookup failed for %s, parsing locally: %s", user_id, e)
        return False
    return bool(consent_state(user).get("current"))


async def record_consent(user_id: str, granted: bool) -> dict:
    """Store the user's answer and return the new state."""
    from core.auth import invalidate_user_cache
    from core.database import db

    value = {
        "granted": bool(granted),
        "version": DISCLOSURE_VERSION,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    await db["users"].update_one(
        {"_id": ObjectId(str(user_id))}, {"$set": {CONSENT_FIELD: value}}
    )
    invalidate_user_cache(str(user_id))
    logger.info(
        "Third-party PDF processing consent %s by user %s (disclosure %s)",
        "granted" if granted else "declined", user_id, DISCLOSURE_VERSION,
    )
    return consent_state({CONSENT_FIELD: value})
