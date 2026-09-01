from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import CandidateProfile
from ..models import User
from ..auth import enforce_user, get_current_user
from ..schemas.core import ProfilePatch
from ..services.profile_service import merge
from ..services.completeness_service import calculate
from ..services.embedding_service import EmbeddingService
from .utils import normalize_profile, ok, to_internal_profile

router = APIRouter(tags=["profile"])


def get_profile(user_id, db):
    p = db.get(CandidateProfile, user_id)
    if not p:
        raise HTTPException(
            404,
            {
                "success": False,
                "message": "Candidate profile not found",
                "data": None,
                "meta": {},
                "errors": [
                    {
                        "code": "PROFILE_NOT_FOUND",
                        "field": "user_id",
                        "message": "Upload a resume before loading the profile.",
                    }
                ],
            },
        )
    return p


@router.get("/api/v1/profile/me", summary="Get authenticated candidate profile")
def read_me(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    profile = get_profile(current_user.id, db)
    return ok("Profile loaded successfully", {"profile": normalize_profile(profile.data), "user_id": current_user.id})


@router.patch("/api/v1/profile/me", summary="Update authenticated candidate profile")
def patch_me(request: ProfilePatch, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    profile = get_profile(current_user.id, db); profile.data = merge(profile.data, to_internal_profile(request.data)); db.commit(); EmbeddingService().upsert_profile(current_user.id, profile.resume_id, profile.data)
    return ok("Profile updated successfully", {"profile": normalize_profile(profile.data), "completeness": calculate(profile.data)})


@router.get("/api/v1/profile/me/completeness", summary="Get authenticated profile completeness")
def completeness_me(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return ok("Profile completeness loaded successfully", {"completeness": calculate(get_profile(current_user.id, db).data)})


@router.get("/api/v1/profile/{user_id}", summary="Get candidate profile")
@router.get("/api/profile/{user_id}", include_in_schema=False)
def read(user_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user(user_id, current_user)
    profile = get_profile(user_id, db)
    return ok(
        "Profile loaded successfully",
        {"profile": normalize_profile(profile.data), "user_id": user_id},
    )


@router.patch("/api/v1/profile/{user_id}", summary="Update candidate profile")
@router.patch("/api/profile/{user_id}", include_in_schema=False)
def patch(user_id: str, request: ProfilePatch, db: Session=Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user(user_id, current_user)
    p = get_profile(user_id, db)
    p.data = merge(p.data, to_internal_profile(request.data))
    db.commit()
    EmbeddingService().upsert_profile(user_id, p.resume_id, p.data)
    return ok(
        "Profile updated successfully",
        {"profile": normalize_profile(p.data), "completeness": calculate(p.data)},
        events=[
            {"type": "PROFILE_EDITED", "label": "Profile edited"},
            {"type": "EMBEDDINGS_UPDATED", "label": "Profile embeddings updated"},
        ],
    )


@router.get("/api/v1/profile/{user_id}/completeness", summary="Get profile completeness")
@router.get("/api/profile/{user_id}/completeness", include_in_schema=False)
def completeness(user_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user(user_id, current_user)
    return ok(
        "Profile completeness loaded successfully",
        {"completeness": calculate(get_profile(user_id, db).data)},
    )
