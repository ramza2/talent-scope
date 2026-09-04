import { useMemo, useState } from 'react'
import {
  Button,
  Card,
  Descriptions,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import type { ColumnsType } from 'antd/es/table'

import { apiErrorMessage } from '@/api/errors'
import { ApiError } from '@/api/client'
import { listCodes } from '@/api/codes'
import {
  GRADE_LABELS,
  formatCareerMonths,
  getPerson,
  listPersonRevisions,
  replacePersonExpertise,
  replacePersonJobs,
  replacePersonSkills,
  updatePersonProfile,
  updatePersonStatus,
  type PersonStatus,
  type RevisionItem,
  type TechnicalGrade,
} from '@/api/people'
import { useAuthMe } from '@/app/auth'

export function PeopleDetailPage() {
  const { personId = '' } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { data: me } = useAuthMe()
  const isAdmin = me?.role === 'ADMIN'

  const [profileOpen, setProfileOpen] = useState(false)
  const [setsOpen, setSetsOpen] = useState(false)
  const [revisionOpen, setRevisionOpen] = useState(false)
  const [selectedRevision, setSelectedRevision] = useState<RevisionItem | null>(null)
  const [profileForm] = Form.useForm()
  const [setsForm] = Form.useForm()

  const detailKey = ['people', personId] as const
  const { data, isLoading, refetch } = useQuery({
    queryKey: detailKey,
    queryFn: () => getPerson(personId),
    enabled: Boolean(personId),
  })
  const person = data?.data

  const revisionsQuery = useQuery({
    queryKey: ['people', personId, 'revisions'],
    queryFn: () => listPersonRevisions(personId),
    enabled: Boolean(personId) && isAdmin,
  })

  const jobCodesQuery = useQuery({
    queryKey: ['codes', 'JOB', 'detail'],
    queryFn: () => listCodes({ type: 'JOB', active: true }),
    enabled: setsOpen,
  })
  const techCodesQuery = useQuery({
    queryKey: ['codes', 'TECH', 'detail'],
    queryFn: () => listCodes({ type: 'TECH', active: true }),
    enabled: setsOpen,
  })
  const expCodesQuery = useQuery({
    queryKey: ['codes', 'EXP', 'detail'],
    queryFn: () => listCodes({ type: 'EXP', active: true }),
    enabled: setsOpen,
  })

  const invalidateAll = async () => {
    await queryClient.invalidateQueries({ queryKey: ['people'] })
    await queryClient.invalidateQueries({ queryKey: detailKey })
    await queryClient.invalidateQueries({ queryKey: ['people', personId, 'revisions'] })
  }

  const handleVersionConflict = async (error: unknown) => {
    if (error instanceof ApiError && error.status === 409) {
      message.warning('다른 사용자가 먼저 프로필을 수정했습니다. 최신 정보를 다시 불러옵니다.')
      await refetch()
      setProfileOpen(false)
      setSetsOpen(false)
      return true
    }
    return false
  }

  const profileMutation = useMutation({
    mutationFn: (body: Record<string, unknown>) => updatePersonProfile(personId, body),
    onSuccess: async () => {
      message.success('프로필이 수정되었습니다.')
      setProfileOpen(false)
      await invalidateAll()
    },
    onError: async (error) => {
      if (await handleVersionConflict(error)) return
      message.error(apiErrorMessage(error, '프로필 수정에 실패했습니다.'))
    },
  })

  const setsMutation = useMutation({
    mutationFn: async (values: {
      jobs: Array<{ job_code: string; job_type: string; sort_order?: number }>
      skills: Array<{
        tech_code: string
        last_used_year?: number
        experience_months?: number
        is_representative?: boolean
      }>
      expertise: Array<{ exp_code: string; evidence_type?: string }>
    }) => {
      const version = person?.profile_version
      if (version == null) throw new Error('missing version')
      await replacePersonJobs(personId, {
        expected_profile_version: version,
        jobs: values.jobs ?? [],
      })
      const refreshed = await getPerson(personId)
      await replacePersonSkills(personId, {
        expected_profile_version: refreshed.data.profile_version,
        skills: values.skills ?? [],
      })
      const refreshed2 = await getPerson(personId)
      await replacePersonExpertise(personId, {
        expected_profile_version: refreshed2.data.profile_version,
        expertise: values.expertise ?? [],
      })
    },
    onSuccess: async () => {
      message.success('직무·기술·전문분야가 저장되었습니다.')
      setSetsOpen(false)
      await invalidateAll()
    },
    onError: async (error) => {
      if (await handleVersionConflict(error)) return
      message.error(apiErrorMessage(error, '직무/기술 저장에 실패했습니다.'))
    },
  })

  const statusMutation = useMutation({
    mutationFn: (status: PersonStatus) => updatePersonStatus(personId, status),
    onSuccess: async () => {
      message.success('상태가 변경되었습니다.')
      await invalidateAll()
    },
    onError: (error) => message.error(apiErrorMessage(error, '상태 변경에 실패했습니다.')),
  })

  const primaryJob = useMemo(
    () => person?.jobs.find((j) => j.job_type === 'PRIMARY'),
    [person],
  )

  if (isLoading || !person) {
    return <Typography.Text>불러오는 중…</Typography.Text>
  }

  const openProfileEdit = () => {
    profileForm.setFieldsValue({
      ...person.profile,
      expected_profile_version: person.profile_version,
    })
    setProfileOpen(true)
  }

  const openSetsEdit = () => {
    setsForm.setFieldsValue({
      jobs: person.jobs.map((j) => ({
        job_code: j.code,
        job_type: j.job_type,
        sort_order: j.sort_order,
      })),
      skills: person.skills.map((s) => ({
        tech_code: s.code,
        last_used_year: s.last_used_year ?? undefined,
        experience_months: s.experience_months ?? undefined,
        is_representative: s.is_representative,
      })),
      expertise: person.expertise.map((e) => ({
        exp_code: e.code,
        evidence_type: e.evidence_type,
      })),
    })
    setSetsOpen(true)
  }

  const revisionColumns: ColumnsType<RevisionItem> = [
    { title: 'Revision', dataIndex: 'revision_no', width: 100, render: (v) => `v${v}` },
    { title: 'Source', dataIndex: 'source_type', width: 140 },
    {
      title: '작업자',
      dataIndex: 'created_by_name',
      width: 140,
      render: (v?: string | null) => v || '—',
    },
    {
      title: '생성일',
      dataIndex: 'created_at',
      render: (v: string) => new Date(v).toLocaleString(),
    },
  ]

  return (
    <div>
      <Button type="link" onClick={() => navigate('/people')} style={{ paddingLeft: 0 }}>
        ← 목록
      </Button>

      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 12 }} align="start">
        <div>
          <Typography.Title level={3} style={{ marginBottom: 4 }}>
            {person.profile.name}{' '}
            <Tag>{person.status}</Tag>
          </Typography.Title>
          <Typography.Paragraph style={{ marginBottom: 4 }}>
            {primaryJob?.name || '주직무 미지정'} ·{' '}
            {person.profile.technical_grade
              ? GRADE_LABELS[person.profile.technical_grade as TechnicalGrade]
              : '등급 미정'}{' '}
            · {formatCareerMonths(person.profile.career_confirmed_months)}
          </Typography.Paragraph>
          <Typography.Text type="secondary">
            {[person.profile.affiliation_company, person.profile.department]
              .filter(Boolean)
              .join(' · ') || '소속 미정'}
          </Typography.Text>
        </div>
        {isAdmin ? (
          <Space>
            <Select
              style={{ width: 140 }}
              value={person.status}
              onChange={(status) => {
                Modal.confirm({
                  title: '상태 변경',
                  content: `상태를 ${status}로 변경할까요?`,
                  onOk: () => statusMutation.mutateAsync(status),
                })
              }}
              options={[
                { value: 'ACTIVE', label: 'ACTIVE' },
                { value: 'INACTIVE', label: 'INACTIVE' },
                { value: 'ARCHIVED', label: 'ARCHIVED' },
                { value: 'DELETED', label: 'DELETED' },
              ]}
            />
            <Button onClick={openProfileEdit}>프로필 수정</Button>
            <Button type="primary" onClick={openSetsEdit}>
              직무·기술 수정
            </Button>
          </Space>
        ) : null}
      </Space>

      <Tabs
        items={[
          {
            key: 'profile',
            label: '프로필',
            children: (
              <Card>
                <Descriptions column={2} bordered size="small">
                  <Descriptions.Item label="이름">{person.profile.name}</Descriptions.Item>
                  <Descriptions.Item label="출생연도">
                    {person.profile.birth_year ?? '—'}
                  </Descriptions.Item>
                  <Descriptions.Item label="연락처">{person.profile.phone || '—'}</Descriptions.Item>
                  <Descriptions.Item label="이메일">{person.profile.email || '—'}</Descriptions.Item>
                  <Descriptions.Item label="지역">
                    {person.profile.address_region || '—'}
                  </Descriptions.Item>
                  <Descriptions.Item label="소속회사">
                    {person.profile.affiliation_company || '—'}
                  </Descriptions.Item>
                  <Descriptions.Item label="부서">{person.profile.department || '—'}</Descriptions.Item>
                  <Descriptions.Item label="직함">
                    {person.profile.current_title || '—'}
                  </Descriptions.Item>
                  <Descriptions.Item label="고용형태">
                    {person.profile.employment_type || '—'}
                  </Descriptions.Item>
                  <Descriptions.Item label="기술등급">
                    {person.profile.technical_grade
                      ? GRADE_LABELS[person.profile.technical_grade as TechnicalGrade]
                      : '—'}
                  </Descriptions.Item>
                  <Descriptions.Item label="경력 시작일">
                    {person.profile.career_start_date || '—'}
                  </Descriptions.Item>
                  <Descriptions.Item label="계산 경력">
                    {formatCareerMonths(person.profile.career_calculated_months)}
                  </Descriptions.Item>
                  <Descriptions.Item label="문서상 경력">
                    {person.profile.career_document_value || '—'}
                  </Descriptions.Item>
                  <Descriptions.Item label="확정 경력">
                    {formatCareerMonths(person.profile.career_confirmed_months)}
                  </Descriptions.Item>
                  <Descriptions.Item label="Profile Version">
                    v{person.profile_version}
                  </Descriptions.Item>
                  <Descriptions.Item label="최종 갱신">
                    {person.profile.profile_updated_at
                      ? new Date(person.profile.profile_updated_at).toLocaleString()
                      : '—'}
                  </Descriptions.Item>
                  <Descriptions.Item label="Summary" span={2}>
                    {person.profile.profile_summary || '—'}
                  </Descriptions.Item>
                </Descriptions>
              </Card>
            ),
          },
          {
            key: 'sets',
            label: '직무·기술',
            children: (
              <Space direction="vertical" style={{ width: '100%' }} size="large">
                <Card title="직무" size="small">
                  {person.jobs.length === 0 ? (
                    <Typography.Text type="secondary">등록된 직무 없음</Typography.Text>
                  ) : (
                    person.jobs.map((j) => (
                      <div key={`${j.code}-${j.job_type}`}>
                        <Tag>{j.job_type}</Tag> {j.name} <Typography.Text code>{j.code}</Typography.Text>
                      </div>
                    ))
                  )}
                </Card>
                <Card title="기술 (TECH)" size="small">
                  {person.skills.length === 0 ? (
                    <Typography.Text type="secondary">등록된 기술 없음</Typography.Text>
                  ) : (
                    person.skills.map((s) => (
                      <div key={s.code}>
                        {s.is_representative ? <Tag color="blue">대표</Tag> : null}
                        {s.name}{' '}
                        <Typography.Text type="secondary">
                          {s.last_used_year ? `${s.last_used_year} · ` : ''}
                          {s.experience_months != null ? `${s.experience_months}개월` : ''}
                        </Typography.Text>
                      </div>
                    ))
                  )}
                </Card>
                <Card title="전문분야 (EXP)" size="small">
                  {person.expertise.length === 0 ? (
                    <Typography.Text type="secondary">등록된 전문분야 없음</Typography.Text>
                  ) : (
                    person.expertise.map((e) => (
                      <div key={e.code}>
                        {e.name}{' '}
                        {e.evidence_type === 'INFERRED' ? <Tag>추론</Tag> : null}
                      </div>
                    ))
                  )}
                </Card>
              </Space>
            ),
          },
          ...(isAdmin
            ? [
                {
                  key: 'revisions',
                  label: '변경이력',
                  children: (
                    <Table
                      rowKey={(r) => String(r.revision_no)}
                      loading={revisionsQuery.isLoading}
                      columns={revisionColumns}
                      dataSource={revisionsQuery.data?.data ?? []}
                      pagination={false}
                      onRow={(row) => ({
                        onClick: () => {
                          setSelectedRevision(row)
                          setRevisionOpen(true)
                        },
                        style: { cursor: 'pointer' },
                      })}
                    />
                  ),
                },
              ]
            : []),
        ]}
      />

      <Modal
        title={`프로필 수정 · v${person.profile_version}`}
        open={profileOpen}
        onCancel={() => setProfileOpen(false)}
        onOk={() => profileForm.submit()}
        confirmLoading={profileMutation.isPending}
        destroyOnHidden
        width={720}
      >
        <Form
          form={profileForm}
          layout="vertical"
          onFinish={(values) =>
            profileMutation.mutate({
              ...values,
              expected_profile_version: person.profile_version,
            })
          }
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
          <Form.Item name="address_region" label="지역">
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
          <Form.Item name="profile_summary" label="Summary">
            <Input.TextArea rows={4} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="직무·기술·전문분야 수정"
        open={setsOpen}
        onCancel={() => setSetsOpen(false)}
        onOk={() => setsForm.submit()}
        confirmLoading={setsMutation.isPending}
        destroyOnHidden
        width={860}
      >
        <Form form={setsForm} layout="vertical" onFinish={(v) => setsMutation.mutate(v)}>
          <Form.List name="jobs">
            {(fields, { add, remove }) => (
              <Card
                size="small"
                title="직무 (JOB)"
                extra={
                  <Button type="link" onClick={() => add({ job_type: 'SECONDARY', sort_order: 0 })}>
                    추가
                  </Button>
                }
                style={{ marginBottom: 12 }}
              >
                {fields.map((field) => (
                  <Space key={field.key} align="baseline" style={{ display: 'flex', marginBottom: 8 }}>
                    <Form.Item
                      {...field}
                      name={[field.name, 'job_code']}
                      rules={[{ required: true }]}
                    >
                      <Select
                        showSearch
                        optionFilterProp="label"
                        style={{ width: 240 }}
                        options={(jobCodesQuery.data?.data ?? []).map((c) => ({
                          value: c.code,
                          label: c.name,
                        }))}
                        placeholder="직무"
                      />
                    </Form.Item>
                    <Form.Item
                      {...field}
                      name={[field.name, 'job_type']}
                      rules={[{ required: true }]}
                    >
                      <Select
                        style={{ width: 140 }}
                        options={[
                          { value: 'PRIMARY', label: 'PRIMARY' },
                          { value: 'SECONDARY', label: 'SECONDARY' },
                          { value: 'EXPERIENCE', label: 'EXPERIENCE' },
                        ]}
                      />
                    </Form.Item>
                    <Form.Item {...field} name={[field.name, 'sort_order']}>
                      <InputNumber placeholder="정렬" />
                    </Form.Item>
                    <Button danger type="link" onClick={() => remove(field.name)}>
                      삭제
                    </Button>
                  </Space>
                ))}
              </Card>
            )}
          </Form.List>

          <Form.List name="skills">
            {(fields, { add, remove }) => (
              <Card
                size="small"
                title="기술 (TECH)"
                extra={<Button type="link" onClick={() => add({ is_representative: false })}>추가</Button>}
                style={{ marginBottom: 12 }}
              >
                {fields.map((field) => (
                  <Space key={field.key} align="baseline" wrap style={{ marginBottom: 8 }}>
                    <Form.Item
                      {...field}
                      name={[field.name, 'tech_code']}
                      rules={[{ required: true }]}
                    >
                      <Select
                        showSearch
                        optionFilterProp="label"
                        style={{ width: 220 }}
                        options={(techCodesQuery.data?.data ?? []).map((c) => ({
                          value: c.code,
                          label: c.name,
                        }))}
                        placeholder="기술"
                      />
                    </Form.Item>
                    <Form.Item {...field} name={[field.name, 'last_used_year']}>
                      <InputNumber placeholder="최근연도" min={1900} max={2100} />
                    </Form.Item>
                    <Form.Item {...field} name={[field.name, 'experience_months']}>
                      <InputNumber placeholder="개월" min={0} />
                    </Form.Item>
                    <Form.Item
                      {...field}
                      name={[field.name, 'is_representative']}
                      valuePropName="checked"
                    >
                      <Switch checkedChildren="대표" unCheckedChildren="일반" />
                    </Form.Item>
                    <Button danger type="link" onClick={() => remove(field.name)}>
                      삭제
                    </Button>
                  </Space>
                ))}
              </Card>
            )}
          </Form.List>

          <Form.List name="expertise">
            {(fields, { add, remove }) => (
              <Card
                size="small"
                title="전문분야 (EXP)"
                extra={
                  <Button type="link" onClick={() => add({ evidence_type: 'EXPLICIT' })}>
                    추가
                  </Button>
                }
              >
                {fields.map((field) => (
                  <Space key={field.key} align="baseline" style={{ marginBottom: 8 }}>
                    <Form.Item
                      {...field}
                      name={[field.name, 'exp_code']}
                      rules={[{ required: true }]}
                    >
                      <Select
                        showSearch
                        optionFilterProp="label"
                        style={{ width: 240 }}
                        options={(expCodesQuery.data?.data ?? []).map((c) => ({
                          value: c.code,
                          label: c.name,
                        }))}
                        placeholder="전문분야"
                      />
                    </Form.Item>
                    <Form.Item {...field} name={[field.name, 'evidence_type']}>
                      <Select
                        style={{ width: 140 }}
                        options={[
                          { value: 'EXPLICIT', label: 'EXPLICIT' },
                          { value: 'INFERRED', label: 'INFERRED' },
                        ]}
                      />
                    </Form.Item>
                    <Button danger type="link" onClick={() => remove(field.name)}>
                      삭제
                    </Button>
                  </Space>
                ))}
              </Card>
            )}
          </Form.List>
        </Form>
      </Modal>

      <Drawer
        title={selectedRevision ? `Revision v${selectedRevision.revision_no}` : 'Revision'}
        open={revisionOpen}
        onClose={() => setRevisionOpen(false)}
        width={560}
      >
        {selectedRevision ? (
          <>
            <Typography.Paragraph>
              {selectedRevision.source_type} · {selectedRevision.created_by_name || '—'} ·{' '}
              {new Date(selectedRevision.created_at).toLocaleString()}
            </Typography.Paragraph>
            <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>
              {JSON.stringify(selectedRevision.snapshot, null, 2)}
            </pre>
          </>
        ) : null}
      </Drawer>
    </div>
  )
}
