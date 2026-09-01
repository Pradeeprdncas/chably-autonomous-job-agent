import asyncio
import os
import unittest
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

os.environ["AI_MOCK_MODE"] = "true"
os.environ["SEARCH_MOCK_MODE"] = "true"
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_phase1.db")

from fastapi.testclient import TestClient
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import (Application, ApplicationEvent, Company, GmailMessage,
                        GmailSyncLock, GoogleConnection, Outreach,
                        OutreachSettings, ReplyAnalysis, ReplyDraft)
from app.scripts.seed_demo import main as seed_demo
from app.scripts.sync_gmail_replies import main as sync_command
from app.services.google_gmail import (GmailService, decrypt_token,
                                       encrypt_token)

settings.ai_mock_mode = True
settings.search_mock_mode = True
settings.google_oauth_mock_mode = True
settings.google_redirect_uri = "http://testserver/api/v1/integrations/google/callback"
settings.auto_reply_enabled = False
settings.rate_limit_sensitive = 100


class OutreachMockTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        seed_demo()
        GmailService.mock_threads.clear()
        with SessionLocal() as db:
            for model in (ApplicationEvent, ReplyDraft, ReplyAnalysis, GmailMessage,
                          Application, OutreachSettings, Outreach, GoogleConnection,
                          GmailSyncLock):
                db.query(model).delete()
            db.commit()
        cls.client = TestClient(app)
        token = cls.client.post("/api/v1/auth/login", json={"email": "demo@chably.ai", "password": "DemoPassword123!"}).json()["data"]["access_token"]
        cls.client.headers.update({"Authorization": f"Bearer {token}"})

    def _connect(self):
        auth = self.client.post("/api/v1/integrations/google/connect").json()["data"]["authorization_url"]
        values = parse_qs(urlparse(auth).query)
        callback = self.client.get("/api/v1/integrations/google/callback", params={"code": values["code"][0], "state": values["state"][0]})
        self.assertEqual(callback.status_code, 200)
        return callback

    def test_full_reply_lifecycle_is_idempotent(self):
        callback = self._connect()
        self.assertNotIn("token", str(callback.json()).lower())
        status = self.client.get("/api/v1/integrations/google/status/demo-user").json()["data"]
        self.assertEqual(status["scopes"], ["gmail.send", "gmail.readonly"])
        self.assertFalse(status["reauth_required"])
        with SessionLocal() as db:
            connection = db.get(GoogleConnection, "demo-user")
            self.assertNotEqual(connection.access_token_encrypted, "mock-access-token")
            self.assertEqual(decrypt_token(connection.access_token_encrypted), "mock-access-token")

        search = self.client.post("/api/v1/job-search", json={"user_id": "demo-user", "query": "Find backend jobs in Bangalore"}).json()["data"]
        item = search["results"][0]
        with SessionLocal() as db:
            company = db.get(Company, item["company"]["id"])
            if "recruiting@example.com" not in company.description:
                company.description += " Careers: recruiting@example.com"
            db.commit()
        contacts = self.client.get(f"/api/v1/opportunities/{item['opportunity_id']}/contacts", params={"user_id": "demo-user"}).json()["data"]["contacts"]
        draft = self.client.post(f"/api/v1/opportunities/{item['opportunity_id']}/draft-email", params={"user_id": "demo-user", "contact_id": contacts[0]["id"]}).json()["data"]["outreach"]
        self.assertEqual(self.client.post(f"/api/v1/outreach/{draft['id']}/approve", params={"user_id": "demo-user"}).status_code, 200)
        sent_response = self.client.post(f"/api/v1/outreach/{draft['id']}/send", params={"user_id": "demo-user"})
        self.assertEqual(sent_response.status_code, 200, sent_response.text)
        sent = sent_response.json()["data"]
        application_response = self.client.post(f"/api/v1/opportunities/{item['opportunity_id']}/application", params={"user_id": "demo-user"}, json={"status": "contacted"})
        application_id = application_response.json()["data"]["id"]

        incoming_id = GmailService.inject_mock_reply(sent["gmail_thread_id"], "Recruiter <recruiting@example.com>", "We would like to schedule an interview Tuesday at 3 PM. Meeting link https://meet.example/test")
        first = self.client.post("/api/v1/integrations/google/demo-user/sync").json()["data"]
        self.assertEqual(first["new_replies"], 1)
        self.assertEqual(first["drafts_created"], 1)
        self.assertEqual(first["applications_updated"], 1)
        self.assertEqual(first["auto_replies_sent"], 0)
        second = self.client.post("/api/v1/integrations/google/demo-user/sync").json()["data"]
        self.assertEqual(second["new_messages"], 0)
        self.assertEqual(second["new_replies"], 0)
        app_data = self.client.get(f"/api/v1/applications/{application_id}", params={"user_id": "demo-user"}).json()["data"]
        self.assertEqual(app_data["application"]["status"], "interview")
        event_count = len(app_data["events"])
        self.assertGreaterEqual(event_count, 5)
        self.client.post("/api/v1/integrations/google/demo-user/sync")
        self.assertEqual(len(self.client.get(f"/api/v1/applications/{application_id}", params={"user_id": "demo-user"}).json()["data"]["events"]), event_count)
        reply = self.client.post(f"/api/v1/outreach/{draft['id']}/draft-reply", params={"user_id": "demo-user"}).json()["data"]
        self.assertEqual(reply["classification"], "interview_invitation")
        self.assertEqual(self.client.post(f"/api/v1/replies/{reply['reply_id']}/approve", params={"user_id": "demo-user"}).status_code, 200)
        self.assertEqual(self.client.post(f"/api/v1/replies/{reply['reply_id']}/send", params={"user_id": "demo-user"}).status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/outreach/{draft['id']}", params={"user_id": "other-user"}).status_code, 404)

        GmailService.inject_mock_reply(sent["gmail_thread_id"], "no-reply@example.com", "Automatic reply", headers={"Auto-Submitted": "auto-replied"})
        automated = self.client.post("/api/v1/integrations/google/demo-user/sync").json()["data"]
        self.assertEqual(automated["new_replies"], 1)
        self.assertEqual(automated["auto_replies_sent"], 0)

        settings.auto_reply_enabled = True
        policy = {"auto_send_enabled": False, "minimum_fit_score": 90, "allowed_contact_types": [], "maximum_daily_emails": 5, "auto_reply_enabled": True, "allowed_categories": ["acknowledgement"], "minimum_classification_confidence": .92, "maximum_auto_replies_per_day": 1}
        self.assertEqual(self.client.patch("/api/v1/users/demo-user/outreach-settings", json=policy).status_code, 200)
        acknowledgement_id = GmailService.inject_mock_reply(sent["gmail_thread_id"], "Recruiter <recruiting@example.com>", "Thank you, we received your application and will review it.")
        auto = self.client.post("/api/v1/integrations/google/demo-user/sync").json()["data"]
        self.assertEqual(auto["auto_replies_sent"], 1)
        self.client.post("/api/v1/integrations/google/demo-user/sync")  # stores the newly sent outgoing message
        loop_check = self.client.post("/api/v1/integrations/google/demo-user/sync").json()["data"]
        self.assertEqual(loop_check["new_replies"], 0)
        self.assertEqual(loop_check["auto_replies_sent"], 0)
        GmailService.inject_mock_reply(sent["gmail_thread_id"], "recruiting@example.com", "Thank you, we received your follow-up and will review it.")
        limited = self.client.post("/api/v1/integrations/google/demo-user/sync").json()["data"]
        self.assertEqual(limited["auto_replies_sent"], 0)

        with SessionLocal() as db:
            self.assertEqual(db.query(GmailMessage).filter(GmailMessage.gmail_message_id == incoming_id).count(), 1)
            analysis = db.query(ReplyAnalysis).filter(ReplyAnalysis.gmail_message_id == incoming_id).one()
            self.assertIn("https://meet.example/test", analysis.extracted_data["links"])
            self.assertEqual(db.query(ReplyDraft).filter(ReplyDraft.source_incoming_message_id == acknowledgement_id).count(), 1)
            self.assertEqual(db.query(ApplicationEvent).filter(ApplicationEvent.application_id == application_id).count(), len(self.client.get(f"/api/v1/applications/{application_id}", params={"user_id": "demo-user"}).json()["data"]["events"]))

        summary = asyncio.run(sync_command())
        self.assertEqual(summary["users_failed"], 0)

    def test_refresh_reauth_disconnect_and_validation(self):
        self._connect()
        with SessionLocal() as db:
            connection = db.get(GoogleConnection, "demo-user")
            connection.token_expiry = datetime.utcnow() - timedelta(minutes=1)
            db.commit()
        self.assertEqual(self.client.post("/api/v1/integrations/google/demo-user/sync").status_code, 200)
        with SessionLocal() as db:
            connection = db.get(GoogleConnection, "demo-user")
            self.assertEqual(decrypt_token(connection.access_token_encrypted), "mock-refreshed-token")
            connection.token_expiry = datetime.utcnow() - timedelta(minutes=1)
            connection.refresh_token_encrypted = encrypt_token("invalid-refresh-token")
            db.commit()
        self.assertEqual(self.client.post("/api/v1/integrations/google/demo-user/sync").status_code, 401)
        self.assertTrue(self.client.get("/api/v1/integrations/google/status/demo-user").json()["data"]["reauth_required"])
        self._connect()
        self.assertEqual(self.client.delete("/api/v1/integrations/google/demo-user").status_code, 200)
        self.assertEqual(self.client.post("/api/v1/integrations/google/demo-user/sync").status_code, 401)


if __name__ == "__main__":
    unittest.main()
