from __future__ import annotations

import re
from typing import Any, Optional

from fastapi import HTTPException


MAX_INTERVIEW_QUESTIONS = 12


def ok(
    message: str,
    data: Any = None,
    meta: Optional[dict[str, Any]] = None,
    events: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    body = {
        "success": True,
        "message": message,
        "data": data if data is not None else {},
        "meta": meta or {},
        "errors": [],
    }
    if events is not None:
        body["events"] = events
    return body


def fail(status_code: int, code: str, message: str, field: Optional[str] = None):
    raise HTTPException(
        status_code=status_code,
        detail={
            "success": False,
            "message": message,
            "data": None,
            "meta": {},
            "errors": [{"code": code, "field": field, "message": message}],
        },
    )


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug or "unknown"


def normalize_profile(profile: Optional[dict[str, Any]]) -> dict[str, Any]:
    source = profile or {}
    personal = source.get("personal_information") or source.get("identity") or {}
    skills = source.get("skills") if isinstance(source.get("skills"), dict) else {}
    preferences = source.get("career_preferences")
    if not isinstance(preferences, dict):
        preferences = {}

    return {
        "identity": {
            "name": personal.get("name") or "",
            "email": personal.get("email") or "",
            "phone": personal.get("phone") or "",
            "location": personal.get("location") or "",
            "linkedin": personal.get("linkedin") or "",
            "github": personal.get("github") or "",
            "portfolio": personal.get("portfolio") or "",
        },
        "professional_summary": source.get("professional_summary") or "",
        "education": list(source.get("education") or []),
        "experience": list(source.get("experience") or []),
        "projects": list(source.get("projects") or []),
        "skills": skills,
        "certifications": list(source.get("certifications") or []),
        "achievements": list(source.get("achievements") or []),
        "career_preferences": preferences,
        "career_goals": source.get("career_goals") or {},
        "strengths": list(source.get("strengths") or []),
        "gaps": list(source.get("gaps") or []),
    }


def to_internal_profile(profile: dict[str, Any]) -> dict[str, Any]:
    data = dict(profile or {})
    if "identity" in data and "personal_information" not in data:
        data["personal_information"] = data.pop("identity")
    return data


def category_status(score: int) -> str:
    if score <= 0:
        return "missing"
    if score < 40:
        return "weak"
    if score < 70:
        return "needs_improvement"
    if score < 90:
        return "good"
    return "strong"


def normalize_completeness(raw: dict[str, Any]) -> dict[str, Any]:
    scores = raw.get("categories", {}) if raw else {}
    weights = {
        "basic_information": 10,
        "education": 8,
        "experience": 12,
        "experience_depth": 15,
        "technical_skills": 12,
        "skill_evidence": 12,
        "projects": 8,
        "achievements": 8,
        "career_direction": 8,
        "job_preferences": 7,
    }
    categories = [
        {
            "key": key,
            "label": key.replace("_", " ").title(),
            "score": int(score),
            "weight": weights.get(key, 5),
            "status": category_status(int(score)),
        }
        for key, score in scores.items()
    ]
    overall = int(raw.get("overall", 0)) if raw else 0
    weakest = min(categories, key=lambda item: item["score"], default={"key": None})
    strongest = max(categories, key=lambda item: item["score"], default={"key": None})
    status = "complete" if overall >= 90 else "interviewing" if overall >= 30 else "needs_resume"
    missing = [
        {"key": item["key"], "label": item["label"]}
        for item in categories
        if item["status"] in {"missing", "weak"}
    ]
    return {
        "overall": overall,
        "status": status,
        "categories": categories,
        "strongest_category": strongest["key"],
        "weakest_category": weakest["key"],
        "missing_information": missing,
        "next_priority": weakest["key"],
    }


def normalize_question(turn: Any) -> Optional[dict[str, Any]]:
    if not turn:
        return None
    return {
        "id": turn.id,
        "text": turn.question,
        "target_category": turn.target_category,
        "target_fields": turn.target_fields or [],
        "reason": turn.reason or "",
    }


def fit_level(score: int) -> str:
    if score >= 90:
        return "excellent"
    if score >= 80:
        return "strong"
    if score >= 65:
        return "good"
    if score >= 45:
        return "moderate"
    return "low"


def normalize_role(role: dict[str, Any]) -> dict[str, Any]:
    title = role.get("title") or role.get("role") or "Recommended Role"
    score = int(role.get("fit_score") or 0)
    return {
        "id": role.get("id") or slugify(title),
        "title": title,
        "fit_score": score,
        "fit_level": role.get("fit_level") or fit_level(score),
        "summary": role.get("summary") or _first_text(role.get("why_it_fits")),
        "matched_skills": list(role.get("matched_skills") or []),
        "missing_skills": list(role.get("missing_skills") or []),
        "strengths": list(role.get("strengths") or role.get("why_it_fits") or []),
        "gaps": list(role.get("gaps") or []),
        "evidence": list(role.get("evidence") or []),
        "recommended_actions": list(role.get("recommended_actions") or ([] if not role.get("recommendation") else [role.get("recommendation")])),
    }


def normalize_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    raw = analysis or {}
    return {
        "overall_score": int(raw.get("overall_score") or raw.get("resume_score") or 0),
        "summary": raw.get("summary") or raw.get("summary_improvement") or "",
        "strengths": list(raw.get("strengths") or raw.get("strong_sections") or []),
        "issues": list(raw.get("issues") or []),
        "missing_information": list(raw.get("missing_information") or []),
        "weak_bullets": list(raw.get("weak_bullets") or []),
        "suggested_bullets": list(raw.get("suggested_bullets") or []),
        "skills_to_highlight": list(raw.get("skills_to_highlight") or []),
        "ats_observations": list(raw.get("ats_observations") or []),
    }


def normalize_rewrite(target_role: Optional[str], rewritten: Any, profile: dict[str, Any]) -> dict[str, Any]:
    if isinstance(rewritten, dict):
        resume = rewritten.get("resume") if isinstance(rewritten.get("resume"), dict) else {}
        rendered = rewritten.get("rendered_markdown") or ""
        changes = list(rewritten.get("changes") or [])
    else:
        clean = normalize_profile(profile)
        resume = {
            "headline": target_role or "",
            "summary": clean["professional_summary"],
            "skills": [skill for values in clean["skills"].values() if isinstance(values, list) for skill in values],
            "experience": clean["experience"],
            "projects": clean["projects"],
            "education": clean["education"],
            "certifications": clean["certifications"],
        }
        rendered = str(rewritten or "")
        changes = [{"section": "resume", "reason": "Generated a truthful role-focused rewrite from the structured profile."}]
    return {
        "target_role": target_role or "",
        "resume": resume,
        "changes": changes,
        "rendered_markdown": rendered,
    }


def _first_text(value: Any) -> str:
    if isinstance(value, list) and value:
        return str(value[0])
    if isinstance(value, str):
        return value
    return ""
