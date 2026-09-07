import { useMemo, useState } from 'react'
import {
  Button,
  Card,
  DatePicker,
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
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { ColumnsType } from 'antd/es/table'

import { apiErrorMessage } from '@/api/errors'
import { listCodes } from '@/api/codes'
import {
  createEmploymentHistory,
  deleteEmploymentHistory,
  listEmploymentHistory,
  updateEmploymentHistory,
  type EmploymentItem,
} from '@/api/career'
import {
  createPersonProject,
  deleteProject,
  listPersonProjects,
  updateProject,
  type ProjectDetail,
} from '@/api/projects'
import { formatPeriod, fromIsoDate, toIsoDate } from '@/pages/people/dateUtils'

type CodeOption = { value: string; label: string }

function mergeCodeOptions(
  activeOptions: CodeOption[],
  linked: Array<{ code: string; name: string }>,
): CodeOption[] {
  const map = new Map(activeOptions.map((o) => [o.value, o]))
  for (const item of linked) {
    if (!map.has(item.code)) {
      map.set(item.code, {
        value: item.code,
        label: `${item.name} (비활성)`,
      })
    }
  }
  return Array.from(map.values())
}

type Props = {
  personId: string
  isAdmin: boolean
  onChanged: () => Promise<void>
}

export function ProjectCareerTab({ personId, isAdmin, onChanged }: Props) {
  const queryClient = useQueryClient()
  const [empOpen, setEmpOpen] = useState(false)
  const [editingEmp, setEditingEmp] = useState<EmploymentItem | null>(null)
  const [projOpen, setProjOpen] = useState(false)
  const [editingProj, setEditingProj] = useState<ProjectDetail | null>(null)
  const [empForm] = Form.useForm()
  const [projForm] = Form.useForm()

  const employmentQuery = useQuery({
    queryKey: ['people', personId, 'employment-history'],
    queryFn: () => listEmploymentHistory(personId),
  })
  const projectsQuery = useQuery({
    queryKey: ['people', personId, 'projects'],
    queryFn: () => listPersonProjects(personId),
  })

  const codeEnabled = projOpen
  const jobCodesQuery = useQuery({
    queryKey: ['codes', 'JOB', 'project'],
    queryFn: () => listCodes({ type: 'JOB', active: true }),
    enabled: codeEnabled,
  })
  const techCodesQuery = useQuery({
    queryKey: ['codes', 'TECH', 'project'],
    queryFn: () => listCodes({ type: 'TECH', active: true }),
    enabled: codeEnabled,
  })
  const expCodesQuery = useQuery({
    queryKey: ['codes', 'EXP', 'project'],
    queryFn: () => listCodes({ type: 'EXP', active: true }),
    enabled: codeEnabled,
  })
  const bizCodesQuery = useQuery({
    queryKey: ['codes', 'BIZ', 'project'],
    queryFn: () => listCodes({ type: 'BIZ', active: true }),
    enabled: codeEnabled,
  })
  const customerCodesQuery = useQuery({
    queryKey: ['codes', 'CUSTOMER_TYPE', 'project'],
    queryFn: () => listCodes({ type: 'CUSTOMER_TYPE', active: true }),
    enabled: codeEnabled,
  })

  const linkedJobs = editingProj?.jobs ?? []
  const linkedSkills = editingProj?.skills ?? []
  const linkedExp = editingProj?.expertise ?? []
  const linkedBiz = editingProj?.business_domains ?? []
  const linkedCust = editingProj?.customer_types ?? []

  const jobOptions = useMemo(
    () =>
      mergeCodeOptions(
        (jobCodesQuery.data?.data ?? []).map((c) => ({ value: c.code, label: c.name })),
        linkedJobs,
      ),
    [jobCodesQuery.data, linkedJobs],
  )
  const techOptions = useMemo(
    () =>
      mergeCodeOptions(
        (techCodesQuery.data?.data ?? []).map((c) => ({ value: c.code, label: c.name })),
        linkedSkills,
      ),
    [techCodesQuery.data, linkedSkills],
  )
  const expOptions = useMemo(
    () =>
      mergeCodeOptions(
        (expCodesQuery.data?.data ?? []).map((c) => ({ value: c.code, label: c.name })),
        linkedExp,
      ),
    [expCodesQuery.data, linkedExp],
  )
  const bizOptions = useMemo(
    () =>
      mergeCodeOptions(
        (bizCodesQuery.data?.data ?? []).map((c) => ({ value: c.code, label: c.name })),
        linkedBiz,
      ),
    [bizCodesQuery.data, linkedBiz],
  )
  const customerOptions = useMemo(
    () =>
      mergeCodeOptions(
        (customerCodesQuery.data?.data ?? []).map((c) => ({
          value: c.code,
          label: c.name,
        })),
        linkedCust,
      ),
    [customerCodesQuery.data, linkedCust],
  )

  const invalidateLocal = async () => {
    await queryClient.invalidateQueries({
      queryKey: ['people', personId, 'employment-history'],
    })
    await queryClient.invalidateQueries({ queryKey: ['people', personId, 'projects'] })
    await onChanged()
  }

  const empMutation = useMutation({
    mutationFn: async (values: Record<string, unknown>) => {
      const body = {
        company_name: values.company_name,
        department: values.department ?? null,
        title: values.title ?? null,
        start_date: toIsoDate(values.start_date as never),
        end_date: toIsoDate(values.end_date as never),
        responsibilities: values.responsibilities ?? null,
      }
      if (editingEmp) return updateEmploymentHistory(editingEmp.id, body)
      return createEmploymentHistory(personId, body)
    },
    onSuccess: async () => {
      message.success(editingEmp ? '근무경력을 수정했습니다.' : '근무경력을 추가했습니다.')
      setEmpOpen(false)
      setEditingEmp(null)
      await invalidateLocal()
    },
    onError: (error) => message.error(apiErrorMessage(error, '근무경력 저장에 실패했습니다.')),
  })

  const empDeleteMutation = useMutation({
    mutationFn: (id: string) => deleteEmploymentHistory(id),
    onSuccess: async () => {
      message.success('근무경력을 삭제했습니다.')
      await invalidateLocal()
    },
    onError: (error) => message.error(apiErrorMessage(error, '근무경력 삭제에 실패했습니다.')),
  })

  const projMutation = useMutation({
    mutationFn: async (values: Record<string, unknown>) => {
      const expertiseRows = (values.expertise as Array<{
        exp_code: string
        evidence_type?: string
      }>) ?? []
      const body = {
        project_name: values.project_name,
        customer_name: values.customer_name ?? null,
        start_date: toIsoDate(values.start_date as never),
        end_date: toIsoDate(values.end_date as never),
        duration_months: values.duration_months ?? null,
        responsibilities: values.responsibilities ?? null,
        project_summary: values.project_summary ?? null,
        job_codes: (values.job_codes as string[]) ?? [],
        tech_codes: (values.tech_codes as string[]) ?? [],
        expertise: expertiseRows.map((e) => ({
          exp_code: e.exp_code,
          evidence_type: e.evidence_type || 'EXPLICIT',
        })),
        biz_codes: (values.biz_codes as string[]) ?? [],
        customer_type_codes: (values.customer_type_codes as string[]) ?? [],
      }
      if (editingProj) return updateProject(editingProj.id, body)
      return createPersonProject(personId, body)
    },
    onSuccess: async () => {
      message.success(editingProj ? '프로젝트를 수정했습니다.' : '프로젝트를 추가했습니다.')
      setProjOpen(false)
      setEditingProj(null)
      await invalidateLocal()
    },
    onError: (error) => message.error(apiErrorMessage(error, '프로젝트 저장에 실패했습니다.')),
  })

  const projDeleteMutation = useMutation({
    mutationFn: (id: string) => deleteProject(id),
    onSuccess: async () => {
      message.success('프로젝트를 삭제했습니다.')
      await invalidateLocal()
    },
    onError: (error) => message.error(apiErrorMessage(error, '프로젝트 삭제에 실패했습니다.')),
  })

  const openEmpCreate = () => {
    setEditingEmp(null)
    empForm.resetFields()
    setEmpOpen(true)
  }

  const openEmpEdit = (row: EmploymentItem) => {
    setEditingEmp(row)
    empForm.setFieldsValue({
      company_name: row.company_name,
      department: row.department,
      title: row.title,
      start_date: fromIsoDate(row.start_date),
      end_date: fromIsoDate(row.end_date),
      responsibilities: row.responsibilities,
    })
    setEmpOpen(true)
  }

  const openProjCreate = () => {
    setEditingProj(null)
    projForm.resetFields()
    projForm.setFieldsValue({ expertise: [] })
    setProjOpen(true)
  }

  const openProjEdit = (row: ProjectDetail) => {
    setEditingProj(row)
    projForm.setFieldsValue({
      project_name: row.project_name,
      customer_name: row.customer_name,
      start_date: fromIsoDate(row.start_date),
      end_date: fromIsoDate(row.end_date),
      duration_months: row.duration_months ?? undefined,
      responsibilities: row.responsibilities,
      project_summary: row.project_summary,
      job_codes: row.jobs.map((j) => j.code),
      tech_codes: row.skills.map((s) => s.code),
      expertise: row.expertise.map((e) => ({
        exp_code: e.code,
        evidence_type: e.evidence_type,
      })),
      biz_codes: row.business_domains.map((b) => b.code),
      customer_type_codes: row.customer_types.map((c) => c.code),
    })
    setProjOpen(true)
  }

  const empColumns: ColumnsType<EmploymentItem> = [
    { title: '회사', dataIndex: 'company_name' },
    { title: '부서', dataIndex: 'department', render: (v) => v || '—' },
    { title: '직위', dataIndex: 'title', render: (v) => v || '—' },
    {
      title: '기간',
      key: 'period',
      render: (_, row) => formatPeriod(row.start_date, row.end_date),
    },
    {
      title: '담당업무',
      dataIndex: 'responsibilities',
      ellipsis: true,
      render: (v) => v || '—',
    },
    ...(isAdmin
      ? [
          {
            title: '관리',
            key: 'actions',
            width: 140,
            render: (_: unknown, row: EmploymentItem) => (
              <Space>
                <Button type="link" size="small" onClick={() => openEmpEdit(row)}>
                  수정
                </Button>
                <Button
                  type="link"
                  danger
                  size="small"
                  onClick={() =>
                    Modal.confirm({
                      title: '근무경력을 삭제하시겠습니까?',
                      onOk: () => empDeleteMutation.mutateAsync(row.id),
                    })
                  }
                >
                  삭제
                </Button>
              </Space>
            ),
          } as ColumnsType<EmploymentItem>[number],
        ]
      : []),
  ]

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Card
        title="근무경력"
        size="small"
        extra={
          isAdmin ? (
            <Button type="primary" onClick={openEmpCreate}>
              + 근무경력 추가
            </Button>
          ) : null
        }
      >
        <Table
          rowKey="id"
          loading={employmentQuery.isLoading}
          columns={empColumns}
          dataSource={employmentQuery.data?.data ?? []}
          pagination={false}
          locale={{ emptyText: '등록된 근무경력이 없습니다.' }}
        />
      </Card>

      <Card
        title="프로젝트"
        size="small"
        extra={
          isAdmin ? (
            <Button type="primary" onClick={openProjCreate}>
              + 프로젝트 추가
            </Button>
          ) : null
        }
      >
        {(projectsQuery.data?.data ?? []).length === 0 ? (
          <Typography.Text type="secondary">등록된 프로젝트가 없습니다.</Typography.Text>
        ) : (
          <Space direction="vertical" style={{ width: '100%' }} size="middle">
            {(projectsQuery.data?.data ?? []).map((p) => (
              <Card
                key={p.id}
                type="inner"
                size="small"
                title={p.project_name}
                extra={
                  isAdmin ? (
                    <Space>
                      <Button type="link" size="small" onClick={() => openProjEdit(p)}>
                        수정
                      </Button>
                      <Button
                        type="link"
                        danger
                        size="small"
                        onClick={() =>
                          Modal.confirm({
                            title: '프로젝트 경력을 삭제하시겠습니까?',
                            content:
                              '검색 및 사업분야/고객유형 집계에서 제외됩니다.',
                            onOk: () => projDeleteMutation.mutateAsync(p.id),
                          })
                        }
                      >
                        삭제
                      </Button>
                    </Space>
                  ) : null
                }
              >
                <Typography.Paragraph style={{ marginBottom: 8 }}>
                  {formatPeriod(p.start_date, p.end_date)}
                  {p.duration_months != null ? ` · ${p.duration_months}개월` : ''}
                  {p.customer_name ? ` · ${p.customer_name}` : ''}
                </Typography.Paragraph>
                {p.responsibilities ? (
                  <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
                    {p.responsibilities}
                  </Typography.Paragraph>
                ) : null}
                <div style={{ marginBottom: 4 }}>
                  <Typography.Text type="secondary">직무: </Typography.Text>
                  {p.jobs.length
                    ? p.jobs.map((j) => <Tag key={j.code}>{j.name}</Tag>)
                    : '—'}
                </div>
                <div style={{ marginBottom: 4 }}>
                  <Typography.Text type="secondary">기술: </Typography.Text>
                  {p.skills.length
                    ? p.skills.map((s) => <Tag key={s.code}>{s.name}</Tag>)
                    : '—'}
                </div>
                <div style={{ marginBottom: 4 }}>
                  <Typography.Text type="secondary">전문분야: </Typography.Text>
                  {p.expertise.length
                    ? p.expertise.map((e) => (
                        <Tag key={e.code}>
                          {e.name}
                          {e.evidence_type === 'INFERRED' ? ' (추론)' : ''}
                        </Tag>
                      ))
                    : '—'}
                </div>
                <div style={{ marginBottom: 4 }}>
                  <Typography.Text type="secondary">사업분야: </Typography.Text>
                  {p.business_domains.length
                    ? p.business_domains.map((b) => <Tag key={b.code}>{b.name}</Tag>)
                    : '—'}
                </div>
                <div>
                  <Typography.Text type="secondary">고객유형: </Typography.Text>
                  {p.customer_types.length
                    ? p.customer_types.map((c) => <Tag key={c.code}>{c.name}</Tag>)
                    : '—'}
                </div>
              </Card>
            ))}
          </Space>
        )}
      </Card>

      <Modal
        title={editingEmp ? '근무경력 수정' : '근무경력 추가'}
        open={empOpen}
        onCancel={() => setEmpOpen(false)}
        onOk={() => empForm.submit()}
        confirmLoading={empMutation.isPending}
        destroyOnHidden
        width={640}
      >
        <Form form={empForm} layout="vertical" onFinish={(v) => empMutation.mutate(v)}>
          <Form.Item
            name="company_name"
            label="회사"
            rules={[{ required: true, max: 300 }]}
          >
            <Input maxLength={300} />
          </Form.Item>
          <Form.Item name="department" label="부서" rules={[{ max: 200 }]}>
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item name="title" label="직위" rules={[{ max: 200 }]}>
            <Input maxLength={200} />
          </Form.Item>
          <Space style={{ width: '100%' }} size="middle">
            <Form.Item name="start_date" label="시작일">
              <DatePicker style={{ width: 180 }} />
            </Form.Item>
            <Form.Item name="end_date" label="종료일">
              <DatePicker style={{ width: 180 }} />
            </Form.Item>
          </Space>
          <Form.Item name="responsibilities" label="담당업무">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={editingProj ? '프로젝트 수정' : '프로젝트 추가'}
        open={projOpen}
        onCancel={() => setProjOpen(false)}
        onOk={() => projForm.submit()}
        confirmLoading={projMutation.isPending}
        destroyOnHidden
        width={760}
      >
        <Form form={projForm} layout="vertical" onFinish={(v) => projMutation.mutate(v)}>
          <Form.Item
            name="project_name"
            label="프로젝트명"
            rules={[{ required: true, max: 500 }]}
          >
            <Input maxLength={500} />
          </Form.Item>
          <Form.Item name="customer_name" label="고객명" rules={[{ max: 300 }]}>
            <Input maxLength={300} />
          </Form.Item>
          <Space wrap>
            <Form.Item name="start_date" label="시작일">
              <DatePicker style={{ width: 180 }} />
            </Form.Item>
            <Form.Item name="end_date" label="종료일">
              <DatePicker style={{ width: 180 }} />
            </Form.Item>
            <Form.Item name="duration_months" label="수행개월">
              <InputNumber min={0} style={{ width: 140 }} />
            </Form.Item>
          </Space>
          <Form.Item name="responsibilities" label="담당업무">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="project_summary" label="프로젝트 요약">
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item name="job_codes" label="직무 (JOB)">
            <Select
              mode="multiple"
              showSearch
              optionFilterProp="label"
              options={jobOptions}
              placeholder="직무 선택"
            />
          </Form.Item>
          <Form.Item name="tech_codes" label="기술 (TECH)">
            <Select
              mode="multiple"
              showSearch
              optionFilterProp="label"
              options={techOptions}
              placeholder="기술 선택"
            />
          </Form.Item>
          <Form.Item label="전문분야 (EXP)">
            <Form.List name="expertise">
              {(fields, { add, remove }) => (
                <>
                  {fields.map((field) => (
                    <Space key={field.key} align="baseline" style={{ display: 'flex' }}>
                      <Form.Item
                        {...field}
                        name={[field.name, 'exp_code']}
                        rules={[{ required: true }]}
                      >
                        <Select
                          showSearch
                          optionFilterProp="label"
                          style={{ width: 280 }}
                          options={expOptions}
                          placeholder="전문분야"
                        />
                      </Form.Item>
                      <Form.Item {...field} name={[field.name, 'evidence_type']}>
                        <Select
                          style={{ width: 140 }}
                          options={[
                            { value: 'EXPLICIT', label: 'EXPLICIT' },
                            { value: 'INFERRED', label: '추론(INFERRED)' },
                          ]}
                        />
                      </Form.Item>
                      <Button danger type="link" onClick={() => remove(field.name)}>
                        삭제
                      </Button>
                    </Space>
                  ))}
                  <Button
                    type="dashed"
                    onClick={() => add({ evidence_type: 'EXPLICIT' })}
                    block
                  >
                    전문분야 추가
                  </Button>
                </>
              )}
            </Form.List>
          </Form.Item>
          <Form.Item name="biz_codes" label="사업분야 (BIZ)">
            <Select
              mode="multiple"
              showSearch
              optionFilterProp="label"
              options={bizOptions}
              placeholder="사업분야 선택"
            />
          </Form.Item>
          <Form.Item name="customer_type_codes" label="고객유형 (CUSTOMER_TYPE)">
            <Select
              mode="multiple"
              showSearch
              optionFilterProp="label"
              options={customerOptions}
              placeholder="고객유형 선택"
            />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  )
}
