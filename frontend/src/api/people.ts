import { apiFetch } from '@/api/client'

export type PersonStatus = 'ACTIVE' | 'INACTIVE' | 'ARCHIVED' | 'DELETED'
export type TechnicalGrade =
  | 'BEGINNER'
  | 'INTERMEDIATE'
  | 'ADVANCED'
  | 'EXPERT'
  | 'UNKNOWN'

export type CodeRef = { code: string; name: string }

export type PeopleListItem = {
  id: string
  status: PersonStatus
  name: string
  primary_job?: CodeRef | null
  technical_grade?: TechnicalGrade | null
  career_confirmed_months?: number | null
  affiliation_company?: string | null
  skills: CodeRef[]
  expertise: CodeRef[]
  profile_version: number
  profile_updated_at?: string | null
  updated_at: string
}

export type PageMeta = {
  page: number
  page_size: number
  total: number
  total_pages: number
}

export type PeopleFilters = {
  q?: string
  status?: PersonStatus | ''
  job_codes?: string
  grade?: TechnicalGrade | ''
  tech_codes?: string
  exp_codes?: string
  affiliation?: string
  sort?: string
  page?: number
  page_size?: number
}

export type ProfileFields = {
  name: string
  birth_year?: number | null
  phone?: string | null
  email?: string | null
  address_region?: string | null
  affiliation_company?: string | null
  department?: string | null
  current_title?: string | null
  employment_type?: string | null
  technical_grade?: TechnicalGrade | null
  career_start_date?: string | null
  career_calculated_months?: number | null
  career_document_value?: string | null
  career_confirmed_months?: number | null
  profile_summary?: string | null
  profile_updated_at?: string | null
}

export type PersonDetail = {
  id: string
  status: PersonStatus
  profile_version: number
  profile: ProfileFields
  jobs: Array<{
    code: string
    name: string
    job_type: 'PRIMARY' | 'SECONDARY' | 'EXPERIENCE'
    sort_order: number
    source_type?: string | null
  }>
  skills: Array<{
    code: string
    name: string
    last_used_year?: number | null
    experience_months?: number | null
    is_representative: boolean
    source_type?: string | null
  }>
  expertise: Array<{
    code: string
    name: string
    evidence_type: 'EXPLICIT' | 'INFERRED'
    source_type?: string | null
  }>
  business_domains: CodeRef[]
  customer_types: CodeRef[]
  recent_projects: Array<{
    id: string
    project_name: string
    customer_name?: string | null
    start_date?: string | null
    end_date?: string | null
  }>
  document_summary: { count: number; latest_document_at?: string | null }
  pending_analysis?: { id: string; status: string } | null
}

export type RevisionItem = {
  revision_no: number
  source_type: string
  created_by?: string | null
  created_by_name?: string | null
  created_at: string
  snapshot: Record<string, unknown>
}

export function listPeople(filters: PeopleFilters = {}) {
  const params = new URLSearchParams()
  if (filters.q) params.set('q', filters.q)
  if (filters.status) params.set('status', filters.status)
  if (filters.job_codes) params.set('job_codes', filters.job_codes)
  if (filters.grade) params.set('grade', filters.grade)
  if (filters.tech_codes) params.set('tech_codes', filters.tech_codes)
  if (filters.exp_codes) params.set('exp_codes', filters.exp_codes)
  if (filters.affiliation) params.set('affiliation', filters.affiliation)
  if (filters.sort) params.set('sort', filters.sort)
  params.set('page', String(filters.page ?? 1))
  params.set('page_size', String(filters.page_size ?? 20))
  return apiFetch<{ data: PeopleListItem[]; meta: PageMeta }>(`/people?${params}`)
}

export function getPerson(personId: string) {
  return apiFetch<{ data: PersonDetail }>(`/people/${personId}`)
}

export function createPerson(body: Record<string, unknown>) {
  return apiFetch<{ data: PersonDetail }>('/people', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function updatePersonStatus(personId: string, status: PersonStatus) {
  return apiFetch<{ data: PersonDetail }>(`/people/${personId}`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  })
}

export function updatePersonProfile(personId: string, body: Record<string, unknown>) {
  return apiFetch<{ data: PersonDetail }>(`/people/${personId}/profile`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

export function replacePersonJobs(personId: string, body: Record<string, unknown>) {
  return apiFetch<{ data: PersonDetail }>(`/people/${personId}/jobs`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function replacePersonSkills(personId: string, body: Record<string, unknown>) {
  return apiFetch<{ data: PersonDetail }>(`/people/${personId}/skills`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function replacePersonExpertise(personId: string, body: Record<string, unknown>) {
  return apiFetch<{ data: PersonDetail }>(`/people/${personId}/expertise`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function listPersonRevisions(personId: string, page = 1, pageSize = 20) {
  return apiFetch<{ data: RevisionItem[]; meta: PageMeta }>(
    `/people/${personId}/revisions?page=${page}&page_size=${pageSize}`,
  )
}

export function formatCareerMonths(months?: number | null): string {
  if (months == null) return '—'
  const years = Math.floor(months / 12)
  const rem = months % 12
  if (years <= 0) return `${rem}개월`
  if (rem === 0) return `${years}년`
  return `${years}년 ${rem}개월`
}

export const GRADE_LABELS: Record<TechnicalGrade, string> = {
  BEGINNER: '초급',
  INTERMEDIATE: '중급',
  ADVANCED: '고급',
  EXPERT: '특급',
  UNKNOWN: '미확정',
}
