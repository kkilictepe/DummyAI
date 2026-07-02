# Frontend — Dummy AI (Vite + React + TypeScript)

Web UI for the agent flows. **One route/page per flow.** Consumes the backend's **AG-UI** SSE
endpoints directly via `@ag-ui/client` (`HttpAgent`) — no CopilotKit; we build our own chat UI.
Package manager: **npm**.

## Commands (run from `frontend/`)
```bash
npm install
npm run dev            # Vite dev server, http://localhost:5173
npm run build          # tsc -b && vite build
npm run preview
npm run lint           # eslint
npm run test           # vitest (watch);  npx vitest run  for CI
npx tsc --noEmit       # type-check
```

## Layout (feature-based)
- `src/app/`             — router (`react-router`) + providers + root layout.
- `src/features/<flow>/` — one folder per flow: `page.tsx`, `components/`,
  `hooks/use<Flow>Agent.ts` (wraps the AG-UI `HttpAgent`), `types.ts`.
- `src/lib/`             — `agui.ts` (HttpAgent factory), `api.ts` (axios REST client), env parsing.
- `src/components/`      — shared UI primitives.
- `*.test.tsx` co-located; global setup in `src/setupTests.ts`.

## AG-UI consumption
- Each flow page creates `new HttpAgent({ url })` (from `@ag-ui/client`) pointed at that flow's backend
  SSE endpoint, then drives it with `agent.runAgent(input)` / subscribes via `AgentSubscriber`.
- Handle the event families: lifecycle `RUN_STARTED`…`RUN_FINISHED`/`RUN_ERROR`; text
  `TEXT_MESSAGE_START/CONTENT/END`; tools `TOOL_CALL_*`; state `STATE_SNAPSHOT`/`STATE_DELTA`.
- The **flow name and endpoint path must match the backend**; the route path mirrors the endpoint path.

## Rules
- **Env vars must be `VITE_`-prefixed and read via `import.meta.env`** — never `process.env` in browser
  code. Use `VITE_AGUI_BASE_URL` (agent endpoints) and `VITE_API_URL` (REST).
- Talk to the backend only through `src/lib/` (`agui.ts` / `api.ts`) — **don't call `fetch()` directly
  in components.**
- TypeScript `strict` on. Keep the AG-UI state type in sync with the backend LangGraph state shape
  (STATE_DELTA is an RFC-6902 JSON Patch — a mismatched shape fails to apply).
- In dev, use Vite `server.proxy` for `/api` + the AG-UI endpoints (or ensure backend CORS allows
  `:5173`). **Disable proxy buffering** or SSE streaming will appear to hang.
- Any chatbox connected to AI Agents should support markdown parsing.

## Testing (required after every change)
- **Vitest + React Testing Library** (`environment: 'jsdom'`, add `/// <reference types="vitest/config" />`
  to `vite.config.ts`). Mock the AG-UI SSE stream with **MSW** so flow pages test without a live backend.
