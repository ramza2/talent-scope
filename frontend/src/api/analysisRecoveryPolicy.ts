import type { AnalysisOrphanCheck } from './analysisRecovery'

/** A result from another run, DB revision, or terminal state must not be reused. */
export function currentOrphanDiagnosis(
  diagnosis: AnalysisOrphanCheck | null,
  analysisId: string,
  updatedAt: string,
): AnalysisOrphanCheck | null {
  return diagnosis?.analysis_id === analysisId &&
    diagnosis.status === 'PROCESSING' &&
    diagnosis.updated_at === updatedAt
    ? diagnosis
    : null
}

/** UI gate only. The administrator API independently checks all conditions again. */
export function canRequestOrphanRecovery(diagnosis: AnalysisOrphanCheck | null): boolean {
  return diagnosis?.status === 'PROCESSING' &&
    diagnosis.stale === true &&
    diagnosis.worker_task_state === 'ABSENT' &&
    diagnosis.recovery_allowed === true
}
