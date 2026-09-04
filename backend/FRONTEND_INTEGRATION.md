# Chably frontend integration contract — API v1

`/api/v1` is the stable frontend integration contract and must not change casually. Breaking changes belong in `/api/v2`. Configure the React client with `VITE_API_BASE_URL`; never put Gemini, Mistral, Google client secrets, JWT secrets, or Gmail tokens in the frontend.

All API responses use:

```json
{"success":true,"message":"...","data":{},"meta":{},"errors":[],"events":[]}
```

Every response includes `X-Request-ID`. The client may send a safe 1–64 character request ID. Protected endpoints use `Authorization: Bearer <access_token>` and are marked with HTTP Bearer security in OpenAPI/Swagger.

## Chably authentication

Register:

```http
POST /api/v1/auth/register
Content-Type: application/json

{"email":"user@example.com","password":"a-long-unique-password","display_name":"Ava"}
```

Login uses the same email/password fields at `POST /api/v1/auth/login`. Both return `data.user`, a short-lived `access_token`, a rotating `refresh_token`, `token_type`, and `expires_in`. Keep the access token in memory where possible. Store the refresh token using an appropriate secure browser/session strategy.

Refresh with `POST /api/v1/auth/refresh` and `{"refresh_token":"..."}`. Refresh tokens rotate: discard the old token immediately. Logout uses `POST /api/v1/auth/logout` with the active refresh token and revokes that SQLite session. Load the identity with `GET /api/v1/auth/me`.

Intended flow:

```text
register/login → access token → GET /api/v1/dashboard
→ refresh when access expires → replace both tokens → logout/revoke
```

## Authenticated product flow

- `GET /api/v1/dashboard` returns compact profile/resume/interview state, recommendations, recent opportunities, application counts, Google connection state, and outreach counts.
- `POST /api/v1/resumes/upload` accepts multipart field `file` (PDF only). The authenticated user owns the upload; no `user_id` is needed.
- `GET|PATCH /api/v1/profile/me`; `GET /api/v1/profile/me/completeness`.
- Adaptive interview legacy routes remain `/api/v1/interview/{user_id}/start`, `/answer`, and `/history`; the path ID must equal the JWT user.
- Recommendations and resume analysis/rewrite currently retain authenticated legacy shapes using `{user_id}`. Cross-user IDs return 404.
- `POST /api/v1/job-search` and `/company-search` currently accept `user_id` in JSON for v1 compatibility, but it must equal the JWT user.
- `GET /api/v1/search-history`, `/opportunities`, `/saved-jobs`, `/applications`, and `/outreach` are authenticated list endpoints supporting `limit`/`offset`; relevant status/fit filters are documented in OpenAPI.
- Detail endpoints for searches, opportunities, outreach, Gmail threads, replies, and applications enforce ownership.
- `GET|PATCH /api/v1/settings` covers career preferences plus outreach/auto-reply settings.
- `GET /api/v1/account/export` returns the user's structured data without password hashes or OAuth credentials.
- `DELETE /api/v1/account` deletes user-owned SQLite records and candidate Chroma vectors.

## Gmail connection versus Chably login

Chably login and Gmail authorization are separate.

```text
authenticated Chably user → POST /api/v1/integrations/google/connect
→ open data.authorization_url → Google consent
→ backend callback → Gmail connection bound to that Chably user
```

The OAuth state is random, expires after ten minutes, and is single-use. Locally, set `GOOGLE_REDIRECT_URI=http://localhost:8000/api/v1/integrations/google/callback` and enter that exact URI in the Google OAuth client's Authorized redirect URIs. Production uses `https://<backend-host>/api/v1/integrations/google/callback`, again with an exact Google Console match. Read status with `GET /api/v1/integrations/google/status`. Tokens are encrypted and never returned.

Keep OAuth permissions limited to `https://www.googleapis.com/auth/gmail.send` and `https://www.googleapis.com/auth/gmail.readonly`. While Google's consent screen is in testing, authorize only listed test users. Public access may require Google verification of the sensitive Gmail scopes plus valid support/contact details, authorized domains, and privacy-policy links.

Outreach remains approval-first:

```text
opportunity contacts → draft email → approve → Gmail send
→ thread stored → POST /api/v1/integrations/google/{user_id}/sync
→ reply classification/application update → review or approved auto-reply
```

## Operations and errors

Use `GET /health` only for process liveness and `GET /api/v1/system/status` or `/api/v1/system/diagnostics` for dependency readiness. A temporary AI or search-provider outage does not make `/health` fail. Handle `401` by attempting one refresh, `404` as missing/not-owned, `409` for conflicts, and `429` with backoff. Do not retry non-idempotent sends automatically.

Local mock development requires explicit `AI_MOCK_MODE=true`, `SEARCH_MOCK_MODE=true`, and optionally `GOOGLE_OAUTH_MOCK_MODE=true`. Production startup rejects mock modes. Real search defaults to the `serper,tavily,ddg,searxng` failover chain: DDG needs no configuration; Serper and Tavily use optional API keys; SearXNG uses the optional `SEARXNG_URL`.
