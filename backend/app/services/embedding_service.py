"""Local ChromaDB persistence for semantic candidate and taxonomy documents."""
import hashlib
import math
import re
import uuid
from ..config import settings


class EmbeddingService:
    dimensions = 384

    def __init__(self):
        self.client = None
        self.available = False
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings
            import os
            os.makedirs(settings.resolved_chroma_path, exist_ok=True)
            self.client = chromadb.PersistentClient(path=settings.resolved_chroma_path, settings=ChromaSettings(anonymized_telemetry=False))
            self.candidate = self.client.get_or_create_collection("candidate_knowledge")
            self.taxonomy = self.client.get_or_create_collection("job_taxonomy")
            self.jobs = self.client.get_or_create_collection("job_knowledge")
            self.companies = self.client.get_or_create_collection("company_knowledge")
            self.available = True
        except Exception:
            self.client = None

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9+.#-]{1,}", text.lower()):
            digest = hashlib.sha256(token.encode()).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def _documents(self, profile: dict):
        if profile.get("professional_summary"):
            yield "summary", "summary", f"Candidate summary: {profile['professional_summary']}"
        for index, item in enumerate(profile.get("experience", [])):
            yield "experience", str(item.get("id") or index), self._format("experience", item)
        for index, item in enumerate(profile.get("projects", [])):
            yield "project", str(item.get("id") or index), self._format("project", item)
        for section, values in (profile.get("skills") or {}).items():
            for value in values if isinstance(values, list) else []:
                yield "skill_evidence", str(value).lower(), f"type: skill_evidence\nskill: {value}\ncategory: {section}"

    def _format(self, kind, item):
        return "type: %s\n%s" % (kind, "\n".join(f"{k}: {v}" for k, v in item.items()))

    def upsert_profile(self, user_id, resume_id, profile):
        if not self.available:
            return []
        docs = list(self._documents(profile))
        ids = [f"{user_id}:{kind}:{entity}" for kind, entity, _ in docs]
        existing = self.candidate.get(where={"user_id": user_id}).get("ids", [])
        stale = [document_id for document_id in existing if document_id not in ids]
        if stale:
            self.candidate.delete(ids=stale)
        if ids:
            self.candidate.upsert(ids=ids, documents=[doc for _, _, doc in docs], embeddings=[self._embed(doc) for _, _, doc in docs], metadatas=[{"user_id": user_id, "resume_id": resume_id, "type": kind, "entity_id": entity, "source": "resume"} for kind, entity, _ in docs])
        return ids

    def delete_user(self, user_id: str) -> None:
        if self.available:
            existing = self.candidate.get(where={"user_id": user_id}).get("ids", [])
            if existing:
                self.candidate.delete(ids=existing)

    def upsert_job_taxonomy(self, roles):
        if not self.available:
            return False
        ids, docs, metas = [], [], []
        for role in roles:
            title = role.get("role") or role.get("title")
            doc = f"role: {title}\ndescription: {role.get('description', '')}\nskills: {', '.join(role.get('required_skills', []))}"
            ids.append(f"role:{title.lower().replace(' ', '-')}"); docs.append(doc); metas.append({"type": "role_taxonomy", "role_name": title, "source": "taxonomy"})
        self.taxonomy.upsert(ids=ids, documents=docs, embeddings=[self._embed(d) for d in docs], metadatas=metas)
        return True

    def find_similar_roles(self, profile, roles):
        if not self.available:
            raise RuntimeError("VECTOR_STORE_UNAVAILABLE")
        query = "\n".join(doc for _, _, doc in self._documents(profile)) or "candidate profile"
        result = self.taxonomy.query(query_embeddings=[self._embed(query)], n_results=min(10, len(roles)))
        names = [m.get("role_name") for m in (result.get("metadatas") or [[]])[0] if m.get("role_name")]
        return [r for name in names for r in roles if (r.get("role") or r.get("title")) == name] or roles[:10]

    def upsert_job(self, job: dict, company: dict):
        if not self.available:
            return False
        document = "\n".join([
            f"JOB TITLE: {job.get('title', '')}", f"COMPANY: {company.get('name', '')}",
            f"DESCRIPTION: {job.get('description', '')}", f"SKILLS: {', '.join(job.get('skills') or [])}",
            f"EXPERIENCE: {job.get('experience_min')} - {job.get('experience_max')} years",
            f"LOCATION: {job.get('location') or ''}",
        ])
        self.jobs.upsert(ids=[f"job:{job['id']}"], documents=[document], embeddings=[self._embed(document)], metadatas=[{"job_id": job["id"], "company_id": company["id"], "type": "job", "location": job.get("location") or "", "status": job.get("status", "open")}])
        return True

    def upsert_company(self, company: dict):
        if not self.available:
            return False
        data = company.get("data") or {}
        document = "\n".join([f"COMPANY: {company.get('name', '')}", f"DESCRIPTION: {company.get('description', '')}", f"PRODUCTS: {', '.join(data.get('products') or [])}", f"TECHNICAL FOCUS: {', '.join(data.get('technologies') or [])}", f"INDUSTRIES: {', '.join(data.get('industries') or [])}"])
        self.companies.upsert(ids=[f"company:{company['id']}"], documents=[document], embeddings=[self._embed(document)], metadatas=[{"company_id": company["id"], "domain": company.get("domain", ""), "type": "company"}])
        return True

    def find_candidate_evidence(self, user_id: str, query: str, limit: int = 5) -> list[dict]:
        if not self.available:
            raise RuntimeError("VECTOR_STORE_UNAVAILABLE")
        result = self.candidate.query(query_embeddings=[self._embed(query)], n_results=limit, where={"user_id": user_id})
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        return [{"text": text, **metadata} for text, metadata in zip(documents, metadatas)]
