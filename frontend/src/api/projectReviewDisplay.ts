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
