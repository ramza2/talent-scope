import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Row,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd'
import {
  FileSearchOutlined,
  PlusOutlined,
  ReloadOutlined,
  TeamOutlined,
  UserAddOutlined,
} from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'

import { apiErrorMessage } from '@/api/errors'
import {
  getDashboard,
  type DashboardDocumentFailure,
  type DashboardRecentAnalysis,
  type DashboardRecentPerson,
} from '@/api/dashboard'
import { GRADE_LABELS, type TechnicalGrade } from '@/api/people'

const { Title, Text } = Typography

const PERSON_STATUS_LABELS: Record<string, string> = {
  ACTIVE: '활성',
  INACTIVE: '비활성',
  ARCHIVED: '보관',
  DELETED: '삭제',
}

const ANALYSIS_STATUS_LABELS: Record<string, { label: string; color: string }> = {
  QUEUED: { label: '대기', color: 'default' },
  PROCESSING: { label: '분석 중', color: 'processing' },
  REVIEWING: { label: '검토 대기', color: 'warning' },
  FAILED: { label: '실패', color: 'error' },
  CONFIRMED: { label: '확정', color: 'success' },
  CANCELLED: { label: '취소', color: 'default' },
}

function formatDateTime(value?: string | null): string {
  if (!value) return '—'
  const d = dayjs(value)
  if (!d.isValid()) return '—'
  return d.format('YYYY.MM.DD HH:mm')
}

function formatDate(value?: string | null): string {
  if (!value) return '—'
  const d = dayjs(value)
  if (!d.isValid()) return '—'
  return d.format('YYYY.MM.DD')
}

function gradeLabel(grade?: string | null): string {
  if (!grade) return '—'
  if (grade in GRADE_LABELS) return GRADE_LABELS[grade as TechnicalGrade]
  return grade
}

function personStatusTag(status: string) {
  const label = PERSON_STATUS_LABELS[status] ?? status
  const color =
    status === 'ACTIVE' ? 'success' : status === 'INACTIVE' ? 'default' : 'processing'
  return <Tag color={color}>{label}</Tag>
}

function analysisStatusTag(status: string) {
  const meta = ANALYSIS_STATUS_LABELS[status] ?? { label: status, color: 'default' }
  return <Tag color={meta.color}>{meta.label}</Tag>
}

