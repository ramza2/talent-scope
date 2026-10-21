import type { AnalysisStatus } from '@/api/analyses'

export type AnalysesListFilters = {
  status: AnalysisStatus | ''
  page: number
  page_size: number
}

export const ANALYSES_QUEUE_DEFAULTS: AnalysesListFilters = {
  status: 'REVIEWING',
  page: 1,
  page_size: 20,
}

export const ADMIN_ANALYSES_DEFAULTS: AnalysesListFilters = {
  status: '',
  page: 1,
  page_size: 20,
}

const ANALYSIS_STATUSES = new Set<AnalysisStatus>([
  'DEFERRED',
  'QUEUED',
  'PROCESSING',
  'REVIEWING',
  'CONFIRMED',
  'FAILED',
  'CANCELLED',
])

const ALLOWED_RETURN_PATHS = new Set(['/analyses', '/admin/analyses'])

const ANALYSES_RETURN_QUERY_KEY = 'from'

function parsePositiveInt(raw: string | null, fallback: number): number {
  if (raw == null || raw === '') return fallback
  const n = Number.parseInt(raw, 10)
  if (!Number.isFinite(n) || n < 1) return fallback
  return n
}

function parsePageSize(raw: string | null, fallback: number): number {
  const n = parsePositiveInt(raw, fallback)
  // Ant Design size changer defaults; reject absurd values.
  if (n > 100) return fallback
  return n
}

export function parseAnalysesListFilters(
  params: URLSearchParams,
  defaults: AnalysesListFilters,
): AnalysesListFilters {
  const rawStatus = params.get('status')
  let status = defaults.status
  if (rawStatus !== null) {
    if (rawStatus === '') {
      status = ''
    } else if (ANALYSIS_STATUSES.has(rawStatus as AnalysisStatus)) {
      status = rawStatus as AnalysisStatus
    } else {
      status = defaults.status
    }
  }

  return {
    status,
    page: parsePositiveInt(params.get('page'), defaults.page),
    page_size: parsePageSize(params.get('page_size'), defaults.page_size),
  }
}

export function toAnalysesListSearchParams(
  filters: AnalysesListFilters,
): URLSearchParams {
  const params = new URLSearchParams()
  // Always emit status so ``status=`` (all) is distinct from a missing
  // param (page defaults, e.g. REVIEWING on ``/analyses``).
  params.set('status', filters.status)
  params.set('page', String(filters.page))
  params.set('page_size', String(filters.page_size))
  return params
}

/** Allow only internal list paths (`/analyses`, `/admin/analyses`) + query. */
export function sanitizeAnalysesReturnTo(
  raw: string | null | undefined,
  fallback = '/analyses',
): string {
  if (raw == null || raw === '') return fallback

  let value = raw.trim()
  try {
    // Detail may pass an already-decoded search param; tolerate once-encoded input.
    if (value.includes('%')) {
      value = decodeURIComponent(value)
    }
  } catch {
    return fallback
  }

  if (!value.startsWith('/') || value.startsWith('//')) {
    return fallback
  }
  if (value.includes('://') || value.includes('\\')) {
    return fallback
  }

  const qIndex = value.indexOf('?')
  const path = qIndex === -1 ? value : value.slice(0, qIndex)
  const search = qIndex === -1 ? '' : value.slice(qIndex)

  if (!ALLOWED_RETURN_PATHS.has(path)) {
    return fallback
  }
  return `${path}${search}`
}

export function buildAnalysisDetailPath(
  analysisId: string,
  returnTo: string,
): string {
  const safe = sanitizeAnalysesReturnTo(returnTo)
  const params = new URLSearchParams()
  params.set(ANALYSES_RETURN_QUERY_KEY, safe)
  return `/analyses/${encodeURIComponent(analysisId)}?${params.toString()}`
}

export function readAnalysesReturnTo(
  params: URLSearchParams,
  fallback = '/analyses',
): string {
  return sanitizeAnalysesReturnTo(
    params.get(ANALYSES_RETURN_QUERY_KEY),
    fallback,
  )
}

export function listLocationToReturnTo(
  pathname: string,
  search: string,
): string {
  const normalizedSearch = search && !search.startsWith('?') ? `?${search}` : search
  return sanitizeAnalysesReturnTo(`${pathname}${normalizedSearch || ''}`)
}
