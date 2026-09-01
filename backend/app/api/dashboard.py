from fastapi import APIRouter, Depends
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Application, Artifact, CandidateProfile, GoogleConnection, InterviewTurn, Opportunity, Outreach, Resume, User
from ..auth import enforce_user, get_current_user
from ..services.completeness_service import calculate
from .utils import MAX_INTERVIEW_QUESTIONS, normalize_profile, normalize_question, ok

router = APIRouter(tags=["dashboard"])


@router.get("/api/v1/dashboard", summary="Load authenticated frontend dashboard state")
@router.get("/api/v1/dashboard/{user_id}", summary="Load frontend dashboard state")
def dashboard(user_id: str = "", db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    user_id = user_id or current_user.id; enforce_user(user_id, current_user)
    profile = db.get(CandidateProfile, user_id)
    resume = None
    if profile and profile.resume_id:
        resume = db.get(Resume, profile.resume_id)

    completeness = calculate(profile.data) if profile else {
        "overall": 0,
        "status": "needs_resume",
        "categories": [],
        "strongest_category": None,
        "weakest_category": None,
        "missing_information": [],
        "next_priority": None,
    }

    turns = (
        db.query(InterviewTurn)
        .filter(InterviewTurn.user_id == user_id)
        .order_by(InterviewTurn.created_at)
        .all()
    )
    answered = len([turn for turn in turns if turn.answer])
    current_question = next((turn for turn in reversed(turns) if not turn.answer), None)

    rec_artifact = (
        db.query(Artifact)
        .filter(Artifact.user_id == user_id, Artifact.kind == "role_recommendations")
        .order_by(desc(Artifact.created_at))
        .first()
    )
    analysis_artifact = (
        db.query(Artifact)
        .filter(Artifact.user_id == user_id, Artifact.kind == "resume_analysis")
        .order_by(desc(Artifact.created_at))
        .first()
    )

    identity = normalize_profile(profile.data)["identity"] if profile else {}
    data = {
        "user": {"id": user_id, "name": identity.get("name", "")},
        "resume": {
            "uploaded": bool(resume),
            "resume_id": resume.id if resume else None,
            "filename": resume.original_filename if resume else "",
        },
        "profile": normalize_profile(profile.data) if profile else normalize_profile({}),
        "completeness": completeness,
        "interview": {
            "started": bool(turns),
            "completed": completeness["overall"] >= 90 or answered >= MAX_INTERVIEW_QUESTIONS,
            "questions_answered": answered,
            "max_questions": MAX_INTERVIEW_QUESTIONS,
            "current_question": normalize_question(current_question),
        },
        "recommendations": {
            "available": bool(rec_artifact),
            "roles": (rec_artifact.data or {}).get("roles", []) if rec_artifact else [],
        },
        "resume_analysis": {
            "available": bool(analysis_artifact),
            "analysis": analysis_artifact.data if analysis_artifact else None,
        },
        "recent_opportunities": [{"id": row.id, "fit_score": row.final_fit_score, "status": row.status} for row in db.query(Opportunity).filter(Opportunity.user_id == user_id).order_by(Opportunity.created_at.desc()).limit(5).all()],
        "application_summary": {status: count for status, count in db.query(Application.status, func.count(Application.id)).filter(Application.user_id == user_id).group_by(Application.status).all()},
        "google_connection": {"connected": bool((connection := db.get(GoogleConnection, user_id)) and connection.status == "active"), "status": connection.status if connection else "disconnected"},
        "outreach_summary": {status: count for status, count in db.query(Outreach.status, func.count(Outreach.id)).filter(Outreach.user_id == user_id).group_by(Outreach.status).all()},
    }
    return ok("Dashboard loaded successfully", data)
