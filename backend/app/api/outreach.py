from __future__ import annotations

import re
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Application, ApplicationEvent, CandidateProfile, Company, Contact, GoogleConnection, Job, OAuthState, Opportunity, Outreach, OutreachSettings, ReplyAnalysis, ReplyDraft, User
from ..auth import enforce_user, get_current_user
from ..schemas.core import ApplicationBody, ApplicationPatch, OutreachSettingsBody
from ..services.gemini_provider import GeminiProvider
from ..services.google_gmail import GmailService, authorization_url, decrypt_token, encrypt_token, public_scopes, scope_list
from ..services.reply_sync import GmailReplySyncService
from .utils import fail, ok

router = APIRouter(tags=["outreach"])
EMAIL = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)

def connection_payload(connection):
    status = connection.status if connection else "disconnected"
    scopes = public_scopes(connection.scopes) if connection else []
    return {"connected": status == "active", "email": connection.google_email if connection else None, "scopes": scopes, "status": status, "reauth_required": status == "reauth_required", "capabilities": {"send": "gmail.send" in scopes or "gmail.modify" in scopes, "reply_tracking": "gmail.readonly" in scopes or "gmail.modify" in scopes}}
def contact_payload(contact): return {"id": contact.id, "name": contact.name, "email": contact.email, "role": contact.role, "source_url": contact.source_url, "source_type": contact.source_type, "confidence": contact.confidence, "verification_status": contact.verification_status}
def outreach_payload(outreach): return {"id": outreach.id, "status": outreach.status, "subject": outreach.subject, "body": outreach.body, "contact_id": outreach.contact_id, "gmail_message_id": outreach.gmail_message_id, "gmail_thread_id": outreach.gmail_thread_id, "created_at": outreach.created_at.isoformat(), "sent_at": outreach.sent_at.isoformat() if outreach.sent_at else None}

def require_owner(resource, user_id: str, code: str):
    if not resource or getattr(resource, "user_id", None) != user_id:
        fail(404, code, "Resource not found for this user.")
    return resource

async def token_for(db, user_id, required_scope: str | None = None):
    connection = db.get(GoogleConnection, user_id)
    if not connection or connection.status != "active": fail(401, "GOOGLE_NOT_CONNECTED", "Google account is not connected.")
    if connection.token_expiry and connection.token_expiry < datetime.utcnow():
        if not connection.refresh_token_encrypted: connection.status = "reauth_required"; db.commit(); fail(401, "GOOGLE_REAUTH_REQUIRED", "Google authorization must be renewed.")
        try:
            refreshed = await GmailService().refresh(decrypt_token(connection.refresh_token_encrypted)); connection.access_token_encrypted = encrypt_token(refreshed["access_token"]); connection.token_expiry = datetime.utcnow() + timedelta(seconds=refreshed.get("expires_in", 3600)); connection.last_refreshed_at = datetime.utcnow(); db.commit()
        except Exception: connection.status = "reauth_required"; db.commit(); fail(401, "GOOGLE_REAUTH_REQUIRED", "Google authorization must be renewed.")
    if required_scope and not any(scope.endswith(required_scope) or (required_scope == "gmail.readonly" and scope.endswith("gmail.modify")) for scope in scope_list(connection.scopes)):
        fail(403, "GOOGLE_SCOPE_REQUIRED", f"Google scope {required_scope} is required for this operation.")
    return decrypt_token(connection.access_token_encrypted)

