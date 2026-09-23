import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Empty,
  Pagination,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router-dom'

import { apiErrorCode, apiErrorMessage } from '@/api/errors'
import { GRADE_LABELS, type TechnicalGrade } from '@/api/people'
import {
  interpretSearch,
  searchPeople,
  type SearchPeopleRequest,
  type SearchRelaxation,
} from '@/api/search'
import { SearchConditionPanel } from '@/pages/search/SearchConditionPanel'
import { SearchEvidenceDrawer } from '@/pages/search/SearchEvidenceDrawer'
import { SearchProjectDrawer } from '@/pages/search/SearchProjectDrawer'
import { SearchResultCard } from '@/pages/search/SearchResultCard'
import {
  PAGE_SIZE_OPTIONS,
  SORT_OPTIONS,
  createEmptyDraftQuery,
  draftToPeopleRequest,
  draftToPreviousQuery,
  hasActiveSearchConditions,
  hydrateDraftQuery,
  interpretDataToDraft,
  isSearchDirty,
  parseTalentSearchState,
  type SearchDraftQuery,
  type TalentSearchRouteState,
  validateDraftQuery,
} from '@/pages/search/searchState'

function summarizeApplied(request: SearchPeopleRequest): string[] {
  const tags: string[] = []
  const r = request.required
  const p = request.preferred
  const pushCodes = (prefix: string, codes: string[]) => {
    for (const code of codes) tags.push(`${prefix}: ${code}`)
  }
  pushCodes('필수', r.jobs)
  for (const g of r.grade?.values ?? []) {
    tags.push(`필수: ${GRADE_LABELS[g as TechnicalGrade] ?? g}`)
  }
  if (r.career?.min_months != null || r.career?.max_months != null) {
    tags.push(
      `필수: 경력 ${r.career.min_months ?? ''}~${r.career.max_months ?? ''}개월`,
    )
  }
  pushCodes('필수', r.skills)
  pushCodes('필수', r.expertise)
  pushCodes('필수', r.business_domains)
  pushCodes('필수', r.customer_types)
  for (const a of r.affiliations) tags.push(`필수 소속: ${a}`)
  for (const c of r.certifications) tags.push(`필수 자격: ${c}`)
  for (const k of r.project_keywords) tags.push(`필수 프로젝트: ${k}`)
  pushCodes('우대', p.jobs)
  pushCodes('우대', p.skills)
  pushCodes('우대', p.expertise)
  pushCodes('우대', p.business_domains)
  pushCodes('우대', p.customer_types)
  if (request.skill_match_mode) {
    tags.push(`기술 조건: ${request.skill_match_mode}`)
  }
  if (request.keyword_query) tags.push(`키워드: ${request.keyword_query}`)
  if (request.semantic_query) tags.push('의미검색 적용')
  return tags
}

function describeSearchError(error: unknown): {
  message: string
  embeddingUnavailable: boolean
  invalidCode: boolean
} {
  const code = apiErrorCode(error)
  if (code === 'SEARCH_EMBEDDING_UNAVAILABLE') {
    return {
      message: '의미검색 서비스를 현재 사용할 수 없습니다.',
      embeddingUnavailable: true,
      invalidCode: false,
    }
  }
  if (code === 'SEARCH_INVALID_CODE') {
    return {
      message:
        '검색조건 코드가 변경되었거나 사용할 수 없습니다. 조건을 다시 확인해주세요.',
      embeddingUnavailable: false,
      invalidCode: true,
    }
  }
  return {
    message: apiErrorMessage(error, '검색에 실패했습니다.'),
    embeddingUnavailable: false,
    invalidCode: false,
  }
}

