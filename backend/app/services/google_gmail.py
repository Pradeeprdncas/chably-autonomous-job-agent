from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
import re
from datetime import datetime, timedelta
from email.message import EmailMessage
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet

from ..config import settings

EMAIL_ADDRESS = re.compile(r"^[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+$", re.I)


def validate_message(to: str, subject: str, body: str) -> None:
    if not EMAIL_ADDRESS.fullmatch((to or "").strip()):
        raise ValueError("INVALID_EMAIL_RECIPIENT")
    if not subject or len(subject) > 500 or "\r" in subject or "\n" in subject:
        raise ValueError("INVALID_EMAIL_SUBJECT")
    if not body or len(body) > 100_000:
        raise ValueError("INVALID_EMAIL_BODY")


def scope_list(value: str | list[str]) -> list[str]:
    if isinstance(value, list):
        parts = value
    else:
        parts = re.split(r"[\s,]+", value or "")
    return [part.strip() for part in parts if part.strip()]


def public_scopes(value: str | list[str]) -> list[str]:
    return [scope.rsplit("/", 1)[-1] for scope in scope_list(value)]


def _fernet() -> Fernet:
    key = settings.token_encryption_key
    if not key:
        if settings.google_oauth_mock_mode:
            key = base64.urlsafe_b64encode(hashlib.sha256(b"chably-test-only-key").digest()).decode()
        else:
            raise RuntimeError("TOKEN_ENCRYPTION_KEY_MISSING")
    return Fernet(key.encode())


def encrypt_token(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_token(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()


class GmailService:
    mock_threads = {}
    async def exchange_code(self, code: str) -> dict:
        if settings.google_oauth_mock_mode:
            return {"access_token": "mock-access-token", "refresh_token": "mock-refresh-token", "expires_in": 3600, "scope": settings.google_oauth_scopes, "email": "mock-user@gmail.test", "sub": "mock-account"}
        async with httpx.AsyncClient(timeout=settings.ai_timeout_seconds) as client:
            response = await client.post("https://oauth2.googleapis.com/token", data={"code": code, "client_id": settings.google_client_id, "client_secret": settings.google_client_secret, "redirect_uri": settings.google_redirect_uri, "grant_type": "authorization_code"})
            response.raise_for_status(); token = response.json()
            identity = await self.identity(token["access_token"])
            return {**token, **identity}

    async def identity(self, access_token: str) -> dict:
        if settings.google_oauth_mock_mode: return {"email": "mock-user@gmail.test", "sub": "mock-account"}
        async with httpx.AsyncClient(timeout=settings.ai_timeout_seconds) as client:
            response = await client.get("https://gmail.googleapis.com/gmail/v1/users/me/profile", headers={"Authorization": f"Bearer {access_token}"}); response.raise_for_status()
            data = response.json(); return {"email": data["emailAddress"], "sub": None}

    async def refresh(self, refresh_token: str) -> dict:
        if settings.google_oauth_mock_mode:
            if refresh_token == "invalid-refresh-token":
                raise RuntimeError("INVALID_REFRESH_TOKEN")
            return {"access_token": "mock-refreshed-token", "expires_in": 3600}
        async with httpx.AsyncClient(timeout=settings.ai_timeout_seconds) as client:
            response = await client.post("https://oauth2.googleapis.com/token", data={"refresh_token": refresh_token, "client_id": settings.google_client_id, "client_secret": settings.google_client_secret, "grant_type": "refresh_token"}); response.raise_for_status(); return response.json()

    async def send_email(self, access_token: str, to: str, subject: str, body: str, thread_id: str | None = None, in_reply_to: str | None = None) -> dict:
        validate_message(to, subject, body)
        if settings.google_oauth_mock_mode:
            identifier = str(uuid.uuid4()); thread = thread_id or f"mock-thread-{identifier}"
            self.mock_threads.setdefault(thread, {"id": thread, "historyId": "1", "messages": []})["messages"].append({"id": identifier, "threadId": thread, "historyId": "1", "internalDate": str(int(time.time() * 1000)), "payload": {"headers": [{"name":"From","value":"mock-user@gmail.test"},{"name":"To","value":to},{"name":"Subject","value":subject}], "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()}}})
            return {"id": identifier, "threadId": thread}
        message = EmailMessage(); message["To"] = to; message["Subject"] = subject
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to; message["References"] = in_reply_to
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        payload = {"raw": raw};
        if thread_id: payload["threadId"] = thread_id
        async with httpx.AsyncClient(timeout=settings.ai_timeout_seconds) as client:
            response = await client.post("https://gmail.googleapis.com/gmail/v1/users/me/messages/send", headers={"Authorization": f"Bearer {access_token}"}, json=payload); response.raise_for_status(); return response.json()

    async def create_draft(self, *args, **kwargs):
        return await self.send_email(*args, **kwargs)

    async def get_thread(self, access_token: str, thread_id: str) -> dict:
        if settings.google_oauth_mock_mode: return self.mock_threads.get(thread_id, {"id": thread_id, "messages": []})
        async with httpx.AsyncClient(timeout=settings.ai_timeout_seconds) as client:
            response = await client.get(f"https://gmail.googleapis.com/gmail/v1/users/me/threads/{thread_id}", headers={"Authorization": f"Bearer {access_token}"}); response.raise_for_status(); return response.json()

    async def list_recent_replies(self, access_token: str, thread_id: str) -> list[dict]:
        return (await self.get_thread(access_token, thread_id)).get("messages", [])

    async def reply_to_thread(self, access_token: str, thread_id: str, to: str, body: str) -> dict:
        thread = await self.get_thread(access_token, thread_id)
        latest = (thread.get("messages") or [])[-1:] or [{}]
        headers = {item.get("name", "").lower(): item.get("value", "") for item in (latest[0].get("payload") or {}).get("headers", [])}
        return await self.send_email(access_token, to, "Re: Chably outreach", body, thread_id, headers.get("message-id"))

    @classmethod
    def inject_mock_reply(cls, thread_id: str, sender: str, body: str, subject: str = "Re: Chably outreach", headers: dict | None = None) -> str:
        identifier = str(uuid.uuid4()); values = {"From": sender, "To": "mock-user@gmail.test", "Subject": subject, **(headers or {})}
        cls.mock_threads.setdefault(thread_id, {"id": thread_id, "historyId": "2", "messages": []})["messages"].append({"id": identifier, "threadId": thread_id, "historyId": "2", "internalDate": str(int(time.time() * 1000)), "payload": {"headers": [{"name": key, "value": value} for key, value in values.items()], "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()}}})
        return identifier


def authorization_url(state: str) -> str:
    if settings.google_oauth_mock_mode: return f"{settings.google_redirect_uri or 'http://localhost/mock-callback'}?code=mock-code&state={state}"
    if not all([settings.google_client_id, settings.google_redirect_uri, settings.google_client_secret, settings.token_encryption_key]): raise RuntimeError("GOOGLE_OAUTH_NOT_CONFIGURED")
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({"client_id": settings.google_client_id, "redirect_uri": settings.google_redirect_uri, "response_type": "code", "access_type": "offline", "prompt": "consent", "scope": " ".join(scope_list(settings.google_oauth_scopes)), "state": state})
