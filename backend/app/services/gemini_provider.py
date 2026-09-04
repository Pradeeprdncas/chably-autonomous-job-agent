import json, logging, re, time
import httpx
from .ai_provider import AIProvider
from ..config import settings
from ..prompts.resume_prompts import (
    ANALYZE_RESUME,
    EXTRACT_RESUME,
    INTERVIEW_QUESTION,
    PROCESS_ANSWER,
    RECOMMEND_ROLES,
    REWRITE_RESUME,
)

EMPTY = {"personal_information": {"name": None, "email": None, "phone": None, "location": None, "linkedin": None, "github": None, "portfolio": None}, "professional_summary": None, "education": [], "experience": [], "projects": [], "skills": {"programming_languages": [], "frameworks": [], "databases": [], "ai_ml": [], "cloud": [], "devops": [], "tools": [], "other": []}, "certifications": [], "achievements": [], "career_preferences": {"target_roles": [], "preferred_locations": [], "remote_preference": None, "company_type": [], "preferred_domains": [], "salary_expectation": None, "notice_period": None}}
usage_logger = logging.getLogger("chably.ai_usage")


def deterministic_role_recommendations(profile: dict, roles: list[dict]) -> list[dict]:
    skill_buckets = profile.get("skills") or {}
    skills = {str(skill).lower() for values in skill_buckets.values() if isinstance(values, list) for skill in values}
    narrative = " ".join([
        str(profile.get("professional_summary") or ""),
        " ".join(str(item) for item in profile.get("experience") or []),
        " ".join(str(item) for item in profile.get("projects") or []),
    ]).lower()

    def has(signal: str) -> bool:
        value = signal.lower()
        aliases = {
            "ai/ml": bool(skill_buckets.get("ai_ml")) or any(term in skills for term in ("rag", "llm applications", "agentic ai", "machine learning")),
            "apis": any(term in skills for term in ("rest apis", "async apis", "fastapi", "api integrations")),
            "databases": bool(skill_buckets.get("databases")),
            "cloud": bool(skill_buckets.get("cloud")) or any("cloud" in term for term in skills),
            "software engineering": bool(profile.get("experience")),
            "communication": any("communication" in term for term in skills),
            "testing": any(term in skills for term in ("pytest", "testing", "test automation")),
        }
        return aliases.get(value, value in skills or value in narrative)

    recommendations = []
    for role in roles:
        required = role.get("required_skills") or []
        preferred = role.get("preferred_skills") or []
        matched_required = [skill for skill in required if has(skill)]
        matched_preferred = [skill for skill in preferred if has(skill)]
        title_terms = [term for term in re.findall(r"[a-z]+", role.get("role", "").lower()) if term not in {"engineer", "developer"}]
        role_evidence = bool(title_terms and any(term in narrative for term in title_terms))
        score = round(42 + 35 * len(matched_required) / max(1, len(required)) + 15 * len(matched_preferred) / max(1, len(preferred)) + (8 if role_evidence else 0))
        score = min(96, score)
        recommendations.append({
            "title": role["role"],
            "fit_score": score,
            "summary": role.get("description", ""),
            "matched_skills": matched_required + matched_preferred,
            "missing_skills": [skill for skill in required + preferred if skill not in matched_required + matched_preferred],
            "strengths": [f"Evidence supports {skill}." for skill in (matched_required + matched_preferred)[:4]],
            "evidence": ["Relevant experience and projects are present in the candidate profile."] if role_evidence else [],
            "gaps": [f"Add evidence for {skill} if you have it." for skill in (required + preferred) if skill not in matched_required + matched_preferred][:3],
            "recommended_actions": [f"Tailor the resume headline and strongest evidence toward {role['role']} roles."],
        })
    return sorted(recommendations, key=lambda item: item["fit_score"], reverse=True)[:7]

def _usage(provider, model, operation, started, success, usage=None):
    usage_logger.info(json.dumps({"timestamp": time.time(), "provider": provider, "model": model, "operation": operation, "success": success, "latency_ms": round((time.perf_counter() - started) * 1000), "input_tokens": (usage or {}).get("input_tokens"), "output_tokens": (usage or {}).get("output_tokens")}, separators=(",", ":")))

