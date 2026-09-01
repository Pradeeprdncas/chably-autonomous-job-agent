from __future__ import annotations

import base64
import logging
import re
import uuid
from datetime import datetime, timedelta
from email.utils import parseaddr
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Application, ApplicationEvent, CandidateProfile, Company, Contact, GmailMessage, GmailSyncLock, GoogleConnection, Job, Outreach, OutreachSettings, ReplyAnalysis, ReplyDraft
from .gemini_provider import GeminiProvider
from .google_gmail import GmailService, decrypt_token, encrypt_token, scope_list, validate_message

logger = logging.getLogger(__name__)


def _headers(message: dict) -> dict:
    return {item.get("name", "").lower(): item.get("value", "") for item in (message.get("payload") or {}).get("headers", [])}


def _body(message: dict) -> str:
    payload = message.get("payload") or {}; data = (payload.get("body") or {}).get("data")
    if not data:
        for part in payload.get("parts") or []:
            if part.get("mimeType") == "text/plain" and (part.get("body") or {}).get("data"): data = part["body"]["data"]; break
    if not data: return ""
    try: return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode(errors="replace")
    except Exception: return ""


def automated_type(headers: dict, sender: str, body: str) -> str:
    value = f"{sender} {body}".lower()
    if "mailer-daemon" in value or "delivery status notification" in value or "undeliverable" in value: return "delivery_failure"
    if headers.get("auto-submitted", "").lower() not in {"", "no"}: return "automatic_reply"
    if headers.get("precedence", "").lower() in {"bulk", "junk", "list"} or "no-reply" in sender.lower() or headers.get("list-unsubscribe"): return "automatic_reply"
    if "out of office" in value or "automatic reply" in value or "away from the office" in value: return "out_of_office"
    return "human_reply"


