import { useMemo, useState } from 'react'
import {
  Button,
  Checkbox,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { ColumnsType } from 'antd/es/table'

import { apiErrorMessage } from '@/api/errors'
import {
  CODE_TYPES,
  type CodeItem,
  type CodeType,
  createCode,
  listCodes,
  replaceCodeAliases,
  updateCode,
} from '@/api/codes'

type Filters = {
  type: CodeType | ''
  q: string
  activeOnly: boolean
}

type CodeFormValues = {
  code: string
  type: CodeType
  name: string
  description?: string
  parent_code?: string | null
  sort_order?: number
  is_active?: boolean
  aliases?: string[]
}

export function AdminCodesPage() {
  const queryClient = useQueryClient()
  const [filters, setFilters] = useState<Filters>({
    type: 'JOB',
    q: '',
    activeOnly: false,
  })
  const [draft, setDraft] = useState<Filters>(filters)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<CodeItem | null>(null)
  const [form] = Form.useForm<CodeFormValues>()

  const queryKey = ['codes', filters] as const
  const { data, isLoading, isFetching } = useQuery({
    queryKey,
    queryFn: () =>
      listCodes({
        type: filters.type || undefined,
        q: filters.q || undefined,
        active: filters.activeOnly ? true : null,
      }),
  })

  const selectedType = Form.useWatch('type', form) ?? editing?.type ?? filters.type

  const parentOptionsQuery = useQuery({
    queryKey: ['codes', 'parents', selectedType],
    queryFn: () => listCodes({ type: (selectedType as CodeType) || undefined }),
    enabled: Boolean(selectedType) && modalOpen,
  })

  const parentOptions = useMemo(() => {
    const items = parentOptionsQuery.data?.data ?? []
    return items
      .filter((item) => !editing || item.code !== editing.code)
      .map((item) => ({
        value: item.code,
        label: `${item.code} — ${item.name}`,
      }))
  }, [parentOptionsQuery.data, editing])

  const saveMutation = useMutation({
    mutationFn: async (values: CodeFormValues) => {
      if (editing) {
        await updateCode(editing.code, {
          name: values.name,
          description: values.description ?? null,
          parent_code: values.parent_code || null,
          sort_order: values.sort_order ?? 0,
          is_active: values.is_active ?? true,
        })
        await replaceCodeAliases(editing.code, values.aliases ?? [])
      } else {
        await createCode({
          code: values.code,
          type: values.type,
          name: values.name,
          description: values.description ?? null,
          parent_code: values.parent_code || null,
          sort_order: values.sort_order ?? 0,
          aliases: values.aliases ?? [],
        })
      }
    },
    onSuccess: async () => {
      message.success(editing ? '코드가 수정되었습니다.' : '코드가 생성되었습니다.')
      setModalOpen(false)
      setEditing(null)
      form.resetFields()
      await queryClient.invalidateQueries({ queryKey: ['codes'] })
    },
    onError: (error) => {
      message.error(apiErrorMessage(error, '코드 저장에 실패했습니다.'))
    },
  })

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({
      type: (filters.type || 'JOB') as CodeType,
      sort_order: 0,
      is_active: true,
      aliases: [],
    })
    setModalOpen(true)
  }

  const openEdit = (row: CodeItem) => {
    setEditing(row)
    form.setFieldsValue({
      code: row.code,
      type: row.type,
      name: row.name,
      description: row.description ?? undefined,
      parent_code: row.parent_code,
      sort_order: row.sort_order,
      is_active: row.is_active,
      aliases: row.aliases,
    })
    setModalOpen(true)
  }

  const columns: ColumnsType<CodeItem> = [
    { title: 'Code', dataIndex: 'code', key: 'code', width: 180 },
    { title: 'Name', dataIndex: 'name', key: 'name', width: 160 },
    {
      title: 'Type',
      dataIndex: 'type',
      key: 'type',
      width: 130,
      render: (value: string) => <Tag>{value}</Tag>,
    },
    {
      title: 'Parent',
      dataIndex: 'parent_code',
      key: 'parent_code',
      width: 160,
      render: (value?: string | null) => value || '—',
    },
    {
      title: 'Aliases',
      dataIndex: 'aliases',
      key: 'aliases',
      ellipsis: true,
      render: (aliases: string[]) => (aliases.length ? aliases.join(', ') : '—'),
    },
    {
      title: '활성',
      dataIndex: 'is_active',
      key: 'is_active',
      width: 80,
      render: (active: boolean) => (active ? <Tag color="green">Y</Tag> : <Tag>N</Tag>),
    },
    { title: '정렬', dataIndex: 'sort_order', key: 'sort_order', width: 70 },
    {
      title: '작업',
      key: 'actions',
      width: 90,
      render: (_, row) => (
        <Button type="link" onClick={() => openEdit(row)}>
          수정
        </Button>
      ),
    },
  ]

  return (
    <div>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>
            코드 관리
          </Typography.Title>
          <Typography.Text type="secondary">
            JOB / TECH / EXP / BIZ / CUSTOMER_TYPE / DOC_TYPE
          </Typography.Text>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          코드 추가
        </Button>
      </Space>

      <Space wrap style={{ marginBottom: 16 }}>
        <Select
          style={{ width: 180 }}
          value={draft.type}
          onChange={(value) => setDraft((prev) => ({ ...prev, type: value }))}
          options={[
            { value: '', label: '전체 유형' },
            ...CODE_TYPES.map((type) => ({ value: type, label: type })),
          ]}
        />
        <Input.Search
          placeholder="코드 / 이름 / Alias 검색"
          allowClear
          style={{ width: 280 }}
          value={draft.q}
          onChange={(e) => setDraft((prev) => ({ ...prev, q: e.target.value }))}
          onSearch={() => setFilters(draft)}
        />
        <Checkbox
          checked={draft.activeOnly}
          onChange={(e) => setDraft((prev) => ({ ...prev, activeOnly: e.target.checked }))}
        >
          활성만
        </Checkbox>
        <Button
          type="primary"
          onClick={() => setFilters(draft)}
          icon={<ReloadOutlined />}
          loading={isFetching}
        >
          검색
        </Button>
      </Space>

      <Table
        rowKey="code"
        loading={isLoading}
        columns={columns}
        dataSource={data?.data ?? []}
        pagination={{ pageSize: 20, showSizeChanger: true }}
        size="middle"
      />

      <Modal
        title={editing ? '코드 수정' : '코드 추가'}
        open={modalOpen}
        onCancel={() => {
          setModalOpen(false)
          setEditing(null)
          form.resetFields()
        }}
        onOk={() => form.submit()}
        confirmLoading={saveMutation.isPending}
        destroyOnHidden
        width={640}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(values) => saveMutation.mutate(values)}
        >
          <Form.Item
            name="code"
            label="Code"
            rules={[{ required: true, message: 'Code를 입력하세요.' }]}
          >
            <Input disabled={Boolean(editing)} placeholder="예: TECH-LANG-PYTHON" />
          </Form.Item>
          <Form.Item
            name="type"
            label="Type"
            rules={[{ required: true, message: '유형을 선택하세요.' }]}
          >
            <Select
              disabled={Boolean(editing)}
              options={CODE_TYPES.map((type) => ({ value: type, label: type }))}
            />
          </Form.Item>
          <Form.Item
            name="name"
            label="Name"
            rules={[{ required: true, message: '표준명을 입력하세요.' }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="description" label="Description">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="parent_code" label="Parent">
            <Select
              allowClear
              showSearch
              optionFilterProp="label"
              options={parentOptions}
              placeholder="동일 Type의 상위 코드"
            />
          </Form.Item>
          <Form.Item name="sort_order" label="Sort Order">
            <InputNumber style={{ width: '100%' }} />
          </Form.Item>
          {editing ? (
            <Form.Item name="is_active" label="Active" valuePropName="checked">
              <Switch />
            </Form.Item>
          ) : null}
          <Form.Item name="aliases" label="Aliases">
            <Select mode="tags" tokenSeparators={[',']} placeholder="Alias 입력 후 Enter" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
