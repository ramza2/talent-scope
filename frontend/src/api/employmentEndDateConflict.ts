/**
 * EMPLOYMENT root REVIEW end_date conflict helpers (pure / unit-testable).
 * Mirrors backend Confirm date parse rules for client-side gating only.
 */

import {
  sanitizeModifiedDecisionObject,
  type DecisionDiffLike,
} from '@/api/analysisDecisions'

/** Same separator backend uses when joining incompatible employment ends. */
export const EMPLOYMENT_END_CONFLICT_SEP = ' / '

/** Matches backend ``confirm_dates._DATE_RE`` after `.`/`/` → `-` normalization. */
const DATE_RE =
  /^(?<y>\d{4})(?:-(?<m>\d{1,2})(?:-(?<d>\d{1,2}))?)?$/
/** Matches backend ``confirm_dates._KOREAN_DATE_RE`` (internal spaces allowed). */
const KOREAN_DATE_RE =
  /^(?<y>\d{4})\s*년(?:\s*(?<m>\d{1,2})\s*월(?:\s*(?<d>\d{1,2})\s*일)?)?\s*\.?$/

export type EmploymentEndConflictDiffLike = DecisionDiffLike & {
  change_type?: string | null
  candidate_path?: string | null
  field_name?: string | null
  new_value?: unknown
}

export type ParsedPartialDate = {
  year: number
  month: number | null
  day: number | null
}

function daysInMonth(year: number, month: number): number {
  return new Date(year, month, 0).getDate()
}

/**
 * Parse YYYY / YYYY-MM / YYYY-MM-DD like Confirm ``parse_partial_date``.
 * Does not strip internal spaces on dotted/ISO forms (backend rejects those).
 * Korean ``2014년 8월`` keeps internal spaces via the Korean regex.
 */
export function parseEmploymentPartialDate(
  value: unknown,
): ParsedPartialDate | null {
  if (value == null) return null
  let text = String(value).trim()
  if (!text) return null
  if (text.includes('T')) {
    text = text.split('T', 1)[0] ?? text
  }

  const korean = KOREAN_DATE_RE.exec(text)
  let match = korean
  if (!match) {
    // Same as backend: replace separators, keep internal spaces (so they fail).
    const normalized = text.replaceAll('/', '-').replaceAll('.', '-').replace(/-+$/, '')
    match = DATE_RE.exec(normalized)
  }
  if (!match?.groups) {
    throw new Error(`invalid date: ${String(value)}`)
  }
  const year = Number(match.groups.y)
  if (year < 1900 || year > 2100) {
    throw new Error(`invalid year: ${String(value)}`)
  }
  const month = match.groups.m ? Number(match.groups.m) : null
  const day = match.groups.d ? Number(match.groups.d) : null
  if (month != null && (month < 1 || month > 12)) {
    throw new Error(`invalid month: ${String(value)}`)
  }
  if (day != null) {
    if (month == null) throw new Error(`invalid date: ${String(value)}`)
    const last = daysInMonth(year, month)
    if (day < 1 || day > last) {
      throw new Error(`invalid day: ${String(value)}`)
    }
  }
  return { year, month, day }
}

export function isParseableEmploymentDate(value: unknown): boolean {
  try {
    return parseEmploymentPartialDate(value) != null
  } catch {
    return false
  }
}

function toBoundDate(
  parts: ParsedPartialDate,
  bound: 'start' | 'end',
): Date {
  const { year, month, day } = parts
  if (bound === 'start') {
    if (month == null) return new Date(year, 0, 1)
    if (day == null) return new Date(year, month - 1, 1)
    return new Date(year, month - 1, day)
  }
  if (month == null) return new Date(year, 11, 31)
  if (day == null) {
    return new Date(year, month - 1, daysInMonth(year, month))
  }
  return new Date(year, month - 1, day)
}

/** True when conflict string still contains multiple alternatives. */
export function containsEmploymentEndConflictSep(value: unknown): boolean {
  return typeof value === 'string' && value.includes(EMPLOYMENT_END_CONFLICT_SEP)
}

/**
 * Split a known conflict end_date into discrete date candidates.
 * Returns null when the pattern is unexpected → caller should JSON-fallback.
 * Empty segments (e.g. ``"A /  / B"``) are abnormal — do not drop them.
 */
