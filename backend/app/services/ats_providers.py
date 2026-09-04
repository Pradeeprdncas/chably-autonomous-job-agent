from __future__ import annotations

import re
from abc import ABC, abstractmethod
from urllib.parse import urlparse

import httpx

from ..config import settings


class ATSProvider(ABC):
    name: str

    @abstractmethod
    def detect(self, url: str, html: str = "") -> bool: ...

    @abstractmethod
    async def list_jobs(self, board_url: str) -> list[dict]: ...

    async def get_job(self, board_url: str, job_id: str) -> dict | None:
        return next((job for job in await self.list_jobs(board_url) if str(job.get("ats_id")) == str(job_id)), None)

    async def _get_json(self, url: str):
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": "ChablyBot/1.0"})
            response.raise_for_status()
            return response.json()


class GreenhouseProvider(ATSProvider):
    name = "greenhouse"

    def detect(self, url: str, html: str = "") -> bool:
        return "greenhouse.io" in f"{url} {html}".lower()

    async def list_jobs(self, board_url: str) -> list[dict]:
        match = re.search(r"(?:boards|job-boards)\.greenhouse\.io/([^/?#]+)", board_url)
        if not match: raise ValueError("INVALID_GREENHOUSE_BOARD")
        data = await self._get_json(f"https://boards-api.greenhouse.io/v1/boards/{match.group(1)}/jobs?content=true")
        return [self.normalize(job) for job in data.get("jobs", [])]

    def normalize(self, job: dict) -> dict:
        return {"ats_id": str(job.get("id")), "raw_title": job.get("title", ""), "title": job.get("title", ""), "location": (job.get("location") or {}).get("name"), "description": job.get("content", ""), "departments": [x.get("name") for x in job.get("departments", [])], "job_url": job.get("absolute_url", ""), "source_url": job.get("absolute_url", ""), "source_type": "greenhouse", "posted_date": job.get("updated_at"), "status": "open"}


class LeverProvider(ATSProvider):
    name = "lever"

    def detect(self, url: str, html: str = "") -> bool:
        return "lever.co" in f"{url} {html}".lower()

    async def list_jobs(self, board_url: str) -> list[dict]:
        match = re.search(r"jobs\.lever\.co/([^/?#]+)", board_url)
        if not match: raise ValueError("INVALID_LEVER_BOARD")
        data = await self._get_json(f"https://api.lever.co/v0/postings/{match.group(1)}?mode=json")
        return [self.normalize(job) for job in data]

    def normalize(self, job: dict) -> dict:
        categories = job.get("categories") or {}
        return {"ats_id": str(job.get("id")), "raw_title": job.get("text", ""), "title": job.get("text", ""), "location": categories.get("location"), "remote_type": job.get("workplaceType"), "employment_type": categories.get("commitment"), "description": job.get("descriptionPlain") or job.get("description", ""), "categories": categories, "job_url": job.get("hostedUrl", ""), "source_url": job.get("hostedUrl", ""), "source_type": "lever", "status": "open"}


class AshbyProvider(ATSProvider):
    name = "ashby"

    def detect(self, url: str, html: str = "") -> bool:
        return "ashbyhq.com" in f"{url} {html}".lower()

    async def list_jobs(self, board_url: str) -> list[dict]:
        match = re.search(r"jobs\.ashbyhq\.com/([^/?#]+)", board_url)
        if not match: raise ValueError("INVALID_ASHBY_BOARD")
        data = await self._get_json(f"https://api.ashbyhq.com/posting-api/job-board/{match.group(1)}")
        return [self.normalize(job) for job in data.get("jobs", [])]

    def normalize(self, job: dict) -> dict:
        return {"ats_id": str(job.get("id")), "raw_title": job.get("title", ""), "title": job.get("title", ""), "location": job.get("location"), "remote_type": "remote" if job.get("isRemote") else None, "employment_type": job.get("employmentType"), "description": job.get("descriptionPlain") or job.get("descriptionHtml", ""), "job_url": job.get("jobUrl") or job.get("applyUrl", ""), "source_url": job.get("jobUrl") or job.get("applyUrl", ""), "source_type": "ashby", "posted_date": job.get("publishedAt"), "status": "open"}


ATS_PROVIDERS = [GreenhouseProvider(), LeverProvider(), AshbyProvider()]


def provider_for(url: str, html: str = "") -> ATSProvider | None:
    return next((provider for provider in ATS_PROVIDERS if provider.detect(url, html)), None)


class BrowserFetcher:
    async def fetch_rendered(self, url: str) -> str:
        if not settings.browser_fetch_enabled:
            raise RuntimeError("BROWSER_FETCH_DISABLED")
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("BROWSER_PROVIDER_UNAVAILABLE") from exc
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page(user_agent="ChablyBot/1.0")
            try:
                await page.goto(url, wait_until="networkidle", timeout=settings.browser_timeout_seconds * 1000)
                return await page.content()
            finally:
                await browser.close()


def appears_javascript_only(html: str, meaningful_text: str) -> bool:
    lower = html.lower()
    return len(meaningful_text.strip()) < 200 and ("enable javascript" in lower or "id=\"root\"" in lower or "id=\"app\"" in lower or lower.count("<script") >= 5)


def classify_url(url: str) -> str:
    value = url.lower(); parsed = urlparse(url); host = parsed.hostname or ""; path = parsed.path.rstrip("/")
    # These are third-party listing/search sites rather than canonical employer
    # postings.  Keep the explicitly supported public job boards below, but do
    # not turn search-result landing pages into opportunities.
    if any(domain in host for domain in ("naukri.com", "foundit.", "cutshort.io", "remoterocketship.com", "wantremote.com", "glassdoor.")):
        return "irrelevant"
    if "linkedin.com" in host:
        return "job_page" if re.search(r"/jobs/view/(?:[^/]*-)?\d+", path) else "irrelevant"
    if "wellfound.com" in host:
        return "job_page" if re.search(r"/jobs/\d+(?:-|/|$)", path) else "irrelevant"
    if "indeed.com" in host:
        return "job_page" if path.endswith("/viewjob") and "jk=" in parsed.query else "irrelevant"
    if "lever.co" in host:
        return "job_page" if len([part for part in path.split("/") if part]) >= 2 else "ats_page"
    if "greenhouse.io" in host:
        return "job_page" if re.search(r"/jobs/\d+", path) or "gh_jid=" in parsed.query else "ats_page"
    if "ashbyhq.com" in host:
        return "job_page" if len([part for part in path.split("/") if part]) >= 2 else "ats_page"
    if re.search(r"/(jobs?|positions?|openings?)/(?:[^/]+/)*[^/]*\d[^/]*", path): return "job_page"
    if re.search(r"/job/(?:[^/]+/)*[^/]+", path): return "job_page"
    if any(term in value for term in ("/careers", "/jobs", "/join-us", "/openings")): return "company_careers"
    if "/about" in value: return "company_about"
    if any(term in value for term in ("/product", "/platform", "/services")): return "company_product"
    if urlparse(url).path in ("", "/"): return "company_homepage"
    if "wikipedia.org" in host: return "irrelevant"
    return "irrelevant"
