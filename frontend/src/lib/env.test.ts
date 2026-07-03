import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// env.ts reads import.meta.env at module-eval time, so each case resets modules and
// re-imports after stubbing.
async function loadEnv() {
  vi.resetModules()
  return (await import('./env')).env
}

describe('env', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.unstubAllEnvs()
  })
  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it('defaults to the dev-proxy prefixes, resolved absolute against the origin', async () => {
    const env = await loadEnv()
    expect(env.aguiBaseUrl).toBe(`${window.location.origin}/agui`)
    expect(env.apiUrl).toBe(`${window.location.origin}/api`)
  })

  it('keeps an absolute URL as-is and strips a trailing slash', async () => {
    vi.stubEnv('VITE_AGUI_BASE_URL', 'https://agents.example.com/')
    vi.stubEnv('VITE_API_URL', 'https://api.example.com')
    const env = await loadEnv()
    expect(env.aguiBaseUrl).toBe('https://agents.example.com')
    expect(env.apiUrl).toBe('https://api.example.com')
  })

  it('resolves a custom relative base against the origin', async () => {
    vi.stubEnv('VITE_AGUI_BASE_URL', '/gateway/agui/')
    const env = await loadEnv()
    expect(env.aguiBaseUrl).toBe(`${window.location.origin}/gateway/agui`)
  })

  it('throws on a blank (explicitly empty) value', async () => {
    vi.stubEnv('VITE_API_URL', '   ')
    await expect(loadEnv()).rejects.toThrow(/VITE_API_URL must not be blank/)
  })
})
