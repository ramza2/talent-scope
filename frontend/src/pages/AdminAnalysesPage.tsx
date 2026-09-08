import { useState } from 'react'
import { Button, Select, Space, Table, Tag, Tooltip, Typography, message } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import type { ColumnsType } from 'antd/es/table'

import { apiErrorMessage } from '@/api/errors'
import {
  isActiveAnalysisStatus,
  listAnalyses,
  retryAnalysis,
  type AnalysisListItem,
  type AnalysisStatus,
} from '@/api/analyses'

type Filters = {
  status: AnalysisStatus | ''
  page: number
  page_size: number
}

const STATUS_OPTIONS: Array<{ value: AnalysisStatus | ''; label: string }> = [
  { value: '', label: '상태 전체' },
  { value: 'QUEUED', label: 'QUEUED' },
  { value: 'PROCESSING', label: 'PROCESSING' },
  { value: 'REVIEWING', label: 'REVIEWING' },
  { value: 'CONFIRMED', label: 'CONFIRMED' },
  { value: 'FAILED', label: 'FAILED' },
  { value: 'CANCELLED', label: 'CANCELLED' },
]

function formatDate(value?: string | null) {
  if (!value) return '—'
  try {
    return new Date(value).toLocaleString('ko-KR')
  } catch {
    return value
  }
}

function statusTag(status: AnalysisStatus, errorMessage?: string | null) {
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
  const tag = <Tag color={color}>{status}</Tag>
  if (status === 'FAILED' && errorMessage) {
    return <Tooltip title={errorMessage}>{tag}</Tooltip>
  }
  return tag
}

export function AdminAnalysesPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [filters, setFilters] = useState<Filters>({
    status: '',
    page: 1,
    page_size: 20,
  })

  const queryKey = ['analyses', 'admin', filters] as const
  const { data, isLoading, isFetching } = useQuery({
    queryKey,
    queryFn: () =>
      listAnalyses({
        status: filters.status || undefined,
        page: filters.page,
        page_size: filters.page_size,
        sort: 'created_desc',
      }),
    refetchInterval: (query) => {
      const items = query.state.data?.data ?? []
      return items.some((row) => isActiveAnalysisStatus(row.status)) ? 3000 : false
    },
  })

  const retryMutation = useMutation({
    mutationFn: (analysisId: string) => retryAnalysis(analysisId),
    onSuccess: async () => {
      message.success('재처리를 요청했습니다.')
      await queryClient.invalidateQueries({ queryKey: ['analyses'] })
    },
    onError: (error) => {
      message.error(apiErrorMessage(error, '재처리 요청에 실패했습니다.'))
    },
  })

  const columns: ColumnsType<AnalysisListItem> = [
    {
      title: '인력',
      key: 'person',
      width: 140,
      render: (_, row) => row.person.name,
    },
    {
      title: '문서',
      dataIndex: 'documents',
      key: 'documents',
      ellipsis: true,
      render: (docs: string[]) => (docs.length ? docs.join(', ') : '—'),
    },
    {
      title: '상태',
      dataIndex: 'status',
      key: 'status',
      width: 130,
      render: (status: AnalysisStatus, row) => statusTag(status, row.error_message),
    },
    {
      title: 'NEW',
      key: 'new',
      width: 70,
      align: 'right',
      render: (_, row) => row.counts.new,
    },
    {
      title: 'UPDATE',
      key: 'update',
      width: 80,
      align: 'right',
      render: (_, row) => row.counts.update,
    },
    {
      title: 'CONFLICT',
      key: 'conflict',
      width: 90,
      align: 'right',
      render: (_, row) => row.counts.conflict,
    },
    {
      title: 'REVIEW',
      key: 'review',
      width: 80,
      align: 'right',
      render: (_, row) => row.counts.review,
    },
    {
      title: 'PENDING',
      key: 'pending',
      width: 90,
      align: 'right',
      render: (_, row) => row.counts.pending,
    },
    {
      title: '생성시각',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 170,
      render: formatDate,
    },
    {
      title: '완료시각',
      dataIndex: 'completed_at',
      key: 'completed_at',
      width: 170,
      render: formatDate,
    },
    {
      title: '작업',
      key: 'actions',
      width: 100,
      render: (_, row) =>
        row.status === 'FAILED' ? (
          <Button
            type="link"
            size="small"
            loading={retryMutation.isPending}
            onClick={(e) => {
              e.stopPropagation()
              retryMutation.mutate(row.analysis_id)
            }}
          >
            Retry
          </Button>
        ) : null,
    },
  ]

  return (
    <div>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>
            AI 분석 현황
          </Typography.Title>
          <Typography.Text type="secondary">
            ADMIN 전용 · 분석 실행/실패/대기 모니터링
          </Typography.Text>
        </div>
        <Button
          icon={<ReloadOutlined />}
          loading={isFetching}
          onClick={() => void queryClient.invalidateQueries({ queryKey: ['analyses'] })}
        >
          새로고침
        </Button>
      </Space>

      <Space wrap style={{ marginBottom: 16 }}>
        <Select
          style={{ width: 180 }}
          value={filters.status}
          onChange={(value) => setFilters((prev) => ({ ...prev, status: value, page: 1 }))}
          options={STATUS_OPTIONS}
        />
      </Space>

      <Table
        rowKey="analysis_id"
        loading={isLoading}
        columns={columns}
        dataSource={data?.data ?? []}
        size="middle"
        onRow={(row) => ({
          onClick: () => navigate(`/analyses/${row.analysis_id}`),
          style: { cursor: 'pointer' },
        })}
        pagination={{
          current: filters.page,
          pageSize: filters.page_size,
          total: data?.meta.total ?? 0,
          showSizeChanger: true,
          onChange: (page, pageSize) =>
            setFilters((prev) => ({ ...prev, page, page_size: pageSize })),
        }}
      />
    </div>
  )
}
