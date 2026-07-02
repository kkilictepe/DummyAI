"""Manual SSE smoke test for the Copilot flow.

Streams one AG-UI turn against a running backend and prints each decoded event, so you can watch
the RUN_STARTED -> TOOL_CALL_* -> TEXT_MESSAGE_* -> RUN_FINISHED sequence (or the refusal path)
against a real OpenAI model + live Prometheus/Elasticsearch.

Prereqs: a running server with a real OPENAI_API_KEY, e.g.
    uv run uvicorn src.main:app --reload --port 8000

Usage (from backend/):
    uv run python scripts/smoke_copilot.py "How is CPU on KHP?"
    uv run python scripts/smoke_copilot.py --off-topic "Write me a poem about the sea."
    uv run python scripts/smoke_copilot.py --systems KHP,KBP "Any errors on my systems?"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid

import httpx


async def main() -> int:
    parser = argparse.ArgumentParser(description="Stream one Copilot turn and print AG-UI events.")
    parser.add_argument("question", help="The user question to send.")
    parser.add_argument("--url", default="http://localhost:8000/copilot")
    parser.add_argument("--systems", default=None, help="Comma-separated system_ids scope.")
    parser.add_argument(
        "--off-topic",
        action="store_true",
        help="Only affects your expectation — the guardrail still classifies server-side.",
    )
    args = parser.parse_args()

    context = []
    if args.systems:
        context.append({"description": "system_ids", "value": args.systems})

    body = {
        "threadId": f"smoke-{uuid.uuid4().hex[:8]}",
        "runId": f"run-{uuid.uuid4().hex[:8]}",
        "state": {},
        "messages": [{"id": "m1", "role": "user", "content": args.question}],
        "tools": [],
        "context": context,
        "forwardedProps": {},
    }

    text_parts: list[str] = []
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("POST", args.url, json=body) as resp:
            if resp.status_code != 200:
                await resp.aread()
                print(f"HTTP {resp.status_code}: {resp.text}", file=sys.stderr)
                return 1
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[len("data:") :].strip())
                etype = event.get("type")
                if etype == "TEXT_MESSAGE_CONTENT":
                    text_parts.append(event.get("delta", ""))
                    print(event.get("delta", ""), end="", flush=True)
                elif etype == "TOOL_CALL_START":
                    print(f"\n[tool: {event.get('toolCallName')}] ", end="", flush=True)
                elif etype in ("RUN_STARTED", "RUN_FINISHED", "RUN_ERROR"):
                    print(f"\n<<< {etype} >>>", flush=True)

    if not text_parts:
        print("\n(no assistant text streamed)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
