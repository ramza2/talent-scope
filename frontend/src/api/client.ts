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

function readCookie(name: string): string | null {
  const parts = document.cookie.split(';')
  for (const part of parts) {
    const [rawKey, ...rest] = part.trim().split('=')
    if (rawKey === name) {
      return decodeURIComponent(rest.join('='))
    }
  }
  return null
}

function buildHeaders(path: string, init: RequestInit): Headers {
  const headers = new Headers(init.headers ?? {})
  if (!headers.has('Accept')) {
    headers.set('Accept', 'application/json')
  }
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const method = (init.method ?? 'GET').toUpperCase()
  const isMutating = ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)
  const isLogin = path === '/auth/login'
  if (isMutating && !isLogin) {
    const csrf = readCookie('ts_csrf')
    if (csrf && !headers.has('X-CSRF-Token')) {
      headers.set('X-CSRF-Token', csrf)
    }
  }
  return headers
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: 'include',
    headers: buildHeaders(path, init),
  })

  if (response.status === 204) {
    return undefined as T
  }

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

export type AuthUser = {
  id: string
  login_id: string
  name: string
  role: 'USER' | 'ADMIN'
  email?: string | null
  department?: string | null
}

export type AuthUserResponse = { data: AuthUser }

export function getHealthLive() {
  return apiFetch<HealthLive>('/health/live')
}

export function getHealthReady() {
  return apiFetch<HealthReady>('/health/ready')
}

export function login(loginId: string, password: string) {
  return apiFetch<AuthUserResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ login_id: loginId, password }),
  })
}

export function logout() {
  return apiFetch<void>('/auth/logout', { method: 'POST' })
}

export function getCurrentUser() {
  return apiFetch<AuthUserResponse>('/auth/me')
}
