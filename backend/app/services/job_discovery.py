from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..models import CandidateProfile, Company, Job, JobSearchSession, Opportunity
from .embedding_service import EmbeddingService
from .gemini_provider import GeminiProvider
from .ats_providers import classify_url, provider_for
from .search_providers import (DuckDuckGoProvider, FailoverSearchProvider,
                               SearchProvider, SearXNGProvider,
                               get_search_provider)


def normalize_domain(url: str) -> str:
    value = url if "://" in (url or "") else f"https://{url}"
    host = (urlparse(value).hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    parts = host.split(".")
    if len(parts) > 2 and parts[0] in {"careers", "career", "jobs", "www"}:
        host = ".".join(parts[1:])
    return host


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
    skills = intent.get("skills") or []
    sources = intent.get("sources") or ["official", "ats", "wellfound", "linkedin"]
    role_clause = " OR ".join(f'\"{role}\"' for role in roles[:3])
    skill_clause = " ".join(skills[:2])
    location_clause = " ".join(locations[:3])
    is_boolean = intent.get("search_mode") == "boolean" and str(intent.get("raw_query") or "").strip()
    base = str(intent.get("raw_query") or "").strip() if is_boolean else " ".join(value for value in (roles[0], skill_clause, location_clause) if value)
    if not is_boolean and "startup" in (intent.get("company_types") or []):
        base += " startup"
    queries = []
    if "official" in sources:
        queries.append(f'{base} careers jobs -courses -salary -guide')
    if "ats" in sources:
        queries.append(f'{base if is_boolean else skill_clause or roles[0]} site:jobs.lever.co OR site:boards.greenhouse.io OR site:jobs.ashbyhq.com')
    if "wellfound" in sources:
        for role in roles[:2]:
            query_base = base if is_boolean else f"{role} Python"
            queries.append(f'{query_base} site:wellfound.com/jobs')
    if "linkedin" in sources:
        for role in roles[:2]:
            query_base = base if is_boolean else f"{role} {skill_clause} {location_clause or 'India'}"
            queries.append(f'{query_base} site:linkedin.com/jobs/view')
            queries.append(f'{query_base} jobs')
    if not queries:
        queries.append(f'{base} careers')
    return list(dict.fromkeys(queries))[:settings.max_search_queries_per_request]


def _profile_roles(profile: dict) -> list[str]:
    text = " ".join([
        str(profile.get("professional_summary") or ""),
        " ".join(str(item.get("title") or item.get("role") or "") for item in profile.get("experience") or [] if isinstance(item, dict)),
    ]).lower()
    roles = []
    for title, terms in (
        ("Applied AI Engineer", ("applied ai", "llm", "rag", "agentic")),
        ("AI Engineer", ("ai engineer", "ai/", "machine learning")),
        ("Backend Engineer", ("backend", "fastapi", "api")),
        ("Python Developer", ("python",)),
    ):
        if any(term in text for term in terms):
            roles.append(title)
    return roles or ["Backend Engineer", "Applied AI Engineer"]


def complete_intent(intent: dict, profile: dict, sources: list[str] | None = None, search_mode: str = "intent") -> dict:
    result = dict(intent or {})
    preferences = profile.get("career_preferences") or {}
    result["roles"] = list(result.get("roles") or preferences.get("target_roles") or _profile_roles(profile))[:5]
    result["skills"] = list(result.get("skills") or [skill for values in (profile.get("skills") or {}).values() if isinstance(values, list) for skill in values])[:8]
    result["locations"] = list(result.get("locations") or preferences.get("preferred_locations") or [])[:4]
    result["remote"] = bool(result.get("remote") or str(preferences.get("remote_preference") or "").lower() in {"remote", "hybrid", "yes"})
    if result.get("experience_max_years") is None:
        result["experience_max_years"] = 4
    result["sources"] = list(dict.fromkeys(sources or result.get("sources") or ["official", "ats", "wellfound", "linkedin"]))
    result["search_mode"] = search_mode
    return result


def source_name(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "linkedin.com" in host: return "linkedin"
    if "wellfound.com" in host: return "wellfound"
    if "lever.co" in host: return "lever"
    if "greenhouse.io" in host: return "greenhouse"
    if "ashbyhq.com" in host: return "ashby"
    return "company_careers"


def company_identity(url: str, title: str = "") -> tuple[str, str, str]:
    parsed = urlparse(url); host = (parsed.hostname or "").lower(); parts = [part for part in parsed.path.split("/") if part]
    if "linkedin.com" in host:
        match = re.match(r"(.+?)\s+hiring\s+", title or "", re.I)
        name = match.group(1).strip() if match else "LinkedIn company not identified"
        return name, "https://www.linkedin.com", f"linkedin-{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')}"
    if "wellfound.com" in host:
        match = re.search(r"\s+at\s+(.+?)(?:\s+•|\s+\||$)", title or "", re.I)
        name = match.group(1).strip() if match else "Wellfound company not identified"
        return name, "https://wellfound.com", f"wellfound-{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')}"
    if "lever.co" in host and parts: slug = parts[0]; return slug.replace("-", " ").title(), f"https://jobs.lever.co/{slug}", f"{slug}.lever.co"
    if "greenhouse.io" in host and parts: slug = parts[0]; return slug.replace("-", " ").title(), f"https://boards.greenhouse.io/{slug}", f"{slug}.greenhouse.io"
    if "ashbyhq.com" in host and parts: slug = parts[0]; return slug.replace("-", " ").title(), f"https://jobs.ashbyhq.com/{slug}", f"{slug}.ashbyhq.com"
    clean_title = re.split(r"\s(?:\||—|-|at)\s", title or "", maxsplit=2)
    name = clean_title[1].strip() if len(clean_title) > 1 and len(clean_title[1].strip()) < 100 else normalize_domain(url).split(".")[0].replace("-", " ").title()
    domain = normalize_domain(url)
    return name or "Company", f"https://{domain}", domain


def parse_job_posting_html(html: str) -> dict:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    values = []
    for node in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            payload = json.loads(node.string or node.get_text() or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        values.extend(payload if isinstance(payload, list) else payload.get("@graph", []) if isinstance(payload, dict) and isinstance(payload.get("@graph"), list) else [payload])
    posting = next((item for item in values if isinstance(item, dict) and "JobPosting" in ([item.get("@type")] if isinstance(item.get("@type"), str) else item.get("@type") or [])), None)
    if not posting:
        return {}
    location = posting.get("jobLocation") or []
    if isinstance(location, dict): location = [location]
    addresses = [item.get("address") or {} for item in location if isinstance(item, dict)]
    location_text = ", ".join(filter(None, [", ".join(filter(None, [address.get("addressLocality"), address.get("addressRegion"), address.get("addressCountry")])) for address in addresses]))
    organization = posting.get("hiringOrganization") or {}
    return {"title": posting.get("title"), "description": CareerPageParser().clean_text(str(posting.get("description") or "")), "location": location_text or None, "employment_type": posting.get("employmentType"), "company_name": organization.get("name") if isinstance(organization, dict) else None, "posted_date": posting.get("datePosted")}


def candidate_skills(profile: dict) -> set[str]:
    return {str(skill).lower() for values in (profile.get("skills") or {}).values() if isinstance(values, list) for skill in values}


def extract_job_skills(text: str) -> list[str]:
    value = f" {re.sub(r'[^a-z0-9+#./-]+', ' ', (text or '').lower())} "
    known = ["Python", "FastAPI", "Django", "Flask", "JavaScript", "TypeScript", "React", "Node.js", "PostgreSQL", "MySQL", "MongoDB", "Redis", "Qdrant", "Kafka", "Docker", "Kubernetes", "AWS", "Azure", "GCP", "LLM", "RAG", "Machine Learning", "NLP", "REST APIs"]
    aliases = {"Node.js": ("node.js", "nodejs"), "PostgreSQL": ("postgresql", "postgres"), "Machine Learning": ("machine learning",), "REST APIs": ("rest api", "restful api"), "LLM": ("llm", "large language model"), "RAG": ("rag", "retrieval augmented")}
    return [skill for skill in known if any(term in value for term in aliases.get(skill, (skill.lower(),)))]


def relevant_job(job: dict, intent: dict) -> bool:
    title = str(job.get("title") or job.get("raw_title") or "").lower()
    details = f"{title} {job.get('description') or ''}".lower()
    if not title or title.startswith("jobs at ") or "job application for" in title:
        return False
    role_terms = ["applied ai", "ai engineer", "backend", "python", "machine learning", "ml engineer", "software engineer"]
    requested = [role.lower() for role in intent.get("roles") or []]
    role_match = any(term in title for term in role_terms) or any(role in title for role in requested)
    technical_title = any(term in title for term in ("engineer", "developer", "scientist", "architect"))
    job_skills = {skill.lower() for skill in extract_job_skills(details)}
    requested_skills = {str(skill).lower() for skill in intent.get("skills") or []}
    skill_match = len(job_skills & requested_skills)
    return (role_match or skill_match >= 2) and technical_title


def hard_filter(job: dict, intent: dict) -> bool:
    if job.get("status") == "closed":
        return False
    maximum = intent.get("experience_max_years")
    if maximum is not None and job.get("experience_min") is not None and job["experience_min"] > maximum:
        return False
    seniority = job.get("seniority") or parse_seniority(job.get("title", ""))
    requested = " ".join(intent.get("roles") or []).lower()
    if seniority in {"senior", "staff", "principal", "lead", "manager"} and seniority not in requested:
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


async def _expand_and_enrich(results: list[dict], intent: dict) -> list[dict]:
    output = []
    seen = set()
    seen_jobs = set()
    for result in results:
        if "company" in result and "job" in result:
            job_url = (result.get("job") or {}).get("job_url") or (result.get("job") or {}).get("source_url")
            if job_url and job_url in seen:
                continue
            if job_url:
                seen.add(job_url)
            output.append(result)
            continue
        url = str(result.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        classification = classify_url(url)
        if classification == "ats_page":
            adapter = provider_for(url)
            if not adapter:
                continue
            try:
                company_name, website, domain = company_identity(url, result.get("title", ""))
                for job in (await adapter.list_jobs(url))[:settings.max_search_results_per_query]:
                    job_url = job.get("job_url") or job.get("source_url")
                    if not job_url or job_url in seen or not relevant_job(job, intent):
                        continue
                    seen.add(job_url)
                    identity = (domain, re.sub(r"[^a-z0-9]+", " ", job.get("title", "").lower()).strip())
                    if identity in seen_jobs:
                        continue
                    seen_jobs.add(identity)
                    output.append({"company": {"name": company_name, "website": website, "domain": domain, "careers_url": url, "description": result.get("snippet", ""), "source_urls": [url]}, "job": {**job, "skills": extract_job_skills(f"{job.get('title', '')} {job.get('description', '')}")}})
            except Exception:
                continue
            continue
        if classification != "job_page":
            continue
        details = {}
        host = (urlparse(url).hostname or "").lower()
        adapter = provider_for(url)
        if adapter:
            path_parts = [part for part in urlparse(url).path.split("/") if part]
            job_id = path_parts[-1] if path_parts else ""
            if adapter.name == "greenhouse" and "jobs" in path_parts:
                job_id = path_parts[path_parts.index("jobs") + 1]
            board_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}/{path_parts[0]}" if path_parts else url
            try:
                ats_job = await adapter.get_job(board_url, job_id)
            except Exception:
                ats_job = None
            if ats_job:
                if not relevant_job(ats_job, intent):
                    continue
                company_name, website, domain = company_identity(url, result.get("title", ""))
                identity = (domain, re.sub(r"[^a-z0-9]+", " ", ats_job.get("title", "").lower()).strip())
                if identity in seen_jobs:
                    continue
                seen_jobs.add(identity)
                output.append({"company": {"name": company_name, "website": website, "domain": domain, "careers_url": board_url, "description": result.get("snippet", ""), "source_urls": [url]}, "job": {**ats_job, "skills": extract_job_skills(f"{ats_job.get('title', '')} {ats_job.get('description', '')}")}})
                continue
        if not any(domain in host for domain in ("linkedin.com", "wellfound.com", "indeed.com", "glassdoor.")):
            try:
                page = await WebsiteFetcher().fetch(url)
                details = parse_job_posting_html(page["html"])
            except Exception:
                details = {}
        raw_title = details.get("title") or result.get("title") or "Open position"
        snippet = details.get("description") or result.get("content") or result.get("snippet") or ""
        minimum, maximum = extract_experience(snippet)
        company_name, website, domain = company_identity(url, result.get("title", ""))
        if details.get("company_name"):
            company_name = details["company_name"]
        if not relevant_job({"title": raw_title, "description": snippet}, intent):
            continue
        identity = (domain, re.sub(r"[^a-z0-9]+", " ", raw_title.lower()).strip())
        if identity in seen_jobs:
            continue
        seen_jobs.add(identity)
        output.append({"company": {"name": company_name, "website": website, "domain": domain, "description": snippet[:1000], "source_urls": [url]}, "job": {"raw_title": raw_title, "title": raw_title, "description": snippet[:5000], "location": details.get("location"), "remote_type": "remote" if "remote" in f"{raw_title} {snippet}".lower() else None, "employment_type": details.get("employment_type"), "experience_min": minimum, "experience_max": maximum, "skills": extract_job_skills(f"{raw_title} {snippet}"), "seniority": parse_seniority(raw_title), "job_url": url, "source_url": url, "source_type": source_name(url), "status": "open", "posted_at": details.get("posted_date") or result.get("published_at")}})
    return output


async def execute_search(db: Session, user_id: str, query: str, search_type: str = "jobs", freshness: str | None = None, sources: list[str] | None = None, search_mode: str = "intent") -> JobSearchSession:
    profile = db.get(CandidateProfile, user_id)
    if not profile:
        raise LookupError("PROFILE_NOT_FOUND")
    normalized_query = " ".join(query.lower().split())
    requested_sources = list(dict.fromkeys(sources or ["official", "ats", "wellfound", "linkedin"]))
    recent = next((row for row in db.query(JobSearchSession).filter(JobSearchSession.user_id == user_id, JobSearchSession.search_type == search_type, JobSearchSession.status == "completed", JobSearchSession.completed_at >= datetime.utcnow() - timedelta(seconds=settings.search_cache_ttl_seconds)).order_by(JobSearchSession.completed_at.desc()).limit(10).all() if " ".join(row.raw_query.lower().split()) == normalized_query and (row.structured_intent or {}).get("freshness") == freshness and (row.structured_intent or {}).get("sources", ["official", "ats", "wellfound", "linkedin"]) == requested_sources and (row.structured_intent or {}).get("search_mode", "intent") == search_mode), None)
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
    intent = complete_intent(intent or parse_intent_locally(query, profile.data), profile.data, sources, search_mode)
    intent["raw_query"] = query if search_mode == "boolean" else ""
    queries = generate_queries(intent)
    if freshness:
        intent = {**intent, "freshness": freshness}
    session = JobSearchSession(id=str(uuid.uuid4()), user_id=user_id, search_type=search_type, raw_query=query, structured_intent=intent, search_queries=queries, status="searching", progress={"queries_generated": len(queries), "queries_completed": 0, "companies_discovered": 0, "companies_processed": 0, "jobs_discovered": 0, "jobs_evaluated": 0, "opportunities_created": 0, "providers_used": [], "provider_attempts": [], "failures": 0, "processing_errors": []})
    db.add(session); db.commit()
    provider = get_search_provider()
    discovered = []
    provider_attempts = []
    providers_used = []
    for search_query in queries:
        discovered.extend(await provider.search(search_query, limit=settings.max_search_results_per_query, freshness=freshness))
        attempts = getattr(provider, "last_attempts", [])
        provider_attempts.extend([{**attempt, "query": search_query, "timestamp": datetime.utcnow().isoformat()} for attempt in attempts])
        used = getattr(provider, "last_provider", None) or getattr(provider, "name", None)
        if used and used not in providers_used: providers_used.append(used)
        session.progress = {**session.progress, "queries_completed": session.progress["queries_completed"] + 1, "providers_used": providers_used, "provider_attempts": provider_attempts, "failures": len([attempt for attempt in provider_attempts if attempt["status"] == "error"])}
        db.commit()
    discovered = await _expand_and_enrich(discovered, intent) if search_type == "jobs" else discovered
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
            row = {"company": {"name": domain.split(".")[0].replace("-", " ").title(), "website": f"https://{domain}", "domain": domain, "description": content[:1000], "source_urls": [result_url]}, "job": {"raw_title": raw_title, "title": raw_title, "description": content[:5000], "location": None, "remote_type": None, "employment_type": None, "experience_min": minimum, "experience_max": maximum, "skills": intent.get("skills", []), "seniority": parse_seniority(raw_title), "job_url": result_url, "source_url": result_url, "source_type": row.get("source") or settings.search_provider, "status": "unknown", "published_at": row.get("published_at")}}
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
    session.status = "completed"; session.results_count = len(results) if search_type == "jobs" else len(companies); session.completed_at = datetime.utcnow(); session.progress = {"queries_generated": len(queries), "queries_completed": len(queries), "companies_discovered": len(companies), "companies_processed": len(companies), "jobs_discovered": len(jobs), "jobs_evaluated": len(results), "opportunities_created": len(results), "providers_used": providers_used, "provider_attempts": provider_attempts, "failures": len([attempt for attempt in provider_attempts if attempt["status"] == "error"]), "processing_errors": []}
    db.commit()
    return session
