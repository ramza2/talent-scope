import { apiFetch } from '@/api/client'

export type AnalysisStatus =
  | 'QUEUED'
  | 'PROCESSING'
  | 'REVIEWING'
  | 'CONFIRMED'
  | 'FAILED'
  | 'CANCELLED'

export type AnalysisType = 'PROFILE'

export type ChangeType = 'SAME' | 'NEW' | 'UPDATE' | 'CONFLICT' | 'REVIEW'

export type ReviewStatus = 'PENDING' | 'ACCEPTED' | 'REJECTED' | 'MODIFIED' | 'MERGED'

export type EntityType =
  | 'PROFILE'
  | 'JOB'
  | 'TECH'
  | 'EXP'
  | 'EMPLOYMENT'
  | 'EDUCATION'
  | 'CERTIFICATION'
  | 'PROJECT'

export type PageMeta = {
  page: number
  page_size: number
  total: number
  total_pages: number
}

export type DiffCounts = {
  same: number
  new: number
  update: number
  conflict: number
  review: number
  pending: number
}

export type PersonBrief = {
  id: string
  name: string
}

export type DocumentBrief = {
  id: string
  original_filename: string
  version_no?: number | null
  document_type_code?: string | null
  document_type_name?: string | null
}

export type AnalysisListItem = {
  analysis_id: string
  person: PersonBrief
  documents: string[]
  status: AnalysisStatus
  counts: DiffCounts
  base_profile_version?: number | null
  llm_model?: string | null
  prompt_version?: string | null
  overall_confidence?: number | string | null
  error_message?: string | null
  started_at?: string | null
  completed_at?: string | null
  created_at: string
}

export type AnalysisDetail = {
  analysis_id: string
  status: AnalysisStatus
  analysis_type: AnalysisType
  person: PersonBrief
  documents: DocumentBrief[]
  counts: DiffCounts
  candidate_json: Record<string, unknown>
  base_profile_version?: number | null
  llm_model?: string | null
  vlm_model?: string | null
  prompt_version?: string | null
  schema_version?: string | null
  overall_confidence?: number | string | null
  error_message?: string | null
  started_at?: string | null
  completed_at?: string | null
  created_at: string
  updated_at: string
}

export type EvidenceLite = {
  id?: string | null
  document_id?: string | null
  page_no?: number | null
  quote_text?: string | null
}

export type DiffItem = {
  id: string
  entity_type: string
  candidate_path?: string | null
  existing_target_id?: string | null
  field_name?: string | null
  change_type: ChangeType
  old_value?: unknown
  new_value?: unknown
  confidence?: number | string | null
  evidence_type?: string | null
  review_status: ReviewStatus
  decided_value?: unknown
  decided_by?: string | null
  decided_at?: string | null
  evidence: EvidenceLite[]
}

export type AnalysisListFilters = {
  status?: AnalysisStatus | ''
  person_id?: string
  page?: number
  page_size?: number
  sort?: string
}

export type DiffListFilters = {
  change_types?: string
  review_status?: ReviewStatus | ''
  entity_type?: EntityType | ''
}

export type DiffDecisionBody = {
  review_status: ReviewStatus
  decided_value?: unknown
  existing_target_id?: string | null
}

export type BulkDiffBody = {
  diff_ids: string[]
  review_status: ReviewStatus
}

/** Confidence display labels — UI only, never auto-approve. */
export const CONFIDENCE_HIGH = 0.85
export const CONFIDENCE_MEDIUM = 0.6

export function confidenceLabel(value: number | string | null | undefined): string {
  if (value == null || value === '') return '—'
  const n = typeof value === 'string' ? Number(value) : value
  if (Number.isNaN(n)) return '—'
  if (n >= CONFIDENCE_HIGH) return '높음'
  if (n >= CONFIDENCE_MEDIUM) return '확인 권장'
  return '확인 필요'
}

export function confidenceColor(
  value: number | string | null | undefined,
): 'success' | 'warning' | 'error' | 'default' {
  if (value == null || value === '') return 'default'
  const n = typeof value === 'string' ? Number(value) : value
  if (Number.isNaN(n)) return 'default'
  if (n >= CONFIDENCE_HIGH) return 'success'
  if (n >= CONFIDENCE_MEDIUM) return 'warning'
  return 'error'
}

