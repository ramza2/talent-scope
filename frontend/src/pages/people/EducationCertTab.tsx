import { useState } from 'react'
import {
  Button,
  Card,
  DatePicker,
  Form,
  Input,
  Modal,
  Space,
  Table,
  Typography,
  message,
} from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { ColumnsType } from 'antd/es/table'

import { apiErrorMessage } from '@/api/errors'
import {
  createCertification,
  createEducation,
  deleteCertification,
  deleteEducation,
  listCertifications,
  listEducation,
  updateCertification,
  updateEducation,
  type CertificationItem,
  type EducationItem,
} from '@/api/career'
import { formatPeriod, fromIsoDate, toIsoDate } from '@/pages/people/dateUtils'

type Props = {
  personId: string
  isAdmin: boolean
  onChanged: () => Promise<void>
}

export function EducationCertTab({ personId, isAdmin, onChanged }: Props) {
  const queryClient = useQueryClient()
  const [eduOpen, setEduOpen] = useState(false)
  const [certOpen, setCertOpen] = useState(false)
  const [editingEdu, setEditingEdu] = useState<EducationItem | null>(null)
  const [editingCert, setEditingCert] = useState<CertificationItem | null>(null)
  const [eduForm] = Form.useForm()
  const [certForm] = Form.useForm()

  const educationQuery = useQuery({
    queryKey: ['people', personId, 'education'],
    queryFn: () => listEducation(personId),
  })
  const certificationsQuery = useQuery({
    queryKey: ['people', personId, 'certifications'],
    queryFn: () => listCertifications(personId),
  })

  const invalidateLocal = async () => {
    await queryClient.invalidateQueries({ queryKey: ['people', personId, 'education'] })
    await queryClient.invalidateQueries({
      queryKey: ['people', personId, 'certifications'],
    })
    await onChanged()
  }

  const eduMutation = useMutation({
    mutationFn: async (values: Record<string, unknown>) => {
      const body = {
        school_name: values.school_name,
        major: values.major ?? null,
        degree: values.degree ?? null,
        start_date: toIsoDate(values.start_date as never),
        end_date: toIsoDate(values.end_date as never),
        status: values.status ?? null,
      }
      if (editingEdu) return updateEducation(editingEdu.id, body)
      return createEducation(personId, body)
    },
    onSuccess: async () => {
      message.success(editingEdu ? '학력을 수정했습니다.' : '학력을 추가했습니다.')
      setEduOpen(false)
      setEditingEdu(null)
      await invalidateLocal()
    },
    onError: (error) => message.error(apiErrorMessage(error, '학력 저장에 실패했습니다.')),
  })

  const eduDeleteMutation = useMutation({
    mutationFn: (id: string) => deleteEducation(id),
    onSuccess: async () => {
      message.success('학력을 삭제했습니다.')
      await invalidateLocal()
    },
    onError: (error) => message.error(apiErrorMessage(error, '학력 삭제에 실패했습니다.')),
  })

  const certMutation = useMutation({
    mutationFn: async (values: Record<string, unknown>) => {
      const body = {
        certification_name: values.certification_name,
        issuer: values.issuer ?? null,
        acquired_date: toIsoDate(values.acquired_date as never),
        expiry_date: toIsoDate(values.expiry_date as never),
        certificate_no: values.certificate_no ?? null,
      }
      if (editingCert) return updateCertification(editingCert.id, body)
      return createCertification(personId, body)
    },
    onSuccess: async () => {
      message.success(editingCert ? '자격을 수정했습니다.' : '자격을 추가했습니다.')
      setCertOpen(false)
      setEditingCert(null)
      await invalidateLocal()
    },
    onError: (error) => message.error(apiErrorMessage(error, '자격 저장에 실패했습니다.')),
  })

  const certDeleteMutation = useMutation({
    mutationFn: (id: string) => deleteCertification(id),
    onSuccess: async () => {
      message.success('자격을 삭제했습니다.')
      await invalidateLocal()
    },
    onError: (error) => message.error(apiErrorMessage(error, '자격 삭제에 실패했습니다.')),
  })

  const openEduCreate = () => {
    setEditingEdu(null)
    eduForm.resetFields()
    setEduOpen(true)
  }

  const openEduEdit = (row: EducationItem) => {
    setEditingEdu(row)
    eduForm.setFieldsValue({
      school_name: row.school_name,
      major: row.major,
      degree: row.degree,
      start_date: fromIsoDate(row.start_date),
      end_date: fromIsoDate(row.end_date),
      status: row.status,
    })
    setEduOpen(true)
  }

  const openCertCreate = () => {
    setEditingCert(null)
    certForm.resetFields()
    setCertOpen(true)
  }

  const openCertEdit = (row: CertificationItem) => {
    setEditingCert(row)
    certForm.setFieldsValue({
      certification_name: row.certification_name,
      issuer: row.issuer,
      acquired_date: fromIsoDate(row.acquired_date),
      expiry_date: fromIsoDate(row.expiry_date),
      certificate_no: row.certificate_no,
    })
    setCertOpen(true)
  }

  const eduColumns: ColumnsType<EducationItem> = [
    { title: '학교', dataIndex: 'school_name' },
    { title: '전공', dataIndex: 'major', render: (v) => v || '—' },
    { title: '학위', dataIndex: 'degree', render: (v) => v || '—' },
    {
      title: '기간',
      key: 'period',
      render: (_, row) => formatPeriod(row.start_date, row.end_date, '—'),
    },
    { title: '상태', dataIndex: 'status', render: (v) => v || '—' },
    ...(isAdmin
      ? [
          {
            title: '관리',
            key: 'actions',
            width: 140,
            render: (_: unknown, row: EducationItem) => (
              <Space>
                <Button type="link" size="small" onClick={() => openEduEdit(row)}>
                  수정
                </Button>
                <Button
                  type="link"
                  danger
                  size="small"
                  onClick={() =>
                    Modal.confirm({
                      title: '학력을 삭제하시겠습니까?',
                      onOk: () => eduDeleteMutation.mutateAsync(row.id),
                    })
                  }
                >
                  삭제
                </Button>
              </Space>
            ),
          } as ColumnsType<EducationItem>[number],
        ]
      : []),
  ]

  const certColumns: ColumnsType<CertificationItem> = [
    { title: '자격명', dataIndex: 'certification_name' },
    { title: '발급기관', dataIndex: 'issuer', render: (v) => v || '—' },
    {
      title: '취득일',
      dataIndex: 'acquired_date',
      render: (v?: string | null) => v || '—',
    },
    {
      title: '만료일',
      dataIndex: 'expiry_date',
      render: (v?: string | null) => v || '—',
    },
    ...(isAdmin
      ? [
          {
            title: '관리',
            key: 'actions',
            width: 140,
            render: (_: unknown, row: CertificationItem) => (
              <Space>
                <Button type="link" size="small" onClick={() => openCertEdit(row)}>
                  수정
                </Button>
                <Button
                  type="link"
                  danger
                  size="small"
                  onClick={() =>
                    Modal.confirm({
                      title: '자격을 삭제하시겠습니까?',
                      onOk: () => certDeleteMutation.mutateAsync(row.id),
                    })
                  }
                >
                  삭제
                </Button>
              </Space>
            ),
          } as ColumnsType<CertificationItem>[number],
        ]
      : []),
  ]

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Card
        title="학력"
        size="small"
        extra={
          isAdmin ? (
            <Button type="primary" onClick={openEduCreate}>
              + 학력 추가
            </Button>
          ) : null
        }
      >
        <Table
          rowKey="id"
          loading={educationQuery.isLoading}
          columns={eduColumns}
          dataSource={educationQuery.data?.data ?? []}
          pagination={false}
          locale={{ emptyText: '등록된 학력이 없습니다.' }}
        />
      </Card>

      <Card
        title="자격"
        size="small"
        extra={
          isAdmin ? (
            <Button type="primary" onClick={openCertCreate}>
              + 자격 추가
            </Button>
          ) : null
        }
      >
        <Table
          rowKey="id"
          loading={certificationsQuery.isLoading}
          columns={certColumns}
          dataSource={certificationsQuery.data?.data ?? []}
          pagination={false}
          locale={{ emptyText: '등록된 자격이 없습니다.' }}
        />
        <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
          자격번호는 목록에 표시하지 않으며, 수정 시에만 확인할 수 있습니다.
        </Typography.Paragraph>
      </Card>

      <Modal
        title={editingEdu ? '학력 수정' : '학력 추가'}
        open={eduOpen}
        onCancel={() => setEduOpen(false)}
        onOk={() => eduForm.submit()}
        confirmLoading={eduMutation.isPending}
        destroyOnHidden
        width={640}
      >
        <Form form={eduForm} layout="vertical" onFinish={(v) => eduMutation.mutate(v)}>
          <Form.Item
            name="school_name"
            label="학교"
            rules={[{ required: true, max: 300 }]}
          >
            <Input maxLength={300} />
          </Form.Item>
          <Form.Item name="major" label="전공" rules={[{ max: 300 }]}>
            <Input maxLength={300} />
          </Form.Item>
          <Form.Item name="degree" label="학위" rules={[{ max: 100 }]}>
            <Input maxLength={100} />
          </Form.Item>
          <Space>
            <Form.Item name="start_date" label="시작일">
              <DatePicker style={{ width: 180 }} />
            </Form.Item>
            <Form.Item name="end_date" label="종료일">
              <DatePicker style={{ width: 180 }} />
            </Form.Item>
          </Space>
          <Form.Item name="status" label="상태" rules={[{ max: 100 }]}>
            <Input maxLength={100} placeholder="예: 졸업, 재학" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={editingCert ? '자격 수정' : '자격 추가'}
        open={certOpen}
        onCancel={() => setCertOpen(false)}
        onOk={() => certForm.submit()}
        confirmLoading={certMutation.isPending}
        destroyOnHidden
        width={640}
      >
        <Form form={certForm} layout="vertical" onFinish={(v) => certMutation.mutate(v)}>
          <Form.Item
            name="certification_name"
            label="자격명"
            rules={[{ required: true, max: 300 }]}
          >
            <Input maxLength={300} />
          </Form.Item>
          <Form.Item name="issuer" label="발급기관" rules={[{ max: 300 }]}>
            <Input maxLength={300} />
          </Form.Item>
          <Space>
            <Form.Item name="acquired_date" label="취득일">
              <DatePicker style={{ width: 180 }} />
            </Form.Item>
            <Form.Item name="expiry_date" label="만료일">
              <DatePicker style={{ width: 180 }} />
            </Form.Item>
          </Space>
          <Form.Item name="certificate_no" label="자격번호" rules={[{ max: 200 }]}>
            <Input maxLength={200} />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  )
}