export function DashboardPage() {
  const navigate = useNavigate()
  const query = useQuery({
    queryKey: ['dashboard'],
    queryFn: getDashboard,
  })

  if (query.isLoading) {
    return (
      <div style={{ padding: 48, textAlign: 'center' }}>
        <Spin size="large" />
      </div>
    )
  }

  if (query.isError || !query.data) {
    return (
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        <Title level={3} style={{ margin: 0 }}>
          대시보드
        </Title>
        <Alert
          type="error"
          showIcon
          message="대시보드 정보를 불러오지 못했습니다."
          description={apiErrorMessage(query.error, '잠시 후 다시 시도해 주세요.')}
          action={
            <Button size="small" icon={<ReloadOutlined />} onClick={() => query.refetch()}>
              다시 시도
            </Button>
          }
        />
      </Space>
    )
  }

  const data = query.data.data
  const canManagePeople = data.permissions.can_manage_people
  const canManageAnalyses = data.permissions.can_manage_analyses
  const analysis = data.analysis
  const inFlight = analysis ? analysis.queued + analysis.processing : 0

  const recentPeopleColumns: ColumnsType<DashboardRecentPerson> = [
    {
      title: '이름',
      dataIndex: 'name',
      render: (name: string | null | undefined, row) => (
        <Link to={`/people/${row.person_id}`}>{name || '—'}</Link>
      ),
    },
    {
      title: '소속',
      dataIndex: 'affiliation_company',
      render: (v?: string | null) => v || '—',
    },
    {
      title: '기술등급',
      dataIndex: 'technical_grade',
      render: (g?: string | null) => gradeLabel(g),
    },
    {
      title: '상태',
      dataIndex: 'status',
      width: 100,
      render: (s: string) => personStatusTag(s),
    },
    {
      title: '등록일',
      dataIndex: 'created_at',
      width: 120,
      render: (v: string) => formatDate(v),
    },
  ]

  const recentAnalysisColumns: ColumnsType<DashboardRecentAnalysis> = [
    {
      title: '인력',
      dataIndex: 'person_name',
      render: (name?: string | null) => name || '—',
    },
    {
      title: '상태',
      dataIndex: 'status',
      width: 110,
      render: (s: string) => analysisStatusTag(s),
    },
    {
      title: '미검토',
      dataIndex: 'pending_count',
      width: 80,
      align: 'right',
    },
    {
      title: '생성',
      dataIndex: 'created_at',
      width: 140,
      render: (v: string) => formatDateTime(v),
    },
    {
      title: '완료',
      dataIndex: 'completed_at',
      width: 140,
      render: (v?: string | null) => formatDateTime(v),
    },
  ]

  const failureColumns: ColumnsType<DashboardDocumentFailure> = [
    {
      title: '파일',
      dataIndex: 'original_filename',
      ellipsis: true,
      render: (name: string, row) => (
        <Link to={`/people/${row.person_id}`}>{name}</Link>
      ),
    },
    {
      title: '인력',
      dataIndex: 'person_name',
      render: (name: string | null | undefined, row) => (
        <Link to={`/people/${row.person_id}`}>{name || '—'}</Link>
      ),
    },
    {
      title: '상태',
      dataIndex: 'processing_status',
      width: 90,
      render: () => <Tag color="error">실패</Tag>,
    },
    {
      title: '사유',
      dataIndex: 'processing_error',
      ellipsis: true,
      render: (v?: string | null) => v || '—',
    },
    {
      title: '업로드',
      dataIndex: 'uploaded_at',
      width: 140,
      render: (v: string) => formatDateTime(v),
    },
  ]

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <div>
        <Title level={3} style={{ margin: 0 }}>
          대시보드
        </Title>
        <Text type="secondary">인력 데이터와 AI 분석 업무 현황</Text>
      </div>

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={canManageAnalyses ? 6 : 12}>
          <Card size="small">
            <Statistic title="전체 인력" value={data.people.total} suffix="명" />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={canManageAnalyses ? 6 : 12}>
          <Card size="small">
            <Statistic title="활성 인력" value={data.people.active} suffix="명" />
          </Card>
        </Col>
        {canManageAnalyses && analysis ? (
          <>
            <Col xs={24} sm={12} lg={6}>
              <Card size="small">
                <Statistic
                  title="검토 대기"
                  value={analysis.review_pending_runs}
                  suffix="건"
                  valueStyle={{ color: '#d48806' }}
                />
              </Card>
            </Col>
            <Col xs={24} sm={12} lg={6}>
              <Card size="small">
                <Statistic title="처리 중" value={inFlight} suffix="건" />
              </Card>
            </Col>
            <Col xs={24} sm={12} lg={6}>
              <Card size="small">
                <Statistic
                  title="실패"
                  value={analysis.failed}
                  suffix="건"
                  valueStyle={analysis.failed > 0 ? { color: '#cf1322' } : undefined}
                />
              </Card>
            </Col>
          </>
        ) : null}
      </Row>

      <Card size="small" title="바로가기">
        <Space wrap>
          <Button icon={<FileSearchOutlined />} onClick={() => navigate('/search')}>
            인력 검색
          </Button>
          <Button icon={<TeamOutlined />} onClick={() => navigate('/people')}>
            인력 목록
          </Button>
          {canManagePeople ? (
            <Button
              type="primary"
              icon={<UserAddOutlined />}
              onClick={() => navigate('/people/new')}
            >
              신규 인력 등록
            </Button>
          ) : null}
          {canManageAnalyses ? (
            <Button icon={<PlusOutlined />} onClick={() => navigate('/analyses')}>
              AI 분석 검토
            </Button>
          ) : null}
        </Space>
      </Card>

      <Card
        size="small"
        title="최근 등록 인력"
        extra={<Link to="/people">전체 보기</Link>}
      >
        {data.recent_people.length === 0 ? (
          <Empty description="등록된 인력이 없습니다." image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <Table
            size="small"
            rowKey="person_id"
            pagination={false}
            columns={recentPeopleColumns}
            dataSource={data.recent_people}
          />
        )}
      </Card>

      {canManageAnalyses && analysis ? (
        <Card size="small" title="AI 분석 현황">
          <Space wrap size="middle">
            <Tag>{`대기 ${analysis.queued}`}</Tag>
            <Tag color="processing">{`분석 중 ${analysis.processing}`}</Tag>
            <Tag color="warning">{`검토 대기 ${analysis.reviewing}`}</Tag>
            <Tag color="error">{`실패 ${analysis.failed}`}</Tag>
          </Space>
        </Card>
      ) : null}

      {canManageAnalyses && data.recent_analyses ? (
        <Card size="small" title="최근 AI 분석" extra={<Link to="/analyses">전체 보기</Link>}>
          {data.recent_analyses.length === 0 ? (
            <Empty
              description="최근 AI 분석이 없습니다."
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            />
          ) : (
            <Table
              size="small"
              rowKey="analysis_id"
              pagination={false}
              columns={recentAnalysisColumns}
              dataSource={data.recent_analyses}
              onRow={(row) => ({
                style: { cursor: 'pointer' },
                onClick: () => navigate(`/analyses/${row.analysis_id}`),
              })}
            />
          )}
        </Card>
      ) : null}

      {canManageAnalyses && data.document_failures ? (
        <Card size="small" title="문서 처리 실패">
          {data.document_failures.length === 0 ? (
            <Empty
              description="최근 문서 처리 실패가 없습니다."
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            />
          ) : (
            <Table
              size="small"
              rowKey="document_id"
              pagination={false}
              columns={failureColumns}
              dataSource={data.document_failures}
            />
          )}
        </Card>
      ) : null}
    </Space>
  )
}
