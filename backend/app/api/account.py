from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..models import (AIUsage, Application, ApplicationEvent, Artifact, AuthSession,
                      CandidateProfile, GmailMessage, GmailSyncLock,
                      GoogleConnection, InterviewTurn, JobSearchSession,
                      OAuthState, Opportunity, Outreach, OutreachSettings,
                      ReplyAnalysis, ReplyDraft, Resume, SavedJob, User)
from ..schemas.core import OutreachSettingsBody
from ..services.embedding_service import EmbeddingService
from .outreach import application_payload
from .utils import normalize_profile, ok

router = APIRouter(tags=["account"])


class SettingsBody(BaseModel):
    career_preferences: dict = Field(default_factory=dict)
    outreach: OutreachSettingsBody = Field(default_factory=OutreachSettingsBody)


def _settings_data(db: Session, user_id: str) -> dict:
    row = db.get(OutreachSettings, user_id)
    return row.data if row else OutreachSettingsBody().model_dump()


@router.get("/api/v1/applications")
def applications(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), status: str = "", db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    query = db.query(Application).filter(Application.user_id == user.id)
    if status: query = query.filter(Application.status == status)
    rows = query.order_by(Application.last_activity_at.desc()).offset(offset).limit(limit).all()
    return ok("Applications loaded", {"applications": [application_payload(row) for row in rows]})


