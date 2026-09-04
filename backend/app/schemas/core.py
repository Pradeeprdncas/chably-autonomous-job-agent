from pydantic import BaseModel, Field
from typing import Any, Optional

class ProfilePatch(BaseModel):
    data: dict[str, Any]

class InterviewStart(BaseModel):
    user_id: str

class InterviewAnswer(BaseModel):
    user_id: str
    question_id: str
    answer: str = Field(min_length=1, max_length=5000)

class InterviewAnswerBody(BaseModel):
    question_id: str
    answer: str = Field(min_length=1, max_length=5000)

class RoleRequest(BaseModel):
    user_id: str

class RewriteRequest(RoleRequest):
    target_role: Optional[str] = None

class AnalyzeRequest(RoleRequest):
    pass

class ResumeRewriteBody(BaseModel):
    user_id: Optional[str] = None
    target_role: Optional[str] = Field(default=None, max_length=256)

class DiscoveryRequest(BaseModel):
    user_id: str
    query: str = Field(min_length=3, max_length=1000)
    freshness: Optional[str] = Field(default=None, pattern="^(24h|48h|day|7d|week|month|year)$")

class SavedJobRequest(BaseModel):
    user_id: str
    status: str = "saved"
    notes: str = Field(default="", max_length=4000)

class OpportunityStatusRequest(BaseModel):
    status: str

class OutreachSettingsBody(BaseModel):
    auto_send_enabled: bool = False
    minimum_fit_score: int = Field(default=90, ge=0, le=100)
    allowed_contact_types: list[str] = []
    maximum_daily_emails: int = Field(default=5, ge=1, le=50)
    auto_reply_enabled: bool = False
    allowed_categories: list[str] = ["acknowledgement", "request_for_resume", "request_for_portfolio"]
    minimum_classification_confidence: float = Field(default=0.92, ge=0, le=1)
    maximum_auto_replies_per_day: int = Field(default=5, ge=1, le=5)

class ApplicationBody(BaseModel):
    status: str = "applied"
    notes: str = Field(default="", max_length=4000)

class ApplicationPatch(BaseModel):
    status: str
    notes: Optional[str] = Field(default=None, max_length=4000)
