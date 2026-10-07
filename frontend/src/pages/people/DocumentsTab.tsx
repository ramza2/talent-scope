import { useEffect, useMemo, useState } from 'react'
import {
  Button,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  Upload,
  message,
} from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { ColumnsType } from 'antd/es/table'
import type { Key } from 'react'
import type { UploadFile } from 'antd/es/upload/interface'
import { useNavigate } from 'react-router-dom'

import { apiErrorMessage } from '@/api/errors'
import { createAnalysis, listAnalyses } from '@/api/analyses'
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

function previewUnavailableTooltip(row: DocumentListItem): string {
  if (row.processing_status === 'READY') {
    return '미리보기를 지원하지 않는 문서입니다. AI 분석용 텍스트는 정상 추출되었습니다.'
  }
  if (row.processing_status === 'UPLOADED' || row.processing_status === 'PROCESSING') {
    return '문서 처리 중입니다. 처리 완료 후 미리보기 가능 여부가 결정됩니다.'
  }
  if (row.processing_status === 'FAILED') {
    return '미리보기를 사용할 수 없습니다. 원본 파일은 다운로드할 수 있습니다.'
  }
  return '미리보기를 사용할 수 없습니다. 원본 파일을 다운로드해 확인해 주세요.'
}

type FileDocTypeSource = 'manual' | 'suggested'

type FileDocTypeState = {
  documentTypeCode?: string
  source: FileDocTypeSource | null
}

/** High-confidence filename rules aligned with backend `_suggest_doc_type` (no DOC-OTHER fallback). */
export function suggestDocTypeFromFilename(filename: string): string | undefined {
  const lower = filename.toLowerCase()
  const mapping: Array<[readonly string[], string]> = [
    [['이력서', 'resume', 'cv'], 'DOC-RESUME'],
    [['경력기술', 'career'], 'DOC-CAREER'],
    [['프로필', 'profile'], 'DOC-PROFILE'],
    [['자격', 'cert'], 'DOC-CERT'],
    [['kosa', '경력증명'], 'DOC-KOSA'],
    [['포트폴리오', 'portfolio'], 'DOC-PORTFOLIO'],
    [['학력', 'diploma', '졸업'], 'DOC-EDU'],
  ]
  for (const [keys, code] of mapping) {
    if (keys.some((k) => lower.includes(k))) {
      return code
    }
  }
  return undefined
}

/** Merge per-file DOC_TYPE state keyed by UploadFile.uid; never overwrite manual picks. */
export function mergeFileDocTypeState(
  files: UploadFile[],
  prev: Record<string, FileDocTypeState>,
  activeCodes: ReadonlySet<string>,
): Record<string, FileDocTypeState> {
  const next: Record<string, FileDocTypeState> = {}
  for (const file of files) {
    const existing = prev[file.uid]
    if (existing?.source === 'manual') {
      next[file.uid] = existing
      continue
    }
    const name = file.name || file.originFileObj?.name || ''
    const suggested = suggestDocTypeFromFilename(name)
    if (suggested && activeCodes.has(suggested)) {
      next[file.uid] = { documentTypeCode: suggested, source: 'suggested' }
    } else {
      next[file.uid] = { documentTypeCode: undefined, source: null }
    }
  }
  return next
}

function statusTag(status: string) {
  const color =
    status === 'READY'
      ? 'success'
      : status === 'FAILED'
        ? 'error'
        : status === 'PROCESSING'
          ? 'processing'
          : 'default'
  return <Tag color={color}>{status}</Tag>
}