@router.post("/api/v1/integrations/google/connect")
@router.get("/api/v1/integrations/google/connect", deprecated=True)
def connect_google(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    raw_state = secrets.token_urlsafe(32)
    db.add(OAuthState(state_hash=hashlib.sha256(raw_state.encode()).hexdigest(), user_id=current_user.id, expires_at=datetime.utcnow() + timedelta(minutes=10))); db.commit()
    try: return ok("Google authorization URL created", {"authorization_url": authorization_url(raw_state)})
    except RuntimeError: fail(503, "GOOGLE_OAUTH_NOT_CONFIGURED", "Google OAuth is not configured.")

@router.get("/api/v1/integrations/google/callback")
async def google_callback(code: str, state: str, db: Session = Depends(get_db)):
    state_row = db.get(OAuthState, hashlib.sha256(state.encode()).hexdigest())
    if not state_row or state_row.used_at or state_row.expires_at <= datetime.utcnow(): fail(400, "INVALID_OAUTH_STATE", "OAuth state is invalid or expired.")
    state_row.used_at = datetime.utcnow(); user_id = state_row.user_id; db.commit()
    try: token = await GmailService().exchange_code(code)
    except Exception: fail(502, "GOOGLE_OAUTH_FAILED", "Google OAuth token exchange failed.")
    connection = db.get(GoogleConnection, user_id)
    if not connection: connection = GoogleConnection(user_id=user_id, id=str(uuid.uuid4()), google_email=token["email"], google_account_id=token.get("sub"), access_token_encrypted=""); db.add(connection)
    connection.google_email = token["email"]; connection.google_account_id = token.get("sub"); connection.access_token_encrypted = encrypt_token(token["access_token"]); connection.refresh_token_encrypted = encrypt_token(token["refresh_token"]) if token.get("refresh_token") else connection.refresh_token_encrypted; connection.token_expiry = datetime.utcnow() + timedelta(seconds=token.get("expires_in", 3600)); connection.scopes = scope_list(token.get("scope") or settings.google_oauth_scopes); connection.status = "active"; db.commit()
    return ok("Google account connected", {**connection_payload(connection), "frontend_return_url": settings.frontend_url}, events=[{"type":"GOOGLE_CONNECTED","label":"Google account connected"}])

@router.get("/api/v1/integrations/google/status")
def google_status_me(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)): return ok("Google connection status loaded", connection_payload(db.get(GoogleConnection, current_user.id)))

@router.get("/api/v1/integrations/google/status/{user_id}", deprecated=True)
def google_status(user_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)): enforce_user(user_id, current_user); return ok("Google connection status loaded", connection_payload(db.get(GoogleConnection, user_id)))

