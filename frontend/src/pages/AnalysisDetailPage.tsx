import { useState } from 'react'
import {
  Alert,
  Button,
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

import { apiErrorMessage } from '@/api/errors'
import {
  bulkReviewDiffs,
  confidenceColor,
  confidenceLabel,
  getAnalysis,
  isActiveAnalysisStatus,
  listAnalysisDiffs,
  reviewDiff,
  type AnalysisStatus,
  type ChangeType,
  type DiffItem,
  type ReviewStatus,
} from '@/api/analyses'

type DiffFilterTab =
  | 'all'
  | 'NEW'
  | 'UPDATE'
  | 'CONFLICT'
  | 'REVIEW'
  | 'reviewed'

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

function isProfileScalar(diff: DiffItem): boolean {
  return (
    diff.entity_type === 'PROFILE' &&
    Boolean(diff.field_name) &&
    PROFILE_SCALAR_FIELDS.has(diff.field_name!)
  )
}

function defaultDecidedValue(diff: DiffItem): string {
  if (diff.new_value === null || diff.new_value === undefined) return ''
  if (isProfileScalar(diff) && (typeof diff.new_value === 'string' || typeof diff.new_value === 'number')) {
    return String(diff.new_value)
  }
  try {
    return JSON.stringify(diff.new_value, null, 2)
  } catch {
    return String(diff.new_value)
  }
}

export function AnalysisDetailPage() {
  const { analysisId = '' } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [filterTab, setFilterTab] = useState<DiffFilterTab>('all')
  const [selectedKeys, setSelectedKeys] = useState<Key[]>([])
  const [editOpen, setEditOpen] = useState(false)
  const [mergeOpen, setMergeOpen] = useState(false)
  const [editingDiff, setEditingDiff] = useState<DiffItem | null>(null)
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

  const openModify = (diff: DiffItem) => {
    setEditingDiff(diff)
    editForm.setFieldsValue({ decided_value: defaultDecidedValue(diff) })
    setEditOpen(true)
  }

  const openMerge = (diff: DiffItem) => {
    setEditingDiff(diff)
    mergeForm.setFieldsValue({
      existing_target_id: diff.existing_target_id ?? '',
      decided_value:
        diff.new_value != null ? JSON.stringify(diff.new_value, null, 2) : undefined,
    })
    setMergeOpen(true)
  }

  const submitModify = async (values: { decided_value: string }) => {
    if (!editingDiff) return
    let decided: unknown = values.decided_value
    if (!isProfileScalar(editingDiff)) {
      try {
        decided = JSON.parse(values.decided_value)
      } catch {
        message.error('JSON 형식이 올바르지 않습니다.')
        return
      }
    } else if (
      editingDiff.field_name === 'birth_year' ||
      editingDiff.field_name === 'career_document_value'
    ) {
      const n = Number(values.decided_value)
      decided = Number.isNaN(n) ? values.decided_value : n
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
    let decided: unknown = undefined
    if (values.decided_value?.trim()) {
      try {
        decided = JSON.parse(values.decided_value)
      } catch {
        message.error('decided_value JSON 형식이 올바르지 않습니다.')
        return
      }
    }
    await decisionMutation.mutateAsync({
      diffId: editingDiff.id,
      body: {
        review_status: 'MERGED',
        existing_target_id: values.existing_target_id,
        decided_value: decided,
      },
    })
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
            {row.entity_type === 'PROJECT' && row.change_type === 'REVIEW' ? (
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
          <TooltipConfirmNotice />
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
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
            message="최종 확정은 다음 단계에서 제공됩니다."
            action={
              <Button disabled type="primary">
                최종 확정
              </Button>
            }
          />

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
            {editingDiff && isProfileScalar(editingDiff) ? (
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
        <Form form={mergeForm} layout="vertical" onFinish={submitMerge}>
          <Form.Item
            name="existing_target_id"
            label="existing_target_id"
            rules={[{ required: true, message: '기존 프로젝트 ID를 입력하세요.' }]}
          >
            <Input placeholder="기존 Project UUID" />
          </Form.Item>
          <Form.Item name="decided_value" label="decided_value (JSON, 선택)">
            <Input.TextArea rows={6} style={{ fontFamily: 'monospace' }} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

function TooltipConfirmNotice() {
  return (
    <Button disabled title="최종 확정은 다음 단계에서 제공됩니다.">
      최종 확정
    </Button>
  )
}
