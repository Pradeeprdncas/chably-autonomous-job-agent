# Chably AI frontend

The frontend is a standalone React + Vite application using normal CSS. It talks only to the FastAPI backend in `../backend`.

## Run locally

```bash
cd frontend
cp .env.example .env
npm install
npm run dev
```

Set `VITE_API_BASE_URL` to the backend origin (default: `http://localhost:8000`). Start the backend separately from `../backend`.

## Production configuration

Set `VITE_API_BASE_URL` to the public Render backend origin, for example `https://your-api.onrender.com`. Then set that frontend deployment URL in the backend's `FRONTEND_URL` and `CORS_ORIGINS` environment variables.

The client holds the access token only in memory. The rotating refresh token is scoped to the browser session via `sessionStorage`; it is not committed or sent anywhere except the backend refresh/logout endpoints.

See `../backend/FRONTEND_INTEGRATION.md` and `../backend/openapi.json` for the frozen API contract.
