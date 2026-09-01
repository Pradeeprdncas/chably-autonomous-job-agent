import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Company, Job, JobSearchSession, Opportunity, SavedJob
from ..models import User
from ..auth import enforce_user, get_current_user
from ..schemas.core import DiscoveryRequest, OpportunityStatusRequest, SavedJobRequest
from ..services.job_discovery import company_payload, execute_search, job_payload
from .utils import fail, ok

router = APIRouter(tags=["discovery"])


def result_payload(db: Session, session: JobSearchSession):
    opportunities = db.query(Opportunity).filter(Opportunity.search_session_id == session.id).order_by(Opportunity.final_fit_score.desc()).all()
    results = []
    for rank, opportunity in enumerate(opportunities, start=1):
        job = db.get(Job, opportunity.job_id); company = db.get(Company, opportunity.company_id)
        if job and company:
            results.append({"rank": rank, "opportunity_id": opportunity.id, "job": job_payload(job), "company": company_payload(company), "match": opportunity.analysis, "status": opportunity.status})
    return {"search_id": session.id, "query": session.raw_query, "search_type": session.search_type, "intent": session.structured_intent, "search_queries": session.search_queries, "status": session.status, "progress": session.progress, "results": results, "results_count": session.results_count}


@router.post("/api/v1/job-search", summary="Discover and rank jobs")
async def job_search(body: DiscoveryRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user(body.user_id, current_user)
    try:
        session = await execute_search(db, body.user_id, body.query, "jobs")
    except LookupError:
        fail(404, "PROFILE_NOT_FOUND", "Candidate profile not found.", "user_id")
    except RuntimeError as exc:
        fail(503, str(exc), "Search provider is unavailable.")
    return ok("Job search completed", result_payload(db, session), events=[{"type": "JOB_SEARCH_STARTED", "label": "Job search started"}, {"type": "SEARCH_QUERY_GENERATED", "label": "Search queries generated"}, {"type": "JOB_SEARCH_COMPLETED", "label": "Job search completed"}])


@router.get("/api/v1/job-search/{search_id}", summary="Get persisted job search results")
def get_search(search_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    session = db.get(JobSearchSession, search_id)
    if not session or session.user_id != current_user.id: fail(404, "SEARCH_NOT_FOUND", "Search session not found.", "search_id")
    return ok("Search results loaded", result_payload(db, session))


@router.get("/api/v1/job-search/{search_id}/progress", summary="Get search progress")
def get_progress(search_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    session = db.get(JobSearchSession, search_id)
    if not session or session.user_id != current_user.id: fail(404, "SEARCH_NOT_FOUND", "Search session not found.", "search_id")
    return ok("Search progress loaded", {"search_id": session.id, "status": session.status, "progress": session.progress})


@router.post("/api/v1/company-search", summary="Discover candidate-relevant companies")
async def company_search(body: DiscoveryRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user(body.user_id, current_user)
    try:
        session = await execute_search(db, body.user_id, body.query, "companies")
    except LookupError:
        fail(404, "PROFILE_NOT_FOUND", "Candidate profile not found.", "user_id")
    except RuntimeError as exc:
        fail(503, str(exc), "Search provider is unavailable.")
    companies = db.query(Company).order_by(Company.last_checked_at.desc()).limit(session.results_count).all()
    return ok("Company search completed", {"search_id": session.id, "query": session.raw_query, "intent": session.structured_intent, "companies": [{"company": company_payload(c), "relevance": {"score": 75, "why_relevant": ["Matches the requested company domain."], "matching_domains": session.structured_intent.get("domains", []), "matching_skills": []}, "open_jobs": [job_payload(j) for j in db.query(Job).filter(Job.company_id == c.id, Job.status == "open").all()]} for c in companies]})


@router.get("/api/v1/users/{user_id}/search-history", summary="Get job and company search history")
def search_history(user_id: str, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user(user_id, current_user)
    sessions = db.query(JobSearchSession).filter(JobSearchSession.user_id == user_id).order_by(JobSearchSession.started_at.desc()).offset(offset).limit(limit).all()
    return ok("Search history loaded", {"searches": [{"id": s.id, "type": s.search_type, "query": s.raw_query, "status": s.status, "results_count": s.results_count, "started_at": s.started_at.isoformat(), "completed_at": s.completed_at.isoformat() if s.completed_at else None} for s in sessions]})


@router.post("/api/v1/jobs/{job_id}/save", summary="Save or classify a job")
def save_job(job_id: str, body: SavedJobRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user(body.user_id, current_user)
    if body.status not in {"saved", "interested", "not_interested", "later"}: fail(400, "INVALID_SAVED_JOB_STATUS", "Invalid saved-job status.", "status")
    if not db.get(Job, job_id): fail(404, "JOB_NOT_FOUND", "Job not found.", "job_id")
    saved = db.query(SavedJob).filter(SavedJob.user_id == body.user_id, SavedJob.job_id == job_id).first()
    if not saved: saved = SavedJob(id=str(uuid.uuid4()), user_id=body.user_id, job_id=job_id); db.add(saved)
    saved.status = body.status; saved.notes = body.notes; db.commit()
    return ok("Job preference saved", {"id": saved.id, "job_id": job_id, "status": saved.status, "notes": saved.notes})


@router.patch("/api/v1/opportunities/{opportunity_id}", summary="Update opportunity status")
def update_opportunity(opportunity_id: str, body: OpportunityStatusRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if body.status not in {"discovered", "reviewing", "saved", "ignored"}: fail(400, "INVALID_OPPORTUNITY_STATUS", "Invalid opportunity status.", "status")
    opportunity = db.get(Opportunity, opportunity_id)
    if not opportunity or opportunity.user_id != current_user.id: fail(404, "OPPORTUNITY_NOT_FOUND", "Opportunity not found.", "opportunity_id")
    opportunity.status = body.status; db.commit()
    return ok("Opportunity updated", {"id": opportunity.id, "status": opportunity.status})


@router.get("/api/v1/jobs/{job_id}", summary="Get normalized job details")
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job: fail(404, "JOB_NOT_FOUND", "Job not found.", "job_id")
    return ok("Job loaded", {"job": job_payload(job), "company": company_payload(db.get(Company, job.company_id))})


@router.get("/api/v1/companies/{company_id}", summary="Get company intelligence and open jobs")
def get_company(company_id: str, db: Session = Depends(get_db)):
    company = db.get(Company, company_id)
    if not company: fail(404, "COMPANY_NOT_FOUND", "Company not found.", "company_id")
    jobs = db.query(Job).filter(Job.company_id == company_id, Job.status != "closed").all()
    return ok("Company loaded", {"company": company_payload(company), "open_jobs": [job_payload(job) for job in jobs]})


@router.get("/api/v1/opportunities/{opportunity_id}", summary="Get an opportunity")
def get_opportunity(opportunity_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    opportunity = db.get(Opportunity, opportunity_id)
    if not opportunity or opportunity.user_id != current_user.id: fail(404, "OPPORTUNITY_NOT_FOUND", "Opportunity not found.", "opportunity_id")
    job = db.get(Job, opportunity.job_id); company = db.get(Company, opportunity.company_id)
    return ok("Opportunity loaded", {"id": opportunity.id, "status": opportunity.status, "fit_score": opportunity.final_fit_score, "analysis": opportunity.analysis, "job": job_payload(job), "company": company_payload(company)})


@router.get("/api/v1/users/{user_id}/opportunities", summary="List and filter user opportunities")
def list_opportunities(user_id: str, minimum_fit: float = Query(0, ge=0, le=100), status: str = "", company: str = "", role: str = "", location: str = "", limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user(user_id, current_user)
    opportunities = db.query(Opportunity).filter(Opportunity.user_id == user_id, Opportunity.final_fit_score >= minimum_fit).order_by(Opportunity.final_fit_score.desc()).offset(offset).limit(limit).all()
    output = []
    for opportunity in opportunities:
        job = db.get(Job, opportunity.job_id); company_row = db.get(Company, opportunity.company_id)
        if status and opportunity.status != status: continue
        if company and company.lower() not in company_row.name.lower(): continue
        if role and role.lower() not in job.title.lower(): continue
        if location and location.lower() not in (job.location or "").lower(): continue
        output.append({"id": opportunity.id, "rank": len(output) + 1, "status": opportunity.status, "fit_score": opportunity.final_fit_score, "fit_level": (opportunity.analysis or {}).get("fit_level"), "job": job_payload(job), "company": company_payload(company_row)})
    return ok("Opportunities loaded", {"opportunities": output})
