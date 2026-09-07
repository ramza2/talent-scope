import { useMemo, useState } from 'react'
import {
  Button,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Typography,
  Upload,
  message,
} from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { ColumnsType } from 'antd/es/table'
import type { UploadFile } from 'antd/es/upload/interface'

import { apiErrorMessage } from '@/api/errors'
import { listCodes } from '@/api/codes'
import {
  deleteDocument,
  documentPreviewUrl,
  downloadDocumentBlob,
  formatFileSize,
  listPersonDocuments,
  promoteExistingPersonDocuments,
  restoreDocument,
  type DocumentListItem,
} from '@/api/documents'

type Props = {
  personId: string
  isAdmin: boolean
  onChanged: () => Promise<void>
}

export function DocumentsTab({ personId, isAdmin, onChanged }: Props) {
  const queryClient = useQueryClient()
  const [uploadOpen, setUploadOpen] = useState(false)
  const [docType, setDocType] = useState<string | undefined>()
  const [fileList, setFileList] = useState<UploadFile[]>([])
  const [showDeleted, setShowDeleted] = useState(false)

  const docsQuery = useQuery({
    queryKey: ['people', personId, 'documents', { showDeleted }],
    queryFn: () =>
      listPersonDocuments(personId, {
        includeDeleted: isAdmin && showDeleted,
      }),
  })

  const docTypesQuery = useQuery({
    queryKey: ['codes', 'DOC_TYPE', 'active'],
    queryFn: () => listCodes({ type: 'DOC_TYPE', active: true }),
    enabled: uploadOpen,
  })

  const docTypeOptions = useMemo(
    () =>
      (docTypesQuery.data?.data ?? []).map((c) => ({
        value: c.code,
        label: `${c.name} (${c.code})`,
      })),
    [docTypesQuery.data],
  )

  const invalidateLocal = async () => {
    await queryClient.invalidateQueries({ queryKey: ['people', personId, 'documents'] })
    await onChanged()
  }

  const uploadMutation = useMutation({
    mutationFn: async () => {
      if (!docType) throw new Error('문서 종류를 선택하세요.')
      const files: File[] = []
      for (const item of fileList) {
        if (item.originFileObj) {
          files.push(item.originFileObj as File)
        }
      }
      if (files.length === 0) throw new Error('업로드할 파일을 선택하세요.')
      return promoteExistingPersonDocuments({
        personId,
        files,
        documentTypeCode: docType,
        mode: 'NEW_GROUP',
      })
    },
    onSuccess: async () => {
      message.success('문서를 업로드했습니다.')
      setUploadOpen(false)
      setFileList([])
      setDocType(undefined)
      await invalidateLocal()
    },
    onError: (error) => message.error(apiErrorMessage(error, '문서 업로드에 실패했습니다.')),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteDocument(id),
    onSuccess: async () => {
      message.success('문서를 삭제했습니다.')
      await invalidateLocal()
    },
    onError: (error) => message.error(apiErrorMessage(error, '문서 삭제에 실패했습니다.')),
  })

  const restoreMutation = useMutation({
    mutationFn: (id: string) => restoreDocument(id),
    onSuccess: async () => {
      message.success('문서를 복원했습니다.')
      await invalidateLocal()
    },
    onError: (error) => message.error(apiErrorMessage(error, '문서 복원에 실패했습니다.')),
  })

  const onDownload = async (doc: DocumentListItem) => {
    try {
      const blob = await downloadDocumentBlob(doc.document_id)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = doc.original_filename
      a.click()
      URL.revokeObjectURL(url)
    } catch (error) {
      message.error(apiErrorMessage(error, '다운로드에 실패했습니다.'))
    }
  }

  const columns: ColumnsType<DocumentListItem> = [
    {
      title: '문서종류',
      dataIndex: 'document_type_name',
      render: (_, row) => row.document_type_name || row.document_type_code,
    },
    { title: '제목', dataIndex: 'title' },
    {
      title: '버전',
      dataIndex: 'version_no',
      width: 80,
      render: (v: number, row) => `v${v}${row.is_latest ? ' · latest' : ''}`,
    },
    {
      title: '파일명',
      dataIndex: 'original_filename',
      ellipsis: true,
      render: (name: string, row) => (
        <span>
          {name}
          <Typography.Text type="secondary" style={{ marginLeft: 8 }}>
            ({formatFileSize(row.file_size)})
          </Typography.Text>
        </span>
      ),
    },
    { title: '상태', dataIndex: 'processing_status', width: 110 },
    {
      title: '업로드일',
      dataIndex: 'uploaded_at',
      width: 170,
      render: (v: string) => new Date(v).toLocaleString('ko-KR'),
    },
    ...(isAdmin && showDeleted
      ? [
          {
            title: '삭제',
            dataIndex: 'deleted_at',
            width: 90,
            render: (v: string | null | undefined) => (v ? '삭제됨' : '활성'),
          } as ColumnsType<DocumentListItem>[number],
        ]
      : []),
    {
      title: '작업',
      key: 'actions',
      width: 220,
      render: (_, row) => {
        const deleted = Boolean(row.deleted_at)
        return (
          <Space size="small" wrap>
            {!deleted ? (
              <>
                <Button type="link" size="small" onClick={() => void onDownload(row)}>
                  다운로드
                </Button>
                <Button
                  type="link"
                  size="small"
                  onClick={() =>
                    window.open(
                      documentPreviewUrl(row.document_id),
                      '_blank',
                      'noopener,noreferrer',
                    )
                  }
                >
                  미리보기
                </Button>
              </>
            ) : null}
            {isAdmin && !deleted ? (
              <Button
                type="link"
                size="small"
                danger
                loading={deleteMutation.isPending}
                onClick={() => {
                  Modal.confirm({
                    title: '문서를 삭제할까요?',
                    content: row.title,
                    okText: '삭제',
                    okButtonProps: { danger: true },
                    onOk: () => deleteMutation.mutateAsync(row.document_id),
                  })
                }}
              >
                삭제
              </Button>
            ) : null}
            {isAdmin && deleted ? (
              <Button
                type="link"
                size="small"
                loading={restoreMutation.isPending}
                onClick={() => restoreMutation.mutate(row.document_id)}
              >
                복원
              </Button>
            ) : null}
          </Space>
        )
      },
    },
  ]

  return (
    <div>
      <Space style={{ marginBottom: 12, width: '100%', justifyContent: 'space-between' }}>
        <Typography.Title level={5} style={{ margin: 0 }}>
          문서
        </Typography.Title>
        <Space>
          {isAdmin ? (
            <Space size="small">
              <Typography.Text type="secondary">삭제 포함</Typography.Text>
              <Switch checked={showDeleted} onChange={setShowDeleted} size="small" />
            </Space>
          ) : null}
          {isAdmin ? (
            <Button type="primary" onClick={() => setUploadOpen(true)}>
              문서 추가
            </Button>
          ) : null}
        </Space>
      </Space>

      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        AI 식별·신규 인력 Wizard는 다음 단계에서 제공됩니다. 여기서는 기존 인력에 문서를
        추가·조회·다운로드할 수 있습니다.
      </Typography.Paragraph>

      <Table
        rowKey={(r) => r.document_id}
        loading={docsQuery.isLoading}
        columns={columns}
        dataSource={docsQuery.data?.data ?? []}
        pagination={false}
        locale={{ emptyText: '등록된 문서가 없습니다.' }}
      />

      <Modal
        title="문서 추가"
        open={uploadOpen}
        onCancel={() => setUploadOpen(false)}
        onOk={() => uploadMutation.mutate()}
        confirmLoading={uploadMutation.isPending}
        okText="업로드"
        destroyOnClose
      >
        <Typography.Paragraph type="secondary">
          각 파일은 새 문서 그룹(version 1)으로 승격됩니다. 버전 추가는 API의 NEW_VERSION으로
          처리합니다.
        </Typography.Paragraph>
        <div style={{ marginBottom: 12 }}>
          <Typography.Text>문서 종류</Typography.Text>
          <Select
            style={{ width: '100%', marginTop: 6 }}
            placeholder="DOC_TYPE 선택"
            options={docTypeOptions}
            value={docType}
            onChange={setDocType}
            loading={docTypesQuery.isLoading}
            showSearch
            optionFilterProp="label"
          />
        </div>
        <Upload
          multiple
          beforeUpload={() => false}
          fileList={fileList}
          onChange={({ fileList: next }) => setFileList(next)}
        >
          <Button>파일 선택</Button>
        </Upload>
      </Modal>
    </div>
  )
}
