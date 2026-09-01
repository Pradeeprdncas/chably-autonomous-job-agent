from __future__ import annotations

import hashlib
import re
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..data.mock_discovery import mock_results
from ..models import CandidateProfile, Company, Job, JobSearchSession, Opportunity
from .embedding_service import EmbeddingService
from .gemini_provider import GeminiProvider
from .ats_providers import classify_url


def normalize_domain(url: str) -> str:
    value = url if "://" in (url or "") else f"https://{url}"
    host = (urlparse(value).hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    parts = host.split(".")
    if len(parts) > 2 and parts[0] in {"careers", "career", "jobs", "www"}:
        host = ".".join(parts[1:])
    return host


class SearchProvider(ABC):
    @abstractmethod
    async def search(self, query: str, limit: int = 10) -> list[dict]: ...


class SearXNGProvider(SearchProvider):
    async def health_check(self) -> bool:
        if not settings.searxng_url:
            return False
        try:
            async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
                response = await client.get(
                    f"{settings.searxng_url.rstrip('/')}/search",
                    params={"q": "chably health check", "format": "json"},
                )
                response.raise_for_status()
                return isinstance(response.json().get("results"), list)
        except (httpx.HTTPError, ValueError, TypeError):
            return False

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        if not settings.searxng_url:
            raise RuntimeError("SEARCH_PROVIDER_UNAVAILABLE")
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            try:
                response = await client.get(f"{settings.searxng_url.rstrip('/')}/search", params={"q": query, "format": "json"})
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise RuntimeError("SEARCH_PROVIDER_UNAVAILABLE") from exc
            rows = response.json().get("results", [])
            if not isinstance(rows, list):
                raise RuntimeError("SEARCH_PROVIDER_INVALID_RESPONSE")
            return [{"title": str(item.get("title") or ""), "url": str(item.get("url") or ""), "snippet": str(item.get("content") or ""), "engine": ",".join(item.get("engines") or []), "source": "searxng"} for item in rows[:limit] if isinstance(item, dict) and item.get("url")]


class DuckDuckGoProvider(SearchProvider):
    """Keyless public-web fallback used only when a SearXNG instance is not configured."""

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        from bs4 import BeautifulSoup

        headers = {"User-Agent": "Mozilla/5.0 (compatible; ChablyCareerSearch/1.0)"}
        try:
            async with httpx.AsyncClient(timeout=settings.http_timeout_seconds, follow_redirects=True, headers=headers) as client:
                response = await client.get("https://html.duckduckgo.com/html/", params={"q": query})
                response.raise_for_status()
        except httpx.HTTPError:
            return []
        soup = BeautifulSoup(response.text, "html.parser")
        rows = []
        for link in soup.select("a.result__a")[:limit]:
            url = link.get("href") or ""
            parsed = urlparse(url)
            if "duckduckgo.com" in parsed.netloc:
                url = unquote((parse_qs(parsed.query).get("uddg") or [""])[0]) or url
            if not url:
                continue
            container = link.find_parent(class_="result")
            snippet = container.select_one(".result__snippet").get_text(" ", strip=True) if container and container.select_one(".result__snippet") else ""
            rows.append({"title": link.get_text(" ", strip=True), "url": url, "snippet": snippet, "engine": "duckduckgo", "source": "duckduckgo"})
        return rows


class MockSearchProvider(SearchProvider):
    async def search(self, query: str, limit: int = 10) -> list[dict]:
        terms = set(re.findall(r"[a-z]{3,}", query.lower()))
        rows = mock_results()
        ranked = sorted(rows, key=lambda row: len(terms & set(re.findall(r"[a-z]{3,}", str(row).lower()))), reverse=True)
        return ranked[:limit]


def get_search_provider() -> SearchProvider:
    if settings.search_mock_mode:
        return MockSearchProvider()
    if settings.search_provider.lower() == "searxng":
        return SearXNGProvider() if settings.searxng_url else DuckDuckGoProvider()
    raise RuntimeError("SEARCH_PROVIDER_UNAVAILABLE")


class WebsiteFetcher:
    async def fetch(self, url: str) -> dict:
        headers = {"User-Agent": "ChablyBot/1.0 (+career-discovery; respectful single-page fetch)"}
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds, follow_redirects=True, headers=headers) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                if "text/html" not in response.headers.get("content-type", ""):
                    raise ValueError("UNSUPPORTED_CONTENT_TYPE")
                limit = settings.max_page_size_mb * 1024 * 1024
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > limit:
                        raise ValueError("PAGE_TOO_LARGE")
        return {"url": str(response.url), "html": body.decode(response.encoding or "utf-8", errors="replace")}


