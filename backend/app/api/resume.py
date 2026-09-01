import uuid
import os
import re
from typing import Optional
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session
from ..config import settings
from ..database import get_db
from ..models import Resume, CandidateProfile, Artifact, User
from ..auth import enforce_user, get_current_user
from ..schemas.core import AnalyzeRequest, ResumeRewriteBody, RewriteRequest
from ..services.resume_parser import extract_pdf
from ..services.gemini_provider import GeminiProvider
from ..services.completeness_service import calculate
from ..services.embedding_service import EmbeddingService
from .utils import fail, normalize_analysis, normalize_profile, normalize_rewrite, ok

router = APIRouter(tags=["resumes"])
ai = GeminiProvider()


@router.post(
    "/api/v1/resumes/upload",
    summary="Upload and process a PDF resume",
    description="Validates a PDF resume, extracts text, creates or replaces the candidate profile, and refreshes vector embeddings.",
)
@router.post("/api/resume/upload", include_in_schema=False)
async def upload(
    file: UploadFile = File(...), user_id: str = "", db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    user_id = user_id or current_user.id; enforce_user(user_id, current_user)
    if file.content_type not in {"application/pdf", "application/x-pdf"} and not (
        file.filename or ""
    ).lower().endswith(".pdf"):
        fail(400, "INVALID_FILE_TYPE", "Only PDF resumes are accepted.", "file")

    content = await file.read()
    if len(content) > settings.max_resume_size_mb * 1024 * 1024:
        fail(413, "FILE_TOO_LARGE", "Resume exceeds file-size limit.", "file")
    if not content.startswith(b"%PDF"):
        fail(400, "INVALID_PDF", "The uploaded file is not a readable PDF.", "file")

    try:
        parsed = extract_pdf(content)
    except ValueError as exc:
        code = "EMPTY_PDF" if "No useful selectable text" in str(exc) else "PDF_EXTRACTION_FAILED"
        fail(422, code, str(exc), "file")

    resume = Resume(
        id=str(uuid.uuid4()),
        user_id=user_id,
        original_filename=re.sub(r"[^A-Za-z0-9._ -]", "_", os.path.basename(file.filename or "resume.pdf"))[:255] or "resume.pdf",
        extracted_text=parsed["text"],
    )
    db.add(resume)
    db.flush()

    try:
        profile_data = await ai.extract_resume(parsed["text"])
    except Exception:
        fail(502, "AI_EXTRACTION_FAILED", "Resume text was extracted, but AI profile extraction failed.", None)

    profile = db.get(CandidateProfile, user_id)
    if profile:
        profile.resume_id = resume.id
        profile.data = profile_data
    else:
        profile = CandidateProfile(
            user_id=user_id, resume_id=resume.id, data=profile_data
        )
        db.add(profile)

    try:
        db.commit()
    except Exception:
        db.rollback()
        fail(500, "PROFILE_CREATION_FAILED", "Profile could not be saved.", None)

    EmbeddingService().upsert_profile(user_id, resume.id, profile_data)

    return ok(
        "Resume uploaded and processed successfully",
        {
            "resume": {
                "resume_id": resume.id,
                "filename": resume.original_filename,
                "file_type": file.content_type or "application/pdf",
                "status": "processed",
                "pages": parsed["pages"],
                "characters_extracted": parsed["characters_extracted"],
            },
            "user_id": user_id,
            "profile": normalize_profile(profile_data),
            "completeness": calculate(profile_data),
        },
        events=[
            {"type": "RESUME_UPLOADED", "label": "Resume uploaded"},
            {"type": "RESUME_PARSED", "label": "Resume parsed successfully"},
            {"type": "PROFILE_CREATED", "label": "Candidate profile created"},
            {"type": "EMBEDDINGS_UPDATED", "label": "Resume embeddings updated"},
        ],
    )


@router.post("/api/v1/resumes/{user_id}/analysis", summary="Analyze a user's resume")
@router.post("/api/resume/analyze", include_in_schema=False)
async def analyze(user_id: str = "", body: Optional[AnalyzeRequest] = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    resolved_user_id = user_id or (body.user_id if body else "")
    resolved_user_id = resolved_user_id or current_user.id; enforce_user(resolved_user_id, current_user)
    profile = db.get(CandidateProfile, resolved_user_id)
    if not profile:
        fail(404, "PROFILE_NOT_FOUND", "Candidate profile not found.", "user_id")

    resume = (
        db.query(Resume)
        .filter(Resume.id == profile.resume_id)
        .first()
    )
    if not resume:
        fail(404, "RESUME_NOT_FOUND", "Original resume not found.", "user_id")

    analysis = await ai.analyze_resume(profile.data, resume.extracted_text)
    normalized = normalize_analysis(analysis)

    artifact = Artifact(
        id=str(uuid.uuid4()),
        user_id=resolved_user_id,
        kind="resume_analysis",
        data=normalized,
    )
    db.add(artifact)
    db.commit()

    return ok(
        "Resume analysis created successfully",
        {"analysis": normalized},
        events=[{"type": "RESUME_ANALYZED", "label": "Resume analyzed"}],
    )


@router.post("/api/v1/resumes/{user_id}/rewrite", summary="Rewrite a user's resume for a target role")
@router.post("/api/resume/rewrite", include_in_schema=False)
async def rewrite(user_id: str = "", body: Optional[ResumeRewriteBody] = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    user_id = user_id or (body.user_id if body else "")
    resolved_user_id = user_id or (body.user_id if body else "")
    target_role = body.target_role if body else None
    resolved_user_id = resolved_user_id or current_user.id; enforce_user(resolved_user_id, current_user)
    profile = db.get(CandidateProfile, resolved_user_id)
    if not profile:
        fail(404, "PROFILE_NOT_FOUND", "Candidate profile not found.", "user_id")

    resume = (
        db.query(Resume)
        .filter(Resume.id == profile.resume_id)
        .first()
    )
    if not resume:
        fail(404, "RESUME_NOT_FOUND", "Original resume not found.", "user_id")

    rewritten = await ai.rewrite_resume(
        profile.data, resume.extracted_text, target_role
    )
    normalized = normalize_rewrite(target_role, rewritten, profile.data)

    artifact = Artifact(
        id=str(uuid.uuid4()),
        user_id=resolved_user_id,
        kind="rewritten_resume",
        data=normalized,
    )
    db.add(artifact)
    db.commit()

    return ok(
        "Resume rewrite created successfully",
        {"rewrite": normalized},
        events=[{"type": "RESUME_REWRITTEN", "label": "Resume rewritten"}],
    )
