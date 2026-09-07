import { useMemo, useState } from 'react'
import {
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import type { ColumnsType } from 'antd/es/table'

import { apiErrorMessage } from '@/api/errors'
import { listCodes } from '@/api/codes'
import {
  GRADE_LABELS,
  createPerson,
  formatCareerMonths,
  listPeople,
  type PeopleListItem,
  type PersonStatus,
  type TechnicalGrade,
} from '@/api/people'
import { useAuthMe } from '@/app/auth'

const STATUS_OPTIONS: Array<{ value: PersonStatus | ''; label: string }> = [
  { value: '', label: '상태 전체' },
  { value: 'ACTIVE', label: 'ACTIVE' },
  { value: 'INACTIVE', label: 'INACTIVE' },
  { value: 'ARCHIVED', label: 'ARCHIVED' },
]

const GRADE_OPTIONS = [
  { value: '', label: '등급 전체' },
  ...Object.entries(GRADE_LABELS).map(([value, label]) => ({
    value,
    label: `${label} (${value})`,
  })),
]

type Filters = {
  q: string
  status: PersonStatus | ''
  grade: TechnicalGrade | ''
  job_codes: string
  tech_codes: string
  exp_codes: string
  affiliation: string
  page: number
  page_size: number
}

export function PeopleListPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { data: me } = useAuthMe()
  const isAdmin = me?.role === 'ADMIN'

  const [filters, setFilters] = useState<Filters>({
    q: '',
    status: '',
    grade: '',
    job_codes: '',
    tech_codes: '',
    exp_codes: '',
    affiliation: '',
    page: 1,
    page_size: 20,
  })
  const [draft, setDraft] = useState(filters)
  const [createOpen, setCreateOpen] = useState(false)
  const [form] = Form.useForm()

  const queryKey = ['people', filters] as const
  const { data, isLoading, isFetching } = useQuery({
    queryKey,
    queryFn: () =>
      listPeople({
        q: filters.q || undefined,
        status: filters.status || undefined,
        grade: filters.grade || undefined,
        job_codes: filters.job_codes || undefined,
        tech_codes: filters.tech_codes || undefined,
        exp_codes: filters.exp_codes || undefined,
        affiliation: filters.affiliation || undefined,
        page: filters.page,
        page_size: filters.page_size,
      }),
  })

  const jobOptionsQuery = useQuery({
    queryKey: ['codes', 'JOB', 'people-filter'],
    queryFn: () => listCodes({ type: 'JOB', active: true }),
  })
  const techOptionsQuery = useQuery({
    queryKey: ['codes', 'TECH', 'people-filter'],
    queryFn: () => listCodes({ type: 'TECH', active: true }),
  })
  const expOptionsQuery = useQuery({
    queryKey: ['codes', 'EXP', 'people-filter'],
    queryFn: () => listCodes({ type: 'EXP', active: true }),
  })

  const createMutation = useMutation({
    mutationFn: createPerson,
    onSuccess: async (res) => {
      message.success('인력이 등록되었습니다.')
      setCreateOpen(false)
      form.resetFields()
      await queryClient.invalidateQueries({ queryKey: ['people'] })
      navigate(`/people/${res.data.id}`)
    },
    onError: (error) => message.error(apiErrorMessage(error, '인력 등록에 실패했습니다.')),
  })

  const columns: ColumnsType<PeopleListItem> = useMemo(
    () => [
      { title: '이름', dataIndex: 'name', key: 'name', width: 120 },
      {
        title: '주직무',
        key: 'primary_job',
        width: 140,
        render: (_, row) => row.primary_job?.name || '—',
      },
      {
        title: '기술등급',
        dataIndex: 'technical_grade',
        key: 'technical_grade',
        width: 100,
        render: (g?: TechnicalGrade | null) => (g ? GRADE_LABELS[g] : '—'),
      },
      {
        title: '경력',
        dataIndex: 'career_confirmed_months',
        key: 'career',
        width: 110,
        render: formatCareerMonths,
      },
      {
        title: '대표기술',
        key: 'skills',
        ellipsis: true,
        render: (_, row) => row.skills.map((s) => s.name).join(', ') || '—',
      },
      {
        title: '전문분야',
        key: 'expertise',
        ellipsis: true,
        render: (_, row) => row.expertise.map((s) => s.name).join(', ') || '—',
      },
      {
        title: '소속',
        dataIndex: 'affiliation_company',
        key: 'affiliation_company',
        width: 140,
        render: (v?: string | null) => v || '—',
      },
      {
        title: '상태',
        dataIndex: 'status',
        key: 'status',
        width: 110,
        render: (status: PersonStatus) => <Tag>{status}</Tag>,
      },
      {
        title: '최근갱신',
        dataIndex: 'updated_at',
        key: 'updated_at',
        width: 170,
        render: (v: string) => new Date(v).toLocaleString(),
      },
    ],
    [],
  )

  const applySearch = () =>
    setFilters({
      ...draft,
      page: 1,
      page_size: filters.page_size,
    })

  return (
    <div>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>
            인력 목록
          </Typography.Title>
          <Typography.Text type="secondary">관리용 목록 · AI 검색은 통합검색 메뉴 사용</Typography.Text>
        </div>
        {isAdmin ? (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            직접 등록
          </Button>
        ) : null}
      </Space>

      <Space wrap style={{ marginBottom: 16 }}>
        <Input
          placeholder="이름/키워드"
          allowClear
          style={{ width: 180 }}
          value={draft.q}
          onChange={(e) => setDraft((p) => ({ ...p, q: e.target.value }))}
          onPressEnter={applySearch}
        />
        <Select
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder="직무"
          style={{ width: 180 }}
          value={draft.job_codes || undefined}
          onChange={(v) => setDraft((p) => ({ ...p, job_codes: v || '' }))}
          options={(jobOptionsQuery.data?.data ?? []).map((c) => ({
            value: c.code,
            label: c.name,
          }))}
        />
        <Select
          style={{ width: 150 }}
          value={draft.grade}
          onChange={(v) => setDraft((p) => ({ ...p, grade: v }))}
          options={GRADE_OPTIONS}
        />
        <Select
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder="기술"
          style={{ width: 180 }}
          value={draft.tech_codes || undefined}
          onChange={(v) => setDraft((p) => ({ ...p, tech_codes: v || '' }))}
          options={(techOptionsQuery.data?.data ?? []).map((c) => ({
            value: c.code,
            label: c.name,
          }))}
        />
        <Select
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder="전문분야"
          style={{ width: 180 }}
          value={draft.exp_codes || undefined}
          onChange={(v) => setDraft((p) => ({ ...p, exp_codes: v || '' }))}
          options={(expOptionsQuery.data?.data ?? []).map((c) => ({
            value: c.code,
            label: c.name,
          }))}
        />
        <Input
          placeholder="소속"
          allowClear
          style={{ width: 140 }}
          value={draft.affiliation}
          onChange={(e) => setDraft((p) => ({ ...p, affiliation: e.target.value }))}
        />
        <Select
          style={{ width: 140 }}
          value={draft.status}
          onChange={(v) => setDraft((p) => ({ ...p, status: v }))}
          options={STATUS_OPTIONS}
        />
        <Button type="primary" icon={<ReloadOutlined />} loading={isFetching} onClick={applySearch}>
          검색
        </Button>
        <Button
          onClick={() => {
            const reset = {
              q: '',
              status: '' as const,
              grade: '' as const,
              job_codes: '',
              tech_codes: '',
              exp_codes: '',
              affiliation: '',
              page: 1,
              page_size: 20,
            }
            setDraft(reset)
            setFilters(reset)
          }}
        >
          초기화
        </Button>
      </Space>

      <Table
        rowKey="id"
        loading={isLoading}
        columns={columns}
        dataSource={data?.data ?? []}
        onRow={(row) => ({
          onClick: () => navigate(`/people/${row.id}`),
          style: { cursor: 'pointer' },
        })}
        pagination={{
          current: filters.page,
          pageSize: filters.page_size,
          total: data?.meta.total ?? 0,
          showSizeChanger: true,
          onChange: (page, pageSize) => setFilters((p) => ({ ...p, page, page_size: pageSize })),
        }}
      />

      <Modal
        title="인력 직접 등록"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={createMutation.isPending}
        destroyOnHidden
        width={640}
      >
        <Typography.Paragraph type="secondary">
          문서 없는 Confirmed Profile 수기 등록입니다. 문서 기반 신규 등록은 「신규 인력 등록」
          메뉴를 사용하세요.
        </Typography.Paragraph>
        <Form
          form={form}
          layout="vertical"
          onFinish={(values) => createMutation.mutate(values)}
        >
          <Form.Item name="name" label="이름" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="birth_year" label="출생연도">
            <InputNumber style={{ width: '100%' }} min={1900} max={2100} />
          </Form.Item>
          <Form.Item name="phone" label="전화번호">
            <Input />
          </Form.Item>
          <Form.Item name="email" label="이메일">
            <Input />
          </Form.Item>
          <Form.Item name="address_region" label="지역(시/도)">
            <Input />
          </Form.Item>
          <Form.Item name="affiliation_company" label="소속회사">
            <Input />
          </Form.Item>
          <Form.Item name="department" label="부서">
            <Input />
          </Form.Item>
          <Form.Item name="current_title" label="직함">
            <Input />
          </Form.Item>
          <Form.Item name="employment_type" label="고용형태">
            <Input />
          </Form.Item>
          <Form.Item name="technical_grade" label="기술등급">
            <Select
              allowClear
              options={Object.entries(GRADE_LABELS).map(([value, label]) => ({
                value,
                label: `${label} (${value})`,
              }))}
            />
          </Form.Item>
          <Form.Item name="career_confirmed_months" label="확정 경력(개월)">
            <InputNumber style={{ width: '100%' }} min={0} />
          </Form.Item>
          <Form.Item name="profile_summary" label="Profile Summary">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