class CareerPageParser:
    LINK_TERMS = ("career", "jobs", "join us", "we're hiring", "open positions", "work with us", "openings")

    def clean_text(self, html: str) -> str:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for node in soup(["script", "style", "nav", "footer", "noscript"]):
            node.decompose()
        return " ".join(soup.get_text(" ", strip=True).split())[:30000]

    def find_careers_url(self, base_url: str, html: str) -> str | None:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for anchor in soup.find_all("a", href=True):
            label = f"{anchor.get_text(' ', strip=True)} {anchor['href']}".lower()
            if any(term in label for term in self.LINK_TERMS):
                return urljoin(base_url, anchor["href"])
        return None

    def detect_ats(self, url: str, html: str = "") -> str | None:
        value = f"{url} {html[:5000]}".lower()
        for provider in ("greenhouse", "lever", "ashby", "workable", "smartrecruiters", "workday", "bamboohr"):
            if provider in value:
                return provider
        return None


def parse_intent_locally(query: str, profile: dict) -> dict:
    lower = query.lower()
    roles = [role for role in ("Applied AI Engineer", "AI Engineer", "Backend Engineer", "Python Developer", "Data Engineer", "Frontend Engineer", "Support Engineer") if role.lower().replace("applied ", "") in lower]
    if not roles:
        roles = list((profile.get("career_preferences") or {}).get("target_roles") or ["Backend Engineer"])
    known_locations = [name for name in ("Bangalore", "Bengaluru", "Chennai", "Hyderabad", "Mumbai", "Delhi", "Remote") if name.lower() in lower]
    max_years = None
    match = re.search(r"(?:<=|less than|under|max(?:imum)?)\s*(\d+)\s*years?", lower)
    if match:
        max_years = int(match.group(1))
    skills = [skill for skill in ("Python", "FastAPI", "LLM", "RAG", "React", "SQL", "AWS", "ChromaDB") if skill.lower() in lower]
    return {"roles": roles[:5], "skills": skills, "locations": known_locations, "remote": "remote" in lower, "experience_max_years": max_years, "company_types": ["startup"] if "startup" in lower else [], "domains": [term for term in ("AI", "SaaS", "voice AI", "data") if term.lower() in lower], "search_terms": []}


def generate_queries(intent: dict) -> list[str]:
    roles = intent.get("roles") or ["Software Engineer"]
    locations = intent.get("locations") or (["Remote"] if intent.get("remote") else [""])
    suffix = " startup" if "startup" in (intent.get("company_types") or []) else ""
    queries = []
    for role in roles[:4]:
        for location in locations[:2]:
            queries.append(f'"{role}" {location}{suffix} careers'.strip())
    return list(dict.fromkeys(queries))[:settings.max_search_queries_per_request]


def candidate_skills(profile: dict) -> set[str]:
    return {str(skill).lower() for values in (profile.get("skills") or {}).values() if isinstance(values, list) for skill in values}


def hard_filter(job: dict, intent: dict) -> bool:
    if job.get("status") == "closed":
        return False
    maximum = intent.get("experience_max_years")
    if maximum is not None and job.get("experience_min") is not None and job["experience_min"] > maximum:
        return False
    locations = [x.lower() for x in intent.get("locations") or []]
    # Search-result snippets often omit the location. Exclude only a confirmed
    # mismatch; retain unknown locations for transparent user review.
    if locations and not intent.get("remote") and job.get("location") and job["location"].lower() not in locations:
        return False
    return True