@router.delete("/api/v1/integrations/google/{user_id}", deprecated=True)
def disconnect_google(user_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user(user_id, current_user)
    connection = db.get(GoogleConnection, user_id)
    if connection: connection.status = "disconnected"; connection.access_token_encrypted = ""; connection.refresh_token_encrypted = None; db.commit()
    return ok("Google account disconnected", {"connected": False})

@router.get("/api/v1/opportunities/{opportunity_id}/contacts")
def discover_contacts(opportunity_id: str, user_id: str = Query(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user(user_id, current_user)
    opportunity = require_owner(db.get(Opportunity, opportunity_id), user_id, "OPPORTUNITY_NOT_FOUND")
    company = db.get(Company, opportunity.company_id); content = " ".join([company.description or "", str(company.data or {})]); emails = set(EMAIL.findall(content)); contacts = db.query(Contact).filter(Contact.company_id == company.id).all()
    existing = {contact.email.lower() for contact in contacts}
    for email in emails - existing:
        if email.lower().startswith(("support@", "billing@", "privacy@", "legal@")): continue
        contact = Contact(id=str(uuid.uuid4()), company_id=company.id, email=email, source_url=company.careers_url or company.website, source_type="company_public_content", confidence=.8, verification_status="public_verified"); db.add(contact); contacts.append(contact)
    db.commit(); return ok("Public contacts loaded", {"contacts": [contact_payload(contact) for contact in contacts]}, events=[{"type":"CONTACT_DISCOVERED","label":"Public contact discovered"}] if emails else [])

@router.post("/api/v1/opportunities/{opportunity_id}/draft-email")
async def draft_email(opportunity_id: str, user_id: str = Query(...), contact_id: str = Query(None), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user(user_id, current_user)
    opportunity = db.get(Opportunity, opportunity_id); profile = db.get(CandidateProfile, user_id)
    if not opportunity or not profile: fail(404, "OPPORTUNITY_OR_PROFILE_NOT_FOUND", "Opportunity or candidate profile not found.")
    require_owner(opportunity, user_id, "OPPORTUNITY_NOT_FOUND")
    job = db.get(Job, opportunity.job_id); company = db.get(Company, opportunity.company_id); contact = db.get(Contact, contact_id) if contact_id else db.query(Contact).filter(Contact.company_id == company.id).order_by(Contact.confidence.desc()).first()
    if not contact or contact.company_id != company.id: fail(404, "CONTACT_NOT_FOUND", "No public contact is available for this opportunity.")
    existing = db.query(Outreach).filter(Outreach.user_id == user_id, Outreach.opportunity_id == opportunity.id, Outreach.contact_id == contact.id).first()
    if existing: return ok("Existing outreach draft loaded", {"outreach": outreach_payload(existing), "contact": contact_payload(contact)})
    name = (profile.data.get("personal_information") or {}).get("name") or "the candidate"; strengths = [skill for values in (profile.data.get("skills") or {}).values() if isinstance(values, list) for skill in values][:2]
    subject = f"Interest in {job.title} at {company.name}"; body = f"Hello,\n\nI’m {name}, and I’m interested in the {job.title} role at {company.name}. My background includes {', '.join(strengths) or 'relevant engineering work'}, which aligns with the role’s requirements.\n\nI’d appreciate the opportunity to be considered.\n\nBest,\n{name}"
    outreach = Outreach(id=str(uuid.uuid4()), user_id=user_id, opportunity_id=opportunity.id, contact_id=contact.id, subject=subject, body=body); db.add(outreach); db.commit()
    return ok("Outreach draft created", {"outreach": outreach_payload(outreach), "contact": contact_payload(contact), "reasoning_summary": "Uses verified candidate skills and the specific job title."}, events=[{"type":"OUTREACH_DRAFTED","label":"Outreach draft created"}])

@router.post("/api/v1/outreach/{outreach_id}/approve")
def approve_outreach(outreach_id: str, user_id: str = Query(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user(user_id, current_user)
    outreach = require_owner(db.get(Outreach, outreach_id), user_id, "OUTREACH_NOT_FOUND")
    if outreach.status != "draft": fail(400, "OUTREACH_NOT_DRAFT", "Only drafts can be approved.")
    outreach.status = "approved"; db.commit(); return ok("Outreach approved", outreach_payload(outreach), events=[{"type":"OUTREACH_APPROVED","label":"Outreach approved"}])

@router.post("/api/v1/outreach/{outreach_id}/send")
async def send_outreach(outreach_id: str, user_id: str = Query(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user(user_id, current_user)
    outreach = require_owner(db.get(Outreach, outreach_id), user_id, "OUTREACH_NOT_FOUND")
    if outreach.status != "approved": fail(400, "OUTREACH_NOT_APPROVED", "Outreach must be approved before sending.")
    if db.query(Outreach).filter(Outreach.user_id == outreach.user_id, Outreach.status == "sent", Outreach.sent_at >= datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)).count() >= settings.max_daily_outreach: fail(429, "DAILY_OUTREACH_LIMIT_REACHED", "Daily outreach limit reached.")
    contact = db.get(Contact, outreach.contact_id); token = await token_for(db, outreach.user_id, "gmail.send")
    try: result = await GmailService().send_email(token, contact.email, outreach.subject, outreach.body)
    except Exception: outreach.status = "failed"; db.commit(); fail(502, "GMAIL_SEND_FAILED", "Gmail could not send this outreach.")
    outreach.status = "sent"; outreach.gmail_message_id = result.get("id"); outreach.gmail_thread_id = result.get("threadId"); outreach.sent_at = datetime.utcnow(); db.commit(); return ok("Outreach sent", outreach_payload(outreach), events=[{"type":"EMAIL_SENT","label":"Email sent"}])

@router.get("/api/v1/outreach/{outreach_id}")
def get_outreach(outreach_id: str, user_id: str = Query(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user(user_id, current_user)
    outreach = require_owner(db.get(Outreach, outreach_id), user_id, "OUTREACH_NOT_FOUND")
    return ok("Outreach loaded", {"outreach": outreach_payload(outreach), "contact": contact_payload(db.get(Contact, outreach.contact_id))})

@router.get("/api/v1/outreach/{outreach_id}/thread")
async def outreach_thread(outreach_id: str, user_id: str = Query(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user(user_id, current_user)
    outreach = require_owner(db.get(Outreach, outreach_id), user_id, "THREAD_NOT_FOUND")
    if not outreach.gmail_thread_id: fail(404, "THREAD_NOT_FOUND", "No Gmail thread is available.")
    try: thread = await GmailService().get_thread(await token_for(db, outreach.user_id, "gmail.readonly"), outreach.gmail_thread_id)
    except Exception: fail(502, "GMAIL_READ_FAILED", "Gmail thread could not be read.")
    outreach.last_reply_checked_at = datetime.utcnow(); db.commit(); return ok("Outreach thread loaded", {"thread": thread})

@router.post("/api/v1/outreach/{outreach_id}/draft-reply")
async def draft_reply(outreach_id: str, user_id: str = Query(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user(user_id, current_user)
    outreach = require_owner(db.get(Outreach, outreach_id), user_id, "OUTREACH_NOT_FOUND")
    draft = db.query(ReplyDraft).filter(ReplyDraft.outreach_id == outreach_id).order_by(ReplyDraft.created_at.desc()).first()
    analysis = db.get(ReplyAnalysis, draft.source_incoming_message_id) if draft else None
    if not draft: fail(404, "REPLY_NOT_FOUND", "No incoming reply is available to draft against.")
    return ok("Reply draft loaded", {"reply_id": draft.id, "reply": draft.body, "classification": analysis.category, "confidence": analysis.confidence, "requires_user_review": analysis.requires_user_review}, events=[{"type":"REPLY_DRAFTED","label":"Reply draft created"}])

@router.post("/api/v1/replies/{reply_id}/approve")
def approve_reply(reply_id: str, user_id: str = Query(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user(user_id, current_user)
    draft = db.query(ReplyDraft).filter(ReplyDraft.id == reply_id).first()
    if not draft: fail(404, "REPLY_NOT_FOUND", "Reply draft not found.")
    require_owner(db.get(Outreach, draft.outreach_id), user_id, "REPLY_NOT_FOUND")
    if draft.status != "draft": fail(400, "REPLY_NOT_DRAFT", "Only draft replies can be approved.")
    draft.status = "approved"; db.commit(); return ok("Reply approved", {"id": draft.id, "status": draft.status})

@router.post("/api/v1/replies/{reply_id}/send")
async def send_reply(reply_id: str, user_id: str = Query(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user(user_id, current_user)
    draft = db.query(ReplyDraft).filter(ReplyDraft.id == reply_id).first()
    if not draft: fail(404, "REPLY_NOT_FOUND", "Reply draft not found.")
    if draft.status != "approved": fail(400, "REPLY_NOT_APPROVED", "Reply must be approved before sending.")
    outreach = require_owner(db.get(Outreach, draft.outreach_id), user_id, "REPLY_NOT_FOUND"); contact = db.get(Contact, outreach.contact_id)
    sent = await GmailService().reply_to_thread(await token_for(db, outreach.user_id, "gmail.send"), outreach.gmail_thread_id, contact.email, draft.body)
    draft.status = "sent"; draft.gmail_message_id = sent.get("id"); draft.sent_at = datetime.utcnow()
    application = db.query(Application).filter(Application.user_id == user_id, Application.opportunity_id == outreach.opportunity_id).first()
    if application: db.add(ApplicationEvent(id=str(uuid.uuid4()), application_id=application.id, event_type="REPLY_SENT", old_status=application.status, new_status=application.status, source="manual", data={"reply_id": draft.id, "gmail_message_id": sent.get("id")}))
    db.commit(); return ok("Reply sent", {"id": draft.id, "status": draft.status}, events=[{"type":"REPLY_SENT","label":"Reply sent"}])

@router.post("/api/v1/integrations/google/{user_id}/sync")
async def sync_google(user_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user(user_id, current_user)
    try: result = await GmailReplySyncService(db).sync_user(user_id)
    except RuntimeError as exc: fail(409 if str(exc) == "SYNC_ALREADY_RUNNING" else 401, str(exc), "Gmail synchronization could not start.")
    return ok("Gmail replies synchronized", result)

def application_payload(application): return {"id": application.id, "opportunity_id": application.opportunity_id, "status": application.status, "source": application.source, "applied_at": application.applied_at.isoformat() if application.applied_at else None, "notes": application.notes}

@router.post("/api/v1/opportunities/{opportunity_id}/application")
def create_application(opportunity_id: str, body: ApplicationBody, user_id: str = Query(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user(user_id, current_user)
    opportunity = require_owner(db.get(Opportunity, opportunity_id), user_id, "OPPORTUNITY_NOT_FOUND")
    application = db.query(Application).filter(Application.user_id == user_id, Application.opportunity_id == opportunity_id).first()
    if not application:
        outreach = db.query(Outreach).filter(Outreach.user_id == user_id, Outreach.opportunity_id == opportunity_id).first()
        application = Application(id=str(uuid.uuid4()), user_id=user_id, opportunity_id=opportunity_id, job_id=opportunity.job_id, company_id=opportunity.company_id, outreach_id=outreach.id if outreach else None, status=body.status, notes=body.notes, applied_at=datetime.utcnow() if body.status == "applied" else None); db.add(application); db.flush()
        db.add(ApplicationEvent(id=str(uuid.uuid4()), application_id=application.id, event_type="APPLICATION_CREATED", old_status=None, new_status=body.status, source="manual", data={}))
    db.commit(); return ok("Application created", application_payload(application), events=[{"type":"APPLICATION_CREATED","label":"Application created"}])

@router.get("/api/v1/users/{user_id}/applications")
def list_applications(user_id: str, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), status: str = "", db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user(user_id, current_user)
    query = db.query(Application).filter(Application.user_id == user_id)
    if status: query = query.filter(Application.status == status)
    return ok("Applications loaded", {"applications": [application_payload(item) for item in query.order_by(Application.last_activity_at.desc()).offset(offset).limit(limit).all()]})

@router.get("/api/v1/applications/{application_id}")
def get_application(application_id: str, user_id: str = Query(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user(user_id, current_user)
    application = require_owner(db.get(Application, application_id), user_id, "APPLICATION_NOT_FOUND")
    events = db.query(ApplicationEvent).filter(ApplicationEvent.application_id == application_id).all()
    return ok("Application loaded", {"application": application_payload(application), "events": [{"type": event.event_type, "old_status": event.old_status, "new_status": event.new_status, "created_at": event.created_at.isoformat()} for event in events]})

@router.patch("/api/v1/applications/{application_id}")
def update_application(application_id: str, body: ApplicationPatch, user_id: str = Query(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user(user_id, current_user)
    application = require_owner(db.get(Application, application_id), user_id, "APPLICATION_NOT_FOUND")
    old = application.status; application.status = body.status; application.notes = body.notes if body.notes is not None else application.notes; application.last_activity_at = datetime.utcnow(); db.add(ApplicationEvent(id=str(uuid.uuid4()), application_id=application.id, event_type="STATUS_MANUALLY_UPDATED", old_status=old, new_status=body.status, source="manual", data={})); db.commit(); return ok("Application updated", application_payload(application), events=[{"type":"APPLICATION_STATUS_CHANGED","label":"Application status changed"}])

@router.get("/api/v1/users/{user_id}/outreach-settings")
def get_settings(user_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user(user_id, current_user)
    row = db.get(OutreachSettings, user_id); return ok("Outreach settings loaded", row.data if row else {"auto_send_enabled": False, "minimum_fit_score": 90, "allowed_contact_types": [], "maximum_daily_emails": 5, "auto_reply_enabled": False, "allowed_categories": ["acknowledgement", "request_for_resume", "request_for_portfolio"], "minimum_classification_confidence": .92, "maximum_auto_replies_per_day": 5})

@router.patch("/api/v1/users/{user_id}/outreach-settings")
def update_settings(user_id: str, body: OutreachSettingsBody, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user(user_id, current_user)
    row = db.get(OutreachSettings, user_id)
    if not row: row = OutreachSettings(user_id=user_id); db.add(row)
    row.data = body.model_dump(); db.commit(); return ok("Outreach settings updated", row.data)
