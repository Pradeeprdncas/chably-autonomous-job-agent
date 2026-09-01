import os
import sqlite3
import unittest
import tempfile
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_phase1.db")

import httpx

from app.config import settings
from app.services.google_gmail import (decrypt_token, encrypt_token,
                                       validate_message)
from app.services.job_discovery import SearXNGProvider, get_search_provider
from app.services.reply_sync import (automated_type, deterministic_classification,
                                     normalize_classification,
                                     normalize_draft_body, reply_text)
from app.database import engine
from app.scripts.backup_data import main as backup_data
from app.scripts.restore_data import main as restore_data


class ProviderHardeningTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_url = settings.searxng_url
        self.original_mock = settings.search_mock_mode
        self.original_provider = settings.search_provider
        settings.searxng_url = "https://search.internal.test"
        settings.search_mock_mode = False
        settings.search_provider = "searxng"

    async def asyncTearDown(self):
        settings.searxng_url = self.original_url
        settings.search_mock_mode = self.original_mock
        settings.search_provider = self.original_provider

    async def test_searxng_normalization_and_health(self):
        response = httpx.Response(200, request=httpx.Request("GET", "https://search.internal.test/search"), json={"results": [{"title": " Backend role ", "url": "https://example.com/jobs/1", "content": "Python", "engines": ["bing"]}, {"title": "missing url"}]})
        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=response)):
            provider = SearXNGProvider()
            rows = await provider.search("backend careers")
            self.assertEqual(rows, [{"title": " Backend role ", "url": "https://example.com/jobs/1", "snippet": "Python", "engine": "bing", "source": "searxng"}])
            self.assertTrue(await provider.health_check())

    async def test_searxng_failure_has_no_mock_fallback(self):
        with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=httpx.ConnectError("offline"))):
            with self.assertRaises(RuntimeError):
                await get_search_provider().search("backend careers")

    async def test_security_and_classification_rules(self):
        token = encrypt_token("secret-token")
        self.assertNotIn("secret-token", token)
        self.assertEqual(decrypt_token(token), "secret-token")
        with self.assertRaises(ValueError):
            validate_message("victim@example.com\nBcc: attacker@example.com", "hello", "body")
        self.assertEqual(automated_type({"auto-submitted": "auto-replied"}, "robot@example.com", "away"), "automatic_reply")
        result = deterministic_classification("Unfortunately, we are not moving forward with your application.", "human_reply")
        self.assertEqual(result["category"], "rejection")
        self.assertGreaterEqual(result["confidence"], .9)
        self.assertEqual(reply_text("Hello\n\nOn Sat, 22 Aug, 2026, Sender wrote:\n> quoted content"), "Hello")
        self.assertIsNone(normalize_classification({"category": None, "confidence": None}, "gemini"))
        normalized = normalize_classification({"classification": {"category": "follow_up", "confidence": "0.7", "requires_user_review": True}}, "mistral")
        self.assertEqual(normalized["category"], "follow_up")
        self.assertEqual(normalized["provider"], "mistral")
        self.assertEqual(normalize_draft_body({"body": {"greeting": "Hello", "body": "Thanks", "closing": "Regards"}}), "Hello\n\nThanks\n\nRegards")

    async def test_production_guards_sqlite_and_backup(self):
        with engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql("PRAGMA foreign_keys").scalar(), 1)
            self.assertEqual(connection.exec_driver_sql("PRAGMA journal_mode").scalar().lower(), "wal")
            self.assertEqual(connection.exec_driver_sql("PRAGMA busy_timeout").scalar(), 5000)
        original = {name: getattr(settings, name) for name in ("app_env", "ai_mock_mode", "search_mock_mode", "google_oauth_mock_mode", "jwt_secret_key", "token_encryption_key", "searxng_url", "cors_origins", "frontend_url", "gemini_api_key", "mistral_api_key", "google_client_id", "google_client_secret", "google_redirect_uri")}
        try:
            settings.app_env = "production"; settings.ai_mock_mode = True
            with self.assertRaises(RuntimeError): settings.validate_production()
            settings.ai_mock_mode = False; settings.search_mock_mode = False; settings.google_oauth_mock_mode = False; settings.jwt_secret_key = "x" * 48; settings.token_encryption_key = "configured"; settings.searxng_url = "https://search.example"; settings.cors_origins = "https://app.example.com"; settings.frontend_url = "https://app.example.com"; settings.gemini_api_key = "configured"; settings.mistral_api_key = "configured"; settings.google_client_id = "configured"; settings.google_client_secret = "configured"; settings.google_redirect_uri = "https://api.example.com/api/v1/integrations/google/callback"
            settings.validate_production()
        finally:
            for name, value in original.items(): setattr(settings, name, value)
        with tempfile.TemporaryDirectory() as directory:
            target = backup_data(directory); self.assertTrue(target.exists()); self.assertGreater(target.stat().st_size, 0)

    async def test_backup_restore_round_trip_uses_temporary_storage(self):
        original_database_url = settings.database_url
        original_chroma_path = settings.chroma_path
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = os.path.abspath(directory)
                database = os.path.join(root, "live.sqlite3")
                chroma = os.path.join(root, "chroma")
                os.makedirs(chroma)
                with open(os.path.join(chroma, "marker.txt"), "w", encoding="utf-8") as marker: marker.write("before")
                with sqlite3.connect(database) as connection:
                    connection.execute("CREATE TABLE marker (value TEXT)")
                    connection.execute("INSERT INTO marker VALUES ('before')")
                settings.database_url = f"sqlite:///{database}"
                settings.chroma_path = chroma
                backup = backup_data(os.path.join(root, "backups"))
                with sqlite3.connect(database) as connection:
                    connection.execute("UPDATE marker SET value='after'")
                with open(os.path.join(chroma, "marker.txt"), "w", encoding="utf-8") as marker: marker.write("after")
                result = restore_data(str(backup))
                with sqlite3.connect(database) as connection:
                    self.assertEqual(connection.execute("SELECT value FROM marker").fetchone()[0], "before")
                with open(os.path.join(chroma, "marker.txt"), encoding="utf-8") as marker:
                    self.assertEqual(marker.read(), "before")
                self.assertEqual(result["chroma_status"], "restored")
        finally:
            settings.database_url = original_database_url
            settings.chroma_path = original_chroma_path


if __name__ == "__main__":
    unittest.main()
