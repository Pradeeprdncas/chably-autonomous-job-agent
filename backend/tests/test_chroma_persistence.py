import tempfile
import unittest

from app.config import settings
from app.data.job_taxonomy import ROLES
from app.services.embedding_service import EmbeddingService


class ChromaPersistenceTest(unittest.TestCase):
    def test_restart_retrieval_and_idempotency(self):
        original = settings.chroma_path
        with tempfile.TemporaryDirectory() as directory:
            settings.chroma_path = directory
            first = EmbeddingService()
            self.assertTrue(first.available)
            profile = {"professional_summary": "Python FastAPI engineer", "experience": [{"id": "exp-1", "role": "Backend Engineer", "description": "Built FastAPI APIs"}], "projects": [{"id": "project-1", "name": "RAG", "description": "Built retrieval"}], "skills": {"technical": ["Python", "FastAPI"]}}
            first.upsert_job_taxonomy(ROLES)
            first.upsert_profile("persist-user", "resume-1", profile)
            first.upsert_company({"id": "company-1", "name": "Example AI", "domain": "example.ai", "description": "Voice AI", "data": {"products": ["Voice AI"]}})
            first.upsert_job({"id": "job-1", "title": "Backend Engineer", "description": "FastAPI", "skills": ["Python", "FastAPI"], "location": "Remote", "status": "open"}, {"id": "company-1", "name": "Example AI"})
            before = [first.candidate.count(), first.taxonomy.count(), first.jobs.count(), first.companies.count()]
            first.upsert_job_taxonomy(ROLES); first.upsert_profile("persist-user", "resume-1", profile)
            first.upsert_company({"id": "company-1", "name": "Example AI", "domain": "example.ai", "description": "Voice AI", "data": {"products": ["Voice AI"]}})
            first.upsert_job({"id": "job-1", "title": "Backend Engineer", "description": "FastAPI", "skills": ["Python", "FastAPI"], "location": "Remote", "status": "open"}, {"id": "company-1", "name": "Example AI"})
            self.assertEqual(before, [first.candidate.count(), first.taxonomy.count(), first.jobs.count(), first.companies.count()])
            second = EmbeddingService()
            after = [second.candidate.count(), second.taxonomy.count(), second.jobs.count(), second.companies.count()]
            self.assertEqual(before, after)
            evidence = second.find_candidate_evidence("persist-user", "FastAPI", 3)
            self.assertTrue(evidence); self.assertTrue(all(item["user_id"] == "persist-user" for item in evidence))
            self.assertEqual(second.jobs.get(ids=["job:job-1"])["ids"], ["job:job-1"])
        settings.chroma_path = original


if __name__ == "__main__":
    unittest.main()
