COMPANIES = [
    ("Voxora AI", "voxora.example", "Voice AI", "Bangalore", ["Python", "FastAPI", "LLM"]),
    ("RAGWorks", "ragworks.example", "Enterprise RAG", "Remote", ["Python", "RAG", "ChromaDB"]),
    ("SaaSForge", "saasforge.example", "B2B SaaS", "Chennai", ["Python", "React", "SQL"]),
    ("DataCurrent", "datacurrent.example", "Data platforms", "Bangalore", ["Python", "SQL", "ETL"]),
    ("SupportPilot", "supportpilot.example", "AI support", "Remote", ["LLM", "APIs", "Customer Support"]),
    ("CloudLoom", "cloudloom.example", "Cloud automation", "Hyderabad", ["AWS", "Docker", "Python"]),
    ("Frontline Labs", "frontline.example", "Product engineering", "Bangalore", ["React", "TypeScript", "APIs"]),
    ("CallSense", "callsense.example", "Voice analytics", "Chennai", ["Python", "NLP", "Audio"]),
    ("MetricNest", "metricnest.example", "Analytics SaaS", "Remote", ["Python", "SQL", "FastAPI"]),
    ("Orbit BPO", "orbitbpo.example", "Business process services", "Bangalore", ["Customer Support", "Sales", "CRM"]),
]


def mock_results():
    rows = []
    role_pairs = [
        ("Applied AI Engineer", "Backend Engineer"), ("Python Developer", "RAG Engineer"),
        ("Full Stack Engineer", "Backend Engineer"), ("Data Engineer", "Business Analyst"),
        ("AI Engineer", "Technical Support Engineer"), ("DevOps Engineer", "Python Engineer"),
        ("Frontend Engineer", "Product Engineer"), ("ML Engineer", "Backend Engineer"),
        ("Data Engineer", "Solutions Engineer"), ("Support Engineer", "Sales Engineer"),
    ]
    for index, company in enumerate(COMPANIES):
        name, domain, product, location, skills = company
        for job_index, title in enumerate(role_pairs[index]):
            slug = title.lower().replace(" ", "-")
            rows.append({
                "company": {"name": name, "website": f"https://{domain}", "domain": domain, "description": f"{name} builds {product} products.", "industries": [product], "products": [product], "technologies": skills, "locations": [location], "company_type": "startup" if index < 8 else "services", "careers_url": f"https://{domain}/careers"},
                "job": {"raw_title": title, "title": title, "description": f"Build and improve {product} systems using {', '.join(skills)}.", "location": location, "remote_type": "remote" if location == "Remote" else "hybrid", "employment_type": "full_time", "experience_min": 1 if job_index == 0 else 2, "experience_max": 3 if job_index == 0 else 5, "skills": skills, "responsibilities": ["Build reliable product capabilities", "Collaborate with product teams"], "requirements": [f"Experience with {skills[0]}"], "preferred_requirements": skills[1:], "job_url": f"https://{domain}/careers/{slug}-{job_index}", "source_url": f"https://{domain}/careers", "source_type": "mock_company_careers", "status": "open"},
            })
    return rows