def extract_experience(text: str) -> tuple[int | None, int | None]:
    value = (text or "").lower()
    if "fresher" in value or "entry level" in value:
        return 0, 1
    match = re.search(r"(\d+)\s*(?:-|–|to)\s*(\d+)\s*(?:years?|yrs?)", value)
    if match: return int(match.group(1)), int(match.group(2))
    match = re.search(r"(?:up to|max(?:imum)?\s*)\s*(\d+)\s*(?:years?|yrs?)", value)
    if match: return None, int(match.group(1))
    match = re.search(r"(\d+)\s*\+\s*(?:years?|yrs?)", value)
    if match: return int(match.group(1)), None
    match = re.search(r"(\d+)\s*(?:years?|yrs?)", value)
    return (int(match.group(1)), int(match.group(1))) if match else (None, None)


def parse_seniority(title: str) -> str | None:
    value = (title or "").lower()
    for seniority in ("principal", "staff", "senior", "lead", "manager", "mid", "associate", "junior", "entry", "intern"):
        if re.search(rf"\b{seniority}\b", value): return seniority
    return None


def fit_score(profile: dict, job: dict, intent: dict) -> dict:
    skills = candidate_skills(profile)
    required = {str(x).lower() for x in job.get("skills") or []}
    technical = 100 * len(skills & required) / max(1, len(required))
    target_roles = [x.lower() for x in (intent.get("roles") or [])]
    role = 100 if any(x in job.get("title", "").lower() or job.get("title", "").lower() in x for x in target_roles) else 55
    location = 100 if intent.get("remote") and job.get("remote_type") == "remote" else 80
    experience = 100 if intent.get("experience_max_years") is None or (job.get("experience_min") or 0) <= intent["experience_max_years"] else 0
    deterministic = round(technical * .35 + experience * .20 + role * .20 + 70 * .15 + 60 * .05 + location * .05)
    ai_score = deterministic
    final = round(deterministic * .65 + ai_score * .35)
    return {"retrieval_score": technical, "deterministic_fit_score": deterministic, "ai_fit_score": ai_score, "final_fit_score": final, "analysis": {"fit_score": final, "fit_level": "excellent" if final >= 85 else "strong" if final >= 75 else "good" if final >= 60 else "moderate", "why_fit": ["Role and candidate skills overlap."], "matched_skills": sorted(skills & required), "missing_skills": sorted(required - skills), "experience_alignment": "Within the requested experience range.", "candidate_advantages": [], "concerns": [], "recommended_resume_version": job.get("title", ""), "apply_recommendation": "strong_apply" if final >= 85 else "apply" if final >= 70 else "maybe" if final >= 55 else "skip"}}


async def evaluate_fit(user_id: str, profile: dict, job: dict, company: dict, intent: dict) -> dict:
    match = fit_score(profile, job, intent)
    if settings.ai_mock_mode:
        return match
    evidence = []
    try:
        evidence = EmbeddingService().find_candidate_evidence(user_id, " ".join(job.get("skills") or []) or job.get("title", ""))
    except Exception:
        evidence = []
    evaluation = await GeminiProvider()._json(
        "Evaluate candidate-to-job fit. Return fit_score 0-100, why_fit, matched_skills, missing_skills, experience_alignment, candidate_advantages, concerns, recommended_resume_version, apply_recommendation. Do not invent evidence.",
        {"candidate": profile, "candidate_evidence": evidence, "job": job, "company": company, "deterministic_match": match, "search_intent": intent},
    )
    if not evaluation:
        return match
    proposed = int(evaluation.get("fit_score", match["deterministic_fit_score"]))
    ai_score = max(match["deterministic_fit_score"] - 15, min(match["deterministic_fit_score"] + 15, proposed))
    final = round(match["deterministic_fit_score"] * .65 + ai_score * .35)
    evaluation["fit_score"] = final
    evaluation["fit_level"] = "excellent" if final >= 85 else "strong" if final >= 75 else "good" if final >= 60 else "moderate"
    match.update({"ai_fit_score": ai_score, "final_fit_score": final, "analysis": evaluation})
    return match


