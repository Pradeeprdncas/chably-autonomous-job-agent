"""Seed a deterministic Phase 1 demo candidate for local development."""
import uuid

from app.database import Base, SessionLocal, engine
from app.models import Artifact, CandidateProfile, InterviewTurn, Resume, User
from app.auth import hash_password

USER_ID = "demo-user"

PROFILE = {
    "personal_information": {"name": "Ava Sharma", "email": "ava@example.com", "location": "Bengaluru"},
    "professional_summary": "Product-minded software engineer building reliable web products.",
    "education": [{"degree": "B.Tech Computer Science", "institution": "PES University", "year": 2023}],
    "experience": [{"title": "Software Engineer", "company": "Acme Labs", "duration": "2023-present", "responsibilities": ["Built FastAPI services", "Improved API latency by 35%"]}],
    "projects": [{"name": "Chably", "description": "Resume intelligence platform", "technologies": ["Python", "FastAPI", "React"]}],
    "skills": {"technical": ["Python", "FastAPI", "SQL", "React"], "soft": ["Communication", "Ownership"]},
    "certifications": [], "achievements": ["Won internal innovation award"],
    "career_preferences": {"target_roles": ["Backend Engineer", "Product Engineer"], "work_mode": "remote"},
    "career_goals": {"short_term": "Lead backend projects"}, "strengths": ["API design", "Delivery"], "gaps": ["Cloud architecture"],
}


def main() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        demo_user = db.get(User, USER_ID)
        if not demo_user:
            db.add(User(id=USER_ID, email="demo@chably.ai", password_hash=hash_password("DemoPassword123!"), display_name="Ava Sharma"))
        else:
            demo_user.email = "demo@chably.ai"
        resume = db.query(Resume).filter(Resume.user_id == USER_ID).first()
        if not resume:
            resume = Resume(id=str(uuid.uuid4()), user_id=USER_ID, original_filename="demo-resume.pdf", extracted_text="Ava Sharma\nSoftware Engineer\nPython FastAPI SQL React")
            db.add(resume)
        profile = db.get(CandidateProfile, USER_ID)
        if not profile:
            db.add(CandidateProfile(user_id=USER_ID, resume_id=resume.id, data=PROFILE))
        else:
            profile.data = PROFILE
            profile.resume_id = resume.id
        if not db.query(Artifact).filter(Artifact.user_id == USER_ID).first():
            db.add(Artifact(id=str(uuid.uuid4()), user_id=USER_ID, kind="role_recommendations", data={"roles": [{"title": "Backend Engineer", "fit_score": 86, "matched_skills": ["Python", "FastAPI"]}]}))
        db.commit()
    print(f"Seeded demo candidate: {USER_ID}")


if __name__ == "__main__":
    main()
