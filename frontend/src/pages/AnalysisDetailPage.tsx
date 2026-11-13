import { useState } from 'react'
import {
  Alert,
  Button,
  ConfigProvider,
  Drawer,
  Modal,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import type { ColumnsType } from 'antd/es/table'
import type { Key } from 'react'

import { apiErrorCode, apiErrorMessage } from '@/api/errors'
import { documentPreviewUrl } from '@/api/documents'
import { getEvidence, type EvidenceDetail } from '@/api/evidence'
import {
  formatDiffValueSummary,
  formatValue,
} from '@/api/diffValueDisplay'
import { projectReviewView } from '@/api/projectReviewDisplay'
import { DiffEvidenceGroups } from '@/pages/analysisDiffEvidence'
import { readAnalysesReturnTo } from '@/pages/analysesListState'
import {
  bulkReviewDiffs,
  canConfirmAnalysis,
  cancelAnalysis,
  confidenceColor,
  confidenceLabel,
  confirmAnalysis,
  countPendingActionableDiffs,
  countReviewedDiffs,
  getAnalysis,
  isActiveAnalysisStatus,
  listAnalysisDiffs,
  type AnalysisStatus,
  type ChangeType,
  type DiffItem,
  type EntityType,
  type EvidenceLite,
  type ReviewStatus,
} from '@/api/analyses'

type DiffFilterTab =
  | 'all'
  | 'NEW'
  | 'UPDATE'
  | 'CONFLICT'
  | 'pending'
  | 'reviewed'

/** Empty string = 전체 항목 (no entity_type query param). */
type EntityFilter = '' | EntityType

const ENTITY_FILTER_OPTIONS: { value: EntityFilter; label: string }[] = [
  { value: '', label: '전체 항목' },
  { value: 'PROFILE', label: '기본정보' },
  { value: 'JOB', label: '직무' },
  { value: 'TECH', label: '보유기술' },
  { value: 'EXP', label: '전문분야' },
  { value: 'EMPLOYMENT', label: '재직 경력' },
  { value: 'EDUCATION', label: '학력' },
  { value: 'CERTIFICATION', label: '자격증' },
  { value: 'PROJECT', label: '프로젝트' },
]

function countChangeTypes(diffs: DiffItem[]) {
  const counts = {
    same: 0,
    new: 0,
    update: 0,
    conflict: 0,
    review: 0,
  }
  for (const diff of diffs) {
    if (diff.change_type === 'SAME') counts.same += 1
    else if (diff.change_type === 'NEW') counts.new += 1
    else if (diff.change_type === 'UPDATE') counts.update += 1
    else if (diff.change_type === 'CONFLICT') counts.conflict += 1
    else if (diff.change_type === 'REVIEW') counts.review += 1
  }
  return counts
}

function formatDate(value?: string | null) {
  if (!value) return '—'
  try {
    return new Date(value).toLocaleString('ko-KR')
  } catch {
    return value
  }
}

function DiffValueCell({ value }: { value: unknown }) {
  const summary = formatDiffValueSummary(value)
  const full = formatValue(value)
  const showFullJson =
    value !== null &&
    typeof value === 'object' &&
    !Array.isArray(value) &&
    summary !== full
  return (
    <div>
      <Typography.Paragraph
        ellipsis={{ rows: 4, expandable: true, symbol: '더보기' }}
        style={{ marginBottom: showFullJson ? 4 : 0, whiteSpace: 'pre-wrap' }}
      >
        {summary}
      </Typography.Paragraph>
      {showFullJson ? (
        <Typography.Paragraph
          type="secondary"
          ellipsis={{ rows: 2, expandable: true, symbol: 'JSON' }}
          style={{ marginBottom: 0, whiteSpace: 'pre-wrap', fontSize: 12 }}
        >
          {full}
        </Typography.Paragraph>
      ) : null}
    </div>
  )
}

function statusTag(status: AnalysisStatus) {
  const color =
    status === 'FAILED'
      ? 'error'
      : status === 'CONFIRMED'
        ? 'success'
        : status === 'CANCELLED'
          ? 'warning'
          : status === 'REVIEWING'
            ? 'processing'
            : status === 'DEFERRED'
              ? 'gold'
              : status === 'PROCESSING' || status === 'QUEUED'
                ? 'blue'
                : 'default'
  return <Tag color={color}>{status}</Tag>
}

function changeTypeTag(type: ChangeType) {
  const color =
    type === 'NEW'
      ? 'green'
      : type === 'UPDATE'
        ? 'blue'
        : type === 'CONFLICT'
          ? 'red'
          : type === 'REVIEW'
            ? 'orange'
            : 'default'
  return <Tag color={color}>{type}</Tag>
}

function reviewStatusTag(status: ReviewStatus) {
  const color =
    status === 'ACCEPTED' || status === 'MERGED'
      ? 'success'
      : status === 'REJECTED'
        ? 'default'
        : status === 'MODIFIED'
          ? 'processing'
          : 'warning'
  return <Tag color={color}>{status}</Tag>
}

export function AnalysisDetailPage() {
  const { analysisId = '' } = useParams()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const returnTo = readAnalysesReturnTo(searchParams)
  const queryClient = useQueryClient()

  const [filterTab, setFilterTab] = useState<DiffFilterTab>('all')
  const [entityFilter, setEntityFilter] = useState<EntityFilter>('')
  const [selectedKeys, setSelectedKeys] = useState<Key[]>([])
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [evidenceOpen, setEvidenceOpen] = useState(false)
  const [evidenceDetail, setEvidenceDetail] = useState<EvidenceDetail | null>(null)
  const [evidenceLoading, setEvidenceLoading] = useState(false)

  const detailKey = ['analyses', analysisId] as const
  const diffsKey = [
    'analyses',
    analysisId,
    'diffs',
    filterTab,
    entityFilter,
  ] as const

  const detailQuery = useQuery({
    queryKey: detailKey,
    queryFn: () => getAnalysis(analysisId),
    enabled: Boolean(analysisId),
    refetchInterval: (query) => {
      const status = query.state.data?.data.status
      return status && isActiveAnalysisStatus(status) ? 3000 : false
    },
  })

  const analysis = detailQuery.data?.data
  const canReview = analysis?.status === 'REVIEWING'
  const canCancel =
    analysis?.status === 'REVIEWING' || analysis?.status === 'FAILED'
  const showDiffs = Boolean(analysis) && !isActiveAnalysisStatus(analysis!.status)

  const diffsQuery = useQuery({
    queryKey: diffsKey,
    queryFn: async () => {
      const entityFilters = entityFilter
        ? { entity_type: entityFilter as EntityType }
        : {}
      if (filterTab === 'reviewed') {
        const scoped = await listAnalysisDiffs(analysisId, entityFilters)
        return {
          data: scoped.data.filter((d) => d.review_status !== 'PENDING'),
        }
      }
      if (filterTab === 'pending') {
        const scoped = await listAnalysisDiffs(analysisId, entityFilters)
        return {
          data: scoped.data.filter(
            (d) => d.review_status === 'PENDING' && d.change_type !== 'SAME',
          ),
        }
      }
      if (filterTab === 'all') {
        return listAnalysisDiffs(analysisId, entityFilters)
      }
      return listAnalysisDiffs(analysisId, {
        ...entityFilters,
        change_types: filterTab,
      })
    },
    enabled: Boolean(analysisId) && showDiffs,
  })

  const allDiffsQuery = useQuery({
    queryKey: ['analyses', analysisId, 'diffs', 'all-for-confirm'],
    queryFn: () => listAnalysisDiffs(analysisId),
    enabled: Boolean(analysisId) && showDiffs,
  })
  const allDiffs = allDiffsQuery.data?.data ?? []
  // Confirm / global pending alert ignore entity filter (full analysis scope).
  const pendingActionable = countPendingActionableDiffs(allDiffs)
  const reviewedCount = countReviewedDiffs(allDiffs)
  const canConfirm = canConfirmAnalysis({
    status: analysis?.status,
    diffsQuerySuccess: allDiffsQuery.isSuccess,
    totalDiffs: allDiffs.length,
    pendingActionable,
    baseProfileVersion: analysis?.base_profile_version,
  })
  const entityScopedDiffs = entityFilter
    ? allDiffs.filter((d) => d.entity_type === entityFilter)
    : allDiffs
  const entityScopedChangeCounts = entityFilter
    ? countChangeTypes(entityScopedDiffs)
    : null
  const entityScopedPending = entityFilter
    ? countPendingActionableDiffs(entityScopedDiffs)
    : pendingActionable
  const entityScopedReviewed = entityFilter
    ? countReviewedDiffs(entityScopedDiffs)
    : reviewedCount

  const invalidateDetail = async () => {
    await queryClient.invalidateQueries({ queryKey: ['analyses', analysisId] })
    await queryClient.invalidateQueries({ queryKey: ['analyses'] })
    setSelectedKeys([])
  }

  const bulkMutation = useMutation({
    mutationFn: (body: {
      diff_ids: string[]
      review_status: ReviewStatus
      allow_conflict_review?: boolean
      simple_review?: boolean
    }) =>
      bulkReviewDiffs(analysisId, { ...body, simple_review: true }),
    onSuccess: async () => {
      message.success('일괄 검토가 저장되었습니다.')
      await invalidateDetail()
    },
    onError: (error) => {
      message.error(apiErrorMessage(error, '일괄 검토에 실패했습니다.'))
    },
  })

  const confirmMutation = useMutation({
    mutationFn: () =>
      confirmAnalysis(analysisId, {
        expected_profile_version: analysis!.base_profile_version!,
        // Native Source-First projects must not be partially skipped on Confirm.
        skip_invalid_values: analysis!.prompt_version !== 'profile-source-first-v30-native-direct',
      }),
    onSuccess: async (res) => {
      setConfirmOpen(false)
      await invalidateDetail()
      await queryClient.invalidateQueries({ queryKey: ['people', res.data.person_id] })
      if (res.data.skipped_diff_count) {
        Modal.warning({
          title: `확정 완료 · ${res.data.skipped_diff_count}건 반영 제외`,
          width: 620,
          content: (
            <div>
              <Typography.Paragraph>
                저장할 수 없는 AI 후보는 운영 프로필에 반영하지 않았습니다.
                분석 원본과 감사 이력은 남아 있습니다. 인력 상세화면에서 보완해 주세요.
              </Typography.Paragraph>
              <div style={{ maxHeight: 180, overflowY: 'auto' }}>
                {(res.data.skipped_diff_paths ?? []).map((path, index) => (
                  <div key={`${path}-${index}`}>{path}</div>
                ))}
              </div>
            </div>
          ),
          okText: '인력 상세 보기',
          onOk: () => navigate(`/people/${res.data.person_id}`),
        })
      } else {
        message.success('AI 분석 결과가 프로필에 반영되었습니다. 상세화면에서 수정할 수 있습니다.')
      }
    },
    onError: (error) => {
      if (apiErrorCode(error) === 'PROFILE_VERSION_CONFLICT') {
        message.error(
          '분석 이후 인력 프로필이 변경되었습니다. 현재 분석 결과를 그대로 확정할 수 없습니다. 최신 프로필 기준으로 다시 분석해 주세요.',
        )
        return
      }
      if (apiErrorCode(error) === 'CONFIRM_VALIDATION_ERROR') {
        Modal.error({
          title: '확정 전 검증 실패',
          width: 660,
          content: (
            <div>
              <Typography.Paragraph>
                확정할 수 없는 데이터 관계나 시스템 제약이 있습니다. 아래 내용을 확인해 주세요.
              </Typography.Paragraph>
              <div style={{ maxHeight: '45vh', overflowY: 'auto' }}>
                <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>
                  {apiErrorMessage(
                    error,
                    '날짜 등 검토 값에 오류가 있어 확정할 수 없습니다.',
                  )}
                </Typography.Paragraph>
              </div>
            </div>
          ),
          okText: '오류 항목 보기',
          onOk: () => {
            setConfirmOpen(false)
            setFilterTab('all')
            setSelectedKeys([])
          },
        })
        return
      }
      message.error(apiErrorMessage(error, '최종 확정에 실패했습니다.'))
    },
  })

  const cancelMutation = useMutation({
    mutationFn: () => cancelAnalysis(analysisId),
    onSuccess: async () => {
      message.success('분석을 폐기했습니다.')
      await invalidateDetail()
    },
    onError: (error) => {
      message.error(apiErrorMessage(error, '분석 폐기에 실패했습니다.'))
    },
  })

  const requestCancel = () => {
    Modal.confirm({
      title: '이 분석을 폐기할까요?',
      content: '분석 결과와 검토 이력은 삭제하지 않고 CANCELLED 상태로 보존됩니다.',
      okText: '폐기',
      okButtonProps: { danger: true },
      cancelText: '취소',
      onOk: () => cancelMutation.mutateAsync(),
    })
  }

  const openEvidence = async (item: EvidenceLite) => {
    if (!item.id) return
    setEvidenceOpen(true)
    setEvidenceLoading(true)
    setEvidenceDetail(null)
    try {
      const res = await getEvidence(item.id)
      setEvidenceDetail(res.data)
    } catch (error) {
      message.error(apiErrorMessage(error, '근거를 불러오지 못했습니다.'))
      setEvidenceOpen(false)
    } finally {
      setEvidenceLoading(false)
    }
  }

  const columns: ColumnsType<DiffItem> = [
    {
      title: 'Entity',
      dataIndex: 'entity_type',
      width: 120,
      render: (v: string, row) => (
        <span>
          {v}
          {row.change_type ? <div>{changeTypeTag(row.change_type)}</div> : null}
        </span>
      ),
    },
    {
      title: 'Field',
      key: 'field',
      width: 160,
      ellipsis: true,
      render: (_, row) => row.field_name || row.candidate_path || '—',
    },
    {
      title: '기존값',
      dataIndex: 'old_value',
      width: 160,
      render: (v) => <DiffValueCell value={v} />,
    },
    {
      title: 'AI후보',
      dataIndex: 'new_value',
      width: 180,
      render: (v) => <DiffValueCell value={v} />,
    },
    {
      title: '적용값',
      key: 'applied_value',
      width: 180,
      render: (_, row) => {
        if (row.review_status === 'MODIFIED') {
          return (
            <div>
              <Tag color="processing" style={{ marginBottom: 4 }}>
                MODIFIED
              </Tag>
              <DiffValueCell value={row.decided_value} />
            </div>
          )
        }
        if (row.review_status === 'ACCEPTED') {
          return (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              AI후보와 동일
            </Typography.Text>
          )
        }
        if (row.review_status === 'REJECTED') {
          return (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              반영 제외
            </Typography.Text>
          )
        }
        if (row.review_status === 'MERGED') {
          return (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              병합 적용
            </Typography.Text>
          )
        }
        return <Typography.Text type="secondary">—</Typography.Text>
      },
    },
    {
      title: '문서 근거',
      key: 'evidence',
      width: 280,
      render: (_, row) => (
        <DiffEvidenceGroups
          evidence={row.evidence ?? []}
          documents={analysis?.documents ?? []}
          rowId={row.id}
          onOpenEvidence={(ev) => void openEvidence(ev)}
        />
      ),
    },
    {
      title: 'Confidence',
      dataIndex: 'confidence',
      width: 110,
      render: (v) => <Tag color={confidenceColor(v)}>{confidenceLabel(v)}</Tag>,
    },
    {
      title: '상태',
      dataIndex: 'review_status',
      width: 110,
      render: (v: ReviewStatus) => reviewStatusTag(v),
    },
    {
      title: '작업',
      key: 'actions',
      width: 220,
      fixed: 'right',
      render: (_, row) => {
        if (!canReview || row.change_type === 'SAME') {
          return <Typography.Text type="secondary">—</Typography.Text>
        }
        return (
          <Space size="small" wrap>
            <Button
              type="link"
              size="small"
              disabled={row.review_status === 'ACCEPTED'}
              loading={bulkMutation.isPending}
              onClick={() => bulkMutation.mutate({ diff_ids: [row.id], review_status: 'ACCEPTED' })}
            >
              승인
            </Button>
            <Button
              type="link"
              size="small"
              danger
              disabled={row.review_status === 'REJECTED'}
              loading={bulkMutation.isPending}
              onClick={() => bulkMutation.mutate({ diff_ids: [row.id], review_status: 'REJECTED' })}
            >
              반려
            </Button>
          </Space>
        )
      },
    },
  ]

  // Project-only view: keep the same Diff decisions and evidence actions,
  // but present native project identity/period/role as readable columns.
  // General v25 and non-project rows continue to use the existing table.
  const projectColumns: ColumnsType<DiffItem> = [
    {
      title: '분류',
      key: 'project_change_type',
      width: 105,
      render: (_, row) => changeTypeTag(row.change_type),
    },
    {
      title: '프로젝트 / 고객사',
      key: 'project_identity',
      width: 290,
      render: (_, row) => {
        const value = projectReviewView(row, analysis?.candidate_json ?? {})
        return (
          <div>
            <Typography.Paragraph
              strong
              ellipsis={{ rows: 2, expandable: true, symbol: '더보기' }}
              style={{ marginBottom: 3, overflowWrap: 'anywhere' }}
            >
              {value.projectName}
            </Typography.Paragraph>
            <Typography.Text type="secondary" style={{ overflowWrap: 'anywhere' }}>
              {value.customerName}
            </Typography.Text>
          </div>
        )
      },
    },
    // Keep actions beside the project identity, not fixed to the right:
    // Ant Design's sticky right cells can disappear for offscreen rows in
    // long project lists/full-page captures. Every row must retain its
    // existing simple-review approval/rejection actions.
    ...columns
      .filter((_, idx) => idx === 8)
      .map((column) => ({ ...column, fixed: undefined, width: 160 })),
    {
      title: '수행기간',
      key: 'project_period',
      width: 180,
      render: (_, row) => (
        <Typography.Text>
          {projectReviewView(row, analysis?.candidate_json ?? {}).period}
        </Typography.Text>
      ),
    },
    {
      title: '수행역할 / 변경내용',
      key: 'project_role',
      width: 270,
      render: (_, row) => {
        const value = projectReviewView(row, analysis?.candidate_json ?? {})
        return (
          <div style={{ overflowWrap: 'anywhere' }}>
            <Typography.Paragraph
              ellipsis={{ rows: 3, expandable: true, symbol: '더보기' }}
              style={{ marginBottom: value.updateDescription ? 4 : 0 }}
            >
              {value.role}
            </Typography.Paragraph>
            {value.updateDescription ? (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {value.updateDescription}
              </Typography.Text>
            ) : null}
          </div>
        )
      },
    },
    {
      title: '기존 비교 대상',
      key: 'project_existing',
      width: 240,
      render: (_, row) => (
        <Typography.Paragraph
          type="secondary"
          ellipsis={{ rows: 3, expandable: true, symbol: '더보기' }}
          style={{ marginBottom: 0, overflowWrap: 'anywhere' }}
        >
          {projectReviewView(row, analysis?.candidate_json ?? {}).existingSummary}
        </Typography.Paragraph>
      ),
    },
    // Retain existing evidence and review status; actions moved beside name.
    ...columns.filter((_, idx) => [5, 7].includes(idx)),
  ]

  if (detailQuery.isLoading || !analysis) {
    return <Typography.Text>불러오는 중…</Typography.Text>
  }

  // Default entity=전체 keeps analysis.counts (identical to prior UI).
  const counts = entityScopedChangeCounts ?? analysis.counts
  const tabPendingCount = entityScopedPending
  const tabReviewedCount = entityScopedReviewed
  const docLabels = analysis.documents.map((d) => {
    const type = d.document_type_name || d.document_type_code
    const ver = d.version_no != null ? ` v${d.version_no}` : ''
    return type ? `${d.original_filename} (${type}${ver})` : d.original_filename
  })

  return (
    <div>
      <Button type="link" onClick={() => navigate(returnTo)} style={{ paddingLeft: 0 }}>
        ← 검토 목록
      </Button>

      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 12 }} align="start">
        <div>
          <Typography.Title level={3} style={{ marginBottom: 8 }}>
            {analysis.person.name} {statusTag(analysis.status)}
          </Typography.Title>
          <Typography.Paragraph style={{ marginBottom: 4 }}>
            Base Profile Version:{' '}
            {analysis.base_profile_version != null ? `v${analysis.base_profile_version}` : '—'}
            {' · '}
            Confidence:{' '}
            <Tag color={confidenceColor(analysis.overall_confidence)}>
              {confidenceLabel(analysis.overall_confidence)}
            </Tag>
          </Typography.Paragraph>
          <Typography.Text type="secondary">
            문서: {docLabels.length ? docLabels.join(', ') : '—'}
          </Typography.Text>
          <br />
          <Typography.Text type="secondary">
            LLM: {analysis.llm_model || '—'} · VLM: {analysis.vlm_model || '—'}
            {analysis.prompt_version ? ` · prompt ${analysis.prompt_version}` : ''}
          </Typography.Text>
          {analysis.status === 'FAILED' && analysis.error_message ? (
            <Alert
              style={{ marginTop: 12 }}
              type="error"
              showIcon
              message="분석 실패"
              description={analysis.error_message}
            />
          ) : null}
          {analysis.status === 'CANCELLED' ? (
            <Alert
              style={{ marginTop: 12 }}
              type="warning"
              showIcon
              message="폐기된 분석입니다."
              description={
                analysis.error_message ||
                '이 분석은 CANCELLED 상태로 보존됩니다. 검토·확정은 할 수 없습니다.'
              }
            />
          ) : null}
        </div>
        <Space direction="vertical" align="end">
          <Typography.Text type="secondary">
            생성 {formatDate(analysis.created_at)} · 완료 {formatDate(analysis.completed_at)}
          </Typography.Text>
          <Space>
            {canCancel ? (
              <Button
                danger
                loading={cancelMutation.isPending}
                onClick={requestCancel}
              >
                분석 폐기
              </Button>
            ) : null}
            {analysis.status === 'CONFIRMED' ? (
              <Button type="primary" onClick={() => navigate(`/people/${analysis.person.id}`)}>
                인력 상세 보기
              </Button>
            ) : analysis.status === 'REVIEWING' ? (
              <Button type="primary" disabled={!canConfirm} onClick={() => setConfirmOpen(true)}>
                최종 확정
              </Button>
            ) : null}
          </Space>
        </Space>
      </Space>

      {analysis.status === 'DEFERRED' ? (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="앞선 AI 분석 완료를 기다리고 있습니다."
          description="대기 중에는 Diff 검토와 확정을 할 수 없습니다. 상태가 바뀌면 자동으로 갱신됩니다."
        />
      ) : isActiveAnalysisStatus(analysis.status) ? (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message={`분석이 ${analysis.status} 상태입니다. 완료될 때까지 자동 갱신합니다.`}
        />
      ) : (
        <>
          {analysis.status === 'REVIEWING' &&
          allDiffsQuery.isSuccess &&
          allDiffs.length === 0 ? (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 16 }}
              message="변경 항목이 없어 최종 확정할 수 없습니다."
              description="이 분석은 프로필에 반영할 Diff가 없습니다. 필요하지 않은 분석이면 폐기하거나 최신 문서로 다시 분석해 주세요."
            />
          ) : null}
          {analysis.status === 'REVIEWING' && pendingActionable > 0 ? (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 16 }}
              message={`미검토 항목 ${pendingActionable}건 — 모두 결정한 뒤 최종 확정할 수 있습니다.`}
            />
          ) : null}
          {analysis.status === 'CONFIRMED' ? (
            <Alert
              type="success"
              showIcon
              style={{ marginBottom: 16 }}
              message="확정 완료 — 검색 인덱스 갱신 대기(PENDING)일 수 있습니다."
            />
          ) : null}

          <div style={{ marginBottom: 8, maxWidth: 320, width: '100%' }}>
            <Typography.Text
              type="secondary"
              style={{ display: 'block', marginBottom: 4 }}
            >
              항목 유형
            </Typography.Text>
            <Select
              aria-label="항목 유형"
              value={entityFilter}
              options={ENTITY_FILTER_OPTIONS}
              onChange={(value) => {
                setEntityFilter(value)
                setSelectedKeys([])
              }}
              style={{ width: '100%' }}
              popupMatchSelectWidth={false}
            />
          </div>

          <Tabs
            activeKey={filterTab}
            onChange={(key) => {
              setFilterTab(key as DiffFilterTab)
              setSelectedKeys([])
            }}
            items={[
              {
                key: 'all',
                label: `전체 (${counts.same + counts.new + counts.update + counts.conflict + counts.review})`,
              },
              { key: 'NEW', label: `신규 (${counts.new})` },
              { key: 'UPDATE', label: `변경 (${counts.update})` },
              { key: 'CONFLICT', label: `충돌 (${counts.conflict})` },
              { key: 'pending', label: `미검토 (${tabPendingCount})` },
              { key: 'reviewed', label: `검토완료 (${tabReviewedCount})` },
            ]}
            style={{ marginBottom: 8 }}
          />

          {entityFilter === 'PROJECT' &&
            analysis.prompt_version === 'profile-source-first-v30-native-direct' ? (
            <Typography.Paragraph type="secondary" style={{ marginBottom: 10 }}>
              PDF 원본 기준 프로젝트를 별도 항목으로 표시합니다.
              REVIEW는 유사성 검토 대상이며 자동 병합되지 않습니다.
            </Typography.Paragraph>
          ) : null}

          {canReview ? (
            <Space style={{ marginBottom: 12 }} wrap>
              <Tag color={selectedKeys.length > 0 ? 'green' : undefined}>
                선택 {selectedKeys.length}건
              </Tag>
              <Button
                disabled={selectedKeys.length === 0}
                loading={bulkMutation.isPending}
                onClick={() => {
                  const targets = (diffsQuery.data?.data ?? []).filter((d) =>
                    selectedKeys.includes(d.id) && d.change_type !== 'SAME',
                  )
                  if (!targets.length) return
                  Modal.confirm({
                    title: `선택 ${targets.length}건을 승인할까요?`,
                    content: (
                      <Typography.Paragraph>
                        기존 결정이 있는 항목도 승인으로 변경합니다. 최종 확정 시 저장할 수 없는 값은
                        반영에서 제외되며, 이후 인력 상세화면에서 수정할 수 있습니다.
                      </Typography.Paragraph>
                    ),
                    okText: '승인',
                    cancelText: '취소',
                    onOk: () =>
                      bulkMutation.mutateAsync({
                        diff_ids: targets.map((d) => d.id),
                        review_status: 'ACCEPTED',
                      }),
                  })
                }}
              >
                선택 승인
              </Button>
              <Button
                disabled={selectedKeys.length === 0}
                loading={bulkMutation.isPending}
                onClick={() => {
                  const targets = (diffsQuery.data?.data ?? []).filter(
                    (d) => selectedKeys.includes(d.id) && d.change_type !== 'SAME',
                  )
                  if (!targets.length) {
                    message.warning('반려할 Diff를 선택해 주세요.')
                    return
                  }
                  Modal.confirm({
                    title: `선택 ${targets.length}건을 반려할까요?`,
                    content: '기존 결정이 있는 항목도 반려로 변경하며, 해당 AI 후보는 확정 시 반영하지 않습니다.',
                    okText: '반려',
                    okButtonProps: { danger: true },
                    cancelText: '취소',
                    onOk: () =>
                      bulkMutation.mutateAsync({
                        diff_ids: targets.map((d) => d.id),
                        review_status: 'REJECTED',
                      }),
                  })
                }}
              >
                선택 반려
              </Button>
              <Button
                disabled={
                  !selectedKeys.some((key) => {
                    const diff = (diffsQuery.data?.data ?? []).find((d) => d.id === key)
                    return diff && diff.change_type !== 'SAME' &&
                      !['PENDING', 'MERGED'].includes(diff.review_status)
                  })
                }
                loading={bulkMutation.isPending}
                onClick={() => {
                  const targets = (diffsQuery.data?.data ?? []).filter(
                    (d) => selectedKeys.includes(d.id) &&
                      d.change_type !== 'SAME' &&
                      !['PENDING', 'MERGED'].includes(d.review_status),
                  )
                  Modal.confirm({
                    title: `승인/수정/반려 결정 ${targets.length}건을 취소할까요?`,
                    content: '검토 결정만 미검토 상태로 되돌립니다. AI후보와 원문 근거는 유지됩니다.',
                    okText: '결정 취소',
                    onOk: () => bulkMutation.mutateAsync({
                      diff_ids: targets.map((d) => d.id),
                      review_status: 'PENDING',
                    }),
                  })
                }}
              >
                선택 결정 취소
              </Button>
              <Typography.Text type="secondary">
                모든 변경 유형에서 다중 승인·반려 가능. 잘못된 값은 확정 후 인력 상세에서 보완하세요.
              </Typography.Text>
            </Space>
          ) : null}

          <ConfigProvider
            theme={{
              components: {
                Table: {
                  rowSelectedBg: '#f3faf7',
                  rowSelectedHoverBg: '#e9f7ef',
                },
              },
            }}
          >
            <Table
              rowKey="id"
              loading={diffsQuery.isLoading}
              columns={entityFilter === 'PROJECT' ? projectColumns : columns}
              dataSource={diffsQuery.data?.data ?? []}
              size="small"
              scroll={{ x: 1340 }}
              pagination={{ pageSize: 50, showSizeChanger: true }}
              rowSelection={
                canReview
                  ? {
                      selectedRowKeys: selectedKeys,
                      onChange: setSelectedKeys,
                      getCheckboxProps: (row) => ({
                        disabled: row.change_type === 'SAME',
                      }),
                    }
                  : undefined
              }
              locale={{ emptyText: '표시할 Diff가 없습니다.' }}
            />
          </ConfigProvider>
        </>
      )}

      <Modal
        title="최종 확정"
        open={confirmOpen}
        onCancel={() => setConfirmOpen(false)}
        onOk={() => confirmMutation.mutate()}
        confirmLoading={confirmMutation.isPending}
        okText="확정"
        destroyOnHidden
      >
        <Typography.Paragraph>
          승인된 AI 분석 결과를 운영 프로필에 반영합니다.
        </Typography.Paragraph>
        {analysis?.prompt_version === 'profile-source-first-v30-native-direct' ? (
          <Typography.Paragraph type="secondary">
            v30 원본 프로젝트는 모두 정상 등록돼야 합니다.
            저장할 수 없는 항목이 있으면 일부만 반영하지 않고 확정 전체를 중단합니다.
          </Typography.Paragraph>
        ) : (
          <Typography.Paragraph type="secondary">
            날짜·코드 등 형식 오류가 있는 항목은 자동 제외하고 확정합니다.
            제외된 항목은 확정 결과에 표시되며, 인력 상세화면에서 추가·수정할 수 있습니다.
          </Typography.Paragraph>
        )
        <Typography.Paragraph>
          Base Profile Version:{' '}
          <strong>
            {analysis?.base_profile_version != null
              ? `v${analysis.base_profile_version}`
              : '—'}
          </strong>
        </Typography.Paragraph>
        <Typography.Paragraph type="secondary">
          확정 후 Profile Version이 +1 되고, 검색 인덱스 갱신 작업이 대기열에 등록됩니다.
        </Typography.Paragraph>
      </Modal>

      <Drawer
        title="문서 근거"
        open={evidenceOpen}
        onClose={() => {
          setEvidenceOpen(false)
          setEvidenceDetail(null)
        }}
        width={480}
        destroyOnHidden
      >
        {evidenceLoading ? (
          <Typography.Text>불러오는 중…</Typography.Text>
        ) : evidenceDetail ? (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <div>
              <Typography.Text type="secondary">문서</Typography.Text>
              <div>
                {evidenceDetail.document.title ||
                  evidenceDetail.document.original_filename ||
                  evidenceDetail.document.id}
              </div>
              {evidenceDetail.document.original_filename ? (
                <Typography.Text type="secondary">
                  {evidenceDetail.document.original_filename}
                  {evidenceDetail.document.version_no != null
                    ? ` v${evidenceDetail.document.version_no}`
                    : ''}
                </Typography.Text>
              ) : null}
            </div>
            <div>
              <Typography.Text type="secondary">페이지</Typography.Text>
              <div>{evidenceDetail.page_no ?? '—'}</div>
            </div>
            <div>
              <Typography.Text type="secondary">추출방식</Typography.Text>
              <div>{evidenceDetail.extraction_method || '—'}</div>
            </div>
            <div>
              <Typography.Text type="secondary">원문 인용</Typography.Text>
              <Typography.Paragraph
                style={{
                  whiteSpace: 'pre-wrap',
                  background: 'rgba(0,0,0,0.04)',
                  padding: 8,
                  marginTop: 4,
                }}
              >
                {evidenceDetail.quote_text || '—'}
              </Typography.Paragraph>
            </div>
            <div>
              <Typography.Text type="secondary">연결 대상</Typography.Text>
              {(evidenceDetail.links || []).length === 0 ? (
                <div>—</div>
              ) : (
                evidenceDetail.links.map((link, i) => (
                  <div key={`${link.target_id}-${link.field_name || ''}-${i}`}>
                    {link.target_type}
                    {link.field_name ? ` · ${link.field_name}` : ''}
                    {' · '}
                    {link.relation_type}
                  </div>
                ))
              )}
            </div>
            <Button
              type="primary"
              href={documentPreviewUrl(evidenceDetail.document.id)}
              target="_blank"
              rel="noreferrer"
            >
              문서 보기
              {evidenceDetail.page_no != null ? ` (p.${evidenceDetail.page_no})` : ''}
            </Button>
            <Typography.Text type="secondary">
              Preview가 페이지 이동을 지원하지 않으면 문서만 열고 페이지 번호를 참고하세요.
            </Typography.Text>
          </Space>
        ) : (
          <Typography.Text type="secondary">근거 데이터가 없습니다.</Typography.Text>
        )}
      </Drawer>
    </div>
  )
}