def company_payload(company: Company) -> dict:
    return {"id": company.id, "name": company.name, "website": company.website, "domain": company.domain, "description": company.description, "careers_url": company.careers_url, "ats_provider": company.ats_provider, **(company.data or {})}


def job_payload(job: Job) -> dict:
    return {"id": job.id, "company_id": job.company_id, "raw_title": job.raw_title, "title": job.title, "description": job.description, "location": job.location, "remote_type": job.remote_type, "employment_type": job.employment_type, "experience_min": job.experience_min, "experience_max": job.experience_max, "skills": job.skills or [], "job_url": job.job_url, "source_url": job.source_url, "source_type": job.source_type, "status": job.status, **(job.data or {})}


async def execute_search(db: Session, user_id: str, query: str, search_type: str = "jobs") -> JobSearchSession:
    profile = db.get(CandidateProfile, user_id)
    if not profile:
        raise LookupError("PROFILE_NOT_FOUND")
    normalized_query = " ".join(query.lower().split())
    recent = next((row for row in db.query(JobSearchSession).filter(JobSearchSession.user_id == user_id, JobSearchSession.search_type == search_type, JobSearchSession.status == "completed", JobSearchSession.completed_at >= datetime.utcnow() - timedelta(seconds=settings.search_cache_ttl_seconds)).order_by(JobSearchSession.completed_at.desc()).limit(10).all() if " ".join(row.raw_query.lower().split()) == normalized_query), None)
    if recent and (search_type != "jobs" or db.query(Opportunity).filter(Opportunity.search_session_id == recent.id).count() > 0):
        return recent
    intent = None
    if not settings.ai_mock_mode:
        intent = await GeminiProvider()._json(
            "Parse the job/company search request into roles, skills, locations, remote, experience_max_years, company_types, domains, search_terms. Return only those keys and preserve unknowns as empty/null.",
            {"query": query, "candidate_profile": profile.data},
        )
        # A real provider is preferred, but a temporary AI failure must not make
        # a user's job search unavailable; deterministic parsing remains useful.
    intent = intent or parse_intent_locally(query, profile.data)
    queries = generate_queries(intent)
    session = JobSearchSession(id=str(uuid.uuid4()), user_id=user_id, search_type=search_type, raw_query=query, structured_intent=intent, search_queries=queries, status="searching", progress={"queries_generated": len(queries), "queries_completed": 0, "companies_discovered": 0, "companies_processed": 0, "jobs_discovered": 0, "jobs_evaluated": 0, "opportunities_created": 0, "failures": 0, "processing_errors": []})
    db.add(session); db.commit()
    provider = get_search_provider()
    discovered = []
    for search_query in queries:
        discovered.extend(await provider.search(search_query, limit=settings.max_search_results_per_query))
        session.progress = {**session.progress, "queries_completed": session.progress["queries_completed"] + 1}
    vector = EmbeddingService()
    companies, jobs = {}, {}
    for row in discovered:
        if "company" not in row or "job" not in row:
            result_url = row.get("url", "")
            if classify_url(result_url) == "irrelevant":
                continue
            domain = normalize_domain(result_url)
            if not domain:
                continue
            content = row.get("content") or row.get("snippet") or ""
            raw_title = row.get("title") or "Open position"
            minimum, maximum = extract_experience(content)
            row = {"company": {"name": domain.split(".")[0].replace("-", " ").title(), "website": f"https://{domain}", "domain": domain, "description": content[:1000], "source_urls": [result_url]}, "job": {"raw_title": raw_title, "title": raw_title, "description": content[:5000], "location": None, "remote_type": None, "employment_type": None, "experience_min": minimum, "experience_max": maximum, "skills": intent.get("skills", []), "seniority": parse_seniority(raw_title), "job_url": result_url, "source_url": result_url, "source_type": settings.search_provider, "status": "unknown"}}
        raw_company, raw_job = row["company"], row["job"]
        domain = normalize_domain(raw_company.get("domain") or raw_company.get("website", ""))
        company = db.query(Company).filter(Company.domain == domain).first()
        if not company:
            company = Company(id=str(uuid.uuid4()), name=raw_company["name"], website=raw_company["website"], domain=domain, description=raw_company.get("description", ""), careers_url=raw_company.get("careers_url"), data={k: v for k, v in raw_company.items() if k not in {"name", "website", "domain", "description", "careers_url"}}, content_hash=hashlib.sha256(str(raw_company).encode()).hexdigest())
            db.add(company); db.flush()
        else:
            company.last_checked_at = datetime.utcnow()
        companies[company.id] = company
        job_url = raw_job.get("job_url") or raw_job.get("source_url")
        job = db.query(Job).filter(Job.company_id == company.id, Job.job_url == job_url).first()
        if not job:
            job = Job(id=str(uuid.uuid4()), company_id=company.id, raw_title=raw_job.get("raw_title") or raw_job.get("title", "Unknown role"), title=raw_job.get("title") or raw_job.get("raw_title", "Unknown role"), description=raw_job.get("description", ""), location=raw_job.get("location"), remote_type=raw_job.get("remote_type"), employment_type=raw_job.get("employment_type"), experience_min=raw_job.get("experience_min"), experience_max=raw_job.get("experience_max"), skills=raw_job.get("skills", []), data={k: v for k, v in raw_job.items() if k not in {"raw_title", "title", "description", "location", "remote_type", "employment_type", "experience_min", "experience_max", "skills", "job_url", "source_url", "source_type", "status"}}, job_url=job_url, source_url=raw_job.get("source_url", job_url), source_type=raw_job.get("source_type", "search"), status=raw_job.get("status", "unknown"), content_hash=hashlib.sha256(str(raw_job).encode()).hexdigest())
            db.add(job); db.flush()
        else:
            job.last_verified_at = datetime.utcnow()
            if raw_job.get("status") in {"open", "closed"}:
                job.status = raw_job["status"]
        jobs[job.id] = job
        vector.upsert_company(company_payload(company)); vector.upsert_job(job_payload(job), company_payload(company))
    db.commit()
    results = []
    if search_type == "jobs":
        for job in jobs.values():
            payload = job_payload(job)
            if not hard_filter(payload, intent): continue
            match = await evaluate_fit(user_id, profile.data, payload, company_payload(companies[job.company_id]), intent)
            opportunity = db.query(Opportunity).filter(Opportunity.user_id == user_id, Opportunity.job_id == job.id).first()
            if not opportunity:
                opportunity = Opportunity(id=str(uuid.uuid4()), user_id=user_id, search_session_id=session.id, company_id=job.company_id, job_id=job.id, retrieval_score=match["retrieval_score"], deterministic_fit_score=match["deterministic_fit_score"], ai_fit_score=match["ai_fit_score"], final_fit_score=match["final_fit_score"], analysis=match["analysis"])
                db.add(opportunity)
            else:
                opportunity.search_session_id = session.id
                opportunity.retrieval_score = match["retrieval_score"]
                opportunity.deterministic_fit_score = match["deterministic_fit_score"]
                opportunity.ai_fit_score = match["ai_fit_score"]
                opportunity.final_fit_score = match["final_fit_score"]
                opportunity.analysis = match["analysis"]
            results.append(opportunity)
    session.status = "completed"; session.results_count = len(results) if search_type == "jobs" else len(companies); session.completed_at = datetime.utcnow(); session.progress = {"queries_generated": len(queries), "queries_completed": len(queries), "companies_discovered": len(companies), "companies_processed": len(companies), "jobs_discovered": len(jobs), "jobs_evaluated": len(results), "opportunities_created": len(results), "failures": 0, "processing_errors": []}
    db.commit()
    return session