def deterministic_classification(body: str, message_type: str) -> dict | None:
    lower = body.lower(); links = re.findall(r"https?://[^\s<>]+", body)
    base = {"extracted": {"dates": re.findall(r"\b(?:mon|tue|wed|thu|fri|sat|sun)\w*\b[^.\n]{0,40}", body, re.I), "times": re.findall(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", body, re.I), "links": links, "requested_information": []}}
    if message_type != "human_reply": return {**base, "category": "automated_message", "confidence": .99, "safe_for_auto_reply": False, "requires_user_review": False, "provider": "rules"}
    rules = [
        ("rejection", ("unfortunately", "not moving forward", "other candidates"), False, .96),
        ("interview_invitation", ("interview", "schedule a call", "meeting link"), False, .95),
        ("assessment_invitation", ("assessment", "coding test", "take-home"), False, .95),
        ("request_for_resume", ("send your resume", "share your resume", "updated resume"), True, .95),
        ("request_for_portfolio", ("portfolio", "work samples"), True, .94),
        ("request_for_availability", ("availability", "available times", "when are you free"), False, .93),
        ("salary_question", ("salary", "compensation", "expected ctc"), False, .95),
        ("notice_period_question", ("notice period",), False, .97),
        ("technical_question", ("technical question", "explain how", "architecture"), False, .85),
        ("experience_question", ("years of experience", "experience with"), False, .92),
        ("location_question", ("current location", "willing to relocate", "where are you based"), False, .94),
        ("work_authorization_question", ("work authorization", "authorized to work", "visa sponsorship"), False, .96),
        ("request_for_more_information", ("additional information", "more information", "provide details"), False, .88),
        ("offer_related", ("offer letter", "pleased to offer"), False, .98),
    ]
    for category, terms, safe, confidence in rules:
        if any(term in lower for term in terms): return {**base, "category": category, "confidence": confidence, "safe_for_auto_reply": safe, "requires_user_review": not safe, "provider": "rules"}
    if any(term in lower for term in ("thank you", "received your", "we'll review", "will review")): return {**base, "category": "acknowledgement", "confidence": .94, "safe_for_auto_reply": True, "requires_user_review": False, "provider": "rules"}
    return None


def reply_text(body: str) -> str:
    lines = []
    for line in body.splitlines():
        if re.match(r"^On .+ wrote:$", line.strip(), re.I): break
        if line.lstrip().startswith(">"):
            continue
        lines.append(line)
    return "\n".join(lines).strip() or body.strip()


def normalize_classification(result: object, provider: str) -> dict | None:
    if not isinstance(result, dict): return None
    if isinstance(result.get("classification"), dict): result = result["classification"]
    category = result.get("category"); confidence = result.get("confidence")
    if not isinstance(category, str) or not category.strip() or isinstance(confidence, bool): return None
    try: confidence = float(confidence)
    except (TypeError, ValueError): return None
    if not 0 <= confidence <= 1: return None
    extracted = result.get("extracted") if isinstance(result.get("extracted"), dict) else {}
    for key in ("dates", "times", "links", "requested_information"):
        if not isinstance(extracted.get(key), list): extracted[key] = []
    return {"category": category.strip(), "confidence": confidence, "safe_for_auto_reply": bool(result.get("safe_for_auto_reply", False)), "requires_user_review": bool(result.get("requires_user_review", True)), "extracted": extracted, "provider": provider}


async def classify_reply(body: str, message_type: str) -> dict:
    result = deterministic_classification(body, message_type)
    if result: return result
    if settings.ai_mock_mode: return {"category": "follow_up", "confidence": .8, "safe_for_auto_reply": False, "requires_user_review": True, "extracted": {"dates": [], "times": [], "links": [], "requested_information": []}, "provider": "mock"}
    instruction = "Classify this recruiting reply. Return category, confidence, safe_for_auto_reply, requires_user_review, extracted with dates,times,links,requested_information. Never invent details."
    provider = GeminiProvider()
    result = normalize_classification(await provider._json(instruction, {"message": body}), "gemini")
    if result: return result
    result = normalize_classification(await provider._mistral_json(instruction, {"message": body}), "mistral")
    if result: return result
    return {"category": "follow_up", "confidence": .5, "safe_for_auto_reply": False, "requires_user_review": True, "extracted": {"dates": [], "times": [], "links": [], "requested_information": []}, "provider": "safe_fallback"}


def draft_for(analysis: dict) -> str:
    category = analysis["category"]
    if category == "acknowledgement": return "Thank you for the update. I appreciate your consideration and look forward to hearing from you."
    if category == "request_for_resume": return "Thank you for your reply. I’m happy to share my resume. Please let me know if you need any additional information."
    if category == "request_for_portfolio": return "Thank you for your reply. I’m happy to share my portfolio after confirming the most relevant work samples for this role."
    return "Thank you for your reply. I’d like to review the details and will respond shortly."


def normalize_draft_body(result: object) -> str:
    if not isinstance(result, dict): return ""
    value = result.get("body")
    if isinstance(value, str): return value.strip()
    if isinstance(value, dict):
        fields = [value.get(key) for key in ("greeting", "body", "closing", "signature")]
        return "\n\n".join(str(item).strip() for item in fields if isinstance(item, str) and item.strip())
    return ""


async def generate_reply_draft(db: Session, outreach: Outreach, message: GmailMessage, analysis: dict) -> str:
    if settings.ai_mock_mode or analysis.get("provider") == "safe_fallback" or analysis["category"] in {"acknowledgement", "request_for_resume", "request_for_portfolio"}:
        return draft_for(analysis)
    profile = db.get(CandidateProfile, outreach.user_id)
    application = db.query(Application).filter(Application.user_id == outreach.user_id, Application.opportunity_id == outreach.opportunity_id).first()
    job = db.get(Job, application.job_id) if application else None
    company = db.get(Company, application.company_id) if application else None
    thread = db.query(GmailMessage).filter(GmailMessage.outreach_id == outreach.id).order_by(GmailMessage.received_at.asc()).all()
    result = await GeminiProvider()._json(
        "Draft a concise professional recruiting email reply. Return {body}. Use only supplied facts. Never invent availability, salary, notice period, location, experience, skills, documents, or work authorization. If a requested fact is absent, include a clear [USER INPUT REQUIRED: ...] marker. Preserve a review-first tone.",
        {"candidate_profile": profile.data if profile else {}, "job": {"title": job.title} if job else {}, "company": {"name": company.name} if company else {}, "application_status": application.status if application else None, "thread": [{"direction": item.direction, "sender": item.sender, "body": item.body} for item in thread[-10:]], "latest_message": message.body, "classification": analysis},
    )
    body = normalize_draft_body(result)
    if not body:
        raise RuntimeError("AI_PROVIDER_UNAVAILABLE")
    return body


class GmailReplySyncService:
    def __init__(self, db: Session): self.db = db; self.gmail = GmailService()

    async def _token(self, connection):
        if connection.token_expiry and connection.token_expiry < datetime.utcnow():
            if not connection.refresh_token_encrypted: connection.status = "reauth_required"; self.db.commit(); raise RuntimeError("GOOGLE_REAUTH_REQUIRED")
            try:
                refreshed = await self.gmail.refresh(decrypt_token(connection.refresh_token_encrypted)); connection.access_token_encrypted = encrypt_token(refreshed["access_token"]); connection.token_expiry = datetime.utcnow() + timedelta(seconds=refreshed.get("expires_in", 3600)); connection.last_refreshed_at = datetime.utcnow(); self.db.commit()
            except Exception as exc: connection.status = "reauth_required"; self.db.commit(); raise RuntimeError("GOOGLE_REAUTH_REQUIRED") from exc
        return decrypt_token(connection.access_token_encrypted)

    async def sync_user(self, user_id: str) -> dict:
        result = {"threads_checked": 0, "new_messages": 0, "new_replies": 0, "applications_updated": 0, "drafts_created": 0, "auto_replies_sent": 0, "errors": []}
        connection = self.db.get(GoogleConnection, user_id)
        if not connection or connection.status != "active": raise RuntimeError("GOOGLE_NOT_CONNECTED")
        scopes = scope_list(connection.scopes)
        if not any(scope.endswith("gmail.readonly") or scope.endswith("gmail.modify") for scope in scopes):
            raise RuntimeError("GMAIL_READ_SCOPE_REQUIRED")
        self.db.query(GmailSyncLock).filter(GmailSyncLock.user_id == user_id, GmailSyncLock.locked_at < datetime.utcnow() - timedelta(minutes=15)).delete()
        self.db.commit()
        lock = GmailSyncLock(user_id=user_id); self.db.add(lock)
        try: self.db.commit()
        except IntegrityError: self.db.rollback(); raise RuntimeError("SYNC_ALREADY_RUNNING")
        try:
            token = await self._token(connection)
            for outreach in self.db.query(Outreach).filter(Outreach.user_id == user_id, Outreach.status.in_(["sent", "replied"]), Outreach.gmail_thread_id.isnot(None)).all():
                result["threads_checked"] += 1
                try:
                    thread = await self.gmail.get_thread(token, outreach.gmail_thread_id)
                    for raw in list(thread.get("messages", [])):
                        if self.db.get(GmailMessage, raw.get("id")): continue
                        result["new_messages"] += 1; headers = _headers(raw); sender = headers.get("from", ""); body = _body(raw)
                        sender_email = parseaddr(sender)[1].lower()
                        direction = "outgoing" if sender_email == connection.google_email.lower() or raw.get("id") == outreach.gmail_message_id else "incoming"
                        message_type = "outgoing" if direction == "outgoing" else automated_type(headers, sender, body)
                        internal_date = raw.get("internalDate")
                        received_at = datetime.utcfromtimestamp(int(internal_date) / 1000) if internal_date and str(internal_date).isdigit() else datetime.utcnow()
                        message = GmailMessage(gmail_message_id=raw["id"], outreach_id=outreach.id, gmail_thread_id=outreach.gmail_thread_id, gmail_history_id=raw.get("historyId"), internal_date=internal_date, sender=sender, recipients=[headers.get("to", "")], subject=headers.get("subject", ""), body=body, headers=headers, direction=direction, message_type=message_type, received_at=received_at, processed_at=datetime.utcnow()); self.db.add(message); self.db.flush()
                        if direction != "incoming": continue
                        result["new_replies"] += 1; classification = await classify_reply(reply_text(body), message_type)
                        classification.setdefault("extracted", {})["recruiter_name"] = parseaddr(sender)[0] or None
                        analysis = ReplyAnalysis(id=str(uuid.uuid4()), gmail_message_id=message.gmail_message_id, outreach_id=outreach.id, category=classification["category"], confidence=classification["confidence"], safe_for_auto_reply=classification["safe_for_auto_reply"], requires_user_review=classification["requires_user_review"], extracted_data=classification.get("extracted", {}), provider=classification["provider"]); self.db.add(analysis)
                        draft = ReplyDraft(id=str(uuid.uuid4()), source_incoming_message_id=message.gmail_message_id, outreach_id=outreach.id, body=await generate_reply_draft(self.db, outreach, message, classification)); self.db.add(draft); result["drafts_created"] += 1
                        outreach.status = "replied"; application = self.db.query(Application).filter(Application.user_id == user_id, Application.opportunity_id == outreach.opportunity_id).first()
                        if application:
                            self.db.add(ApplicationEvent(id=str(uuid.uuid4()), application_id=application.id, event_type="REPLY_RECEIVED", old_status=application.status, new_status=application.status, source="gmail_sync", data={"gmail_message_id": message.gmail_message_id}))
                            self.db.add(ApplicationEvent(id=str(uuid.uuid4()), application_id=application.id, event_type="REPLY_CLASSIFIED", old_status=application.status, new_status=application.status, source="gmail_sync", data={"gmail_message_id": message.gmail_message_id, "category": classification["category"]}))
                            self.db.add(ApplicationEvent(id=str(uuid.uuid4()), application_id=application.id, event_type="REPLY_DRAFTED", old_status=application.status, new_status=application.status, source="gmail_sync", data={"gmail_message_id": message.gmail_message_id, "reply_id": draft.id}))
                            mapping = {"rejection":"rejected", "interview_invitation":"interview", "assessment_invitation":"assessment", "offer_related":"offer_pending"}
                            new_status = mapping.get(classification["category"], "replied") if classification["confidence"] >= .8 else "replied"
                            if application.status != new_status:
                                old = application.status; application.status = new_status; application.last_activity_at = datetime.utcnow(); self.db.add(ApplicationEvent(id=str(uuid.uuid4()), application_id=application.id, event_type="APPLICATION_STATUS_CHANGED", old_status=old, new_status=new_status, source="gmail_sync", data={"gmail_message_id": message.gmail_message_id, "category": classification["category"]})); result["applications_updated"] += 1
                                event_type = {"rejection":"REJECTION_DETECTED", "interview_invitation":"INTERVIEW_INVITATION_DETECTED", "assessment_invitation":"ASSESSMENT_DETECTED"}.get(classification["category"])
                                if event_type: self.db.add(ApplicationEvent(id=str(uuid.uuid4()), application_id=application.id, event_type=event_type, old_status=old, new_status=new_status, source="gmail_sync", data={"gmail_message_id": message.gmail_message_id}))
                        user_settings = self.db.get(OutreachSettings, user_id); policy = user_settings.data if user_settings else {}
                        allowed = policy.get("allowed_categories") or policy.get("auto_reply_categories") or []
                        threshold = float(policy.get("minimum_classification_confidence", .92)); enabled = bool(policy.get("auto_reply_enabled", False)) and settings.auto_reply_enabled
                        daily_limit = min(int(policy.get("maximum_auto_replies_per_day", 5)), settings.max_auto_replies_per_day)
                        today_count = self.db.query(ReplyDraft).join(Outreach, ReplyDraft.outreach_id == Outreach.id).filter(Outreach.user_id == user_id, ReplyDraft.status == "auto_sent", ReplyDraft.sent_at >= datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)).count()
                        if enabled and message_type == "human_reply" and classification["safe_for_auto_reply"] and classification["category"] in allowed and classification["confidence"] >= threshold and today_count < daily_limit:
                            contact = self.db.get(Contact, outreach.contact_id)
                            validate_message(contact.email, "Re: Chably outreach", draft.body)
                            sent = await self.gmail.reply_to_thread(token, outreach.gmail_thread_id, contact.email, draft.body); draft.status = "auto_sent"; draft.gmail_message_id = sent.get("id"); draft.sent_at = datetime.utcnow(); result["auto_replies_sent"] += 1
                            if application: self.db.add(ApplicationEvent(id=str(uuid.uuid4()), application_id=application.id, event_type="AUTO_REPLY_SENT", old_status=application.status, new_status=application.status, source="gmail_sync", data={"gmail_message_id": message.gmail_message_id, "reply_id": draft.id}))
                    outreach.last_reply_checked_at = datetime.utcnow()
                    self.db.commit()
                except Exception as exc:
                    self.db.rollback()
                    logger.exception("Gmail thread synchronization failed", extra={"outreach_id": outreach.id})
                    result["errors"].append({"outreach_id": outreach.id, "code": type(exc).__name__})
            return result
        finally:
            self.db.query(GmailSyncLock).filter(GmailSyncLock.user_id == user_id).delete(); self.db.commit()