export function extractEmploymentEndDateConflictCandidates(
  endDate: unknown,
): string[] | null {
  if (typeof endDate !== 'string') return null
  if (!endDate.includes(EMPLOYMENT_END_CONFLICT_SEP)) return null
  const parts = endDate.split(EMPLOYMENT_END_CONFLICT_SEP).map((p) => p.trim())
  // Empty candidate after split → unexpected pattern → JSON fallback.
  if (parts.some((p) => !p)) return null
  if (parts.length < 2) return null
  for (const part of parts) {
    // Nested conflict or non-date tokens → do not invent a decomposition.
    if (part.includes(EMPLOYMENT_END_CONFLICT_SEP)) return null
    if (!isParseableEmploymentDate(part)) return null
  }
  const unique: string[] = []
  const seen = new Set<string>()
  for (const part of parts) {
    const key = part.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    unique.push(part)
  }
  return unique.length >= 2 ? unique : null
}

export function isEmploymentRootReview(
  diff: EmploymentEndConflictDiffLike,
): boolean {
  if (diff.entity_type !== 'EMPLOYMENT') return false
  if (diff.change_type !== 'REVIEW') return false
  if (diff.field_name != null && diff.field_name !== '') return false
  return /^employment_history\[\d+\]$/.test(diff.candidate_path || '')
}

/** Dedicated UX only when end_date is a clean multi-date conflict token. */
export function isEmploymentEndDateConflictReview(
  diff: EmploymentEndConflictDiffLike,
): boolean {
  if (!isEmploymentRootReview(diff)) return false
  if (!diff.new_value || typeof diff.new_value !== 'object' || Array.isArray(diff.new_value)) {
    return false
  }
  const endDate = (diff.new_value as Record<string, unknown>).end_date
  return extractEmploymentEndDateConflictCandidates(endDate) != null
}

export function validateChosenEmploymentEndDate(input: {
  endDate: string
  startDate?: unknown
}): { ok: true; value: string } | { ok: false; message: string } {
  const trimmed = input.endDate.trim()
  if (!trimmed) {
    return { ok: false, message: '종료일을 선택하거나 입력하세요.' }
  }
  if (containsEmploymentEndConflictSep(trimmed)) {
    return {
      ok: false,
      message: '충돌된 여러 날짜를 그대로 저장할 수 없습니다. 하나의 날짜만 선택하세요.',
    }
  }
  let endParts: ParsedPartialDate
  try {
    const parsed = parseEmploymentPartialDate(trimmed)
    if (!parsed) {
      return { ok: false, message: '종료일 형식이 올바르지 않습니다.' }
    }
    endParts = parsed
  } catch {
    return {
      ok: false,
      message: '달력상 유효하지 않은 날짜입니다. YYYY.MM.DD 형식으로 입력하세요.',
    }
  }

  if (input.startDate != null && String(input.startDate).trim()) {
    try {
      const startParts = parseEmploymentPartialDate(input.startDate)
      if (startParts) {
        const startBound = toBoundDate(startParts, 'start')
        const endBound = toBoundDate(endParts, 'end')
        if (endBound.getTime() < startBound.getTime()) {
          return {
            ok: false,
            message: '종료일은 시작일보다 빠를 수 없습니다.',
          }
        }
      }
    } catch {
      // Unparseable start is a separate REVIEW concern; do not block end choice.
    }
  }

  return { ok: true, value: trimmed }
}

/**
 * Build MODIFIED decided_value: sanitized root object with only end_date replaced.
 * Follows {@link sanitizeModifiedDecisionObject} metadata strip rules.
 */
export function buildEmploymentEndDateModifiedDecision(
  diff: EmploymentEndConflictDiffLike,
  chosenEndDate: string,
): Record<string, unknown> {
  const sanitized = sanitizeModifiedDecisionObject(
    diff.new_value,
    diff.entity_type,
  )
  const base =
    sanitized && typeof sanitized === 'object' && !Array.isArray(sanitized)
      ? { ...(sanitized as Record<string, unknown>) }
      : {}
  return {
    ...base,
    end_date: chosenEndDate,
  }
}

/** Radio value for free-text end date entry. */
export const EMPLOYMENT_END_DATE_CUSTOM_VALUE = '__custom__'

export function resolveEmploymentEndDateSelection(input: {
  selected: string | null
  customDate: string
}): string {
  if (!input.selected) return ''
  if (input.selected === EMPLOYMENT_END_DATE_CUSTOM_VALUE) {
    return input.customDate.trim()
  }
  return input.selected
}

export function employmentEndDateSelectionError(input: {
  selected: string | null
  customDate: string
  startDate?: unknown
}): string | null {
  if (!input.selected) return '종료일 후보를 선택하세요.'
  const value = resolveEmploymentEndDateSelection(input)
  if (input.selected === EMPLOYMENT_END_DATE_CUSTOM_VALUE && !value) {
    return '직접 입력한 종료일을 작성하세요.'
  }
  const result = validateChosenEmploymentEndDate({
    endDate: value,
    startDate: input.startDate,
  })
  return result.ok ? null : result.message
}
