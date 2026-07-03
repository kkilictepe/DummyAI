# Rasyona — SAP Basis AI Operations Platform

AI agents that act as SAP Basis operators: they monitor ~20 SAP systems and answer/act on
operational questions, sourcing from **Prometheus** (metrics) and **Elasticsearch** (logs).
Features are organized as **flows** (Copilot chat, Root Cause Analysis, Alert Analysis, Daily
HealthCheck, …). The first flow, **Copilot**, is implemented end to end (backend + UI).

- **`backend/`** — FastAPI REST API + LangChain/LangGraph agents. Python 3.12, managed with **uv**.
- **`frontend/`** — Vite + React + TypeScript UI (the "Rasyona" console), one page per flow. **npm**.
- The UI streams from the backend over the **AG-UI** protocol (Server-Sent Events).

> Run every command from inside the relevant package directory (`backend/` or `frontend/`),
> never the repo root.

---

## Prerequisites

| Tool | Version | Used by |
|------|---------|---------|
| [uv](https://docs.astral.sh/uv/) | latest | backend (installs & pins Python 3.12 itself) |
| Python | 3.12.x (`>=3.12,<3.13`) | backend (uv provisions it if missing) |
| Node.js | `^20.19.0` or `>=22.12.0` | frontend |
| npm | ships with Node | frontend |

---

## Quickstart

Start the **backend first** (the frontend dev server proxies to it), then the frontend.

### 1. Backend — `http://localhost:8000`

```bash
cd backend
cp .env.example .env      # then fill in the secrets — see below (PowerShell: Copy-Item .env.example .env)
uv sync                   # create .venv and install from uv.lock
uv run uvicorn src.main:app --reload --port 8000
```

The Copilot flow needs an **LLM provider key** in `.env`; Prometheus / Elasticsearch / Langfuse
credentials are optional for a first run but required for live metric/log answers. `.env` is
gitignored — **never commit it**. Check the server is up:

```bash
curl http://localhost:8000/health
```

### 2. Frontend — `http://localhost:5173`

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173** — it redirects to the **Copilot** page (`/copilot`). The dev
server proxies `/agui/*` and `/api/*` to the backend on `:8000`, so no CORS setup is needed
locally. To point at a non-default backend, copy `frontend/.env.example` to `.env.local` and
adjust (see the comments in that file).

> If the backend isn't running you'll see a "Backend unreachable" badge in the header and chat
> requests will fail — start the backend and reload.

---

## Common commands

**Backend** (from `backend/`):

```bash
uv run pytest -q                              # tests
uv run ruff check . && uv run ruff format .   # lint + format
uv run mypy src                               # type-check
```

**Frontend** (from `frontend/`):

```bash
npm run test:run    # vitest (CI mode)
npm run lint        # oxlint
npm run typecheck   # tsc -b
npm run build       # production build
```

---

## Where to look next

- **[backend/README.md](backend/README.md)** · **[backend/CLAUDE.md](backend/CLAUDE.md)** — backend setup and conventions.
- **[frontend/CLAUDE.md](frontend/CLAUDE.md)** — frontend structure and conventions.
- **[docs/flows/copilot.md](docs/flows/copilot.md)** — the Copilot flow design doc (graph, tools,
  streaming contract, and the live end-to-end verification checklist).
