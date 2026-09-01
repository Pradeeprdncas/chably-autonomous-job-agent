from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import CandidateProfile, Artifact
from ..models import User
from ..auth import enforce_user, get_current_user
from ..services.gemini_provider import GeminiProvider
from ..services.completeness_service import calculate
from ..services.embedding_service import EmbeddingService
from ..data.job_taxonomy import ROLES
from .utils import fail, normalize_role, ok
import uuid

router = APIRouter(tags=["recommendations"])
ai = GeminiProvider()


@router.get("/api/v1/roles/{user_id}/recommendations", summary="Get role recommendations")
@router.get("/api/roles/{user_id}/recommendations", include_in_schema=False)
@router.post("/api/roles/{user_id}/recommendations", include_in_schema=False)
async def get_recommendations(user_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user(user_id, current_user)
    profile = db.get(CandidateProfile, user_id)
    if not profile:
        fail(404, "PROFILE_NOT_FOUND", "Candidate profile not found.", "user_id")

    completeness = calculate(profile.data)
    if completeness["overall"] < 30:
        fail(
            400,
            "PROFILE_TOO_SPARSE",
            "Profile is too sparse for meaningful recommendations. Complete the interview first.",
            "user_id",
        )

    embedding_service = EmbeddingService()
    try:
        candidate_roles = embedding_service.find_similar_roles(profile.data, ROLES)
    except Exception:
        # Keep recommendations available when the optional local vector index is
        # rebuilding or unavailable; Gemini also receives the full profile.
        candidate_roles = ROLES[:10]

    recommendations = await ai.recommend_roles(profile.data, candidate_roles)
    normalized = [normalize_role(role) for role in recommendations if isinstance(role, dict)]

    artifact = Artifact(
        id=str(uuid.uuid4()),
        user_id=user_id,
        kind="role_recommendations",
        data={"roles": normalized},
    )
    db.add(artifact)
    db.commit()

    return ok(
        "Role recommendations created successfully",
        {"roles": normalized, "completeness": completeness},
        events=[
            {
                "type": "ROLE_RECOMMENDATIONS_CREATED",
                "label": "Role recommendations created",
            }
        ],
    )
