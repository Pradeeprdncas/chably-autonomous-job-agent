from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from .config import settings
from .database import engine, Base
from .api.resume import router as resume_router
from .api.profile import router as profile_router
from .api.interview import router as interview_router
from .api.recommendations import router as recommendations_router
from .api.dashboard import router as dashboard_router
from .api.system import router as system_router
from .api.discovery import router as discovery_router
from .api.outreach import router as outreach_router
from .api.auth import router as auth_router
from .api.account import router as account_router
from .http_middleware import request_context_middleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_production()
    Base.metadata.create_all(bind=engine)
    # Older drafts predate the application tracker.  Reconcile them once at
    # startup so the Applications view is useful instead of silently empty.
    from .database import SessionLocal
    from .models import Outreach
    from .api.outreach import _ensure_application
    from .services.embedding_service import EmbeddingService
    from .data.job_taxonomy import ROLES

    EmbeddingService().upsert_job_taxonomy(ROLES)
    db = SessionLocal()
    try:
        for outreach in db.query(Outreach).all():
            status = "email_sent" if outreach.status == "sent" else "email_approved" if outreach.status == "approved" else "outreach_ready"
            _ensure_application(db, outreach, status)
        db.commit()
    finally:
        db.close()
    yield


app = FastAPI(
    title="Chably AI - Resume Intelligence API",
    version="1.0.0",
    description="Stable v1 backend for authenticated resume intelligence, career discovery, Gmail outreach, reply synchronization, and application tracking.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.middleware("http")(request_context_middleware)

app.include_router(auth_router)
app.include_router(account_router)
app.include_router(resume_router)
app.include_router(profile_router)
app.include_router(interview_router)
app.include_router(recommendations_router)
app.include_router(dashboard_router)
app.include_router(system_router)
app.include_router(discovery_router)
app.include_router(outreach_router)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict) and "success" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "message": str(exc.detail),
            "data": None,
            "meta": {},
            "errors": [
                {"code": "HTTP_ERROR", "field": None, "message": str(exc.detail)}
            ],
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = []
    for err in exc.errors():
        field = ".".join(str(part) for part in err.get("loc", []) if part != "body")
        errors.append(
            {
                "code": "VALIDATION_ERROR",
                "field": field or None,
                "message": err.get("msg", "Invalid request"),
            }
        )
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "message": "Request validation failed",
            "data": None,
            "meta": {},
            "errors": errors,
        },
    )


@app.get("/health")
def health():
    return {"status": "ok"}
