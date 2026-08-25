"""Request payload models shared by the routers."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

# Long enough for any real address (RFC 5321 caps the path at 254) and short
# enough that a megabyte of "email" is rejected before it reaches the validator.
_EMAIL_MAX = 254

# bcrypt hashes at most 72 bytes; see core.auth.MAX_PASSWORD_BYTES. Content
# rules (letter + digit, not a common password) live in
# `core.auth.password_rule_violation` so signup and reset cannot drift apart.
_PASSWORD_MIN = 8
_PASSWORD_MAX = 72


class SignupPayload(BaseModel):
    email: EmailStr = Field(..., max_length=_EMAIL_MAX)
    password: str = Field(..., min_length=_PASSWORD_MIN, max_length=_PASSWORD_MAX)
    name: str = Field(..., min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Name cannot be blank.")
        return v

class LoginPayload(BaseModel):
    email: EmailStr = Field(..., max_length=_EMAIL_MAX)
    password: str = Field(..., min_length=1, max_length=_PASSWORD_MAX)

class GoogleAuthPayload(BaseModel):
    token: str = Field(..., min_length=1, max_length=8192)

class ForgotPasswordPayload(BaseModel):
    email: EmailStr = Field(..., max_length=_EMAIL_MAX)

class ResendVerificationPayload(BaseModel):
    email: EmailStr = Field(..., max_length=_EMAIL_MAX)

class ResetPasswordPayload(BaseModel):
    token: str = Field(..., min_length=1, max_length=4096)
    new_password: str = Field(..., min_length=_PASSWORD_MIN, max_length=_PASSWORD_MAX)

class RefreshPayload(BaseModel):
    """The rotating refresh token (1.14). In the body, never the query string —
    a session token in a URL lands in browser history and every proxy log."""
    refresh_token: str = Field(..., min_length=1, max_length=4096)

class TokenPayload(BaseModel):
    """A bare token in a POST body.

    Used by reset-token validation and email verification: both used to travel
    as a query string, which lands the token in browser history, the Referer
    header and every proxy access log along the way.
    """
    token: str = Field(..., min_length=1, max_length=4096)

class ManuscriptPayload(BaseModel):
    topic: str
    section: str = "abstract"
    context: str = ""
    citation_style: str = "ieee"

class ManuscriptStreamPayload(ManuscriptPayload):
    mode: str = "manual"
    provider: str = None
    model: str = None

class GapAnalysisPayload(BaseModel):
    topic: str

class ResearchPayload(BaseModel):
    """Which topic to prepare the corpus for (1.5)."""
    topic: str = Field(..., min_length=1, max_length=500)

class ManuscriptEditPayload(BaseModel):
    topic: str
    section: str = "abstract"
    current_content: str
    instructions: str
    # Without this the editor silently grounded every revision against IEEE,
    # even on a draft the user is writing in APA.
    citation_style: str = "ieee"
    # Optional element-level scoping. When target_text names a span of
    # current_content, only that span is rewritten and the reply is spliced back;
    # everything else is copied verbatim and cannot drift. Offsets are a hint --
    # the server re-verifies them against current_content. target_kind is
    # "selection" or "diagram"; a diagram span is the *body* of a ```mermaid
    # block, so the reply must come back without fences.
    target_text: Optional[str] = None
    target_start: Optional[int] = None
    target_end: Optional[int] = None
    target_kind: Optional[str] = None
    mode: str = "manual"
    provider: Optional[str] = None
    model: Optional[str] = None

class ManuscriptSavePayload(BaseModel):
    topic: str
    content: Dict[str, Any]
    gap_analysis: Optional[Dict[str, Any]] = None
    manuscript_refs: Optional[Dict[str, Any]] = None
    citation_style: Optional[str] = "ieee"

class ManuscriptRestorePayload(BaseModel):
    """Which stored version to put back. The draft is derived from the version
    rather than sent, so a restore cannot be aimed at a different draft."""
    version_id: str = Field(..., min_length=1, max_length=64)

class LatexExportPayload(BaseModel):
    topic: str
    venue: str
    author_name: str = "Author Name"
    author_affil: str = "Affiliation, City, Country"
    keywords: Optional[str] = None

class ProcessingConsentPayload(BaseModel):
    """Whether uploaded PDFs may be sent to the third-party parser (1.16)."""
    granted: bool

class PdfAnalyzePayload(BaseModel):
    text: str
    custom_prompt: Optional[str] = None

class PdfChatSavePayload(BaseModel):
    chat_id: Optional[str] = None
    filename: str
    text: str
    structure: Optional[Dict[str, Any]] = None
    messages: List[Dict[str, Any]]
    file_id: Optional[str] = None

class VenuePayload(BaseModel):
    abstract: str = ""
    domain: str = ""

class GuidelinePayload(BaseModel):
    manuscript: Dict[str, Any]
    venue: Dict[str, Any]

class LiteratureSavePayload(BaseModel):
    query: str
    papers: List[Any]
    screened: Optional[int] = None
    sources: Optional[List[Any]] = None
