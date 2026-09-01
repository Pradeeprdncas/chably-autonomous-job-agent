# Chably AI Resume Intelligence Backend

Phase 1 backend for an AI-powered career assistant. It ingests PDF resumes,
extracts structured candidate profiles, tracks completeness, asks one adaptive
interview question at a time, stores history, recommends roles, analyzes resumes,
and rewrites resumes truthfully for a target role.
Phase 2 adds job and company discovery, normalized opportunity storage, semantic indexing, deterministic filtering, fit evaluation, and saved-job workflows.

## Stack

- FastAPI
- SQLAlchemy 2
- Pydantic
- SQLite as the local source of truth
- pypdf for PDF extraction
- Gemini behind an `AIProvider` abstraction
- ChromaDB PersistentClient for semantic resume chunks and role taxonomy retrieval

SQLite is the local default so the backend can boot without external services.
Set `DATA_DIR` to a mounted persistent disk path when deploying to a persistent cloud service.

## Setup

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Optional Gemini:

```bash
GEMINI_API_KEY=your_key
GEMINI_MODEL=gemini-2.5-flash-lite
MISTRAL_API_KEY=optional_backup_key
MISTRAL_MODEL=mistral-small-latest
```

Gemini 2.5 Flash-Lite is the default for cost-efficient high-volume structured tasks. Mistral is used automatically when Gemini is unavailable, returns an error, or returns an invalid reply-classification schema. Without either provider, deterministic local fallbacks keep the API usable for development.
ChromaDB is local and requires no server or Docker. If it cannot initialize, core profile and interview features remain available while semantic recommendations return a controlled error.
ChromaDB is pinned to `0.5.23` for the bundled Python 3.9 environment, with a compatible PostHog version.

`requirements.txt` contains supported compatibility ranges for development. `requirements.lock` pins the direct application dependencies for reproducible Python 3.11 builds while allowing pip to choose platform-specific transitive wheels. Production builds may use `pip install -r requirements.lock`; update both files together after dependency testing.

Render start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.

Production targets Python 3.11. `render.yaml` provisions a single web instance with a persistent `/var/data` disk for SQLite and Chroma. This SQLite/WAL design is intended for one early-production application instance, not horizontal multi-instance writes.

Operational commands:

```bash
python -m app.scripts.verify_production_services
python -m app.scripts.backup_data
python -m app.scripts.restore_data /path/to/chably-YYYYMMDDTHHMMSSZ.sqlite3
python -m app.scripts.sync_gmail_replies
```

Backup uses SQLite's consistent backup API, verifies the resulting database, and copies Chroma into a sibling `.chroma` directory. Stop the API and workers before restore; restore verifies and atomically replaces SQLite and stages Chroma before swapping it. Always test restore using temporary storage, never the live developer database. `TOKEN_ENCRYPTION_KEY` rotation currently requires reconnecting Gmail accounts; in-place token re-encryption is not automated.

## External integration verification

As of 2026-08-22, Google OAuth and the controlled real Gmail flow have been verified with mock modes disabled: authorization and encrypted refresh-token storage, approval-first send, exact-thread retrieval, incoming human-reply persistence, classification, application transition, reviewable reply drafting, and zero-change second-sync idempotency all passed. Real automatic replies remained disabled, and no manual reply draft was sent.

The SearXNG implementation is complete but still awaits verification against a configured real instance. Do not treat an unconfigured SearXNG service as a Gmail verification failure.

## Versioned API

- `POST /api/v1/resumes/upload?user_id={user_id}` with multipart PDF file
- `GET|PATCH /api/v1/profile/{user_id}` and `GET /api/v1/profile/{user_id}/completeness`
- `POST /api/v1/interview/{user_id}/start`, `/answer`; `GET /history`
- `GET /api/v1/roles/{user_id}/recommendations`
- `POST /api/v1/resumes/{user_id}/analysis` and `/rewrite`
- `GET /api/v1/dashboard/{user_id}` and `GET /api/v1/system/status`
- `POST /api/v1/job-search` and `GET /api/v1/job-search/{search_id}`
- `POST /api/v1/company-search` and `GET /api/v1/users/{user_id}/search-history`
- `POST /api/v1/jobs/{job_id}/save` and `PATCH /api/v1/opportunities/{opportunity_id}`

Every versioned response uses the same `{success, message, data, meta, errors, events}` envelope. Legacy `/api/...` aliases remain available for existing clients. See [FRONTEND_INTEGRATION.md](FRONTEND_INTEGRATION.md).

Phase 2 uses a configured SearXNG instance and never falls back to mock results unless `SEARCH_MOCK_MODE=true`. Greenhouse, Lever, and Ashby public job boards have structured adapters. Static HTML is preferred; optional Playwright rendering is isolated behind `BROWSER_FETCH_ENABLED=true` and limited by the browser settings in `.env.example`.

When `SEARCH_PROVIDER=searxng` but `SEARXNG_URL` is empty, Chably uses a keyless DuckDuckGo HTML search fallback rather than failing the user's search. It returns public job and careers results; configure SearXNG for a controlled production search source.

Outreach uses per-user Google OAuth, not a shared Gmail account. Configure `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, and a Fernet-compatible `TOKEN_ENCRYPTION_KEY`. For local development, the redirect URI is exactly `http://localhost:8000/api/v1/integrations/google/callback`; add that exact value to the Google OAuth client's Authorized redirect URIs. A deployed environment must use its public HTTPS backend origin with the same callback path and an exact matching Google Console entry. Keep the scopes limited to `gmail.send` and `gmail.readonly`, and leave `GOOGLE_OAUTH_MOCK_MODE=false` for real verification. The default is approval-first: a user must approve an outreach draft before Gmail sends it.

Passwordless Google sign-in is a separate identity-only flow. Add `GOOGLE_LOGIN_REDIRECT_URI` (locally, `http://localhost:8000/api/v1/auth/google/callback`) as a second Authorized redirect URI and environment variable. It only requests `openid email profile`; it does not grant Gmail access.

Before public use, configure the OAuth consent screen's app name, support email, developer contact, privacy policy, and authorized domains. Add test users while the app remains in Google testing status. Moving to production for users outside that list can require Google verification because Gmail scopes are sensitive; publishing the consent screen does not remove that requirement.

Reply synchronization is thread-scoped and idempotent: it reads only Gmail thread IDs created by Chably outreach, stores each Gmail message once, classifies incoming replies, updates linked applications, and creates reviewable drafts. Automatic replies require both the global `AUTO_REPLY_ENABLED=true` switch and explicit per-user allowlist settings; the default is off.

Run synchronization manually or from a cron-style scheduler:

```bash
cd backend
python -m app.scripts.sync_gmail_replies
```

Relevant outreach endpoints include:

- `GET /api/v1/integrations/google/connect?user_id=...`
- `GET /api/v1/integrations/google/status/{user_id}`
- `POST /api/v1/integrations/google/{user_id}/sync`
- `POST /api/v1/outreach/{outreach_id}/draft-reply?user_id=...`
- `POST /api/v1/replies/{reply_id}/approve?user_id=...`
- `POST /api/v1/replies/{reply_id}/send?user_id=...`

Object endpoints require a valid Chably bearer token and verify that every requested `user_id` and resource belongs to that authenticated account.

## Current boundaries

This repository is backend-only. It does not include frontend UI, payment flows, or automated job-application submission.
