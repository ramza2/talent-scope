import { apiFetch } from '@/api/client'

export type CodeType =
  | 'JOB'
  | 'TECH'
  | 'EXP'
  | 'BIZ'
  | 'CUSTOMER_TYPE'
  | 'DOC_TYPE'

export const CODE_TYPES: CodeType[] = [
  'JOB',
  'TECH',
  'EXP',
  'BIZ',
  'CUSTOMER_TYPE',
  'DOC_TYPE',
]

export type CodeItem = {
  code: string
  type: CodeType
  name: string
  description?: string | null
  parent_code?: string | null
  sort_order: number
  aliases: string[]
  is_active: boolean
}

export type CodeListResponse = { data: CodeItem[] }
export type CodeDetailResponse = { data: CodeItem }

export type CodeFilters = {
  type?: CodeType | ''
  q?: string
  active?: boolean | null
  parent_code?: string
}

export function listCodes(filters: CodeFilters = {}) {
  const params = new URLSearchParams()
  if (filters.type) params.set('type', filters.type)
  if (filters.q) params.set('q', filters.q)
  if (filters.parent_code) params.set('parent_code', filters.parent_code)
  if (filters.active === true) params.set('active', 'true')
  if (filters.active === false) params.set('active', 'false')
  const qs = params.toString()
  return apiFetch<CodeListResponse>(`/codes${qs ? `?${qs}` : ''}`)
}

export function getCode(code: string) {
  return apiFetch<CodeDetailResponse>(`/codes/${encodeURIComponent(code)}`)
}

export function createCode(body: {
  code: string
  type: CodeType
  name: string
  description?: string | null
  parent_code?: string | null
  sort_order?: number
  aliases?: string[]
}) {
  return apiFetch<CodeDetailResponse>('/codes', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function updateCode(
  code: string,
  body: {
    name?: string
    description?: string | null
    parent_code?: string | null
    sort_order?: number
    is_active?: boolean
  },
) {
  return apiFetch<CodeDetailResponse>(`/codes/${encodeURIComponent(code)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

export function replaceCodeAliases(code: string, aliases: string[]) {
  return apiFetch<CodeDetailResponse>(`/codes/${encodeURIComponent(code)}/aliases`, {
    method: 'PUT',
    body: JSON.stringify({ aliases }),
  })
}
