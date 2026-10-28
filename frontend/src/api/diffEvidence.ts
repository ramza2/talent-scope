/**
 * Original-document grounding helpers for Diff review (unit-testable).
 */

import type { DiffItem, EvidenceLite } from '@/api/analyses'

const ROOT_JOB_PATH_RE = /^jobs\[\d+\]$/
const ROOT_EXP_PATH_RE = /^expertise\[\d+\]$/

export function iterNewValueSourceRefs(
  newValue: unknown,
): Record<string, unknown>[] {
  if (!newValue || typeof newValue !== 'object' || Array.isArray(newValue)) {
    return []
  }
  const refs = (newValue as Record<string, unknown>).source_refs
  if (!Array.isArray(refs)) return []
  return refs.filter(
    (ref): ref is Record<string, unknown> =>
      Boolean(ref) && typeof ref === 'object' && !Array.isArray(ref),
  )
}

export function sourceRefHasOriginalGrounding(
  ref: Record<string, unknown>,
): boolean {
  return Boolean(String(ref.quote_text ?? '').trim())
}

export function evidenceItemHasOriginalGrounding(
  item: EvidenceLite | Record<string, unknown> | null | undefined,
): boolean {
  if (!item) return false
  const row = item as Record<string, unknown>
  if (row.id != null && String(row.id).trim()) return true
  return Boolean(String(row.quote_text ?? '').trim())
}

/** True when Diff carries real 원문 grounding (refs and/or linked Evidence). */
export function diffHasOriginalEvidence(diff: {
  new_value?: unknown
  evidence?: EvidenceLite[] | null
}): boolean {
  for (const ref of iterNewValueSourceRefs(diff.new_value)) {
    if (sourceRefHasOriginalGrounding(ref)) return true
  }
  for (const item of diff.evidence ?? []) {
    if (evidenceItemHasOriginalGrounding(item)) return true
  }
  return false
}

/** Root JOB/EXP NEW only — never PROJECT relation rows. */
export function isRootJobOrExpNew(diff: {
  entity_type?: string | null
  change_type?: string | null
  field_name?: string | null
  candidate_path?: string | null
}): boolean {
  if ((diff.change_type || '').toUpperCase() !== 'NEW') return false
  if (diff.field_name != null && diff.field_name !== '') return false
  const entity = (diff.entity_type || '').toUpperCase()
  const path = diff.candidate_path || ''
  if (entity === 'JOB') return ROOT_JOB_PATH_RE.test(path)
  if (entity === 'EXP') return ROOT_EXP_PATH_RE.test(path)
  return false
}

export function isUngroundedRootJobOrExpNew(diff: DiffItem): boolean {
  return (
    isRootJobOrExpNew(diff) && !diffHasOriginalEvidence(diff)
  )
}
