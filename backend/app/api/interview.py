import uuid
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import CandidateProfile, InterviewTurn, User
from ..auth import enforce_user, get_current_user
from ..schemas.core import InterviewAnswerBody
from ..services.gemini_provider import GeminiProvider
from ..services.completeness_service import calculate
from ..services.profile_service import merge
from ..services.embedding_service import EmbeddingService
from .utils import MAX_INTERVIEW_QUESTIONS, fail, normalize_profile, normalize_question, ok

router = APIRouter(tags=["interview"])
ai = GeminiProvider()


def _turns(db: Session, user_id: str):
    return (
        db.query(InterviewTurn)
        .filter(InterviewTurn.user_id == user_id)
        .order_by(InterviewTurn.created_at)
        .all()
    )


async def _create_question(db: Session, user_id: str, profile: CandidateProfile, completeness: dict):
    turns = _turns(db, user_id)
    previous = [
        {"question": t.question, "answer": t.answer, "target_category": t.target_category}
        for t in turns
    ]
    question = await ai.generate_question(profile.data, completeness, previous)
    turn = InterviewTurn(
        id=str(uuid.uuid4()),
        user_id=user_id,
        question=question["question"],
        answer=None,
        target_category=question.get("target_category", "general"),
        target_fields=question.get("target_fields", []),
        reason=question.get("reason", ""),
        score_before=completeness["overall"],
        score_after=None,
    )
    db.add(turn)
    db.commit()
    return turn


@router.post("/api/v1/interview/{user_id}/start", summary="Start or continue the adaptive interview")
@router.post("/api/interview/start", include_in_schema=False)
async def start(user_id: str = "", db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    user_id = user_id or current_user.id; enforce_user(user_id, current_user)
    profile = db.get(CandidateProfile, user_id)
    if not profile:
        fail(404, "PROFILE_NOT_FOUND", "Candidate profile not found. Upload a resume first.", "user_id")

    completeness = calculate(profile.data)
    turns = _turns(db, user_id)
    answered_count = len([turn for turn in turns if turn.answer])
    current = next((turn for turn in reversed(turns) if not turn.answer), None)

    if completeness["overall"] >= 90 or answered_count >= MAX_INTERVIEW_QUESTIONS:
        return ok(
            "Interview complete",
            {
                "status": "complete",
                "question": None,
                "progress": {
                    "overall": completeness["overall"],
                    "questions_answered": answered_count,
                    "max_questions": MAX_INTERVIEW_QUESTIONS,
                },
            },
            events=[{"type": "PROFILE_COMPLETED", "label": "Career profile completed"}],
        )

    if not current:
        current = await _create_question(db, user_id, profile, completeness)

    return ok(
        "Interview question ready",
        {
            "status": "interviewing",
            "question": normalize_question(current),
            "progress": {
                "overall": completeness["overall"],
                "questions_answered": answered_count,
                "max_questions": MAX_INTERVIEW_QUESTIONS,
            },
        },
        events=[{"type": "QUESTION_GENERATED", "label": "Interview question generated"}],
    )


@router.post("/api/v1/interview/{user_id}/answer", summary="Answer the current adaptive interview question")
@router.post("/api/interview/answer", include_in_schema=False)
async def answer(user_id: str, body: InterviewAnswerBody, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user(user_id, current_user)
    turn = db.get(InterviewTurn, body.question_id)
    if not turn:
        fail(404, "QUESTION_NOT_FOUND", "Question not found.", "question_id")
    if turn.user_id != user_id:
        fail(404, "QUESTION_NOT_FOUND", "Question not found.", "question_id")
    if turn.answer is not None:
        fail(400, "QUESTION_ALREADY_ANSWERED", "This question has already been answered.", "question_id")

    profile = db.get(CandidateProfile, user_id)
    if not profile:
        fail(404, "PROFILE_NOT_FOUND", "Candidate profile not found.", "user_id")

    score_before = calculate(profile.data)["overall"]

    question_context = {
        "question": turn.question,
        "target_category": turn.target_category,
        "target_fields": turn.target_fields,
        "reason": turn.reason,
    }

    patch = await ai.process_answer(profile.data, question_context, body.answer)
    profile.data = merge(profile.data, patch)
    db.commit()

    score_after = calculate(profile.data)["overall"]

    turn.answer = body.answer
    turn.score_before = score_before
    turn.score_after = score_after
    db.commit()

    EmbeddingService().upsert_profile(user_id, profile.resume_id, profile.data)
    completeness = calculate(profile.data)
    answered_count = len([t for t in _turns(db, user_id) if t.answer])
    complete = completeness["overall"] >= 90 or answered_count >= MAX_INTERVIEW_QUESTIONS
    next_turn = None if complete else await _create_question(db, user_id, profile, completeness)
    events = [
        {"type": "ANSWER_PROCESSED", "label": "Interview answer processed"},
        {"type": "PROFILE_UPDATED", "label": "Candidate profile updated"},
        {"type": "EMBEDDINGS_UPDATED", "label": "Profile embeddings updated"},
    ]
    if score_after != score_before:
        events.append(
            {
                "type": "COMPLETENESS_CHANGED",
                "label": "Profile completeness changed",
                "previous": score_before,
                "current": score_after,
            }
        )
    if complete:
        events.append({"type": "PROFILE_COMPLETED", "label": "Career profile completed"})

    return ok(
        "Interview answer processed successfully",
        {
            "status": "complete" if complete else "interviewing",
            "profile_updates": [{"field": key, "value": value} for key, value in (patch or {}).items()],
            "previous_score": score_before,
            "current_score": score_after,
            "next_question": normalize_question(next_turn),
            "profile": normalize_profile(profile.data),
            "message": "Your career profile is ready." if complete else "",
        },
        events=events,
    )


@router.get("/api/v1/interview/{user_id}/history", summary="Get interview chat history")
@router.get("/api/interview/{user_id}/history", include_in_schema=False)
def history(user_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user(user_id, current_user)
    messages = []
    for t in _turns(db, user_id):
        created_at = t.created_at.isoformat() if t.created_at else None
        messages.append(
            {
                "id": f"{t.id}:question",
                "question_id": t.id,
                "role": "assistant",
                "type": "question",
                "content": t.question,
                "target_category": t.target_category,
                "created_at": created_at,
            }
        )
        if t.answer:
            messages.append(
                {
                    "id": f"{t.id}:answer",
                    "question_id": t.id,
                    "role": "user",
                    "type": "answer",
                    "content": t.answer,
                    "created_at": created_at,
                }
            )
    return ok("Interview history loaded successfully", {"messages": messages})
