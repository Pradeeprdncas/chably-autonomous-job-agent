"""Safely normalize local Google OAuth settings without printing secrets."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet


CANONICAL = {
    "GOOGLE_REDIRECT_URI": "http://localhost:8000/api/v1/integrations/google/callback",
    "GOOGLE_OAUTH_SCOPES": "https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.readonly",
    "GOOGLE_OAUTH_MOCK_MODE": "false",
}


def main() -> dict:
    path = Path(__file__).resolve().parents[2] / ".env"
    lines = path.read_text().splitlines() if path.exists() else []
    values, order = {}, []
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        name, value = line.split("=", 1); name = name.strip()
        if name not in values: order.append(name)
        values[name] = value.strip()
    values["GOOGLE_CLIENT_ID"] = values.get("GOOGLE_CLIENT_ID") or values.get("GMAIL_CLIENT_ID") or "307066150253-hdef3lm3dg5s9dch4527tkmv1nb0a20a.apps.googleusercontent.com"
    values["GOOGLE_CLIENT_SECRET"] = values.get("GOOGLE_CLIENT_SECRET") or values.get("GMAIL_CLIENT_SECRET") or ""
    if not values["GOOGLE_CLIENT_SECRET"]:
        raise RuntimeError("GOOGLE_CLIENT_SECRET_NOT_FOUND")
    values.pop("GMAIL_CLIENT_ID", None); values.pop("GMAIL_CLIENT_SECRET", None)
    for name, value in CANONICAL.items(): values[name] = value
    generated = not bool(values.get("TOKEN_ENCRYPTION_KEY"))
    if generated:
        values["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    names = [name for name in order if name in values]
    names.extend(name for name in values if name not in names)
    content = "\n".join(f"{name}={values[name]}" for name in names) + "\n"
    descriptor, temp_name = tempfile.mkstemp(prefix=".env.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle: handle.write(content)
        os.chmod(temp_name, 0o600); os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name): os.unlink(temp_name)
    result = {"configured": True, "legacy_names_removed": True, "fernet_key_generated": generated, "redirect_uri": CANONICAL["GOOGLE_REDIRECT_URI"], "mock_mode": False}
    print(result); return result


if __name__ == "__main__": main()
