import os
import unittest

os.environ.setdefault("AI_MOCK_MODE", "true")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_phase1.db")

from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app
from app.scripts.seed_demo import main as seed_demo


class Phase1SmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        seed_demo()
        cls.client = TestClient(app)
        token = cls.client.post("/api/v1/auth/login", json={"email": "demo@chably.ai", "password": "DemoPassword123!"}).json()["data"]["access_token"]
        cls.headers = {"Authorization": f"Bearer {token}"}

    def test_status_dashboard_and_cors(self):
        status = self.client.get("/api/v1/system/status")
        self.assertTrue(status.json()["success"])
        self.assertTrue(status.headers.get("x-request-id")); self.assertEqual(status.headers.get("x-content-type-options"), "nosniff")
        dashboard = self.client.get("/api/v1/dashboard/demo-user", headers=self.headers)
        self.assertTrue(dashboard.json()["success"])
        self.assertIn("completeness", dashboard.json()["data"])
        cors = self.client.options("/api/v1/system/status", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
        self.assertEqual(cors.headers.get("access-control-allow-origin"), "http://localhost:5173")
        schema = self.client.get("/openapi.json").json()
        self.assertIn("HTTPBearer", schema["components"]["securitySchemes"])


if __name__ == "__main__":
    unittest.main()