export function listAnalyses(filters: AnalysisListFilters = {}) {
  const params = new URLSearchParams()
  if (filters.status) params.set('status', filters.status)
  if (filters.person_id) params.set('person_id', filters.person_id)
  if (filters.sort) params.set('sort', filters.sort)
  params.set('page', String(filters.page ?? 1))
  params.set('page_size', String(filters.page_size ?? 20))
  return apiFetch<{ data: AnalysisListItem[]; meta: PageMeta }>(
    `/analyses?${params.toString()}`,
  )
}

export function createAnalysis(body: {
  person_id: string
  document_ids: string[]
  analysis_type?: AnalysisType
}) {
  return apiFetch<{ data: { analysis_id: string; status: AnalysisStatus } }>(
    '/analyses',
    {
      method: 'POST',
      body: JSON.stringify({
        analysis_type: 'PROFILE',
        ...body,
      }),
    },
  )
}

export function getAnalysis(analysisId: string) {
  return apiFetch<{ data: AnalysisDetail }>(`/analyses/${analysisId}`)
}

export function listAnalysisDiffs(analysisId: string, filters: DiffListFilters = {}) {
  const params = new URLSearchParams()
  if (filters.change_types) params.set('change_types', filters.change_types)
  if (filters.review_status) params.set('review_status', filters.review_status)
  if (filters.entity_type) params.set('entity_type', filters.entity_type)
  const qs = params.toString()
  return apiFetch<{ data: DiffItem[] }>(
    `/analyses/${analysisId}/diffs${qs ? `?${qs}` : ''}`,
  )
}

export function reviewDiff(
  analysisId: string,
  diffId: string,
  body: DiffDecisionBody,
) {
  return apiFetch<{ data: DiffItem }>(`/analyses/${analysisId}/diffs/${diffId}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

export function bulkReviewDiffs(analysisId: string, body: BulkDiffBody) {
  return apiFetch<{ data: DiffItem[] }>(`/analyses/${analysisId}/diffs/bulk`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function retryAnalysis(analysisId: string) {
  return apiFetch<{ data: { analysis_id: string; status: AnalysisStatus } }>(
    `/analyses/${analysisId}/retry`,
    {
      method: 'POST',
      body: JSON.stringify({}),
    },
  )
}

export type ConfirmAnalysisResult = {
  analysis_id: string
  person_id: string
  profile_version: number
  status: AnalysisStatus
  search_index_status: string
}

export function confirmAnalysis(
  analysisId: string,
  body: { expected_profile_version: number },
) {
  return apiFetch<{ data: ConfirmAnalysisResult }>(`/analyses/${analysisId}/confirm`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/** Project root REVIEW only — relation REVIEW must not show merge. */
export function isProjectRootReview(diff: DiffItem): boolean {
  return (
    diff.entity_type === 'PROJECT' &&
    diff.change_type === 'REVIEW' &&
    (diff.field_name == null || diff.field_name === '') &&
    /^projects\[\d+\]$/.test(diff.candidate_path || '')
  )
}

export function countPendingActionableDiffs(diffs: DiffItem[]): number {
  return diffs.filter(
    (d) =>
      d.review_status === 'PENDING' &&
      d.change_type !== 'SAME' &&
      ['NEW', 'UPDATE', 'CONFLICT', 'REVIEW'].includes(d.change_type),
  ).length
}

/** Confirm CTA gate — require diffs query success so loading ≠ pending 0. */
export function canConfirmAnalysis(input: {
  status?: AnalysisStatus | string | null
  diffsQuerySuccess: boolean
  pendingActionable: number
  baseProfileVersion: number | null | undefined
}): boolean {
  return (
    input.status === 'REVIEWING' &&
    input.diffsQuerySuccess &&
    input.pendingActionable === 0 &&
    input.baseProfileVersion != null
  )
}

export function isActiveAnalysisStatus(status: AnalysisStatus): boolean {
  return status === 'QUEUED' || status === 'PROCESSING'
}

export {
  MODIFIED_DEFAULT_STRIP_KEYS,
  buildDefaultModifiedDecision,
  buildMergeDecisionRequestBody,
  initialMergeDecidedValueText,
  isProfileScalarDiff,
} from '@/api/analysisDecisions'
