// The ONE place `import.meta.env` is read. Everything else imports `env` from here.
//
// Both bases default to the dev-proxy prefixes ("/agui", "/api"). We resolve any
// relative base to an ABSOLUTE URL against the current origin, because the AG-UI SDK
// and axios both use the global `fetch` (undici under Vitest/jsdom), which throws
// "Failed to parse URL" on relative URLs. In the browser this is equivalent to the
// relative path; in tests it resolves against jsdom's origin.

function resolveBase(raw: string | undefined, fallback: string, name: string): string {
  const value = (raw ?? fallback).trim()
  if (!value) {
    throw new Error(`${name} must not be blank`)
  }
  const trimmed = value.replace(/\/+$/, '') // drop trailing slash(es); callers append "/copilot" etc.
  if (/^https?:\/\//i.test(trimmed)) {
    return trimmed
  }
  const origin =
    typeof window !== 'undefined' && window.location ? window.location.origin : 'http://localhost'
  return new URL(trimmed || '/', origin).href.replace(/\/+$/, '')
}

export interface Env {
  /** Absolute base for AG-UI SSE endpoints; append a flow path, e.g. `${aguiBaseUrl}/copilot`. */
  readonly aguiBaseUrl: string
  /** Absolute base for the REST API; used as the axios baseURL. */
  readonly apiUrl: string
}

export const env: Env = {
  aguiBaseUrl: resolveBase(import.meta.env.VITE_AGUI_BASE_URL, '/agui', 'VITE_AGUI_BASE_URL'),
  apiUrl: resolveBase(import.meta.env.VITE_API_URL, '/api', 'VITE_API_URL'),
}
