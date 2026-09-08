import { useState } from 'react'
import {
  Alert,
  Button,
  Drawer,
  Form,
  Input,
  Modal,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import type { ColumnsType } from 'antd/es/table'
import type { Key } from 'react'

import { apiErrorCode, apiErrorMessage } from '@/api/errors'
import { documentPreviewUrl } from '@/api/documents'
import { getEvidence, type EvidenceDetail } from '@/api/evidence'
import {
  bulkReviewDiffs,
  buildDefaultModifiedDecision,
  buildMergeDecisionRequestBody,
  canConfirmAnalysis,
  confidenceColor,
  confidenceLabel,
  confirmAnalysis,
  countPendingActionableDiffs,
  getAnalysis,
  initialMergeDecidedValueText,
  isActiveAnalysisStatus,
  isProfileScalarDiff,
  isProjectRootReview,
  listAnalysisDiffs,
  reviewDiff,
  type AnalysisStatus,
  type ChangeType,
  type DiffItem,
  type EvidenceLite,
  type ReviewStatus,
} from '@/api/analyses'

type DiffFilterTab =
  | 'all'
  | 'NEW'
  | 'UPDATE'
  | 'CONFLICT'
  | 'REVIEW'
  | 'reviewed'

function formatDate(value?: string | null) {
  if (!value) return '—'
  try {
    return new Date(value).toLocaleString('ko-KR')
  } catch {
    return value
  }
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function statusTag(status: AnalysisStatus) {
  const color =
    status === 'FAILED'
      ? 'error'
      : status === 'CONFIRMED'
        ? 'success'
        : status === 'REVIEWING'
          ? 'processing'
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
  const queryClient = useQueryClient()

  const [filterTab, setFilterTab] = useState<DiffFilterTab>('all')
  const [selectedKeys, setSelectedKeys] = useState<Key[]>([])
  const [editOpen, setEditOpen] = useState(false)
  const [mergeOpen, setMergeOpen] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [editingDiff, setEditingDiff] = useState<DiffItem | null>(null)
  const [evidenceOpen, setEvidenceOpen] = useState(false)
  const [evidenceDetail, setEvidenceDetail] = useState<EvidenceDetail | null>(null)
  const [evidenceLoading, setEvidenceLoading] = useState(false)
  const [editForm] = Form.useForm<{ decided_value: string }>()
  const [mergeForm] = Form.useForm<{
    existing_target_id: string
    decided_value?: string
  }>()

  const detailKey = ['analyses', analysisId] as const
  const diffsKey = ['analyses', analysisId, 'diffs', filterTab] as const

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
  const showDiffs = Boolean(analysis) && !isActiveAnalysisStatus(analysis!.status)

  const diffsQuery = useQuery({
    queryKey: diffsKey,
    queryFn: async () => {
      if (filterTab === 'reviewed') {
        const all = await listAnalysisDiffs(analysisId)
        return {
          data: all.data.filter((d) => d.review_status !== 'PENDING'),
        }
      }
      if (filterTab === 'all') {
        return listAnalysisDiffs(analysisId)
      }
      return listAnalysisDiffs(analysisId, { change_types: filterTab })
    },
    enabled: Boolean(analysisId) && showDiffs,
  })

  const allDiffsQuery = useQuery({
    queryKey: ['analyses', analysisId, 'diffs', 'all-for-confirm'],
    queryFn: () => listAnalysisDiffs(analysisId),
    enabled: Boolean(analysisId) && showDiffs,
  })
  const pendingActionable = countPendingActionableDiffs(allDiffsQuery.data?.data ?? [])
  const canConfirm = canConfirmAnalysis({
    status: analysis?.status,
    diffsQuerySuccess: allDiffsQuery.isSuccess,
    pendingActionable,
    baseProfileVersion: analysis?.base_profile_version,
  })

  const invalidateDetail = async () => {
    await queryClient.invalidateQueries({ queryKey: ['analyses', analysisId] })
    await queryClient.invalidateQueries({ queryKey: ['analyses'] })
    setSelectedKeys([])
  }

  const decisionMutation = useMutation({
    mutationFn: ({
      diffId,
      body,
    }: {
      diffId: string
      body: {
        review_status: ReviewStatus
        decided_value?: unknown
        existing_target_id?: string | null
      }
    }) => reviewDiff(analysisId, diffId, body),
    onSuccess: async () => {
      message.success('검토 결과가 저장되었습니다.')
      setEditOpen(false)
      setMergeOpen(false)
      setEditingDiff(null)
      await invalidateDetail()
    },
    onError: (error) => {
      message.error(apiErrorMessage(error, '검토 저장에 실패했습니다.'))
    },
  })

  const bulkMutation = useMutation({
    mutationFn: (body: { diff_ids: string[]; review_status: ReviewStatus }) =>
      bulkReviewDiffs(analysisId, body),
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
      }),
    onSuccess: async (res) => {
      message.success('AI 분석 결과가 프로필에 반영되었습니다.')
      setConfirmOpen(false)
      await invalidateDetail()
      await queryClient.invalidateQueries({ queryKey: ['people', res.data.person_id] })
    },
    onError: (error) => {
      if (apiErrorCode(error) === 'PROFILE_VERSION_CONFLICT') {
        message.error(
          '분석 이후 인력 프로필이 변경되었습니다. 현재 분석 결과를 그대로 확정할 수 없습니다. 최신 프로필 기준으로 다시 분석해 주세요.',
        )
        return
      }
      message.error(apiErrorMessage(error, '최종 확정에 실패했습니다.'))
    },
  })

  const openModify = (diff: DiffItem) => {
    setEditingDiff(diff)
    editForm.setFieldsValue({ decided_value: buildDefaultModifiedDecision(diff) })
    setEditOpen(true)
  }

  const openMerge = (diff: DiffItem) => {
    setEditingDiff(diff)
    mergeForm.setFieldsValue({
      existing_target_id: diff.existing_target_id ?? '',
      decided_value: initialMergeDecidedValueText(),
    })
    setMergeOpen(true)
  }

  const submitModify = async (values: { decided_value: string }) => {
    if (!editingDiff) return
    let decided: unknown = values.decided_value
    if (!isProfileScalarDiff(editingDiff)) {
      try {
        decided = JSON.parse(values.decided_value)
      } catch {
        message.error('JSON 형식이 올바르지 않습니다.')
        return
      }
    } else if (editingDiff.field_name === 'birth_year') {
      const n = Number(values.decided_value)
      decided = Number.isNaN(n) ? values.decided_value : n
    } else {
      decided = values.decided_value
    }
    await decisionMutation.mutateAsync({
      diffId: editingDiff.id,
      body: { review_status: 'MODIFIED', decided_value: decided },
    })
  }

  const submitMerge = async (values: {
    existing_target_id: string
    decided_value?: string
  }) => {
    if (!editingDiff) return
    let body: {
      review_status: 'MERGED'
      existing_target_id: string
      decided_value?: unknown
    }
    try {
      body = buildMergeDecisionRequestBody({
        existing_target_id: values.existing_target_id,
        decided_value_text: values.decided_value,
      })
    } catch {
      message.error('decided_value JSON 형식이 올바르지 않습니다.')
      return
    }
    await decisionMutation.mutateAsync({
      diffId: editingDiff.id,
      body,
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
      width: 180,
      render: (v) => (
        <Typography.Paragraph
          ellipsis={{ rows: 3, expandable: true, symbol: '더보기' }}
          style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}
        >
          {formatValue(v)}
        </Typography.Paragraph>
      ),
    },
    {
      title: 'AI후보',
      dataIndex: 'new_value',
      width: 180,
      render: (v) => (
        <Typography.Paragraph
          ellipsis={{ rows: 3, expandable: true, symbol: '더보기' }}
          style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}
        >
          {formatValue(v)}
        </Typography.Paragraph>
      ),
    },
    {
      title: '문서 근거',
      key: 'evidence',
      width: 200,
      render: (_, row) => {
        const items = row.evidence ?? []
        if (!items.length) {
          return <Typography.Text type="secondary">—</Typography.Text>
        }
        return (
          <Space direction="vertical" size={2}>
            {items.slice(0, 3).map((ev, idx) => (
              <div key={ev.id || `${row.id}-ev-${idx}`}>
                <Typography.Text
                  ellipsis
                  style={{ maxWidth: 160, display: 'inline-block' }}
                  title={ev.quote_text || undefined}
                >
                  {ev.page_no != null ? `p.${ev.page_no} ` : ''}
                  {ev.quote_text || '근거'}
                </Typography.Text>
                {ev.id ? (
                  <Button type="link" size="small" onClick={() => void openEvidence(ev)}>
                    근거 보기
                  </Button>
                ) : null}
              </div>
            ))}
          </Space>
        )
      },
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
        if (!canReview || row.review_status !== 'PENDING') {
          return <Typography.Text type="secondary">—</Typography.Text>
        }
        return (
          <Space size="small" wrap>
            <Button
              type="link"
              size="small"
              loading={decisionMutation.isPending}
              onClick={() =>
                decisionMutation.mutate({
                  diffId: row.id,
                  body: { review_status: 'ACCEPTED' },
                })
              }
            >
              승인
            </Button>
            <Button
              type="link"
              size="small"
              danger
              loading={decisionMutation.isPending}
              onClick={() =>
                decisionMutation.mutate({
                  diffId: row.id,
                  body: { review_status: 'REJECTED' },
                })
              }
            >
              반려
            </Button>
            <Button type="link" size="small" onClick={() => openModify(row)}>
              수정
            </Button>
            {isProjectRootReview(row) ? (
              <Button type="link" size="small" onClick={() => openMerge(row)}>
                병합
              </Button>
            ) : null}
          </Space>
        )
      },
    },
  ]

  if (detailQuery.isLoading || !analysis) {
    return <Typography.Text>불러오는 중…</Typography.Text>
  }

  const counts = analysis.counts
  const docLabels = analysis.documents.map((d) => {
    const type = d.document_type_name || d.document_type_code
    const ver = d.version_no != null ? ` v${d.version_no}` : ''
    return type ? `${d.original_filename} (${type}${ver})` : d.original_filename
  })

  return (
    <div>
      <Button type="link" onClick={() => navigate('/analyses')} style={{ paddingLeft: 0 }}>
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
          {analysis.error_message ? (
            <Alert
              style={{ marginTop: 12 }}
              type="error"
              showIcon
              message="분석 실패"
              description={analysis.error_message}
            />
          ) : null}
        </div>
        <Space direction="vertical" align="end">
          <Typography.Text type="secondary">
            생성 {formatDate(analysis.created_at)} · 완료 {formatDate(analysis.completed_at)}
          </Typography.Text>
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

      {isActiveAnalysisStatus(analysis.status) ? (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message={`분석이 ${analysis.status} 상태입니다. 완료될 때까지 자동 갱신합니다.`}
        />
      ) : (
        <>
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

          <Tabs
            activeKey={filterTab}
            onChange={(key) => {
              setFilterTab(key as DiffFilterTab)
              setSelectedKeys([])
            }}
            items={[
              { key: 'all', label: `전체 (${counts.same + counts.new + counts.update + counts.conflict + counts.review})` },
              { key: 'NEW', label: `신규 (${counts.new})` },
              { key: 'UPDATE', label: `변경 (${counts.update})` },
              { key: 'CONFLICT', label: `충돌 (${counts.conflict})` },
              { key: 'REVIEW', label: `확인필요 (${counts.review})` },
              { key: 'reviewed', label: '검토완료' },
            ]}
            style={{ marginBottom: 8 }}
          />

          {canReview ? (
            <Space style={{ marginBottom: 12 }}>
              <Button
                disabled={selectedKeys.length === 0}
                loading={bulkMutation.isPending}
                onClick={() =>
                  bulkMutation.mutate({
                    diff_ids: selectedKeys.map(String),
                    review_status: 'ACCEPTED',
                  })
                }
              >
                선택 승인
              </Button>
              <Button
                disabled={selectedKeys.length === 0}
                loading={bulkMutation.isPending}
                onClick={() =>
                  bulkMutation.mutate({
                    diff_ids: selectedKeys.map(String),
                    review_status: 'REJECTED',
                  })
                }
              >
                선택 반려
              </Button>
              <Typography.Text type="secondary">
                CONFLICT/REVIEW는 일괄 승인할 수 없습니다.
              </Typography.Text>
            </Space>
          ) : null}

          <Table
            rowKey="id"
            loading={diffsQuery.isLoading}
            columns={columns}
            dataSource={diffsQuery.data?.data ?? []}
            size="small"
            scroll={{ x: 1100 }}
            pagination={{ pageSize: 50, showSizeChanger: true }}
            rowSelection={
              canReview
                ? {
                    selectedRowKeys: selectedKeys,
                    onChange: setSelectedKeys,
                    getCheckboxProps: (row) => ({
                      disabled: row.review_status !== 'PENDING',
                    }),
                  }
                : undefined
            }
            locale={{ emptyText: '표시할 Diff가 없습니다.' }}
          />
        </>
      )}

      <Modal
        title="Diff 수정"
        open={editOpen}
        onCancel={() => {
          setEditOpen(false)
          setEditingDiff(null)
        }}
        onOk={() => editForm.submit()}
        confirmLoading={decisionMutation.isPending}
        destroyOnHidden
        okText="저장"
      >
        <Typography.Paragraph type="secondary">
          {editingDiff
            ? `${editingDiff.entity_type} · ${editingDiff.field_name || editingDiff.candidate_path || ''}`
            : null}
        </Typography.Paragraph>
        <Form form={editForm} layout="vertical" onFinish={submitModify}>
          <Form.Item
            name="decided_value"
            label="decided_value"
            rules={[{ required: true, message: '값을 입력하세요.' }]}
          >
            {editingDiff && isProfileScalarDiff(editingDiff) ? (
              <Input />
            ) : (
              <Input.TextArea rows={8} style={{ fontFamily: 'monospace' }} />
            )}
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="프로젝트 병합 (MERGED)"
        open={mergeOpen}
        onCancel={() => {
          setMergeOpen(false)
          setEditingDiff(null)
        }}
        onOk={() => mergeForm.submit()}
        confirmLoading={decisionMutation.isPending}
        destroyOnHidden
        okText="병합"
      >
        {editingDiff?.new_value != null ? (
          <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
            Candidate (읽기 전용)
          </Typography.Paragraph>
        ) : null}
        {editingDiff?.new_value != null ? (
          <Typography.Paragraph>
            <pre
              style={{
                margin: 0,
                marginBottom: 16,
                maxHeight: 180,
                overflow: 'auto',
                padding: 8,
                background: 'rgba(0,0,0,0.04)',
                fontFamily: 'monospace',
                fontSize: 12,
              }}
            >
              {formatValue(editingDiff.new_value)}
            </pre>
          </Typography.Paragraph>
        ) : null}
        <Form form={mergeForm} layout="vertical" onFinish={submitMerge}>
          <Form.Item
            name="existing_target_id"
            label="existing_target_id"
            rules={[{ required: true, message: '기존 프로젝트 ID를 입력하세요.' }]}
          >
            <Input placeholder="기존 Project UUID" />
          </Form.Item>
          <Form.Item
            name="decided_value"
            label="override JSON (선택 — 명시한 필드만 overwrite)"
            extra="비우면 null-fill + additive만 적용됩니다. 값이 있을 때만 decided_value를 전송합니다."
          >
            <Input.TextArea
              rows={6}
              style={{ fontFamily: 'monospace' }}
              placeholder="비워 두면 decided_value를 보내지 않습니다"
            />
          </Form.Item>
        </Form>
      </Modal>

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
          검토 결과를 운영 프로필에 반영합니다.
        </Typography.Paragraph>
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
