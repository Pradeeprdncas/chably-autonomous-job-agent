from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Optional

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import AuthSession, User

password_hasher = PasswordHasher()
bearer = HTTPBearer(auto_error=False)
_development_secret = secrets.token_urlsafe(48)


def _secret() -> str:
    return settings.jwt_secret_key or _development_secret


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def _encode(user_id: str, token_type: str, expires: timedelta, session_id: Optional[str] = None) -> str:
    now = datetime.utcnow()
    payload = {"sub": user_id, "typ": token_type, "iat": now, "exp": now + expires, "jti": str(uuid.uuid4())}
    if session_id:
        payload["sid"] = session_id
    return jwt.encode(payload, _secret(), algorithm=settings.jwt_algorithm)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_tokens(db: Session, user: User, device_info: str | None = None) -> dict:
    session = AuthSession(id=str(uuid.uuid4()), user_id=user.id, refresh_token_hash="pending", device_info=(device_info or "")[:512], expires_at=datetime.utcnow() + timedelta(days=settings.refresh_token_expire_days))
    refresh = _encode(user.id, "refresh", timedelta(days=settings.refresh_token_expire_days), session.id)
    session.refresh_token_hash = token_hash(refresh)
    db.add(session); db.commit()
    return {"access_token": _encode(user.id, "access", timedelta(minutes=settings.access_token_expire_minutes)), "refresh_token": refresh, "token_type": "bearer", "expires_in": settings.access_token_expire_minutes * 60}


def decode_token(token: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(token, _secret(), algorithms=[settings.jwt_algorithm])
        if payload.get("typ") != expected_type or not payload.get("sub"):
            raise ValueError
        return payload
    except (jwt.PyJWTError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired authentication token") from exc


def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer), db: Session = Depends(get_db)) -> User:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Authentication required", headers={"WWW-Authenticate": "Bearer"})
    payload = decode_token(credentials.credentials, "access")
    user = db.get(User, payload["sub"])
    if not user or user.status != "active":
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def enforce_user(requested_user_id: str, current_user: User) -> str:
    if requested_user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Resource not found")
    return current_user.id
