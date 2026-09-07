import { apiFetch } from '@/api/client'
import type { CodeRef, PageMeta } from '@/api/people'

export type ProjectExpertiseItem = CodeRef & {
  evidence_type: 'EXPLICIT' | 'INFERRED'
}

export type ProjectDetail = {
  id: string
  person_id: string
  project_name: string
  customer_name?: string | null
  start_date?: string | null
  end_date?: string | null
  duration_months?: number | null
  responsibilities?: string | null
  project_summary?: string | null
  source_type: string
  source_analysis_run_id?: string | null
  jobs: CodeRef[]
  skills: CodeRef[]
  expertise: ProjectExpertiseItem[]
  business_domains: CodeRef[]
  customer_types: CodeRef[]
  created_at: string
  updated_at: string
}

export type ProjectFilters = {
  job_codes?: string
  tech_codes?: string
  exp_codes?: string
  biz_codes?: string
  customer_type_codes?: string
  from?: string
  to?: string
  page?: number
  page_size?: number
}

export function listPersonProjects(personId: string, filters: ProjectFilters = {}) {
  const params = new URLSearchParams()
  if (filters.job_codes) params.set('job_codes', filters.job_codes)
  if (filters.tech_codes) params.set('tech_codes', filters.tech_codes)
  if (filters.exp_codes) params.set('exp_codes', filters.exp_codes)
  if (filters.biz_codes) params.set('biz_codes', filters.biz_codes)
  if (filters.customer_type_codes) params.set('customer_type_codes', filters.customer_type_codes)
  if (filters.from) params.set('from', filters.from)
  if (filters.to) params.set('to', filters.to)
  params.set('page', String(filters.page ?? 1))
  params.set('page_size', String(filters.page_size ?? 50))
  const qs = params.toString()
  return apiFetch<{ data: ProjectDetail[]; meta: PageMeta }>(
    `/people/${personId}/projects?${qs}`,
  )
}

export function getProject(projectId: string) {
  return apiFetch<{ data: ProjectDetail }>(`/projects/${projectId}`)
}

export function createPersonProject(personId: string, body: Record<string, unknown>) {
  return apiFetch<{ data: ProjectDetail }>(`/people/${personId}/projects`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function updateProject(projectId: string, body: Record<string, unknown>) {
  return apiFetch<{ data: ProjectDetail }>(`/projects/${projectId}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

export function deleteProject(projectId: string) {
  return apiFetch<void>(`/projects/${projectId}`, { method: 'DELETE' })
}
