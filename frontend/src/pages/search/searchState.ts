import type { CodeItem } from '@/api/codes'
import type { TechnicalGrade } from '@/api/people'
import type {
  PreferredConditionBlock,
  SearchConditionBlock,
  SearchExecutableQuery,
  SearchInterpretData,
  SearchPeopleRequest,
  SearchSort,
  SkillMatchMode,
} from '@/api/search'

export const SEARCH_ROUTE_STATE_VERSION = 1 as const

export type SearchDraftQuery = SearchExecutableQuery

export type TalentSearchRouteState = {
  version: typeof SEARCH_ROUTE_STATE_VERSION
  draftQuery: SearchDraftQuery
  submittedRequest: SearchPeopleRequest | null
  naturalText: string
  assumptions: string[]
  followUpEnabled: boolean
}

export type SearchLocationState = {
  talentSearch?: TalentSearchRouteState
}

export function emptyConditionBlock(): SearchConditionBlock {
  return {
    jobs: [],
    skills: [],
    expertise: [],
    business_domains: [],
    customer_types: [],
    grade: null,
    career: null,
    affiliations: [],
    certifications: [],
    project_keywords: [],
  }
}

export function emptyPreferredBlock(): PreferredConditionBlock {
  return {
    jobs: [],
    skills: [],
    expertise: [],
    business_domains: [],
    customer_types: [],
  }
}

export function createEmptyDraftQuery(
  overrides: Partial<SearchDraftQuery> = {},
): SearchDraftQuery {
  return {
    required: emptyConditionBlock(),
    preferred: emptyPreferredBlock(),
    skill_match_mode: 'ANY',
    semantic_query: null,
    keyword_query: null,
    sort: 'RELEVANCE',
    ...overrides,
  }
}

export function normalizeConditionBlock(
  block: SearchConditionBlock | null | undefined,
): SearchConditionBlock {
  const src = block ?? emptyConditionBlock()
  const gradeValues = src.grade?.values?.filter(Boolean) ?? []
  const min = src.career?.min_months
  const max = src.career?.max_months
  const hasCareer = min != null || max != null
  return {
    jobs: [...(src.jobs ?? [])],
    skills: [...(src.skills ?? [])],
    expertise: [...(src.expertise ?? [])],
    business_domains: [...(src.business_domains ?? [])],
    customer_types: [...(src.customer_types ?? [])],
    grade: gradeValues.length ? { values: gradeValues as TechnicalGrade[] } : null,
    career: hasCareer
      ? {
          min_months: min ?? null,
          max_months: max ?? null,
        }
      : null,
    affiliations: [...(src.affiliations ?? [])],
    certifications: [...(src.certifications ?? [])],
    project_keywords: [...(src.project_keywords ?? [])],
  }
}

export function normalizePreferredBlock(
  block: PreferredConditionBlock | null | undefined,
): PreferredConditionBlock {
  const src = block ?? emptyPreferredBlock()
  return {
    jobs: [...(src.jobs ?? [])],
    skills: [...(src.skills ?? [])],
    expertise: [...(src.expertise ?? [])],
    business_domains: [...(src.business_domains ?? [])],
    customer_types: [...(src.customer_types ?? [])],
  }
}

export function normalizeDraftQuery(query: SearchDraftQuery): SearchDraftQuery {
  return {
    required: normalizeConditionBlock(query.required),
    preferred: normalizePreferredBlock(query.preferred),
    skill_match_mode: (query.skill_match_mode ?? 'ANY') as SkillMatchMode,
    semantic_query: query.semantic_query?.trim() ? query.semantic_query.trim() : null,
    keyword_query: query.keyword_query?.trim() ? query.keyword_query.trim() : null,
    sort: (query.sort ?? 'RELEVANCE') as SearchSort,
  }
}

/** UI/route hydrate: shape blocks without trimming in-progress text fields. */
export function hydrateDraftQuery(query: SearchDraftQuery): SearchDraftQuery {
  return {
    required: normalizeConditionBlock(query.required),
    preferred: normalizePreferredBlock(query.preferred),
    skill_match_mode: (query.skill_match_mode ?? 'ANY') as SkillMatchMode,
    semantic_query: query.semantic_query ?? null,
    keyword_query: query.keyword_query ?? null,
    sort: (query.sort ?? 'RELEVANCE') as SearchSort,
  }
}

export function draftToPeopleRequest(
  draft: SearchDraftQuery,
  opts: { page: number; page_size: number },
): SearchPeopleRequest {
  const normalized = normalizeDraftQuery(draft)
  return {
    ...normalized,
    page: opts.page,
    page_size: opts.page_size,
    suggest_relaxations: true,
  }
}

export function draftToPreviousQuery(
  draft: SearchDraftQuery,
  assumptions: string[] = [],
): SearchInterpretData {
  const normalized = normalizeDraftQuery(draft)
  return {
    query_version: '1.0',
    ...normalized,
    assumptions: [...assumptions],
  }
}

export function interpretDataToDraft(data: SearchInterpretData): SearchDraftQuery {
  return normalizeDraftQuery({
    required: data.required,
    preferred: data.preferred,
    skill_match_mode: data.skill_match_mode,
    semantic_query: data.semantic_query,
    keyword_query: data.keyword_query,
    sort: data.sort,
  })
}

