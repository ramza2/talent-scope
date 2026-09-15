import { apiFetch } from '@/api/client'
import type { TechnicalGrade } from '@/api/people'

export type { TechnicalGrade }

export type SkillMatchMode = 'ANY' | 'ALL'

export type SearchSort =
  | 'RELEVANCE'
  | 'CAREER_DESC'
  | 'UPDATED_DESC'
  | 'RECENT_PROJECT_DESC'
  | 'NAME_ASC'

export type MatchType = 'REQUIRED' | 'PREFERRED'
export type MatchStatus = 'MATCH' | 'NO_MATCH'

export type GradeFilter = {
  values: TechnicalGrade[]
}

export type CareerFilter = {
  min_months?: number | null
  max_months?: number | null
}

export type SearchConditionBlock = {
  jobs: string[]
  skills: string[]
  expertise: string[]
  business_domains: string[]
  customer_types: string[]
  grade?: GradeFilter | null
  career?: CareerFilter | null
  affiliations: string[]
  certifications: string[]
  project_keywords: string[]
}

export type PreferredConditionBlock = {
  jobs: string[]
  skills: string[]
  expertise: string[]
  business_domains: string[]
  customer_types: string[]
}

/** Executable fields shared by Interpret data and Search people request. */
export type SearchExecutableQuery = {
  required: SearchConditionBlock
  preferred: PreferredConditionBlock
  skill_match_mode: SkillMatchMode
  semantic_query: string | null
  keyword_query: string | null
  sort: SearchSort
}

export type SearchInterpretData = SearchExecutableQuery & {
  query_version: '1.0'
  assumptions: string[]
}

export type SearchInterpretRequest = {
  text: string
  previous_query?: SearchInterpretData | null
}

export type SearchInterpretResponse = {
  data: SearchInterpretData
}

export type SearchPeopleRequest = SearchExecutableQuery & {
  page: number
  page_size: number
  suggest_relaxations: boolean
}

export type MatchItem = {
  condition: string
  type: MatchType
  status: MatchStatus
  evidence_count: number
}

export type SearchPersonSummary = {
  name: string
  technical_grade?: string | null
  career_months?: number | null
  primary_jobs: string[]
  skills: string[]
  expertise: string[]
}

export type TopProjectItem = {
  project_id: string
  project_name: string
  period?: string | null
  roles: string[]
  evidence_ids: string[]
}

export type EvidenceItem = {
  evidence_id?: string | null
  source_level: string
  target_type?: string | null
  target_id?: string | null
  field_name?: string | null
  relation_type?: string | null
  document_id?: string | null
  document_title?: string | null
  original_filename?: string | null
  version_no?: number | null
  page_no?: number | null
  snippet?: string | null
}

export type SearchPersonResult = {
  person_id: string
  score: number
  person: SearchPersonSummary
  matches: MatchItem[]
  top_projects: TopProjectItem[]
  evidence: EvidenceItem[]
}

export type SearchMeta = {
  page: number
  page_size: number
  total: number
  total_pages: number
  candidate_limit_reached: boolean
}

export type SearchPeopleResponse = {
  data: SearchPersonResult[]
  meta: SearchMeta
  query: Record<string, unknown>
  relaxations: Array<Record<string, unknown>>
}

export function interpretSearch(body: SearchInterpretRequest) {
  return apiFetch<SearchInterpretResponse>('/search/interpret', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function searchPeople(body: SearchPeopleRequest) {
  return apiFetch<SearchPeopleResponse>('/search/people', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}
