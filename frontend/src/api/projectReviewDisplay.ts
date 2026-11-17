/** Pure, privacy-safe UI selectors for project Diff rows (no data mutation). */
import type { DiffItem } from '@/api/analyses'
import { formatDiffValueSummary } from '@/api/diffValueDisplay'

type ProjectValues = Record<string, unknown>

export type ProjectReviewView = {
  projectName: string
  customerName: string
  period: string
  role: string
  updateDescription: string | null
  existingSummary: string
}

/** Verified PPTX table-row locator — distinct from citation Evidence quotes. */
export type PptxSourceLocation = {
  document_id: string
  page_no: number
  table_index: number
  row_index: number
  row_id: string
}

function objectValue(value: unknown): ProjectValues | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as ProjectValues)
    : null
}

function cell(value: unknown): string {
  return typeof value === 'string' && value.trim() ? value.trim() : '—'
}

function projectFromCandidate(
  candidateJson: Record<string, unknown>,
  candidatePath?: string | null,
): ProjectValues | null {
  const index = /^projects\[(\d+)\](?:\.|$)/.exec(candidatePath ?? '')
  const projects = candidateJson.projects
  if (!index || !Array.isArray(projects)) return null
  return objectValue(projects[Number(index[1])])
}

const PROJECT_FIELD_LABELS: Record<string, string> = {
  project_name: '프로젝트명',
  customer_name: '고객사',
  start_date: '시작',
  end_date: '종료',
  duration_months: '수행개월',
  responsibilities: '수행역할',
  project_summary: '프로젝트 설명',
  jobs: '직무',
  skills: '보유기술',
  expertise: '전문분야',
  business_domains: '사업분야',
  customer_types: '고객유형',
}

function projectLabel(field: string | null | undefined): string {
  if (!field) return '프로젝트'
  return PROJECT_FIELD_LABELS[field] ?? field
}

function summarizeExisting(value: unknown): string {
  if (Array.isArray(value)) {
    const summaries = value.map(summarizeExisting).filter((x) => x !== '—')
    return summaries.length ? summaries.join(' / ') : '—'
  }
  const record = objectValue(value)
  if (!record) return formatDiffValueSummary(value)
  const name = cell(record.project_name)
  const customer = cell(record.customer_name)
  const start = cell(record.start_date)
  const end = cell(record.end_date)
  return [name, customer !== '—' ? customer : null,
    start !== '—' || end !== '—' ? `${start} ~ ${end}` : null,
  ].filter(Boolean).join(' · ') || '—'
}

function isPptxSourceLocation(value: unknown): value is PptxSourceLocation {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const row = value as Record<string, unknown>
  return (
    typeof row.document_id === 'string' &&
    Boolean(row.document_id.trim()) &&
    typeof row.page_no === 'number' &&
    Number.isInteger(row.page_no) &&
    row.page_no >= 1 &&
    typeof row.table_index === 'number' &&
    Number.isInteger(row.table_index) &&
    row.table_index >= 1 &&
    typeof row.row_index === 'number' &&
    Number.isInteger(row.row_index) &&
    row.row_index >= 0 &&
    typeof row.row_id === 'string' &&
    Boolean(row.row_id.trim())
  )
}

function locationsFromProject(record: ProjectValues | null): PptxSourceLocation[] {
  if (!record) return []
  const raw = record.native_source_locations
  if (!Array.isArray(raw)) return []
  const out: PptxSourceLocation[] = []
  const seen = new Set<string>()
  for (const item of raw) {
    if (!isPptxSourceLocation(item)) continue
    const key = [
      item.document_id,
      item.page_no,
      item.table_index,
      item.row_index,
      item.row_id,
    ].join('|')
    if (seen.has(key)) continue
    seen.add(key)
    out.push(item)
  }
  return out
}

/**
 * Prefer root Diff new_value locators; field Diffs read the parent project in
 * candidate_json. Never invent locations from Evidence quotes.
 */
export function projectPptxSourceLocations(
  diff: Pick<DiffItem, 'candidate_path' | 'new_value'>,
  candidateJson: Record<string, unknown>,
): PptxSourceLocation[] {
  const isRoot = /^projects\[\d+\]$/.test(diff.candidate_path ?? '')
  if (isRoot) {
    const fromRoot = locationsFromProject(objectValue(diff.new_value))
    if (fromRoot.length) return fromRoot
  }
  return locationsFromProject(
    projectFromCandidate(candidateJson, diff.candidate_path),
  )
}

/** Keeps source-identity display distinct from any legacy fuzzy Diff matching. */
export function projectReviewView(
  diff: Pick<DiffItem, 'candidate_path' | 'field_name' | 'new_value' | 'old_value'>,
  candidateJson: Record<string, unknown>,
): ProjectReviewView {
  const isRoot = /^projects\[\d+\]$/.test(diff.candidate_path ?? '')
  const rootCandidate = projectFromCandidate(candidateJson, diff.candidate_path)
  // For root Diff, use the persisted decision candidate; for child Diff,
  // use the parent project's full candidate rather than the scalar new_value.
  const proposed = (isRoot ? objectValue(diff.new_value) : null)
    ?? rootCandidate ?? objectValue(diff.new_value) ?? {}
  const start = cell(proposed.start_date)
  const end = cell(proposed.end_date)
  const field = diff.field_name
    ?? (isRoot ? null : diff.candidate_path?.split('.').at(-1) ?? null)
  return {
    projectName: cell(proposed.project_name),
    customerName: cell(proposed.customer_name),
    period: start === '—' && end === '—' ? '—' : `${start} ~ ${end}`,
    role: cell(proposed.responsibilities),
    updateDescription: field
      ? `${projectLabel(field)}: ${formatDiffValueSummary(diff.new_value)}`
      : null,
    existingSummary: summarizeExisting(diff.old_value),
  }
}
