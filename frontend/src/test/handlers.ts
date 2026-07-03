import { http, HttpResponse } from 'msw'

// Default MSW handlers, persisted across server.resetHandlers(). Keep these benign so
// components that poll on mount (e.g. BackendStatusBadge -> GET /health) don't trip
// `onUnhandledRequest: 'error'`. Individual tests override with `server.use(...)`.
export const defaultHandlers = [
  http.get(/\/health$/, () => HttpResponse.json({ status: 'ok', environment: 'test' })),
]
