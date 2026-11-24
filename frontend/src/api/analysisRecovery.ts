import { apiFetch } from '@/api/client'

export type AnalysisOrphanCheck = {
  analysis_id: string
  status: string
  started_at: string | null
  updated_at: string | null
  stale: boolean
  worker_task_state: 'ACTIVE' | 'ABSENT' | 'UNKNOWN' | 'NOT_CHECKED'
  recovery_allowed: boolean
  reason: string
}

export function checkAnalysisOrphan(analysisId: string) {
  return apiFetch<{ data: AnalysisOrphanCheck }>(
    `/analyses/${encodeURIComponent(analysisId)}/orphan-check`,
  )
}

export function recoverAnalysisOrphan(analysisId: string) {
  return apiFetch<{ data: { analysis_id: string; status: 'FAILED'; recovered: boolean } }>(
    `/analyses/${encodeURIComponent(analysisId)}/recover-orphan`,
    { method: 'POST' },
  )
}