@router.get("/api/v1/search-history")
def search_history(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(JobSearchSession).filter(JobSearchSession.user_id == user.id).order_by(JobSearchSession.started_at.desc()).offset(offset).limit(limit).all()
    return ok("Search history loaded", {"searches": [{"id": row.id, "type": row.search_type, "query": row.raw_query, "status": row.status, "results_count": row.results_count, "started_at": row.started_at.isoformat()} for row in rows]})


@router.get("/api/v1/saved-jobs")
def saved_jobs(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), status: str = "", db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    query = db.query(SavedJob).filter(SavedJob.user_id == user.id)
    if status: query = query.filter(SavedJob.status == status)
    rows = query.order_by(SavedJob.saved_at.desc()).offset(offset).limit(limit).all()
    return ok("Saved jobs loaded", {"saved_jobs": [{"id": row.id, "job_id": row.job_id, "status": row.status, "notes": row.notes, "saved_at": row.saved_at.isoformat()} for row in rows]})


@router.get("/api/v1/outreach")
def outreach_list(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), status: str = "", db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    query = db.query(Outreach).filter(Outreach.user_id == user.id)
    if status: query = query.filter(Outreach.status == status)
    rows = query.order_by(Outreach.created_at.desc()).offset(offset).limit(limit).all()
    return ok("Outreach loaded", {"outreach": [{"id": row.id, "opportunity_id": row.opportunity_id, "status": row.status, "subject": row.subject, "created_at": row.created_at.isoformat()} for row in rows]})


@router.get("/api/v1/opportunities")
def opportunity_list(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), status: str = "", minimum_fit: float = Query(0, ge=0, le=100), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    query = db.query(Opportunity).filter(Opportunity.user_id == user.id, Opportunity.final_fit_score >= minimum_fit)
    if status: query = query.filter(Opportunity.status == status)
    rows = query.order_by(Opportunity.final_fit_score.desc()).offset(offset).limit(limit).all()
    return ok("Opportunities loaded", {"opportunities": [{"id": row.id, "job_id": row.job_id, "company_id": row.company_id, "status": row.status, "fit_score": row.final_fit_score, "analysis": row.analysis} for row in rows]})


@router.get("/api/v1/settings")
def get_settings(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    profile = db.get(CandidateProfile, user.id)
    return ok("Settings loaded", {"career_preferences": (profile.data.get("career_preferences") if profile else {}) or {}, "outreach": _settings_data(db, user.id)})


@router.patch("/api/v1/settings")
def patch_settings(body: SettingsBody, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.get(OutreachSettings, user.id)
    if not row: row = OutreachSettings(user_id=user.id); db.add(row)
    row.data = body.outreach.model_dump()
    profile = db.get(CandidateProfile, user.id)
    if profile and body.career_preferences:
        profile.data = {**(profile.data or {}), "career_preferences": body.career_preferences}
    db.commit()
    return ok("Settings updated", {"career_preferences": (profile.data.get("career_preferences") if profile else {}) or {}, "outreach": row.data})


@router.get("/api/v1/account/export")
def export_account(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    profile = db.get(CandidateProfile, user.id)
    resumes = db.query(Resume).filter(Resume.user_id == user.id).all()
    searches = db.query(JobSearchSession).filter(JobSearchSession.user_id == user.id).all()
    opportunities = db.query(Opportunity).filter(Opportunity.user_id == user.id).all()
    applications = db.query(Application).filter(Application.user_id == user.id).all()
    outreach = db.query(Outreach).filter(Outreach.user_id == user.id).all()
    return ok("Account export created", {"account": {"id": user.id, "email": user.email, "display_name": user.display_name, "created_at": user.created_at.isoformat()}, "profile": normalize_profile(profile.data) if profile else None, "resumes": [{"id": row.id, "filename": row.original_filename, "uploaded_at": row.uploaded_at.isoformat()} for row in resumes], "interview_history": [{"question": row.question, "answer": row.answer, "created_at": row.created_at.isoformat()} for row in db.query(InterviewTurn).filter(InterviewTurn.user_id == user.id).all()], "search_history": [{"id": row.id, "type": row.search_type, "query": row.raw_query, "status": row.status, "results_count": row.results_count} for row in searches], "saved_jobs": [{"job_id": row.job_id, "status": row.status, "notes": row.notes} for row in db.query(SavedJob).filter(SavedJob.user_id == user.id).all()], "opportunities": [{"id": row.id, "job_id": row.job_id, "status": row.status, "fit_score": row.final_fit_score} for row in opportunities], "applications": [application_payload(row) for row in applications], "outreach": [{"id": row.id, "opportunity_id": row.opportunity_id, "status": row.status, "subject": row.subject, "created_at": row.created_at.isoformat()} for row in outreach], "settings": _settings_data(db, user.id)})


@router.delete("/api/v1/account")
def delete_account(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    user_id = user.id
    outreach_ids = [row.id for row in db.query(Outreach.id).filter(Outreach.user_id == user_id).all()]
    application_ids = [row.id for row in db.query(Application.id).filter(Application.user_id == user_id).all()]
    if application_ids: db.query(ApplicationEvent).filter(ApplicationEvent.application_id.in_(application_ids)).delete(synchronize_session=False)
    if outreach_ids:
        db.query(ReplyDraft).filter(ReplyDraft.outreach_id.in_(outreach_ids)).delete(synchronize_session=False)
        db.query(ReplyAnalysis).filter(ReplyAnalysis.outreach_id.in_(outreach_ids)).delete(synchronize_session=False)
        db.query(GmailMessage).filter(GmailMessage.outreach_id.in_(outreach_ids)).delete(synchronize_session=False)
    db.query(Application).filter(Application.user_id == user_id).delete(synchronize_session=False)
    db.query(Outreach).filter(Outreach.user_id == user_id).delete(synchronize_session=False)
    db.query(SavedJob).filter(SavedJob.user_id == user_id).delete(synchronize_session=False)
    db.query(Opportunity).filter(Opportunity.user_id == user_id).delete(synchronize_session=False)
    db.query(JobSearchSession).filter(JobSearchSession.user_id == user_id).delete(synchronize_session=False)
    for model in (Artifact, InterviewTurn): db.query(model).filter(model.user_id == user_id).delete(synchronize_session=False)
    db.query(CandidateProfile).filter(CandidateProfile.user_id == user_id).delete(synchronize_session=False)
    db.query(Resume).filter(Resume.user_id == user_id).delete(synchronize_session=False)
    for model in (GoogleConnection, OutreachSettings, GmailSyncLock, OAuthState, AuthSession): db.query(model).filter(model.user_id == user_id).delete(synchronize_session=False)
    db.query(AIUsage).filter(AIUsage.user_id == user_id).delete(synchronize_session=False)
    db.delete(user); db.commit(); EmbeddingService().delete_user(user_id)
    return ok("Account deleted", {"deleted": True})