/** Compare executable fields only (ignore page / page_size / suggest_relaxations). */
export function isSearchDirty(
  draft: SearchDraftQuery,
  submitted: SearchPeopleRequest | null,
): boolean {
  if (!submitted) return false
  const a = normalizeDraftQuery(draft)
  const b = normalizeDraftQuery({
    required: submitted.required,
    preferred: submitted.preferred,
    skill_match_mode: submitted.skill_match_mode,
    semantic_query: submitted.semantic_query,
    keyword_query: submitted.keyword_query,
    sort: submitted.sort,
  })
  return JSON.stringify(a) !== JSON.stringify(b)
}

export function hasActiveSearchConditions(draft: SearchDraftQuery): boolean {
  const q = normalizeDraftQuery(draft)
  const r = q.required
  const p = q.preferred
  return Boolean(
    r.jobs.length ||
      r.skills.length ||
      r.expertise.length ||
      r.business_domains.length ||
      r.customer_types.length ||
      (r.grade?.values.length ?? 0) ||
      r.career ||
      r.affiliations.length ||
      r.certifications.length ||
      r.project_keywords.length ||
      p.jobs.length ||
      p.skills.length ||
      p.expertise.length ||
      p.business_domains.length ||
      p.customer_types.length ||
      q.keyword_query ||
      q.semantic_query,
  )
}

export function validateDraftQuery(draft: SearchDraftQuery): string | null {
  const career = draft.required.career
  if (career) {
    const min = career.min_months
    const max = career.max_months
    if (min != null && (!Number.isFinite(min) || min < 0)) {
      return '최소 경력(개월)은 0 이상이어야 합니다.'
    }
    if (max != null && (!Number.isFinite(max) || max < 0)) {
      return '최대 경력(개월)은 0 이상이어야 합니다.'
    }
    if (
      (min != null && !Number.isInteger(min)) ||
      (max != null && !Number.isInteger(max))
    ) {
      return '경력(개월)은 정수로 입력해주세요.'
    }
    if (min != null && max != null && min > max) {
      return '최소 경력은 최대 경력보다 클 수 없습니다.'
    }
  }
  const keyword = draft.keyword_query?.trim() ?? ''
  if (keyword.length > 500) return '정확 키워드 검색은 500자를 초과할 수 없습니다.'
  return null
}

export function codeSelectOptions(codes: CodeItem[], orphanCodes: string[] = []) {
  const known = new Set(codes.map((c) => c.code))
  const options = codes.map((c) => ({
    value: c.code,
    label: c.name,
    searchText: [c.name, c.code, ...(c.aliases ?? [])].join(' ').toLowerCase(),
  }))
  for (const code of orphanCodes) {
    if (!code || known.has(code)) continue
    options.push({
      value: code,
      label: code,
      searchText: code.toLowerCase(),
    })
  }
  return options
}

export function filterCodeOption(
  input: string,
  option?: { searchText?: string; label?: unknown; value?: unknown },
): boolean {
  const needle = input.trim().toLowerCase()
  if (!needle) return true
  if (option?.searchText) return option.searchText.includes(needle)
  const label = String(option?.label ?? '').toLowerCase()
  const value = String(option?.value ?? '').toLowerCase()
  return label.includes(needle) || value.includes(needle)
}

export const SORT_OPTIONS: Array<{ value: SearchSort; label: string }> = [
  { value: 'RELEVANCE', label: '적합도순' },
  { value: 'CAREER_DESC', label: '경력 많은 순' },
  { value: 'UPDATED_DESC', label: '최근 갱신순' },
  { value: 'RECENT_PROJECT_DESC', label: '최근 프로젝트순' },
  { value: 'NAME_ASC', label: '이름순' },
]

export const PAGE_SIZE_OPTIONS = [20, 50, 100] as const

export function parseTalentSearchState(raw: unknown): TalentSearchRouteState | null {
  if (!raw || typeof raw !== 'object') return null
  const state = raw as Partial<TalentSearchRouteState>
  if (state.version !== SEARCH_ROUTE_STATE_VERSION) return null
  if (!state.draftQuery || typeof state.draftQuery !== 'object') return null
  return {
    version: SEARCH_ROUTE_STATE_VERSION,
    draftQuery: hydrateDraftQuery(state.draftQuery as SearchDraftQuery),
    submittedRequest: (state.submittedRequest as SearchPeopleRequest | null) ?? null,
    naturalText: typeof state.naturalText === 'string' ? state.naturalText : '',
    assumptions: Array.isArray(state.assumptions)
      ? state.assumptions.map(String)
      : [],
    followUpEnabled: Boolean(state.followUpEnabled),
  }
}

export function monthsHelperText(months: number | null | undefined): string | null {
  if (months == null || Number.isNaN(months)) return null
  const years = Math.floor(months / 12)
  const rem = months % 12
  if (years <= 0) return `${months}개월`
  if (rem === 0) return `${months}개월 = ${years}년`
  return `${months}개월 = ${years}년 ${rem}개월`
}