class GeminiProvider(AIProvider):
    async def _mistral_json(self, instruction: str, payload: dict):
        if settings.ai_mock_mode or not settings.mistral_api_key:
            return None
        prompt = f"{instruction}\nReturn only valid JSON. Never invent facts. Unknown values must be null or [].\n{json.dumps(payload)}"
        operation = instruction.split(".", 1)[0][:64]
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    "https://api.mistral.ai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {settings.mistral_api_key}"},
                    json={
                        "model": settings.mistral_model,
                        "messages": [
                            {"role": "system", "content": "You return only valid JSON."},
                            {"role": "user", "content": prompt},
                        ],
                        "response_format": {"type": "json_object"},
                        "temperature": 0.2,
                    },
                )
                response.raise_for_status()
                data = response.json(); usage = data.get("usage") or {}; _usage("mistral", settings.mistral_model, operation, started, True, {"input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens")})
                return json.loads(data["choices"][0]["message"]["content"])
        except Exception:
            _usage("mistral", settings.mistral_model, operation, started, False)
            return None

    async def _json(self, instruction: str, payload: dict):
        if settings.ai_mock_mode:
            return None
        prompt = f"{instruction}\nReturn only valid JSON. Never invent facts. Unknown values must be null or [].\n{json.dumps(payload)}"
        operation = instruction.split(".", 1)[0][:64]
        if settings.gemini_api_key:
            started = time.perf_counter()
            try:
                from google import genai
                client = genai.Client(api_key=settings.gemini_api_key)
                response = await client.aio.models.generate_content(
                    model=settings.gemini_model,
                    contents=prompt,
                    config={"response_mime_type": "application/json"},
                )
                meta = getattr(response, "usage_metadata", None)
                _usage("gemini", settings.gemini_model, operation, started, True, {"input_tokens": getattr(meta, "prompt_token_count", None), "output_tokens": getattr(meta, "candidates_token_count", None)})
                return json.loads(response.text)
            except Exception:
                _usage("gemini", settings.gemini_model, operation, started, False)
        return await self._mistral_json(instruction, payload)

    async def extract_resume(self, text):
        if settings.ai_mock_mode:
            return self._mock_profile(text)
        result = await self._json(EXTRACT_RESUME + "\nExact schema: " + json.dumps(EMPTY), {"resume": text})
        if result: return result
        data = json.loads(json.dumps(EMPTY)); p = data["personal_information"]
        p["email"] = (re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text) or [None])[0]
        p["phone"] = (re.search(r"(?:\+?\d[\d .()-]{8,}\d)", text) or [None])[0]
        known = {"Python":"programming_languages", "JavaScript":"programming_languages", "FastAPI":"frameworks", "React":"frameworks", "SQLite":"databases", "Docker":"devops", "AWS":"cloud", "ChromaDB":"ai_ml", "TensorFlow":"ai_ml"}
        for term, bucket in known.items():
            if re.search(rf"\b{term}\b", text, re.I): data["skills"][bucket].append(term)
        return data

    async def generate_question(self, profile, completeness, previous):
        result = await self._json(INTERVIEW_QUESTION + "\nReturn question,target_category,target_fields,reason.", {"profile":profile,"completeness":completeness,"previous_questions":[x["question"] for x in previous]})
        if result: return result
        categories = completeness.get("categories", [])
        weakest = min(categories, key=lambda item: item.get("score", 0), default={"key": "career_direction"})["key"]
        prompts = {"career_direction":"Which role are you primarily targeting next—backend, AI, or full-stack—and why?", "job_preferences":"Which locations, remote arrangement, and company environment would work best for your next role?", "experience_depth":"What is the most complex system you personally worked on, and what decisions and outcomes were yours?", "skill_evidence":"Choose one key technology from your resume: what did you personally build with it and how did you validate it?", "achievements":"What concrete outcome or improvement are you most proud of from a project or role?"}
        return {"question":prompts.get(weakest, "What important work or project experience is missing from your resume?"),"target_category":weakest,"target_fields":[],"reason":"This is the weakest profile category."}

    async def process_answer(self, profile, question, answer):
        result = await self._json(PROCESS_ANSWER, {"profile":profile,"question":question,"answer":answer})
        return result or {"interview_evidence": [{"category":question["target_category"], "answer":answer}]}

    async def recommend_roles(self, profile, roles):
        result = await self._json(RECOMMEND_ROLES + "\nReturn 3-7 objects with title,fit_score 0-100,why_it_fits,evidence,gaps,recommendation.", {"profile":profile,"roles":roles})
        if isinstance(result, dict):
            result = result.get("roles") or result.get("recommendations") or result.get("items")
        if isinstance(result, list) and all(isinstance(role, dict) for role in result):
            return result
        return deterministic_role_recommendations(profile, roles)

    async def analyze_resume(self, profile, original):
        result = await self._json(ANALYZE_RESUME + "\nReturn resume_score,missing_information,weak_sections,strong_sections,skills_to_highlight,experience_improvements,project_improvements,summary_improvement,recommended_role_positioning.", {"profile":profile,"original_resume":original})
        return result or {
            "overall_score": 72 if settings.ai_mock_mode else 60,
            "summary": "The resume has usable structure but needs stronger verified impact and role positioning.",
            "strengths": ["Clear technical profile", "Relevant project or experience signals"],
            "issues": [
                {
                    "section": "experience",
                    "severity": "medium",
                    "message": "Experience bullets need clearer ownership and outcomes.",
                    "recommendation": "Add truthful scope, decisions, tools, and measurable results where available.",
                }
            ],
            "missing_information": profile.get("interview_evidence", []),
            "weak_bullets": [],
            "suggested_bullets": [],
            "skills_to_highlight": [],
            "ats_observations": ["Use common role keywords only when they match real experience."],
        }

    async def rewrite_resume(self, profile, original, target_role):
        result = await self._json(REWRITE_RESUME + "\nReturn JSON with key resume.", {"profile":profile,"original_resume":original,"target_role":target_role})
        if result:
            return result
        return {
            "target_role": target_role,
            "resume": {
                "headline": target_role or "Career Profile",
                "summary": profile.get("professional_summary") or "",
                "skills": [
                    skill
                    for values in profile.get("skills", {}).values()
                    if isinstance(values, list)
                    for skill in values
                ],
                "experience": profile.get("experience", []),
                "projects": profile.get("projects", []),
                "education": profile.get("education", []),
                "certifications": profile.get("certifications", []),
            },
            "changes": [
                {
                    "section": "summary",
                    "reason": "Aligned existing verified information to the selected target role.",
                }
            ],
            "rendered_markdown": original,
        }

    def _mock_profile(self, text):
        data = json.loads(json.dumps(EMPTY))
        data["personal_information"] = {
            "name": "Demo Candidate",
            "email": "demo@chably.ai",
            "phone": "+1 555 0100",
            "location": "Remote",
            "linkedin": "",
            "github": "",
            "portfolio": "",
        }
        data["professional_summary"] = "Backend-focused engineer with experience building API services and AI-enabled product workflows."
        data["education"] = [{"degree": "B.Tech", "institution": "Demo University", "field": "Computer Science"}]
        data["experience"] = [
            {
                "role": "Backend Developer",
                "company": "Demo Systems",
                "description": "Built FastAPI services and integrated vector search workflows.",
                "responsibilities": ["Designed REST APIs", "Integrated database-backed profile workflows"],
                "achievements": [],
                "technologies": ["Python", "FastAPI", "SQLAlchemy", "SQLite", "ChromaDB"],
            }
        ]
        data["projects"] = [
            {
                "name": "Resume Intelligence API",
                "description": "Created a resume parsing and profile enrichment backend.",
                "technologies": ["Python", "FastAPI", "Gemini", "ChromaDB"],
            }
        ]
        data["skills"] = {
            "programming_languages": ["Python", "JavaScript"],
            "frameworks": ["FastAPI", "React"],
            "databases": ["SQLite"],
            "ai_ml": ["Gemini", "ChromaDB"],
            "cloud": [],
            "devops": ["Docker"],
            "tools": ["Git"],
            "other": [],
        }
        data["career_preferences"]["target_roles"] = ["Backend Engineer", "Applied AI Engineer"]
        if text:
            email = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
            if email:
                data["personal_information"]["email"] = email.group(0)
        return data
