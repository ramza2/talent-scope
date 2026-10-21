/** Pure Diff value display helpers (unit-testable via tsx). */

export const DIFF_VALUE_PRIORITY_KEYS = [
  'company_name',
  'school_name',
  'certification_name',
  'project_name',
  'name',
  'code',
  'raw_value',
  'title',
  'department',
  'start_date',
  'end_date',
  'acquired_date',
  'degree',
  'major',
  'status',
] as const

export function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

/** Prefer key career/date fields; keep full JSON available separately.
 *
 * Explicit ``null`` / ``""`` on priority keys (e.g. MODIFIED ``end_date: null``)
 * render as ``미지정`` so cleared fields stay visible. Missing keys are omitted.
 */
export function formatDiffValueSummary(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value !== 'object' || Array.isArray(value)) {
    return formatValue(value)
  }
  const row = value as Record<string, unknown>
  const parts: string[] = []
  for (const key of DIFF_VALUE_PRIORITY_KEYS) {
    if (!Object.prototype.hasOwnProperty.call(row, key)) continue
    const raw = row[key]
    if (raw === undefined) continue
    if (raw === null || raw === '') {
      parts.push(`${key}: 미지정`)
      continue
    }
    if (typeof raw === 'object') continue
    parts.push(`${key}: ${String(raw)}`)
  }
  if (parts.length) return parts.join('\n')
  return formatValue(value)
}
