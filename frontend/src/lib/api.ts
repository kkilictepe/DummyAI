// REST client for the backend. Components never call fetch/axios directly — they go
// through the typed helpers here (CLAUDE.md: backend access only via src/lib).

import axios from 'axios'
import { env } from './env'

export const api = axios.create({
  baseURL: env.apiUrl,
  timeout: 15_000,
  headers: { Accept: 'application/json' },
})

/** Mirrors the backend `HealthResponse` (backend/src/schemas.py). */
export interface HealthResponse {
  status: 'ok' | 'degraded'
  environment: string
  dependencies?: Record<string, string> | null
}

export async function getHealth(): Promise<HealthResponse> {
  const { data } = await api.get<HealthResponse>('/health')
  return data
}
