from ..api.utils import normalize_completeness


def _present(value):
    return bool(value) and value not in ([], {}, "unknown")


def calculate(profile: dict) -> dict:
    personal = profile.get("personal_information", {})
    experience = profile.get("experience", [])
    projects = profile.get("projects", [])
    skills = profile.get("skills", {})
    preferences = profile.get("career_preferences", {})
    scores = {
        "basic_information": round(100 * sum(_present(personal.get(k)) for k in ["name", "email", "location"]) / 3),
        "education": 100 if _present(profile.get("education")) else 0,
        "experience": 100 if _present(experience) else 0,
        "experience_depth": min(100, 35 * sum(bool(x.get("responsibilities") or x.get("achievements") or x.get("impact")) for x in experience)),
        "technical_skills": min(100, 18 * sum(len(v) for v in skills.values() if isinstance(v, list))),
        "skill_evidence": min(100, 30 * sum(bool(x.get("technologies") and (x.get("description") or x.get("responsibilities"))) for x in experience + projects)),
        "projects": min(100, 50 * len(projects)),
        "achievements": min(100, 35 * len(profile.get("achievements", []))),
        "career_direction": 100 if _present(preferences.get("target_roles")) else 0,
        "job_preferences": round(100 * sum(_present(preferences.get(k)) for k in ["preferred_locations", "remote_preference", "company_type"]) / 3),
    }
    return normalize_completeness(
        {"overall": round(sum(scores.values()) / len(scores)), "categories": scores}
    )
