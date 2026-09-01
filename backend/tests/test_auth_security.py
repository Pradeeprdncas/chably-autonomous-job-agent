import os
import uuid
import unittest
from datetime import datetime, timedelta

os.environ["AI_MOCK_MODE"] = "true"
os.environ["SEARCH_MOCK_MODE"] = "true"
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_phase1.db")

from fastapi.testclient import TestClient

from app.auth import _encode
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import (Application, CandidateProfile, Company, Contact,
                        GoogleConnection, InterviewTurn, Job, JobSearchSession,
                        Opportunity, Outreach, OutreachSettings, Resume, User)
from app.services.google_gmail import encrypt_token
from app.services.embedding_service import EmbeddingService

settings.ai_mock_mode = True
settings.search_mock_mode = True
settings.google_oauth_mock_mode = True
settings.rate_limit_sensitive = 100


class AuthenticationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine); cls.client = TestClient(app)
        cls.email = f"auth-{uuid.uuid4().hex}@chably.ai"; cls.password = "SecurePassword123!"

    def test_authentication_session_lifecycle(self):
        registered = self.client.post("/api/v1/auth/register", json={"email": self.email, "password": self.password, "display_name": "Auth User"})
        self.assertEqual(registered.status_code, 201, registered.text)
        with SessionLocal() as db:
            stored = db.query(User).filter(User.email == self.email).one(); self.assertNotEqual(stored.password_hash, self.password); self.assertTrue(stored.password_hash.startswith("$argon2id$"))
        self.assertEqual(self.client.post("/api/v1/auth/register", json={"email": self.email, "password": self.password}).status_code, 409)
        self.assertEqual(self.client.post("/api/v1/auth/login", json={"email": self.email, "password": "wrong"}).status_code, 401)
        blocked = f"blocked-{uuid.uuid4().hex}@chably.ai"
        codes = [self.client.post("/api/v1/auth/login", json={"email": blocked, "password": "wrong"}).status_code for _ in range(6)]
        self.assertEqual(codes[-1], 429)
        login = self.client.post("/api/v1/auth/login", json={"email": self.email, "password": self.password}).json()["data"]
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        self.assertEqual(self.client.get("/api/v1/auth/me", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {_encode('missing', 'access', timedelta(seconds=-1))}"}).status_code, 401)
        refreshed = self.client.post("/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]})
        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual(self.client.post("/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]}).status_code, 401)
        refresh_two = refreshed.json()["data"]["refresh_token"]
        self.assertEqual(self.client.post("/api/v1/auth/logout", json={"refresh_token": refresh_two}).status_code, 200)
        self.assertEqual(self.client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_two}).status_code, 401)

    def test_account_export_and_deletion_removes_vectors(self):
        email = f"delete-{uuid.uuid4().hex}@chably.ai"
        data = self.client.post("/api/v1/auth/register", json={"email": email, "password": self.password}).json()["data"]
        user_id = data["user"]["id"]; headers = {"Authorization": f"Bearer {data['access_token']}"}
        with SessionLocal() as db:
            resume_id = str(uuid.uuid4()); resume = Resume(id=resume_id, user_id=user_id, original_filename="delete.pdf", extracted_text="delete")
            db.add(resume); db.flush(); db.add(CandidateProfile(user_id=user_id, resume_id=resume.id, data={"professional_summary": "Delete me"})); db.commit()
        service = EmbeddingService(); service.upsert_profile(user_id, resume_id, {"professional_summary": "Delete me"})
        exported = self.client.get("/api/v1/account/export", headers=headers)
        self.assertEqual(exported.status_code, 200); self.assertNotIn("password_hash", str(exported.json()))
        self.assertEqual(self.client.delete("/api/v1/account", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/api/v1/auth/me", headers=headers).status_code, 401)
        if service.available: self.assertEqual(service.candidate.get(where={"user_id": user_id}).get("ids", []), [])


class AuthorizationIsolationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine); cls.client = TestClient(app)
        cls.users = []
        for label in ("a", "b"):
            email = f"isolation-{label}-{uuid.uuid4().hex}@chably.ai"
            data = cls.client.post("/api/v1/auth/register", json={"email": email, "password": "SecurePassword123!", "display_name": label}).json()["data"]
            cls.users.append(data)
        cls.a, cls.b = cls.users
        cls.headers = {"Authorization": f"Bearer {cls.a['access_token']}"}
        suffix = uuid.uuid4().hex
        with SessionLocal() as db:
            resume = Resume(id=str(uuid.uuid4()), user_id=cls.b["user"]["id"], original_filename="private.pdf", extracted_text="Private resume")
            db.add(resume); db.flush(); db.add(CandidateProfile(user_id=cls.b["user"]["id"], resume_id=resume.id, data={"skills": {}}))
            db.add(InterviewTurn(id=str(uuid.uuid4()), user_id=cls.b["user"]["id"], question="Private?", target_category="private", target_fields=[], reason="private", score_before=0))
            company = Company(id=str(uuid.uuid4()), name="Private Co", website=f"https://{suffix}.example.com", domain=f"{suffix}.example.com")
            db.add(company); db.flush(); job = Job(id=str(uuid.uuid4()), company_id=company.id, raw_title="Engineer", title="Engineer", job_url=f"https://{suffix}.example.com/job", source_url=f"https://{suffix}.example.com/job")
            db.add(job); db.flush(); search = JobSearchSession(id=str(uuid.uuid4()), user_id=cls.b["user"]["id"], raw_query="private", status="completed")
            db.add(search); db.flush(); opportunity = Opportunity(id=str(uuid.uuid4()), user_id=cls.b["user"]["id"], search_session_id=search.id, company_id=company.id, job_id=job.id)
            db.add(opportunity); db.flush(); contact = Contact(id=str(uuid.uuid4()), company_id=company.id, email="recruiter@example.com", source_url=company.website, source_type="public")
            db.add(contact); db.flush(); outreach = Outreach(id=str(uuid.uuid4()), user_id=cls.b["user"]["id"], opportunity_id=opportunity.id, contact_id=contact.id, subject="Private", body="Private", status="sent", gmail_thread_id="private-thread")
            db.add(outreach); db.flush(); application = Application(id=str(uuid.uuid4()), user_id=cls.b["user"]["id"], opportunity_id=opportunity.id, job_id=job.id, company_id=company.id, outreach_id=outreach.id)
            db.add(application); db.add(OutreachSettings(user_id=cls.b["user"]["id"], data={})); db.add(GoogleConnection(user_id=cls.b["user"]["id"], id=str(uuid.uuid4()), google_email="private@gmail.example", access_token_encrypted=encrypt_token("private-token"), scopes=[]))
            db.commit(); cls.ids = {"resume": resume.id, "search": search.id, "job": job.id, "opportunity": opportunity.id, "outreach": outreach.id, "application": application.id}

    def test_cross_user_resources_are_hidden(self):
        b = self.b["user"]["id"]; h = self.headers; ids = self.ids
        requests = [
            self.client.get(f"/api/v1/profile/{b}", headers=h),
            self.client.patch(f"/api/v1/profile/{b}", headers=h, json={"data": {}}),
            self.client.post(f"/api/v1/resumes/{b}/analysis", headers=h, json={"user_id": b}),
            self.client.post(f"/api/v1/interview/{b}/start", headers=h),
            self.client.get(f"/api/v1/interview/{b}/history", headers=h),
            self.client.get(f"/api/v1/job-search/{ids['search']}", headers=h),
            self.client.post(f"/api/v1/jobs/{ids['job']}/save", headers=h, json={"user_id": b, "status": "saved"}),
            self.client.get(f"/api/v1/opportunities/{ids['opportunity']}", headers=h),
            self.client.get(f"/api/v1/outreach/{ids['outreach']}", headers=h, params={"user_id": b}),
            self.client.post(f"/api/v1/outreach/{ids['outreach']}/send", headers=h, params={"user_id": b}),
            self.client.get(f"/api/v1/outreach/{ids['outreach']}/thread", headers=h, params={"user_id": b}),
            self.client.get(f"/api/v1/applications/{ids['application']}", headers=h, params={"user_id": b}),
            self.client.patch(f"/api/v1/users/{b}/outreach-settings", headers=h, json={}),
            self.client.get(f"/api/v1/integrations/google/status/{b}", headers=h),
        ]
        self.assertTrue(all(response.status_code == 404 for response in requests), [(response.status_code, response.text) for response in requests])
        self.assertEqual(self.client.get(f"/api/v1/profile/{b}").status_code, 401)


if __name__ == "__main__": unittest.main()
