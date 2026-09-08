/**
 * Pure Confirm review helpers — no API client imports (unit-testable via tsx).
 */

export type DecisionDiffLike = {
  entity_type: string
  field_name?: string | null
  new_value?: unknown
}

const PROFILE_SCALAR_FIELDS = new Set([
  'name',
  'birth_year',
  'phone',
  'email',
  'address_region',
  'affiliation_company',
  'department',
  'current_title',
  'employment_type',
  'technical_grade',
  'career_start_date',
  'career_document_value',
  'profile_summary',
])

/** Metadata / non-DB keys never used as Confirm MODIFIED defaults. */
export const MODIFIED_DEFAULT_STRIP_KEYS = new Set([
  'source_refs',
  'confidence',
  'analysis',
  'notes',
  'raw_value',
])

export function isProfileScalarDiff(diff: DecisionDiffLike): boolean {
  return (
    diff.entity_type === 'PROFILE' &&
    Boolean(diff.field_name) &&
    PROFILE_SCALAR_FIELDS.has(diff.field_name!)
  )
}

function sanitizeModifiedDefaultValue(
  value: unknown,
  opts: { entityType: string },
): unknown {
  if (value === null || value === undefined) {
    return value
  }
  if (Array.isArray(value)) {
    return value.map((item) => sanitizeModifiedDefaultValue(item, opts))
  }
  if (typeof value !== 'object') {
    return value
  }

  const src = value as Record<string, unknown>
  const out: Record<string, unknown> = {}
  for (const [key, raw] of Object.entries(src)) {
    if (MODIFIED_DEFAULT_STRIP_KEYS.has(key)) continue
    if (raw === null || raw === undefined) continue

    if (opts.entityType === 'TECH' && key === 'is_representative') {
      // Candidate schema default false must not look like an explicit demotion.
      if (raw === true) out[key] = true
      continue
    }

    out[key] = sanitizeModifiedDefaultValue(raw, opts)
  }
  return out
}

/**
 * Safe default for Diff 수정 Modal.
 * Omits null/undefined, metadata, and TECH is_representative:false defaults.
 * Explicit user edits may still add null/false later.
 */
export function buildDefaultModifiedDecision(diff: DecisionDiffLike): string {
  if (diff.new_value === null || diff.new_value === undefined) return ''

  if (
    isProfileScalarDiff(diff) &&
    (typeof diff.new_value === 'string' || typeof diff.new_value === 'number')
  ) {
    return String(diff.new_value)
  }

  try {
    const sanitized = sanitizeModifiedDefaultValue(diff.new_value, {
      entityType: diff.entity_type,
    })
    return JSON.stringify(sanitized, null, 2)
  } catch {
    return String(diff.new_value)
  }
}

/** Merge Modal starts with empty override; candidate is shown read-only separately. */
export function initialMergeDecidedValueText(): string {
  return ''
}

/**
 * Build MERGED review body. Empty override omits decided_value property entirely.
 * Throws SyntaxError when override JSON is non-empty but invalid.
 */
export function buildMergeDecisionRequestBody(input: {
  existing_target_id: string
  decided_value_text?: string | null
}): {
  review_status: 'MERGED'
  existing_target_id: string
  decided_value?: unknown
} {
  const body: {
    review_status: 'MERGED'
    existing_target_id: string
    decided_value?: unknown
  } = {
    review_status: 'MERGED',
    existing_target_id: input.existing_target_id,
  }
  const trimmed = input.decided_value_text?.trim()
  if (trimmed) {
    body.decided_value = JSON.parse(trimmed) as unknown
  }
  return body
}
