EXTRACT_RESUME = """
Extract a candidate profile from the resume text using the provided schema.
Only use facts explicitly present in the resume. Unknown scalar values must be null.
Unknown list values must be empty arrays. Do not invent seniority, metrics, dates,
salary, preferences, employers, schools, links, or skills.
"""

INTERVIEW_QUESTION = """
Ask exactly one concise adaptive interview question that fills the weakest profile
area. Do not repeat previous questions. Prefer evidence about ownership, outcomes,
target roles, preferences, and skill depth. Return JSON only.
"""

PROCESS_ANSWER = """
Extract only new factual information from the user's answer as a merge patch for
the candidate profile. Never overwrite unrelated fields, never invent missing
facts, and keep subjective claims as interview evidence unless directly factual.
"""

RECOMMEND_ROLES = """
Evaluate candidate roles truthfully against the profile and supplied taxonomy.
Use semantic matches only as retrieval candidates; final fit must be reasoned from
profile evidence, gaps, and career preferences. Do not infer seniority.
"""

ANALYZE_RESUME = """
Analyze the original resume against the structured profile. Identify missing
information, weak sections, stronger evidence to highlight, and role positioning.
Do not invent improvements that require unsupported facts.
"""

REWRITE_RESUME = """
Rewrite the resume truthfully for the target role. Preserve factual accuracy and
do not invent metrics, dates, employers, education, certifications, or skills.
"""
