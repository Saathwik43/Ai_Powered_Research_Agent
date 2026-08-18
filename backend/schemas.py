"""Request payload models shared by the routers."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SignupPayload(BaseModel):
    email: str = Field(..., min_length=1)
    password: str = Field(..., min_length=8)
    name: str = Field(..., min_length=1)

class LoginPayload(BaseModel):
    email: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)

class GoogleAuthPayload(BaseModel):
    token: str = Field(..., min_length=1)

class ForgotPasswordPayload(BaseModel):
    email: str = Field(..., min_length=1)

class ResetPasswordPayload(BaseModel):
    token: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8)

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

class ManuscriptEditPayload(BaseModel):
    topic: str
    section: str = "abstract"
    current_content: str
    instructions: str
    # Without this the editor silently grounded every revision against IEEE,
    # even on a draft the user is writing in APA.
    citation_style: str = "ieee"

class ManuscriptSavePayload(BaseModel):
    topic: str
    content: Dict[str, Any]
    gap_analysis: Optional[Dict[str, Any]] = None
    manuscript_refs: Optional[Dict[str, Any]] = None
    citation_style: Optional[str] = "ieee"

class LatexExportPayload(BaseModel):
    topic: str
    venue: str
    author_name: str = "Author Name"
    author_affil: str = "Affiliation, City, Country"

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

class GithubSyncPayload(BaseModel):
    repo: Optional[str] = None
