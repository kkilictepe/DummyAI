# Dummy AI — SAP Basis AI Operations Platform

AI agents that act as SAP Basis operators: they monitor ~20 SAP systems and answer/act on
operational questions. Data sources for almost every flow: **Prometheus** (metrics) and
**Elasticsearch** (logs). Features are organized as **flows** (Chatbot Copilot, Root Cause
Analysis, Alert Analysis, Daily HealthCheck, ...).

## Monorepo layout
- `backend/`  — FastAPI REST API + LangChain/LangGraph agents. Python 3.12, **uv**. See [backend/CLAUDE.md](backend/CLAUDE.md).
- `frontend/` — Vite + React + TypeScript UI, one page per flow. **npm**. See [frontend/CLAUDE.md](frontend/CLAUDE.md).
- Backend↔frontend agent streaming uses the **AG-UI** protocol over SSE.

**Run commands from inside the relevant package dir** (`backend/` or `frontend/`), never the repo root.

## Architecture — the mental model
- A **flow** = one feature = one LangGraph graph (backend) + one page (frontend). The backend
  endpoint path mirrors the frontend route (e.g. `/copilot` endpoint ↔ `/copilot` page); the
  agent/flow name string must match on both sides.
- Layer discipline: **agents → tools → clients → (Prometheus / Elasticsearch)**. Never skip a layer.

## Global rules
- **NEVER commit `.env`, secrets, passwords, or API keys.** Secrets live only in `.env` (gitignored);
  non-secret config lives in committed YAML under `backend/config/`.
- Commit `backend/pyproject.toml` + `uv.lock` and `frontend/package.json` + `package-lock.json`.
  **Never commit** `.venv/` or `node_modules/`.
- **After adding or changing a feature, add or update its tests in the same change.** (Project rule.)
- Default branch is `main`.
- Don't hardcode volatile facts (LLM model IDs, dependency versions) in docs — read them from
  `backend/src/config.py` or the lockfiles.
