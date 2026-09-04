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
  type PersonDetail,
  type PersonStatus,
  type RevisionItem,
  type TechnicalGrade,
} from '@/api/people'
import { useAuthMe } from '@/app/auth'

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

export function PeopleDetailPage() {
  const { personId = '' } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { data: me } = useAuthMe()
  const isAdmin = me?.role === 'ADMIN'

  const [profileOpen, setProfileOpen] = useState(false)
  const [jobsOpen, setJobsOpen] = useState(false)
  const [skillsOpen, setSkillsOpen] = useState(false)
  const [expertiseOpen, setExpertiseOpen] = useState(false)
  const [revisionOpen, setRevisionOpen] = useState(false)
  const [selectedRevision, setSelectedRevision] = useState<RevisionItem | null>(null)

  const [profileForm] = Form.useForm()
  const [jobsForm] = Form.useForm()
  const [skillsForm] = Form.useForm()
  const [expertiseForm] = Form.useForm()

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
    enabled: jobsOpen,
  })
  const techCodesQuery = useQuery({
    queryKey: ['codes', 'TECH', 'detail'],
    queryFn: () => listCodes({ type: 'TECH', active: true }),
    enabled: skillsOpen,
  })
  const expCodesQuery = useQuery({
    queryKey: ['codes', 'EXP', 'detail'],
    queryFn: () => listCodes({ type: 'EXP', active: true }),
    enabled: expertiseOpen,
  })

  const jobOptions = useMemo(
    () =>
      mergeCodeOptions(
        (jobCodesQuery.data?.data ?? []).map((c) => ({ value: c.code, label: c.name })),
        (person?.jobs ?? []).map((j) => ({ code: j.code, name: j.name })),
      ),
    [jobCodesQuery.data, person?.jobs],
  )
  const techOptions = useMemo(
    () =>
      mergeCodeOptions(
        (techCodesQuery.data?.data ?? []).map((c) => ({ value: c.code, label: c.name })),
        (person?.skills ?? []).map((s) => ({ code: s.code, name: s.name })),
      ),
    [techCodesQuery.data, person?.skills],
  )
  const expOptions = useMemo(
    () =>
      mergeCodeOptions(
        (expCodesQuery.data?.data ?? []).map((c) => ({ value: c.code, label: c.name })),
        (person?.expertise ?? []).map((e) => ({ code: e.code, name: e.name })),
      ),
    [expCodesQuery.data, person?.expertise],
  )

  const invalidateAll = async () => {
    await queryClient.invalidateQueries({ queryKey: ['people'] })
    await queryClient.invalidateQueries({ queryKey: detailKey })
    await queryClient.invalidateQueries({ queryKey: ['people', personId, 'revisions'] })
  }

  const closeEditModals = () => {
    setProfileOpen(false)
    setJobsOpen(false)
    setSkillsOpen(false)
    setExpertiseOpen(false)
  }

  const handleVersionConflict = async (error: unknown) => {
    if (error instanceof ApiError && error.status === 409) {
      message.warning('다른 사용자가 먼저 프로필을 수정했습니다. 최신 정보를 다시 불러옵니다.')
      await refetch()
      closeEditModals()
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

  const jobsMutation = useMutation({
    mutationFn: (jobs: Array<{ job_code: string; job_type: string; sort_order?: number }>) =>
      replacePersonJobs(personId, {
        expected_profile_version: person!.profile_version,
        jobs,
      }),
    onSuccess: async () => {
      message.success('직무가 저장되었습니다.')
      setJobsOpen(false)
      await invalidateAll()
    },
    onError: async (error) => {
      if (await handleVersionConflict(error)) return
      message.error(apiErrorMessage(error, '직무 저장에 실패했습니다.'))
    },
  })

  const skillsMutation = useMutation({
    mutationFn: (
      skills: Array<{
        tech_code: string
        last_used_year?: number
        experience_months?: number
        is_representative?: boolean
      }>,
    ) =>
      replacePersonSkills(personId, {
        expected_profile_version: person!.profile_version,
        skills,
      }),
    onSuccess: async () => {
      message.success('기술이 저장되었습니다.')
      setSkillsOpen(false)
      await invalidateAll()
    },
    onError: async (error) => {
      if (await handleVersionConflict(error)) return
      message.error(apiErrorMessage(error, '기술 저장에 실패했습니다.'))
    },
  })

  const expertiseMutation = useMutation({
    mutationFn: (expertise: Array<{ exp_code: string; evidence_type?: string }>) =>
      replacePersonExpertise(personId, {
        expected_profile_version: person!.profile_version,
        expertise,
      }),
    onSuccess: async () => {
      message.success('전문분야가 저장되었습니다.')
      setExpertiseOpen(false)
      await invalidateAll()
    },
    onError: async (error) => {
      if (await handleVersionConflict(error)) return
      message.error(apiErrorMessage(error, '전문분야 저장에 실패했습니다.'))
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
    profileForm.setFieldsValue({ ...person.profile })
    setProfileOpen(true)
  }

  const openJobsEdit = (p: PersonDetail) => {
    jobsForm.setFieldsValue({
      jobs: p.jobs.map((j) => ({
        job_code: j.code,
        job_type: j.job_type,
        sort_order: j.sort_order,
      })),
    })
    setJobsOpen(true)
  }

  const openSkillsEdit = (p: PersonDetail) => {
    skillsForm.setFieldsValue({
      skills: p.skills.map((s) => ({
        tech_code: s.code,
        last_used_year: s.last_used_year ?? undefined,
        experience_months: s.experience_months ?? undefined,
        is_representative: s.is_representative,
      })),
    })
    setSkillsOpen(true)
  }

  const openExpertiseEdit = (p: PersonDetail) => {
    expertiseForm.setFieldsValue({
      expertise: p.expertise.map((e) => ({
        exp_code: e.code,
        evidence_type: e.evidence_type,
      })),
    })
    setExpertiseOpen(true)
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
            {person.profile.name} <Tag>{person.status}</Tag>
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
                <Card
                  title="직무"
                  size="small"
                  extra={
                    isAdmin ? (
                      <Button type="link" onClick={() => openJobsEdit(person)}>
                        직무 수정
                      </Button>
                    ) : null
                  }
                >
                  {person.jobs.length === 0 ? (
                    <Typography.Text type="secondary">등록된 직무 없음</Typography.Text>
                  ) : (
                    person.jobs.map((j) => (
                      <div key={`${j.code}-${j.job_type}`}>
                        <Tag>{j.job_type}</Tag> {j.name}{' '}
                        <Typography.Text code>{j.code}</Typography.Text>
                      </div>
                    ))
                  )}
                </Card>
                <Card
                  title="기술 (TECH)"
                  size="small"
                  extra={
                    isAdmin ? (
                      <Button type="link" onClick={() => openSkillsEdit(person)}>
                        기술 수정
                      </Button>
                    ) : null
                  }
                >
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
                <Card
                  title="전문분야 (EXP)"
                  size="small"
                  extra={
                    isAdmin ? (
                      <Button type="link" onClick={() => openExpertiseEdit(person)}>
                        전문분야 수정
                      </Button>
                    ) : null
                  }
                >
                  {person.expertise.length === 0 ? (
                    <Typography.Text type="secondary">등록된 전문분야 없음</Typography.Text>
                  ) : (
                    person.expertise.map((e) => (
                      <div key={e.code}>
                        {e.name} {e.evidence_type === 'INFERRED' ? <Tag>추론</Tag> : null}
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
          <Form.Item name="name" label="이름" rules={[{ required: true, max: 150 }]}>
            <Input maxLength={150} />
          </Form.Item>
          <Form.Item name="birth_year" label="출생연도">
            <InputNumber style={{ width: '100%' }} min={1900} max={2100} />
          </Form.Item>
          <Form.Item name="phone" label="전화번호" rules={[{ max: 50 }]}>
            <Input maxLength={50} />
          </Form.Item>
          <Form.Item name="email" label="이메일" rules={[{ max: 255 }]}>
            <Input maxLength={255} />
          </Form.Item>
          <Form.Item name="address_region" label="지역" rules={[{ max: 200 }]}>
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item name="affiliation_company" label="소속회사" rules={[{ max: 300 }]}>
            <Input maxLength={300} />
          </Form.Item>
          <Form.Item name="department" label="부서" rules={[{ max: 200 }]}>
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item name="current_title" label="직함" rules={[{ max: 200 }]}>
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item name="employment_type" label="고용형태" rules={[{ max: 50 }]}>
            <Input maxLength={50} />
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
        title={`직무 수정 · v${person.profile_version}`}
        open={jobsOpen}
        onCancel={() => setJobsOpen(false)}
        onOk={() => jobsForm.submit()}
        confirmLoading={jobsMutation.isPending}
        destroyOnHidden
        width={720}
      >
        <Form
          form={jobsForm}
          layout="vertical"
          onFinish={(values) => jobsMutation.mutate(values.jobs ?? [])}
        >
          <Form.List name="jobs">
            {(fields, { add, remove }) => (
              <>
                {fields.map((field) => (
                  <Space key={field.key} align="baseline" style={{ display: 'flex', marginBottom: 8 }}>
                    <Form.Item {...field} name={[field.name, 'job_code']} rules={[{ required: true }]}>
                      <Select
                        showSearch
                        optionFilterProp="label"
                        style={{ width: 240 }}
                        options={jobOptions}
                        placeholder="직무"
                      />
                    </Form.Item>
                    <Form.Item {...field} name={[field.name, 'job_type']} rules={[{ required: true }]}>
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
                <Button type="dashed" onClick={() => add({ job_type: 'SECONDARY', sort_order: 0 })} block>
                  직무 추가
                </Button>
              </>
            )}
          </Form.List>
        </Form>
      </Modal>

      <Modal
        title={`기술 수정 · v${person.profile_version}`}
        open={skillsOpen}
        onCancel={() => setSkillsOpen(false)}
        onOk={() => skillsForm.submit()}
        confirmLoading={skillsMutation.isPending}
        destroyOnHidden
        width={820}
      >
        <Form
          form={skillsForm}
          layout="vertical"
          onFinish={(values) => skillsMutation.mutate(values.skills ?? [])}
        >
          <Form.List name="skills">
            {(fields, { add, remove }) => (
              <>
                {fields.map((field) => (
                  <Space key={field.key} align="baseline" wrap style={{ marginBottom: 8 }}>
                    <Form.Item {...field} name={[field.name, 'tech_code']} rules={[{ required: true }]}>
                      <Select
                        showSearch
                        optionFilterProp="label"
                        style={{ width: 220 }}
                        options={techOptions}
                        placeholder="기술"
                      />
                    </Form.Item>
                    <Form.Item {...field} name={[field.name, 'last_used_year']}>
                      <InputNumber placeholder="최근연도" min={1900} max={2100} />
                    </Form.Item>
                    <Form.Item {...field} name={[field.name, 'experience_months']}>
                      <InputNumber placeholder="개월" min={0} />
                    </Form.Item>
                    <Form.Item {...field} name={[field.name, 'is_representative']} valuePropName="checked">
                      <Switch checkedChildren="대표" unCheckedChildren="일반" />
                    </Form.Item>
                    <Button danger type="link" onClick={() => remove(field.name)}>
                      삭제
                    </Button>
                  </Space>
                ))}
                <Button type="dashed" onClick={() => add({ is_representative: false })} block>
                  기술 추가
                </Button>
              </>
            )}
          </Form.List>
        </Form>
      </Modal>

      <Modal
        title={`전문분야 수정 · v${person.profile_version}`}
        open={expertiseOpen}
        onCancel={() => setExpertiseOpen(false)}
        onOk={() => expertiseForm.submit()}
        confirmLoading={expertiseMutation.isPending}
        destroyOnHidden
        width={720}
      >
        <Form
          form={expertiseForm}
          layout="vertical"
          onFinish={(values) => expertiseMutation.mutate(values.expertise ?? [])}
        >
          <Form.List name="expertise">
            {(fields, { add, remove }) => (
              <>
                {fields.map((field) => (
                  <Space key={field.key} align="baseline" style={{ marginBottom: 8 }}>
                    <Form.Item {...field} name={[field.name, 'exp_code']} rules={[{ required: true }]}>
                      <Select
                        showSearch
                        optionFilterProp="label"
                        style={{ width: 240 }}
                        options={expOptions}
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
                <Button type="dashed" onClick={() => add({ evidence_type: 'EXPLICIT' })} block>
                  전문분야 추가
                </Button>
              </>
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
