from __future__ import annotations

import time
import uuid
import hashlib
import secrets
from collections import defaultdict, deque
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from ..auth import decode_token, get_current_user, hash_password, issue_tokens, token_hash, verify_password
from ..config import settings
from ..database import get_db
from ..models import AuthSession, GoogleLoginExchange, GoogleLoginState, User
from .utils import fail, ok

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
_failed_logins: dict[str, deque] = defaultdict(deque)


class RegisterBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    display_name: str = Field(default="", max_length=256)


class LoginBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshBody(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=4096)


class GoogleExchangeBody(BaseModel):
    code: str = Field(min_length=20, max_length=512)


def user_payload(user: User) -> dict:
    return {"id": user.id, "email": user.email, "display_name": user.display_name, "status": user.status, "created_at": user.created_at.isoformat()}


def _google_login_url(state: str) -> str:
    if not all([settings.google_client_id, settings.google_client_secret, settings.google_login_redirect_uri]):
        raise RuntimeError("GOOGLE_LOGIN_NOT_CONFIGURED")
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({"client_id": settings.google_client_id, "redirect_uri": settings.google_login_redirect_uri, "response_type": "code", "scope": settings.google_login_oauth_scopes, "state": state, "prompt": "select_account"})


async def _google_identity(code: str) -> dict:
    async with httpx.AsyncClient(timeout=settings.ai_timeout_seconds) as client:
        response = await client.post("https://oauth2.googleapis.com/token", data={"code": code, "client_id": settings.google_client_id, "client_secret": settings.google_client_secret, "redirect_uri": settings.google_login_redirect_uri, "grant_type": "authorization_code"})
        response.raise_for_status()
        identity = await client.get("https://openidconnect.googleapis.com/v1/userinfo", headers={"Authorization": f"Bearer {response.json()['access_token']}"})
        identity.raise_for_status()
        return identity.json()


@router.post("/register", status_code=201)
def register(body: RegisterBody, request: Request, db: Session = Depends(get_db)):
    email = body.email.lower().strip()
    if db.query(User).filter(User.email == email).first():
        fail(409, "EMAIL_ALREADY_REGISTERED", "An account with this email already exists.", "email")
    user = User(id=str(uuid.uuid4()), email=email, password_hash=hash_password(body.password), display_name=body.display_name.strip())
    db.add(user); db.commit()
    return ok("Account registered", {"user": user_payload(user), **issue_tokens(db, user, request.headers.get("user-agent"))})


@router.post("/login")
def login(body: LoginBody, request: Request, db: Session = Depends(get_db)):
    key = f"{request.client.host if request.client else 'unknown'}:{body.email.lower()}"
    now = time.monotonic(); attempts = _failed_logins[key]
    while attempts and attempts[0] < now - 300: attempts.popleft()
    if len(attempts) >= 5:
        fail(429, "LOGIN_TEMPORARILY_THROTTLED", "Too many failed login attempts. Try again later.")
    user = db.query(User).filter(User.email == body.email.lower().strip()).first()
    if not user or not verify_password(user.password_hash, body.password) or user.status != "active":
        attempts.append(now); fail(401, "INVALID_CREDENTIALS", "Email or password is incorrect.")
    attempts.clear(); user.last_login_at = datetime.utcnow(); db.commit()
    return ok("Login successful", {"user": user_payload(user), **issue_tokens(db, user, request.headers.get("user-agent"))})


@router.get("/google/login")
def google_login(db: Session = Depends(get_db)):
    state = secrets.token_urlsafe(32)
    try:
        authorization_url = _google_login_url(state)
    except RuntimeError:
        fail(503, "GOOGLE_LOGIN_NOT_CONFIGURED", "Google sign-in is not configured.")
    db.add(GoogleLoginState(state_hash=hashlib.sha256(state.encode()).hexdigest(), expires_at=datetime.utcnow() + timedelta(minutes=10)))
    db.commit()
    return ok("Google sign-in URL created", {"authorization_url": authorization_url})


@router.get("/google/callback")
async def google_login_callback(code: str, state: str, db: Session = Depends(get_db)):
    state_row = db.get(GoogleLoginState, hashlib.sha256(state.encode()).hexdigest())
    if not state_row or state_row.used_at or state_row.expires_at <= datetime.utcnow():
        fail(400, "INVALID_OAUTH_STATE", "Google sign-in state is invalid or expired.")
    state_row.used_at = datetime.utcnow()
    try:
        identity = await _google_identity(code)
    except Exception:
        db.commit()
        fail(502, "GOOGLE_LOGIN_FAILED", "Google sign-in could not be completed.")
    email = (identity.get("email") or "").lower().strip()
    if not email or not identity.get("email_verified", False):
        db.commit()
        fail(401, "GOOGLE_EMAIL_NOT_VERIFIED", "A verified Google email address is required.")
    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(id=str(uuid.uuid4()), email=email, password_hash=hash_password(secrets.token_urlsafe(48)), display_name=(identity.get("name") or "").strip())
        db.add(user)
    if user.status != "active":
        db.commit()
        fail(403, "ACCOUNT_INACTIVE", "This Chably account is not active.")
    user.last_login_at = datetime.utcnow()
    exchange_code = secrets.token_urlsafe(32)
    db.add(GoogleLoginExchange(code_hash=hashlib.sha256(exchange_code.encode()).hexdigest(), user_id=user.id, expires_at=datetime.utcnow() + timedelta(minutes=2)))
    db.commit()
    return RedirectResponse(f"{settings.frontend_url.rstrip('/')}/#/oauth/callback?code={exchange_code}", status_code=303)


@router.post("/google/exchange")
def google_login_exchange(body: GoogleExchangeBody, request: Request, db: Session = Depends(get_db)):
    row = db.get(GoogleLoginExchange, hashlib.sha256(body.code.encode()).hexdigest())
    if not row or row.used_at or row.expires_at <= datetime.utcnow():
        fail(400, "INVALID_GOOGLE_LOGIN_CODE", "Google sign-in code is invalid or expired.")
    user = db.get(User, row.user_id)
    if not user or user.status != "active":
        fail(401, "ACCOUNT_INACTIVE", "This Chably account is not active.")
    row.used_at = datetime.utcnow()
    db.commit()
    return ok("Google sign-in successful", {"user": user_payload(user), **issue_tokens(db, user, request.headers.get("user-agent"))})


@router.post("/refresh")
def refresh(body: RefreshBody, request: Request, db: Session = Depends(get_db)):
    payload = decode_token(body.refresh_token, "refresh")
    session = db.get(AuthSession, payload.get("sid"))
    if not session or session.user_id != payload["sub"] or session.revoked_at or session.expires_at <= datetime.utcnow() or session.refresh_token_hash != token_hash(body.refresh_token):
        fail(401, "REFRESH_TOKEN_REVOKED", "Refresh session is invalid or revoked.")
    session.revoked_at = datetime.utcnow(); user = db.get(User, session.user_id); db.commit()
    return ok("Tokens refreshed", issue_tokens(db, user, request.headers.get("user-agent")))


@router.post("/logout")
def logout(body: RefreshBody, db: Session = Depends(get_db)):
    payload = decode_token(body.refresh_token, "refresh")
    session = db.get(AuthSession, payload.get("sid"))
    if not session or session.refresh_token_hash != token_hash(body.refresh_token):
        fail(401, "REFRESH_TOKEN_INVALID", "Refresh session is invalid.")
    if not session.revoked_at: session.revoked_at = datetime.utcnow(); db.commit()
    return ok("Logged out", {"revoked": True})


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return ok("Authenticated user loaded", {"user": user_payload(user)})