export function DocumentsTab({ personId, isAdmin, onChanged }: Props) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [uploadOpen, setUploadOpen] = useState(false)
  const [fileList, setFileList] = useState<UploadFile[]>([])
  const [fileDocTypes, setFileDocTypes] = useState<Record<string, FileDocTypeState>>(
    {},
  )
  const [showDeleted, setShowDeleted] = useState(false)
  const [selectedKeys, setSelectedKeys] = useState<Key[]>([])

  const docsQuery = useQuery({
    queryKey: ['people', personId, 'documents', { showDeleted }],
    queryFn: () =>
      listPersonDocuments(personId, {
        includeDeleted: isAdmin && showDeleted,
      }),
    refetchInterval: (query) => {
      const items = query.state.data?.data ?? []
      const pending = items.some(
        (d) =>
          d.processing_status === 'UPLOADED' || d.processing_status === 'PROCESSING',
      )
      return pending ? 3000 : false
    },
  })

  const analysesQuery = useQuery({
    queryKey: ['analyses', { person_id: personId }],
    queryFn: () =>
      listAnalyses({
        person_id: personId,
        page: 1,
        page_size: 20,
        sort: 'created_desc',
      }),
    enabled: isAdmin,
    refetchInterval: (query) => {
      const items = query.state.data?.data ?? []
      const active = items.some(
        (a) =>
          a.status === 'DEFERRED' ||
          a.status === 'QUEUED' ||
          a.status === 'PROCESSING',
      )
      return active ? 3000 : false
    },
  })

  const hasActiveAnalysis = useMemo(() => {
    const items = analysesQuery.data?.data ?? []
    return items.some(
      (a) =>
        a.status === 'DEFERRED' ||
        a.status === 'QUEUED' ||
        a.status === 'PROCESSING',
    )
  }, [analysesQuery.data])

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

  const activeDocTypeCodes = useMemo(
    () => new Set(docTypeOptions.map((o) => o.value)),
    [docTypeOptions],
  )

  useEffect(() => {
    if (!uploadOpen) return
    if (docTypesQuery.isLoading) return
    setFileDocTypes((prev) =>
      mergeFileDocTypeState(fileList, prev, activeDocTypeCodes),
    )
  }, [uploadOpen, fileList, activeDocTypeCodes, docTypesQuery.isLoading])

  const filesMissingDocType = useMemo(
    () =>
      fileList.filter((file) => !fileDocTypes[file.uid]?.documentTypeCode),
    [fileList, fileDocTypes],
  )

  const canUpload =
    fileList.length > 0 &&
    filesMissingDocType.length === 0 &&
    !docTypesQuery.isLoading

  const resetUploadModal = () => {
    setUploadOpen(false)
    setFileList([])
    setFileDocTypes({})
  }

  const invalidateLocal = async () => {
    await queryClient.invalidateQueries({ queryKey: ['people', personId, 'documents'] })
    await onChanged()
  }

  const uploadMutation = useMutation({
    mutationFn: async () => {
      if (fileList.length === 0) throw new Error('업로드할 파일을 선택하세요.')
      if (filesMissingDocType.length > 0) {
        const names = filesMissingDocType
          .map((f) => f.name || f.originFileObj?.name || f.uid)
          .join(', ')
        throw new Error(`문서 종류를 선택하세요: ${names}`)
      }
      const items = []
      for (const item of fileList) {
        if (!item.originFileObj) continue
        const code = fileDocTypes[item.uid]?.documentTypeCode
        if (!code) continue
        items.push({
          file: item.originFileObj as File,
          documentTypeCode: code,
        })
      }
      if (items.length === 0) throw new Error('업로드할 파일을 선택하세요.')
      if (items.length !== fileList.length) {
        throw new Error('선택한 파일 정보를 확인할 수 없습니다.')
      }
      return promoteExistingPersonDocuments({
        personId,
        items,
        mode: 'NEW_GROUP',
      })
    },
    onSuccess: async (result) => {
      const reused = result.data.reused_document_ids ?? []
      const all = result.data.document_ids ?? []
      const newCount = all.length - reused.length
      if (reused.length > 0 && newCount <= 0) {
        message.success(
          `동일한 기존 문서 ${reused.length}건을 재사용했습니다. 중복 저장 및 문서 처리는 수행하지 않습니다.`,
        )
      } else if (reused.length > 0) {
        message.success(
          `문서를 등록했습니다. 동일 파일 ${reused.length}건은 기존 문서를 재사용했습니다.`,
        )
      } else {
        message.success(
          '문서를 업로드했습니다. 문서 처리 완료 후 AI 상세 분석이 자동으로 시작됩니다.',
        )
      }
      resetUploadModal()
      await invalidateLocal()
    },
    onError: (error) => message.error(apiErrorMessage(error, '문서 업로드에 실패했습니다.')),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteDocument(id),
    onSuccess: async (_data, deletedId) => {
      setSelectedKeys((prev) => prev.filter((key) => String(key) !== deletedId))
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

  const analysisMutation = useMutation({
    mutationFn: (documentIds: string[]) =>
      createAnalysis({
        person_id: personId,
        document_ids: documentIds,
        analysis_type: 'PROFILE',
      }),
    onSuccess: async (res) => {
      message.success('AI 상세 분석을 시작했습니다.')
      setSelectedKeys([])
      await invalidateLocal()
      await queryClient.invalidateQueries({ queryKey: ['analyses'] })
      await queryClient.invalidateQueries({
        queryKey: ['analyses', { person_id: personId }],
      })
      navigate(`/analyses/${res.data.analysis_id}`)
    },
    onError: (error) =>
      message.error(apiErrorMessage(error, 'AI 상세 분석 시작에 실패했습니다.')),
  })

  const selectableDocs = useMemo(() => {
    const items = docsQuery.data?.data ?? []
    return items.filter((d) => d.processing_status === 'READY' && !d.deleted_at)
  }, [docsQuery.data])

  const startAnalysis = () => {
    const ids = selectedKeys.map(String)
    Modal.confirm({
      title: 'AI 상세 분석을 시작할까요?',
      content: `선택한 문서 ${ids.length}건으로 PROFILE 분석을 요청합니다.`,
      okText: '시작',
      cancelText: '취소',
      onOk: () => analysisMutation.mutateAsync(ids),
    })
  }

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
    {
      title: '상태',
      dataIndex: 'processing_status',
      width: 130,
      render: (status: string, row) => {
        const tag = statusTag(status)
        if (status === 'FAILED' && isAdmin && row.processing_error) {
          return <Tooltip title={row.processing_error}>{tag}</Tooltip>
        }
        return tag
      },
    },
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
            render: (v: string | null | undefined) =>
              v ? <Tag color="error">삭제됨</Tag> : <Tag>활성</Tag>,
          } as ColumnsType<DocumentListItem>[number],
        ]
      : []),
    {
      title: '작업',
      key: 'actions',
      width: 220,
      render: (_, row) => {
        const deleted = Boolean(row.deleted_at)
        const previewButton = row.preview_available ? (
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
        ) : (
          <Tooltip title={previewUnavailableTooltip(row)}>
            <span>
              <Button type="link" size="small" disabled>
                미리보기
              </Button>
            </span>
          </Tooltip>
        )
        return (
          <Space size="small" wrap>
            {!deleted ? (
              <>
                <Button type="link" size="small" onClick={() => void onDownload(row)}>
                  다운로드
                </Button>
                {previewButton}
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
                    content: `${row.title || row.original_filename} 문서를 삭제합니다. 삭제된 문서는 기본 목록과 AI 분석 대상에서 제외되지만 원본 데이터는 보존되며 관리자가 복원할 수 있습니다.`,
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
                onClick={() => {
                  Modal.confirm({
                    title: '문서를 복원할까요?',
                    content: row.title || row.original_filename,
                    okText: '복원',
                    onOk: () => restoreMutation.mutateAsync(row.document_id),
                  })
                }}
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
              <Typography.Text type="secondary">삭제된 문서 포함</Typography.Text>
              <Switch
                checked={showDeleted}
                onChange={(checked) => {
                  setShowDeleted(checked)
                  setSelectedKeys([])
                }}
                size="small"
              />
            </Space>
          ) : null}
          {isAdmin ? (
            <Tooltip
              title={
                hasActiveAnalysis
                  ? '이미 진행 중이거나 대기 중인 AI 분석이 있습니다.'
                  : undefined
              }
            >
              <span>
                <Button
                  disabled={selectedKeys.length === 0 || hasActiveAnalysis}
                  loading={analysisMutation.isPending}
                  onClick={startAnalysis}
                >
                  AI 상세 분석
                </Button>
              </span>
            </Tooltip>
          ) : null}
          {isAdmin ? (
            <Button type="primary" onClick={() => setUploadOpen(true)}>
              문서 추가
            </Button>
          ) : null}
        </Space>
      </Space>

      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        업로드 후 텍스트 추출과 AI 상세 분석이 백그라운드에서 처리됩니다. 문서
        형식에 따라 미리보기가 제공되지 않을 수 있습니다.
      </Typography.Paragraph>

      <Table
        rowKey={(r) => r.document_id}
        loading={docsQuery.isLoading}
        columns={columns}
        dataSource={docsQuery.data?.data ?? []}
        pagination={false}
        locale={{ emptyText: '등록된 문서가 없습니다.' }}
        rowSelection={
          isAdmin
            ? {
                selectedRowKeys: selectedKeys,
                onChange: setSelectedKeys,
                getCheckboxProps: (row) => ({
                  disabled:
                    row.processing_status !== 'READY' || Boolean(row.deleted_at),
                }),
                selections: [
                  {
                    key: 'ready-all',
                    text: 'READY 전체 선택',
                    onSelect: () =>
                      setSelectedKeys(selectableDocs.map((d) => d.document_id)),
                  },
                ],
              }
            : undefined
        }
      />

      <Modal
        title="문서 추가"
        open={uploadOpen}
        onCancel={resetUploadModal}
        onOk={() => uploadMutation.mutate()}
        confirmLoading={uploadMutation.isPending}
        okText="업로드"
        okButtonProps={{ disabled: !canUpload }}
        destroyOnClose
        width={640}
      >
        <Typography.Paragraph type="secondary">
          각 파일은 새 문서 그룹(version 1)으로 승격됩니다. 버전 추가는 API의 NEW_VERSION으로
          처리합니다. 파일마다 문서 종류를 지정한 뒤 한 번에 등록합니다.
        </Typography.Paragraph>
        <Upload
          multiple
          beforeUpload={() => false}
          fileList={fileList}
          showUploadList={false}
          onChange={({ fileList: next }) => setFileList(next)}
        >
          <Button style={{ marginBottom: 12 }}>파일 선택</Button>
        </Upload>
        {fileList.length > 0 ? (
          <Table
            size="small"
            pagination={false}
            rowKey={(row) => row.uid}
            dataSource={fileList}
            columns={[
              {
                title: '파일명',
                key: 'name',
                ellipsis: true,
                render: (_, file) =>
                  file.name || file.originFileObj?.name || file.uid,
              },
              {
                title: '문서 종류',
                key: 'docType',
                width: 260,
                render: (_, file) => (
                  <Select
                    style={{ width: '100%' }}
                    placeholder="DOC_TYPE 선택"
                    options={docTypeOptions}
                    value={fileDocTypes[file.uid]?.documentTypeCode}
                    allowClear
                    loading={docTypesQuery.isLoading}
                    showSearch
                    optionFilterProp="label"
                    onChange={(value) => {
                      setFileDocTypes((prev) => ({
                        ...prev,
                        [file.uid]: {
                          documentTypeCode: value,
                          source: 'manual',
                        },
                      }))
                    }}
                  />
                ),
              },
              {
                title: '',
                key: 'remove',
                width: 64,
                render: (_, file) => (
                  <Button
                    type="link"
                    danger
                    size="small"
                    onClick={() => {
                      setFileList((prev) => prev.filter((f) => f.uid !== file.uid))
                    }}
                  >
                    삭제
                  </Button>
                ),
              },
            ]}
          />
        ) : null}
        {filesMissingDocType.length > 0 ? (
          <Typography.Text type="danger" style={{ display: 'block', marginTop: 8 }}>
            문서 종류를 선택하세요:{' '}
            {filesMissingDocType
              .map((f) => f.name || f.originFileObj?.name || f.uid)
              .join(', ')}
          </Typography.Text>
        ) : null}
      </Modal>
    </div>
  )
}
