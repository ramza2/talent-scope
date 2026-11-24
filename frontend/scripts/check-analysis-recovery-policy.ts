import assert from 'node:assert/strict'
import { currentOrphanDiagnosis, canRequestOrphanRecovery } from '../src/api/analysisRecoveryPolicy.ts'
import type { AnalysisOrphanCheck } from '../src/api/analysisRecovery.ts'

const id = 'test-run'
const updatedAt = '2026-11-24T01:00:00Z'
const valid: AnalysisOrphanCheck = {
  analysis_id: id,
  status: 'PROCESSING',
  started_at: '2026-11-24T00:00:00Z',
  updated_at: updatedAt,
  stale: true,
  worker_task_state: 'ABSENT',
  recovery_allowed: true,
  reason: 'OK',
}
const allowed = (result: AnalysisOrphanCheck | null, runId = id, revision = updatedAt) =>
  canRequestOrphanRecovery(currentOrphanDiagnosis(result, runId, revision))

assert.equal(allowed(valid), true)
assert.equal(allowed(null), false)
assert.equal(allowed(valid, 'another-run'), false)
assert.equal(allowed(valid, id, '2026-11-24T01:01:00Z'), false)
assert.equal(allowed({ ...valid, updated_at: null }), false)
assert.equal(allowed({ ...valid, status: 'FAILED' }), false)
assert.equal(allowed({ ...valid, stale: false }), false)
assert.equal(allowed({ ...valid, recovery_allowed: false }), false)
for (const state of ['ACTIVE', 'UNKNOWN', 'NOT_CHECKED'] as const) {
  assert.equal(allowed({ ...valid, worker_task_state: state }), false)
}
console.log('V41B_RECOVERY_POLICY_11_CASES_PASS')