export function SearchPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()

  const [boot] = useState(() =>
    parseTalentSearchState(
      (location.state as { talentSearch?: unknown } | null)?.talentSearch,
    ),
  )

  const [draftQuery, setDraftQuery] = useState<SearchDraftQuery>(
    () => boot?.draftQuery ?? createEmptyDraftQuery(),
  )
  const [submittedRequest, setSubmittedRequest] = useState<SearchPeopleRequest | null>(
    () => boot?.submittedRequest ?? null,
  )
  const [naturalText, setNaturalText] = useState(() => boot?.naturalText ?? '')
  const [assumptions, setAssumptions] = useState<string[]>(
    () => boot?.assumptions ?? [],
  )
  const [followUpEnabled, setFollowUpEnabled] = useState(
    () =>
      boot?.followUpEnabled ??
      hasActiveSearchConditions(boot?.draftQuery ?? createEmptyDraftQuery()),
  )
  // Once the user explicitly toggles follow-up, do not auto-enable on draft edits.
  const [followUpTouched, setFollowUpTouched] = useState(() => boot != null)
  const [interpretError, setInterpretError] = useState<string | null>(null)
  const [dismissedSearchError, setDismissedSearchError] = useState(false)
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null)
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<string | null>(null)

  const dirty = isSearchDirty(draftQuery, submittedRequest)

  const buildRouteState = useCallback((): TalentSearchRouteState => {
    return {
      version: 1,
      // Keep raw draft text (no live-trim) so dirty in-progress edits restore.
      draftQuery,
      submittedRequest,
      naturalText,
      assumptions,
      followUpEnabled,
    }
  }, [assumptions, draftQuery, followUpEnabled, naturalText, submittedRequest])

  const persistSearchState = useCallback(async () => {
    await navigate('/search', {
      replace: true,
      state: { talentSearch: buildRouteState() },
    })
  }, [buildRouteState, navigate])

  const interpretMutation = useMutation({
    mutationFn: interpretSearch,
    onMutate: () => {
      setInterpretError(null)
    },
    onSuccess: (resp) => {
      const data = resp.data
      setDraftQuery(interpretDataToDraft(data))
      setAssumptions(data.assumptions ?? [])
      setFollowUpEnabled(true)
      message.success('AI 해석 조건을 적용했습니다. 확인 후 검색을 실행하세요.')
    },
    onError: (error) => {
      const code = apiErrorCode(error)
      if (code === 'SEARCH_INTERPRETATION_UNAVAILABLE') {
        setInterpretError(
          'AI 검색조건 해석 서비스를 사용할 수 없습니다. 직접 조건검색은 계속 사용할 수 있습니다.',
        )
      } else if (code === 'SEARCH_INTERPRETATION_INVALID') {
        setInterpretError(
          '자연어 검색조건을 해석하지 못했습니다. 표현을 바꾸거나 조건을 직접 선택해주세요.',
        )
      } else {
        setInterpretError(apiErrorMessage(error, 'AI 조건 해석에 실패했습니다.'))
      }
    },
  })

  const searchQuery = useQuery({
    queryKey: ['search', 'people', submittedRequest],
    queryFn: () => searchPeople(submittedRequest!),
    enabled: submittedRequest != null,
  })

  const searchErrorInfo =
    searchQuery.isError && !dismissedSearchError
      ? describeSearchError(searchQuery.error)
      : null

  useEffect(() => {
    if (!searchQuery.isError) return
    if (apiErrorCode(searchQuery.error) !== 'SEARCH_INVALID_CODE') return
    void queryClient.invalidateQueries({ queryKey: ['codes'] })
  }, [queryClient, searchQuery.error, searchQuery.isError])

  const handleDraftChange = (next: SearchDraftQuery) => {
    // Preserve in-progress spaces; canonicalize only at API/dirty boundaries.
    setDraftQuery(next)
    if (
      !followUpTouched &&
      !followUpEnabled &&
      hasActiveSearchConditions(next)
    ) {
      setFollowUpEnabled(true)
    }
  }

  const handleFollowUpChange = (value: boolean) => {
    setFollowUpTouched(true)
    setFollowUpEnabled(value)
  }

  const handleInterpret = () => {
    const text = naturalText.trim()
    if (!text) {
      message.warning('자연어 검색문을 입력해주세요.')
      return
    }
    if (text.length > 2000) {
      message.warning('자연어 검색문은 2000자를 초과할 수 없습니다.')
      return
    }
    const useFollowUp = followUpEnabled && hasActiveSearchConditions(draftQuery)
    interpretMutation.mutate({
      text,
      previous_query: useFollowUp
        ? draftToPreviousQuery(draftQuery, assumptions)
        : null,
    })
  }

  const handleSearch = () => {
    const validation = validateDraftQuery(draftQuery)
    if (validation) {
      message.warning(validation)
      return
    }
    const pageSize = submittedRequest?.page_size ?? 20
    const next = draftToPeopleRequest(draftQuery, { page: 1, page_size: pageSize })
    setSubmittedRequest(next)
    setDismissedSearchError(false)
    navigate('/search', {
      replace: true,
      state: {
        talentSearch: {
          ...buildRouteState(),
          draftQuery,
          submittedRequest: next,
        },
      },
    })
  }

  const handleReset = () => {
    setDraftQuery(createEmptyDraftQuery())
    setSubmittedRequest(null)
    setNaturalText('')
    setAssumptions([])
    setFollowUpEnabled(false)
    setFollowUpTouched(false)
    setInterpretError(null)
    setDismissedSearchError(false)
    setSelectedProjectId(null)
    setSelectedEvidenceId(null)
    navigate('/search', { replace: true, state: {} })
    queryClient.removeQueries({ queryKey: ['search', 'people'] })
  }

  const handleApplyRelaxation = (relaxation: SearchRelaxation) => {
    setDraftQuery(hydrateDraftQuery(relaxation.suggested_query))
    message.info('조건을 완화했습니다. 검색 버튼을 눌러 다시 검색하세요.')
  }

  const handleSortChange = (sort: SearchDraftQuery['sort']) => {
    const nextDraft = { ...draftQuery, sort }
    setDraftQuery(nextDraft)
    if (submittedRequest) {
      const nextReq = {
        ...submittedRequest,
        sort,
        page: 1,
      }
      setSubmittedRequest(nextReq)
      setDismissedSearchError(false)
      navigate('/search', {
        replace: true,
        state: {
          talentSearch: {
            ...buildRouteState(),
            draftQuery: nextDraft,
            submittedRequest: nextReq,
          },
        },
      })
    }
  }

  const handlePageChange = (page: number, pageSize: number) => {
    if (!submittedRequest) return
    const nextReq = {
      ...submittedRequest,
      page,
      page_size: pageSize,
    }
    setSubmittedRequest(nextReq)
    setDismissedSearchError(false)
    navigate('/search', {
      replace: true,
      state: {
        talentSearch: {
          ...buildRouteState(),
          submittedRequest: nextReq,
        },
      },
    })
  }

  const handleOpenProfile = async (personId: string) => {
    await persistSearchState()
    navigate(`/people/${personId}`, { state: { fromSearch: true } })
  }

  const results = searchQuery.data?.data ?? []
  const meta = searchQuery.data?.meta
  const relaxations = searchQuery.data?.relaxations ?? []
  const appliedTags = submittedRequest ? summarizeApplied(submittedRequest) : []

  return (
    <div>
      <Typography.Title level={3} style={{ marginBottom: 4 }}>
        통합 인력검색
      </Typography.Title>
      <Typography.Paragraph type="secondary">
        자연어 또는 조건을 이용해 적합한 인력을 검색합니다.
      </Typography.Paragraph>

      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <SearchConditionPanel
          draft={draftQuery}
          onChange={handleDraftChange}
          naturalText={naturalText}
          onNaturalTextChange={setNaturalText}
          followUpEnabled={followUpEnabled}
          onFollowUpChange={handleFollowUpChange}
          assumptions={assumptions}
          interpretPending={interpretMutation.isPending}
          searchPending={searchQuery.isFetching}
          onInterpret={handleInterpret}
          onSearch={handleSearch}
          onReset={handleReset}
        />

        {interpretError ? (
          <Alert
            type="warning"
            showIcon
            closable
            message={interpretError}
            onClose={() => setInterpretError(null)}
          />
        ) : null}

        {searchErrorInfo ? (
          <Alert
            type="error"
            showIcon
            closable
            onClose={() => setDismissedSearchError(true)}
            message={searchErrorInfo.message}
            action={
              searchErrorInfo.embeddingUnavailable ? (
                <Button
                  size="small"
                  onClick={() => {
                    setDraftQuery({ ...draftQuery, semantic_query: null })
                    setDismissedSearchError(true)
                    message.info(
                      '의미검색 조건을 제거했습니다. 검색을 다시 실행해주세요.',
                    )
                  }}
                >
                  의미검색 조건 제거
                </Button>
              ) : undefined
            }
          />
        ) : null}

        {submittedRequest ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
              <Space wrap>
                <Typography.Title level={4} style={{ margin: 0 }}>
                  검색 결과 {meta?.total ?? 0}명
                </Typography.Title>
                {dirty ? <Tag color="orange">조건 변경됨</Tag> : null}
              </Space>
              <Select
                value={draftQuery.sort}
                options={SORT_OPTIONS}
                style={{ width: 180 }}
                onChange={handleSortChange}
              />
            </Space>

            {dirty ? (
              <Alert
                type="info"
                showIcon
                message="조건이 변경되었습니다. 검색을 다시 실행해주세요."
              />
            ) : null}

            {meta?.candidate_limit_reached ? (
              <Alert
                type="warning"
                showIcon
                message="검색 후보 상한에 도달하여 일부 후보가 평가되지 않았을 수 있습니다. 조건을 추가하면 더 정확한 결과를 얻을 수 있습니다."
              />
            ) : null}

            {appliedTags.length > 0 ? (
              <div>
                <Typography.Text type="secondary">적용된 검색조건</Typography.Text>
                <div style={{ marginTop: 6 }}>
                  <Space wrap>
                    {appliedTags.map((t) => (
                      <Tag key={t}>{t}</Tag>
                    ))}
                  </Space>
                </div>
              </div>
            ) : null}

            {searchQuery.isFetching && results.length === 0 ? (
              <div style={{ textAlign: 'center', padding: 48 }}>
                <Spin />
              </div>
            ) : null}

            {!searchQuery.isFetching && meta && meta.total === 0 ? (
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                <Empty
                  description={
                    <span>
                      조건을 만족하는 인력이 없습니다.
                      <br />
                      필수조건은 자동으로 완화되지 않습니다. 조건을 직접 수정한 뒤 다시
                      검색해주세요.
                    </span>
                  }
                />
                {relaxations.length > 0 ? (
                  <div
                    style={{
                      maxWidth: 640,
                      margin: '0 auto',
                      width: '100%',
                    }}
                  >
                    <Typography.Text strong>조건 완화 제안</Typography.Text>
                    <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
                      아래 제안을 반영한 뒤 검색 버튼을 다시 눌러 주세요. 자동으로
                      재검색되지 않습니다.
                    </Typography.Paragraph>
                    <Space direction="vertical" size={8} style={{ width: '100%' }}>
                      {relaxations.map((item) => (
                        <div
                          key={item.id}
                          style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            gap: 12,
                            alignItems: 'center',
                            border: '1px solid #f0f0f0',
                            borderRadius: 8,
                            padding: '8px 12px',
                          }}
                        >
                          <Typography.Text>{item.label}</Typography.Text>
                          <Button
                            size="small"
                            onClick={() => handleApplyRelaxation(item)}
                          >
                            조건에 반영
                          </Button>
                        </div>
                      ))}
                    </Space>
                  </div>
                ) : null}
              </Space>
            ) : null}

            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              {results.map((row) => (
                <SearchResultCard
                  key={row.person_id}
                  result={row}
                  onOpenProfile={handleOpenProfile}
                  onOpenProject={(id) => setSelectedProjectId(id)}
                  onOpenEvidence={(id) => setSelectedEvidenceId(id)}
                />
              ))}
            </Space>

            {meta && meta.total > 0 ? (
              <Pagination
                current={meta.page}
                pageSize={meta.page_size}
                total={meta.total}
                showSizeChanger
                pageSizeOptions={PAGE_SIZE_OPTIONS.map(String)}
                onChange={handlePageChange}
                style={{ textAlign: 'right' }}
              />
            ) : null}
          </Space>
        ) : (
          <Empty description="조건을 설정한 뒤 검색을 실행하세요." />
        )}
      </Space>

      <SearchProjectDrawer
        projectId={selectedProjectId}
        open={Boolean(selectedProjectId)}
        onClose={() => setSelectedProjectId(null)}
      />
      <SearchEvidenceDrawer
        evidenceId={selectedEvidenceId}
        open={Boolean(selectedEvidenceId)}
        onClose={() => setSelectedEvidenceId(null)}
      />
    </div>
  )
}
