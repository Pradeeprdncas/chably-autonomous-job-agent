from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from ..auth import decode_token, get_current_user, hash_password, issue_tokens, token_hash, verify_password
from ..database import get_db
from ..models import AuthSession, User
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


def user_payload(user: User) -> dict:
    return {"id": user.id, "email": user.email, "display_name": user.display_name, "status": user.status, "created_at": user.created_at.isoformat()}


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
