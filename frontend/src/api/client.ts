/**
 * Minimal API client for Backend `/api/v1`.
 * Frontend must never call LLM/VLM runtimes directly.
 */

export class ApiError extends Error {
  status: number
  body: unknown

  constructor(message: string, status: number, body: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

const API_BASE = '/api/v1'

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: 'include',
    headers: {
      Accept: 'application/json',
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...(init.headers ?? {}),
    },
    ...init,
  })

  const text = await response.text()
  const body = text ? (JSON.parse(text) as unknown) : null

  if (!response.ok) {
    throw new ApiError(`API ${response.status}`, response.status, body)
  }

  return body as T
}

export type HealthLive = { status: string }
export type HealthReady = {
  status: string
  database: string
  redis: string
}

export function getHealthLive() {
  return apiFetch<HealthLive>('/health/live')
}

export function getHealthReady() {
  return apiFetch<HealthReady>('/health/ready')
}
