import os
import uuid
import unittest
from urllib.parse import parse_qs, urlparse

os.environ["AI_MOCK_MODE"] = "true"
os.environ["SEARCH_MOCK_MODE"] = "true"
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_phase1.db")

from fastapi.testclient import TestClient
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Company
from app.services.google_gmail import GmailService

settings.ai_mock_mode = True; settings.search_mock_mode = True; settings.google_oauth_mock_mode = True
settings.google_redirect_uri = "http://testserver/api/v1/integrations/google/callback"; settings.rate_limit_sensitive = 100


def text_pdf(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>", b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
    output = bytearray(b"%PDF-1.4\n"); offsets = [0]
    for index, obj in enumerate(objects, 1): offsets.append(len(output)); output.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(output); output.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]: output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer << /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()); return bytes(output)


class FullProductE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls): Base.metadata.create_all(bind=engine); cls.client = TestClient(app); GmailService.mock_threads.clear()

    def test_authenticated_resume_to_reply_export_logout(self):
        email = f"e2e-{uuid.uuid4().hex}@chably.ai"; password = "SecurePassword123!"
        registered = self.client.post("/api/v1/auth/register", json={"email": email, "password": password, "display_name": "E2E User"})
        self.assertEqual(registered.status_code, 201)
        login = self.client.post("/api/v1/auth/login", json={"email": email, "password": password}).json()["data"]
        user_id = login["user"]["id"]; headers = {"Authorization": f"Bearer {login['access_token']}"}
        upload = self.client.post("/api/v1/resumes/upload", headers=headers, files={"file": ("resume.pdf", text_pdf("E2E User Python FastAPI engineer e2e@example.com"), "application/pdf")})
        self.assertEqual(upload.status_code, 200, upload.text)
        self.assertEqual(self.client.get(f"/api/v1/profile/{user_id}", headers=headers).status_code, 200)
        self.assertEqual(self.client.post(f"/api/v1/interview/{user_id}/start", headers=headers).status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/roles/{user_id}/recommendations", headers=headers).status_code, 200)
        search = self.client.post("/api/v1/job-search", headers=headers, json={"user_id": user_id, "query": "Find backend jobs in Bangalore"}).json()["data"]
        item = search["results"][0]
        self.assertEqual(self.client.post(f"/api/v1/jobs/{item['job']['id']}/save", headers=headers, json={"user_id": user_id, "status": "saved"}).status_code, 200)
        with SessionLocal() as db:
            company = db.get(Company, item["company"]["id"]); company.description += " Careers recruiting@example.com"; db.commit()
        contacts = self.client.get(f"/api/v1/opportunities/{item['opportunity_id']}/contacts", headers=headers, params={"user_id": user_id}).json()["data"]["contacts"]
        draft = self.client.post(f"/api/v1/opportunities/{item['opportunity_id']}/draft-email", headers=headers, params={"user_id": user_id, "contact_id": contacts[0]["id"]}).json()["data"]["outreach"]
        auth_url = self.client.post("/api/v1/integrations/google/connect", headers=headers).json()["data"]["authorization_url"]
        oauth = parse_qs(urlparse(auth_url).query)
        self.assertEqual(self.client.get("/api/v1/integrations/google/callback", params={"code": oauth["code"][0], "state": oauth["state"][0]}).status_code, 200)
        self.assertEqual(self.client.get("/api/v1/integrations/google/callback", params={"code": oauth["code"][0], "state": oauth["state"][0]}).status_code, 400)
        self.assertEqual(self.client.post(f"/api/v1/outreach/{draft['id']}/approve", headers=headers, params={"user_id": user_id}).status_code, 200)
        sent = self.client.post(f"/api/v1/outreach/{draft['id']}/send", headers=headers, params={"user_id": user_id}).json()["data"]
        self.client.post(f"/api/v1/opportunities/{item['opportunity_id']}/application", headers=headers, params={"user_id": user_id}, json={"status": "contacted"})
        GmailService.inject_mock_reply(sent["gmail_thread_id"], "Recruiter <recruiting@example.com>", "We would like to schedule an interview Tuesday at 3 PM.")
        synced = self.client.post(f"/api/v1/integrations/google/{user_id}/sync", headers=headers).json()["data"]
        self.assertEqual(synced["new_replies"], 1)
        exported = self.client.get("/api/v1/account/export", headers=headers)
        self.assertEqual(exported.status_code, 200); self.assertNotIn("token", str(exported.json()).lower())
        self.assertEqual(self.client.post("/api/v1/auth/logout", json={"refresh_token": login["refresh_token"]}).status_code, 200)
        self.assertEqual(self.client.post("/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]}).status_code, 401)


if __name__ == "__main__": unittest.main()
