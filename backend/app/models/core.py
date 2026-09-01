from datetime import datetime
from typing import Optional
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from ..database import Base


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[str] = mapped_column(String(24), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    device_info: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class OAuthState(Base):
    __tablename__ = "oauth_states"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class GoogleLoginState(Base):
    __tablename__ = "google_login_states"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class GoogleLoginExchange(Base):
    __tablename__ = "google_login_exchanges"
    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class AIUsage(Base):
    __tablename__ = "ai_usage"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    operation: Mapped[str] = mapped_column(String(64))
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Resume(Base):
    __tablename__ = "resumes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    original_filename: Mapped[str] = mapped_column(String(512))
    extracted_text: Mapped[str] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CandidateProfile(Base):
    __tablename__ = "candidate_profiles"
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    resume_id: Mapped[str] = mapped_column(String(36), ForeignKey("resumes.id"))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class InterviewTurn(Base):
    __tablename__ = "interview_turns"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    target_category: Mapped[str] = mapped_column(String(80))
    target_fields: Mapped[list] = mapped_column(JSON, default=list)
    reason: Mapped[str] = mapped_column(Text)
    score_before: Mapped[int] = mapped_column(Integer)
    score_after: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class JobSearchSession(Base):
    __tablename__ = "job_search_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    search_type: Mapped[str] = mapped_column(String(20), default="jobs")
    raw_query: Mapped[str] = mapped_column(Text)
    structured_intent: Mapped[dict] = mapped_column(JSON, default=dict)
    search_queries: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="created")
    progress: Mapped[dict] = mapped_column(JSON, default=dict)
    results_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    website: Mapped[str] = mapped_column(String(1024))
    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    careers_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    ats_provider: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_checked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("company_id", "job_url", name="uq_job_company_url"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("companies.id"), index=True)
    raw_title: Mapped[str] = mapped_column(String(512))
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    remote_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    employment_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    experience_min: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    experience_max: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    skills: Mapped[list] = mapped_column(JSON, default=list)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    job_url: Mapped[str] = mapped_column(String(2048))
    source_url: Mapped[str] = mapped_column(String(2048))
    source_type: Mapped[str] = mapped_column(String(60), default="search")
    status: Mapped[str] = mapped_column(String(20), default="open")
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_verified_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Opportunity(Base):
    __tablename__ = "opportunities"
    __table_args__ = (UniqueConstraint("user_id", "job_id", name="uq_opportunity_user_job"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    search_session_id: Mapped[str] = mapped_column(String(36), ForeignKey("job_search_sessions.id"))
    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("companies.id"))
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id"))
    retrieval_score: Mapped[float] = mapped_column(Float, default=0)
    deterministic_fit_score: Mapped[float] = mapped_column(Float, default=0)
    ai_fit_score: Mapped[float] = mapped_column(Float, default=0)
    final_fit_score: Mapped[float] = mapped_column(Float, default=0)
    analysis: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="discovered")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SavedJob(Base):
    __tablename__ = "saved_jobs"
    __table_args__ = (UniqueConstraint("user_id", "job_id", name="uq_saved_user_job"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id"))
    status: Mapped[str] = mapped_column(String(24), default="saved")
    notes: Mapped[str] = mapped_column(Text, default="")
    saved_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class GoogleConnection(Base):
    __tablename__ = "google_connections"
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(36), unique=True)
    google_email: Mapped[str] = mapped_column(String(320))
    google_account_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    refresh_token_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    token_expiry: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    connected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_refreshed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")


class Contact(Base):
    __tablename__ = "contacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("companies.id"), index=True)
    name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    role: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    source_url: Mapped[str] = mapped_column(String(2048))
    source_type: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    verification_status: Mapped[str] = mapped_column(String(32), default="public_unverified")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Outreach(Base):
    __tablename__ = "outreach"
    __table_args__ = (UniqueConstraint("user_id", "opportunity_id", "contact_id", name="uq_outreach_target"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    opportunity_id: Mapped[str] = mapped_column(String(36), ForeignKey("opportunities.id"))
    contact_id: Mapped[str] = mapped_column(String(36), ForeignKey("contacts.id"))
    resume_version_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    subject: Mapped[str] = mapped_column(String(512))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="draft")
    gmail_message_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    gmail_thread_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    last_reply_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("user_id", "opportunity_id", name="uq_application_opportunity"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    opportunity_id: Mapped[str] = mapped_column(String(36), ForeignKey("opportunities.id"))
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id"))
    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("companies.id"))
    outreach_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("outreach.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="drafting")
    source: Mapped[str] = mapped_column(String(64), default="outreach")
    applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    notes: Mapped[str] = mapped_column(Text, default="")


class ApplicationEvent(Base):
    __tablename__ = "application_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    application_id: Mapped[str] = mapped_column(String(36), ForeignKey("applications.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    old_status: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    new_status: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    source: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OutreachSettings(Base):
    __tablename__ = "outreach_settings"
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class GmailMessage(Base):
    __tablename__ = "gmail_messages"
    gmail_message_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    outreach_id: Mapped[str] = mapped_column(String(36), ForeignKey("outreach.id"), index=True)
    gmail_thread_id: Mapped[str] = mapped_column(String(256), index=True)
    gmail_history_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    internal_date: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    sender: Mapped[str] = mapped_column(String(512), default="")
    recipients: Mapped[list] = mapped_column(JSON, default=list)
    subject: Mapped[str] = mapped_column(String(512), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    headers: Mapped[dict] = mapped_column(JSON, default=dict)
    direction: Mapped[str] = mapped_column(String(24))
    message_type: Mapped[str] = mapped_column(String(32), default="unknown")
    received_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ReplyAnalysis(Base):
    __tablename__ = "reply_analyses"
    gmail_message_id: Mapped[str] = mapped_column(String(256), ForeignKey("gmail_messages.gmail_message_id"), primary_key=True)
    id: Mapped[str] = mapped_column(String(36), unique=True)
    outreach_id: Mapped[str] = mapped_column(String(36), ForeignKey("outreach.id"), index=True)
    category: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float)
    safe_for_auto_reply: Mapped[bool] = mapped_column(default=False)
    requires_user_review: Mapped[bool] = mapped_column(default=True)
    extracted_data: Mapped[dict] = mapped_column(JSON, default=dict)
    provider: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ReplyDraft(Base):
    __tablename__ = "reply_drafts"
    source_incoming_message_id: Mapped[str] = mapped_column(String(256), ForeignKey("gmail_messages.gmail_message_id"), primary_key=True)
    id: Mapped[str] = mapped_column(String(36), unique=True)
    outreach_id: Mapped[str] = mapped_column(String(36), ForeignKey("outreach.id"), index=True)
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="draft")
    gmail_message_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class GmailSyncLock(Base):
    __tablename__ = "gmail_sync_locks"
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    locked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
