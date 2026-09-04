import os
import unittest

os.environ["AI_MOCK_MODE"] = "true"
os.environ["SEARCH_MOCK_MODE"] = "true"
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_phase1.db")

from fastapi.testclient import TestClient
from app.database import Base, engine
from app.main import app
from app.scripts.seed_demo import main as seed_demo
from app.services.job_discovery import CareerPageParser, extract_experience, extract_job_skills, fit_score, generate_queries, hard_filter, normalize_domain, parse_intent_locally, parse_job_posting_html, parse_seniority
from app.services.ats_providers import AshbyProvider, GreenhouseProvider, LeverProvider, appears_javascript_only, classify_url
from app.services.gemini_provider import deterministic_role_recommendations
from app.data.job_taxonomy import ROLES
from app.config import settings

settings.search_mock_mode = True
settings.ai_mock_mode = True


class DiscoveryUnitTest(unittest.TestCase):
    def test_normalization_intent_filters_and_fit(self):
        self.assertEqual(normalize_domain("https://www.Example.ai/about"), "example.ai")
        intent = parse_intent_locally("Find remote AI Engineer jobs under 3 years", {"career_preferences": {}, "skills": {}})
        self.assertTrue(intent["remote"]); self.assertEqual(intent["experience_max_years"], 3)
        self.assertGreaterEqual(len(generate_queries(intent)), 1)
        self.assertFalse(hard_filter({"status": "open", "experience_min": 8}, intent))
        match = fit_score({"skills": {"technical": ["Python", "FastAPI"]}}, {"title": "AI Engineer", "skills": ["Python"], "status": "open", "remote_type": "remote", "experience_min": 1}, intent)
        self.assertGreaterEqual(match["final_fit_score"], 60)
        self.assertEqual(extract_experience("Requires 3+ years"), (3, None))
        self.assertEqual(extract_experience("1-3 years experience"), (1, 3))
        self.assertEqual(parse_seniority("Senior AI Engineer"), "senior")

    def test_career_detection_and_ats(self):
        parser = CareerPageParser()
        html = '<html><body><a href="https://jobs.lever.co/acme">Join us</a></body></html>'
        self.assertEqual(parser.find_careers_url("https://acme.test", html), "https://jobs.lever.co/acme")
        self.assertEqual(parser.detect_ats("https://jobs.lever.co/acme"), "lever")
        self.assertEqual(classify_url("https://jobs.ashbyhq.com/acme"), "ats_page")
        self.assertEqual(classify_url("https://www.linkedin.com/jobs/backend-engineer-jobs"), "irrelevant")
        self.assertEqual(classify_url("https://www.linkedin.com/jobs/view/123456789"), "job_page")
        self.assertEqual(classify_url("https://wellfound.com/jobs/123456-backend-engineer"), "job_page")
        self.assertEqual(classify_url("https://www.remoterocketship.com/jobs/ai-engineer/"), "irrelevant")
        self.assertEqual(classify_url("https://www.foundit.in/job/python-engineer-bengaluru-123"), "irrelevant")
        self.assertTrue(appears_javascript_only('<div id="root"></div><script></script>', ""))
        self.assertEqual(GreenhouseProvider().normalize({"id": 1, "title": "Engineer", "location": {"name": "Remote"}})["source_type"], "greenhouse")
        self.assertEqual(LeverProvider().normalize({"id": "1", "text": "Engineer", "categories": {}})["source_type"], "lever")
        self.assertEqual(AshbyProvider().normalize({"id": "1", "title": "Engineer"})["source_type"], "ashby")

    def test_boolean_queries_and_structured_job_parsing(self):
        queries = generate_queries({"roles": ["Applied AI Engineer", "Backend Engineer"], "skills": ["Python", "FastAPI"], "locations": ["Bengaluru", "Chennai"], "sources": ["ats", "wellfound", "linkedin"]})
        self.assertTrue(any("site:jobs.lever.co" in query for query in queries))
        self.assertTrue(any("site:wellfound.com/jobs" in query for query in queries))
        self.assertTrue(any("site:linkedin.com/jobs/view" in query for query in queries))
        html = '<script type="application/ld+json">{"@type":"JobPosting","title":"Backend Engineer","description":"Python and FastAPI, 2 years","datePosted":"2026-09-03","hiringOrganization":{"name":"Acme"},"jobLocation":{"address":{"addressLocality":"Bengaluru","addressCountry":"IN"}}}</script>'
        parsed = parse_job_posting_html(html)
        self.assertEqual(parsed["title"], "Backend Engineer")
        self.assertEqual(parsed["company_name"], "Acme")
        self.assertIn("Bengaluru", parsed["location"])
        self.assertEqual(extract_job_skills(parsed["description"]), ["Python", "FastAPI"])

    def test_role_fallback_scores_profile_evidence(self):
        profile = {"professional_summary": "Applied AI and backend engineer building RAG APIs", "experience": [{"title": "AI Engineer"}], "projects": [], "skills": {"programming_languages": ["Python"], "frameworks": ["FastAPI"], "databases": ["PostgreSQL"], "ai_ml": ["RAG", "LLM Applications"], "other": ["REST APIs"]}}
        recommendations = deterministic_role_recommendations(profile, ROLES)
        self.assertGreaterEqual(recommendations[0]["fit_score"], 80)
        self.assertGreater(len({item["fit_score"] for item in recommendations}), 1)


class DiscoveryApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine); seed_demo(); cls.client = TestClient(app)
        token = cls.client.post("/api/v1/auth/login", json={"email": "demo@chably.ai", "password": "DemoPassword123!"}).json()["data"]["access_token"]
        cls.headers = {"Authorization": f"Bearer {token}"}

    def test_mock_search_save_and_history(self):
        response = self.client.post("/api/v1/job-search", headers=self.headers, json={"user_id": "demo-user", "query": "Find AI backend jobs in Bangalore under 3 years"})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertGreater(data["results_count"], 0)
        search_id = data["search_id"]
        cached = self.client.post("/api/v1/job-search", headers=self.headers, json={"user_id": "demo-user", "query": "  find   AI backend jobs in Bangalore under 3 YEARS "}).json()["data"]
        self.assertEqual(cached["search_id"], search_id)
        self.assertEqual(self.client.get(f"/api/v1/job-search/{search_id}/progress", headers=self.headers).status_code, 200)
        first = data["results"][0]
        saved = self.client.post(f"/api/v1/jobs/{first['job']['id']}/save", headers=self.headers, json={"user_id": "demo-user", "status": "saved", "notes": "Review later"})
        self.assertEqual(saved.status_code, 200)
        updated = self.client.patch(f"/api/v1/opportunities/{first['opportunity_id']}", headers=self.headers, json={"status": "reviewing"})
        self.assertEqual(updated.status_code, 200)
        history = self.client.get("/api/v1/users/demo-user/search-history", headers=self.headers)
        self.assertGreater(len(history.json()["data"]["searches"]), 0)


if __name__ == "__main__":
    unittest.main()
