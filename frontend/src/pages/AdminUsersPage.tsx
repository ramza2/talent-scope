import { useState } from 'react'
import {
  Button,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { KeyOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { ColumnsType } from 'antd/es/table'

import { apiErrorMessage } from '@/api/errors'
import { useAuthMe } from '@/app/auth'
import {
  createUser,
  listUsers,
  resetUserPassword,
  updateUser,
  type UserItem,
  type UserRole,
  type UserStatus,
} from '@/api/users'

type Filters = {
  q: string
  role: UserRole | ''
  status: UserStatus | ''
  page: number
  page_size: number
}

type CreateForm = {
  login_id: string
  name: string
  email?: string
  department?: string
  role: UserRole
  password: string
  password_confirm: string
}

type EditForm = {
  name: string
  email?: string
  department?: string
  role: UserRole
  status: UserStatus
}

type ResetForm = {
  new_password: string
  password_confirm: string
}

function formatDate(value?: string | null) {
  if (!value) return '—'
  try {
    return new Date(value).toLocaleString()
  } catch {
    return value
  }
}

export function AdminUsersPage() {
  const queryClient = useQueryClient()
  const { data: me } = useAuthMe()
  const [filters, setFilters] = useState<Filters>({
    q: '',
    role: '',
    status: '',
    page: 1,
    page_size: 20,
  })
  const [draft, setDraft] = useState({ q: '', role: '' as UserRole | '', status: '' as UserStatus | '' })

  const [createOpen, setCreateOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [resetOpen, setResetOpen] = useState(false)
  const [editing, setEditing] = useState<UserItem | null>(null)
  const [resetTarget, setResetTarget] = useState<UserItem | null>(null)

  const [createForm] = Form.useForm<CreateForm>()
  const [editForm] = Form.useForm<EditForm>()
  const [resetForm] = Form.useForm<ResetForm>()

  const queryKey = ['users', filters] as const
  const { data, isLoading, isFetching } = useQuery({
    queryKey,
    queryFn: () =>
      listUsers({
        q: filters.q || undefined,
        role: filters.role || undefined,
        status: filters.status || undefined,
        page: filters.page,
        page_size: filters.page_size,
      }),
  })

  const createMutation = useMutation({
    mutationFn: createUser,
    onSuccess: async () => {
      message.success('사용자가 생성되었습니다.')
      setCreateOpen(false)
      createForm.resetFields()
      await queryClient.invalidateQueries({ queryKey: ['users'] })
    },
    onError: (error) => {
      message.error(apiErrorMessage(error, '사용자 생성에 실패했습니다.'))
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, body }: { id: string; body: EditForm }) => updateUser(id, body),
    onSuccess: async () => {
      message.success('사용자 정보가 수정되었습니다.')
      setEditOpen(false)
      setEditing(null)
      await queryClient.invalidateQueries({ queryKey: ['users'] })
    },
    onError: (error) => {
      message.error(apiErrorMessage(error, '사용자 수정에 실패했습니다.'))
    },
  })

  const resetMutation = useMutation({
    mutationFn: ({ id, password }: { id: string; password: string }) =>
      resetUserPassword(id, password),
    onSuccess: async () => {
      message.success('비밀번호가 변경되었으며 기존 로그인 세션이 종료되었습니다.')
      setResetOpen(false)
      setResetTarget(null)
      resetForm.resetFields()
      await queryClient.invalidateQueries({ queryKey: ['users'] })
    },
    onError: (error) => {
      message.error(apiErrorMessage(error, '비밀번호 초기화에 실패했습니다.'))
    },
  })

  const openEdit = (row: UserItem) => {
    setEditing(row)
    editForm.setFieldsValue({
      name: row.name,
      email: row.email ?? undefined,
      department: row.department ?? undefined,
      role: row.role,
      status: row.status,
    })
    setEditOpen(true)
  }

  const submitEdit = async (values: EditForm) => {
    if (!editing) return
    const roleChanged = values.role !== editing.role
    const deactivated = values.status === 'INACTIVE' && editing.status !== 'INACTIVE'
    if (roleChanged || deactivated) {
      Modal.confirm({
        title: '세션 종료 안내',
        content:
          '권한 또는 상태 변경 시 해당 사용자의 기존 로그인 세션이 종료됩니다. 계속하시겠습니까?',
        okText: '변경',
        cancelText: '취소',
        onOk: () => updateMutation.mutateAsync({ id: editing.id, body: values }),
      })
      return
    }
    updateMutation.mutate({ id: editing.id, body: values })
  }

  const columns: ColumnsType<UserItem> = [
    { title: 'Login ID', dataIndex: 'login_id', key: 'login_id', width: 140 },
    { title: '이름', dataIndex: 'name', key: 'name', width: 120 },
    {
      title: '이메일',
      dataIndex: 'email',
      key: 'email',
      ellipsis: true,
      render: (v?: string | null) => v || '—',
    },
    {
      title: '부서',
      dataIndex: 'department',
      key: 'department',
      width: 140,
      render: (v?: string | null) => v || '—',
    },
    {
      title: 'Role',
      dataIndex: 'role',
      key: 'role',
      width: 100,
      render: (role: UserRole) => (
        <Tag color={role === 'ADMIN' ? 'blue' : 'default'}>{role}</Tag>
      ),
    },
    {
      title: 'Status',
      dataIndex: 'status',
      key: 'status',
      width: 110,
      render: (status: UserStatus) => (
        <Tag color={status === 'ACTIVE' ? 'green' : 'default'}>{status}</Tag>
      ),
    },
    {
      title: '최근 로그인',
      dataIndex: 'last_login_at',
      key: 'last_login_at',
      width: 170,
      render: formatDate,
    },
    {
      title: '작업',
      key: 'actions',
      width: 200,
      render: (_, row) => (
        <Space>
          <Button type="link" onClick={() => openEdit(row)}>
            수정
          </Button>
          <Button
            type="link"
            icon={<KeyOutlined />}
            onClick={() => {
              setResetTarget(row)
              resetForm.resetFields()
              setResetOpen(true)
            }}
          >
            비밀번호 초기화
          </Button>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>
            사용자 관리
          </Typography.Title>
          <Typography.Text type="secondary">ADMIN 전용 · Role/Status 변경 시 세션 종료</Typography.Text>
        </div>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => {
            createForm.resetFields()
            createForm.setFieldsValue({ role: 'USER' })
            setCreateOpen(true)
          }}
        >
          사용자 추가
        </Button>
      </Space>

      <Space wrap style={{ marginBottom: 16 }}>
        <Input.Search
          placeholder="login_id / 이름 / 이메일 / 부서"
          allowClear
          style={{ width: 280 }}
          value={draft.q}
          onChange={(e) => setDraft((prev) => ({ ...prev, q: e.target.value }))}
          onSearch={() =>
            setFilters((prev) => ({
              ...prev,
              q: draft.q,
              role: draft.role,
              status: draft.status,
              page: 1,
            }))
          }
        />
        <Select
          style={{ width: 140 }}
          value={draft.role}
          onChange={(value) => setDraft((prev) => ({ ...prev, role: value }))}
          options={[
            { value: '', label: 'Role 전체' },
            { value: 'USER', label: 'USER' },
            { value: 'ADMIN', label: 'ADMIN' },
          ]}
        />
        <Select
          style={{ width: 150 }}
          value={draft.status}
          onChange={(value) => setDraft((prev) => ({ ...prev, status: value }))}
          options={[
            { value: '', label: 'Status 전체' },
            { value: 'ACTIVE', label: 'ACTIVE' },
            { value: 'INACTIVE', label: 'INACTIVE' },
          ]}
        />
        <Button
          type="primary"
          icon={<ReloadOutlined />}
          loading={isFetching}
          onClick={() =>
            setFilters((prev) => ({
              ...prev,
              q: draft.q,
              role: draft.role,
              status: draft.status,
              page: 1,
            }))
          }
        >
          검색
        </Button>
      </Space>

      <Table
        rowKey="id"
        loading={isLoading}
        columns={columns}
        dataSource={data?.data ?? []}
        pagination={{
          current: filters.page,
          pageSize: filters.page_size,
          total: data?.meta.total ?? 0,
          showSizeChanger: true,
          onChange: (page, pageSize) =>
            setFilters((prev) => ({ ...prev, page, page_size: pageSize })),
        }}
        size="middle"
      />

      <Modal
        title="사용자 추가"
        open={createOpen}
        onCancel={() => {
          setCreateOpen(false)
          createForm.resetFields()
        }}
        onOk={() => createForm.submit()}
        confirmLoading={createMutation.isPending}
        destroyOnHidden
      >
        <Form
          form={createForm}
          layout="vertical"
          onFinish={(values) => {
            const { password_confirm: _, ...body } = values
            createMutation.mutate(body)
          }}
        >
          <Form.Item
            name="login_id"
            label="Login ID"
            rules={[{ required: true, message: 'Login ID를 입력하세요.' }]}
          >
            <Input autoComplete="off" />
          </Form.Item>
          <Form.Item name="name" label="이름" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="email" label="이메일">
            <Input />
          </Form.Item>
          <Form.Item name="department" label="부서">
            <Input />
          </Form.Item>
          <Form.Item name="role" label="Role" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'USER', label: 'USER' },
                { value: 'ADMIN', label: 'ADMIN' },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="password"
            label="초기 Password"
            rules={[
              { required: true, message: '비밀번호를 입력하세요.' },
              { min: 8, message: '8자 이상 입력하세요.' },
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item
            name="password_confirm"
            label="Password 확인"
            dependencies={['password']}
            rules={[
              { required: true, message: '비밀번호 확인을 입력하세요.' },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue('password') === value) {
                    return Promise.resolve()
                  }
                  return Promise.reject(new Error('비밀번호가 일치하지 않습니다.'))
                },
              }),
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={editing ? `사용자 수정 · ${editing.login_id}` : '사용자 수정'}
        open={editOpen}
        onCancel={() => {
          setEditOpen(false)
          setEditing(null)
        }}
        onOk={() => editForm.submit()}
        confirmLoading={updateMutation.isPending}
        destroyOnHidden
      >
        <Form form={editForm} layout="vertical" onFinish={submitEdit}>
          <Form.Item label="Login ID">
            <Input value={editing?.login_id} disabled />
          </Form.Item>
          <Form.Item name="name" label="이름" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="email" label="이메일">
            <Input />
          </Form.Item>
          <Form.Item name="department" label="부서">
            <Input />
          </Form.Item>
          <Form.Item name="role" label="Role" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'USER', label: 'USER' },
                { value: 'ADMIN', label: 'ADMIN' },
              ]}
            />
          </Form.Item>
          <Form.Item name="status" label="Status" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'ACTIVE', label: 'ACTIVE' },
                {
                  value: 'INACTIVE',
                  label: 'INACTIVE',
                  disabled: editing?.id === me?.id,
                },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={resetTarget ? `비밀번호 초기화 · ${resetTarget.login_id}` : '비밀번호 초기화'}
        open={resetOpen}
        onCancel={() => {
          setResetOpen(false)
          setResetTarget(null)
          resetForm.resetFields()
        }}
        onOk={() => resetForm.submit()}
        confirmLoading={resetMutation.isPending}
        destroyOnHidden
      >
        <Form
          form={resetForm}
          layout="vertical"
          onFinish={(values) => {
            if (!resetTarget) return
            resetMutation.mutate({ id: resetTarget.id, password: values.new_password })
          }}
        >
          <Form.Item
            name="new_password"
            label="새 Password"
            rules={[
              { required: true },
              { min: 8, message: '8자 이상 입력하세요.' },
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item
            name="password_confirm"
            label="Password 확인"
            dependencies={['new_password']}
            rules={[
              { required: true },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue('new_password') === value) {
                    return Promise.resolve()
                  }
                  return Promise.reject(new Error('비밀번호가 일치하지 않습니다.'))
                },
              }),
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
