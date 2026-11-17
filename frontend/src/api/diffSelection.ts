/**
 * Cross-page pending Diff selection helpers (unit-testable, no API calls).
 */

export type SelectableDiffLike = {
  id: string
  review_status?: string | null
  change_type?: string | null
}

/** PENDING and not SAME — matches Issue #152 bulk select-all scope. */
export function isSelectablePendingDiff(diff: SelectableDiffLike): boolean {
  return (
    (diff.review_status || '').toUpperCase() === 'PENDING' &&
    (diff.change_type || '').toUpperCase() !== 'SAME'
  )
}

/** Ordered unique ids for pending actionable rows in the current filter result. */
export function selectablePendingDiffIds(
  diffs: SelectableDiffLike[] | null | undefined,
): string[] {
  if (!diffs?.length) return []
  const seen = new Set<string>()
  const out: string[] = []
  for (const diff of diffs) {
    if (!isSelectablePendingDiff(diff)) continue
    if (!diff.id || seen.has(diff.id)) continue
    seen.add(diff.id)
    out.push(diff.id)
  }
  return out
}

/** True when every selectable pending id is already selected. */
export function allSelectablePendingSelected(
  diffs: SelectableDiffLike[] | null | undefined,
  selectedKeys: readonly unknown[],
): boolean {
  const ids = selectablePendingDiffIds(diffs)
  if (!ids.length) return false
  const selected = new Set(selectedKeys.map(String))
  return ids.every((id) => selected.has(id))
}
