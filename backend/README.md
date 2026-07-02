# Dummy AI — Backend

FastAPI + LangGraph agents for the SAP Basis AI Operations Platform. See
[CLAUDE.md](CLAUDE.md) for conventions and commands.

```bash
uv sync
uv run uvicorn src.main:app --reload --port 8000
uv run pytest -q
```
