import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  Radio,
  Select,
  Space,
  Steps,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import type { ColumnsType } from 'antd/es/table'
import type { UploadFile } from 'antd/es/upload/interface'

import { apiErrorMessage } from '@/api/errors'
import { listCodes } from '@/api/codes'
import {
  cancelUploadSession,
  createUploadSession,
  deleteTempFile,
  formatFileSize,
  getUploadSession,
  identifyUploadSession,
  listPersonDocuments,
  matchReasonLabel,
  patchTempFile,
  resolveUploadSession,
  uploadSessionFiles,
  type DocumentResolutionItem,
  type DuplicateCandidate,
  type TempFileItem,
  type UploadSession,
} from '@/api/documents'

const { Title, Paragraph, Text } = Typography

type Decision = 'CREATE_NEW' | 'LINK_EXISTING'

type IdentityForm = {
  name: string
  company?: string
  phone?: string
  email?: string
}

const STEP_ITEMS = [
  { title: '문서 업로드' },
  { title: '문서종류' },
  { title: '인력 식별' },
  { title: '중복 확인' },
  { title: '등록 방식' },
  { title: '완료' },
]

export function PeopleNewPage() {
  const navigate = useNavigate()
  const [step, setStep] = useState(0)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [session, setSession] = useState<UploadSession | null>(null)
  const [fileList, setFileList] = useState<UploadFile[]>([])
  const [identity, setIdentity] = useState<IdentityForm>({ name: '' })
  const [decision, setDecision] = useState<Decision>('CREATE_NEW')
  const [selectedPersonId, setSelectedPersonId] = useState<string | null>(null)
  const [resolutions, setResolutions] = useState<Record<string, DocumentResolutionItem>>(
    {},
  )
  const [identifyError, setIdentifyError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const pollingRef = useRef<number | null>(null)
  const prevStatusRef = useRef<string | null>(null)

  const docTypesQuery = useQuery({
    queryKey: ['codes', 'DOC_TYPE', 'active'],
    queryFn: () => listCodes({ type: 'DOC_TYPE', active: true }),
  })

  const docTypeOptions = useMemo(
    () =>
      (docTypesQuery.data?.data ?? []).map((c) => ({
        value: c.code,
        label: `${c.name} (${c.code})`,
      })),
    [docTypesQuery.data],
  )

  const groupsQuery = useQuery({
    queryKey: ['people', selectedPersonId, 'documents', 'wizard'],
    queryFn: () => listPersonDocuments(selectedPersonId!),
    enabled: decision === 'LINK_EXISTING' && !!selectedPersonId,
  })

  const groupOptionsByType = useMemo(() => {
    const map = new Map<string, { value: string; label: string }[]>()
    for (const doc of groupsQuery.data?.data ?? []) {
      if (!doc.is_latest || doc.deleted_at) continue
      const list = map.get(doc.document_type_code) ?? []
      list.push({
        value: doc.document_group_id,
        label: `${doc.title} (v${doc.version_no})`,
      })
      map.set(doc.document_type_code, list)
    }
    return map
  }, [groupsQuery.data])

  useEffect(() => {
    return () => {
      if (pollingRef.current) window.clearInterval(pollingRef.current)
    }
  }, [])

  const refreshSession = async (id: string) => {
    const res = await getUploadSession(id)
    setSession(res.data)
    return res.data
  }

  const ensureSession = async () => {
    if (sessionId) return sessionId
    const created = await createUploadSession()
    setSessionId(created.data.id)
    setSession(created.data)
    return created.data.id
  }

  const stopPolling = () => {
    if (pollingRef.current) {
      window.clearInterval(pollingRef.current)
      pollingRef.current = null
    }
  }

  const startIdentifyPolling = (id: string) => {
    stopPolling()
    prevStatusRef.current = 'IDENTIFYING'
    pollingRef.current = window.setInterval(async () => {
      try {
        const data = await refreshSession(id)
        if (data.status === 'IDENTIFIED') {
          stopPolling()
          setIdentifyError(null)
          setIdentity({
            name: data.identity?.name ?? '',
            company: data.identity?.company ?? undefined,
            phone: data.identity?.phone ?? undefined,
            email: data.identity?.email ?? undefined,
          })
          setStep(3)
          setBusy(false)
        } else if (
          data.status === 'UPLOADING' &&
          prevStatusRef.current === 'IDENTIFYING'
        ) {
          stopPolling()
          setIdentifyError('인력 식별에 실패했습니다. 다시 시도해 주세요.')
          setBusy(false)
        }
        prevStatusRef.current = data.status
      } catch {
        /* keep polling; surface on next action */
      }
    }, 2500)
  }

  const uploadMutation = useMutation({
    mutationFn: async (files: File[]) => {
      const id = await ensureSession()
      await uploadSessionFiles(id, files)
      return refreshSession(id)
    },
    onSuccess: () => {
      message.success('파일을 업로드했습니다.')
      setFileList([])
    },
    onError: (error) => message.error(apiErrorMessage(error, '업로드에 실패했습니다.')),
  })

  const identifying = session?.status === 'IDENTIFYING'
  const filesLocked = identifying || busy

  const fileColumns: ColumnsType<TempFileItem> = [
    {
      title: '파일명',
      dataIndex: 'original_filename',
    },
    {
      title: '크기',
      dataIndex: 'file_size',
      width: 100,
      render: (v: number) => formatFileSize(v),
    },
    {
      title: '검증',
      dataIndex: 'validation_status',
      width: 110,
      render: (v: string) => <Tag>{v}</Tag>,
    },
    {
      title: '문서종류',
      dataIndex: 'document_type_code',
      width: 220,
      render: (value, row) => (
        <Select
          style={{ width: '100%' }}
          value={value ?? undefined}
          options={docTypeOptions}
          disabled={filesLocked || step > 2}
          placeholder="DOC_TYPE"
          onChange={async (code) => {
            if (!sessionId) return
            try {
              await patchTempFile(sessionId, row.temp_file_id, code)
              await refreshSession(sessionId)
            } catch (error) {
              message.error(apiErrorMessage(error, '문서종류 변경에 실패했습니다.'))
            }
          }}
        />
      ),
    },
    {
      title: '',
      width: 80,
      render: (_, row) => (
        <Button
          danger
          type="link"
          disabled={filesLocked || step > 2}
          onClick={async () => {
            if (!sessionId) return
            try {
              await deleteTempFile(sessionId, row.temp_file_id)
              await refreshSession(sessionId)
            } catch (error) {
              message.error(apiErrorMessage(error, '파일 삭제에 실패했습니다.'))
            }
          }}
        >
          삭제
        </Button>
      ),
    },
  ]

  const candidateColumns: ColumnsType<DuplicateCandidate> = [
    { title: '이름', dataIndex: 'name' },
    { title: '회사', dataIndex: 'company', render: (v) => v || '-' },
    {
      title: '일치 이유',
      dataIndex: 'match_reasons',
      render: (reasons: string[]) => (
        <Space wrap>
          {reasons.map((r) => (
            <Tag key={r}>{matchReasonLabel(r)}</Tag>
          ))}
        </Space>
      ),
    },
    {
      title: '중복 가능성',
      dataIndex: 'score',
      width: 120,
      render: (score: number) => `${Math.round(score * 100)}%`,
    },
  ]

  const ensureResolutions = (files: TempFileItem[], personId: string | null) => {
    setResolutions((prev) => {
      const next: Record<string, DocumentResolutionItem> = {}
      for (const file of files) {
        const existing = prev[file.temp_file_id]
        next[file.temp_file_id] = existing ?? {
          temp_file_id: file.temp_file_id,
          mode: 'NEW_GROUP',
          document_type_code: file.document_type_code ?? undefined,
          title: file.original_filename,
        }
        if (decision === 'CREATE_NEW') {
          next[file.temp_file_id] = {
            ...next[file.temp_file_id],
            mode: 'NEW_GROUP',
            document_group_id: undefined,
          }
        } else if (!personId) {
          next[file.temp_file_id].mode = 'NEW_GROUP'
        }
      }
      return next
    })
  }

  const onCancelWizard = async () => {
    stopPolling()
    if (sessionId) {
      try {
        await cancelUploadSession(sessionId)
      } catch {
        /* ignore */
      }
    }
    navigate('/people')
  }

  const onStartIdentify = async () => {
    if (!sessionId || !session?.files.length) {
      message.warning('업로드된 파일이 필요합니다.')
      return
    }
    if (session.files.some((f) => !f.document_type_code)) {
      message.warning('모든 파일의 문서종류를 선택하세요.')
      return
    }
    setBusy(true)
    setIdentifyError(null)
    try {
      await identifyUploadSession(sessionId)
      await refreshSession(sessionId)
      startIdentifyPolling(sessionId)
    } catch (error) {
      setBusy(false)
      message.error(apiErrorMessage(error, '인력 식별을 시작하지 못했습니다.'))
    }
  }

  const onConfirmDecision = () => {
    if (decision === 'CREATE_NEW' && !identity.name.trim()) {
      message.warning('이름은 필수입니다.')
      return
    }
    if (decision === 'LINK_EXISTING' && !selectedPersonId) {
      message.warning('연결할 기존 인력을 선택하세요.')
      return
    }
    ensureResolutions(session?.files ?? [], selectedPersonId)
    setStep(4)
  }

  const onResolve = async () => {
    if (!sessionId || !session) return
    const files = session.files
    for (const file of files) {
      const item = resolutions[file.temp_file_id]
      if (!item?.document_type_code && item?.mode === 'NEW_GROUP') {
        message.warning('문서종류가 없는 파일이 있습니다.')
        return
      }
      if (item?.mode === 'NEW_VERSION' && !item.document_group_id) {
        message.warning('NEW_VERSION에는 문서 그룹 선택이 필요합니다.')
        return
      }
    }
    setBusy(true)
    try {
      const document_resolution = files.map(
        (f) => resolutions[f.temp_file_id] ?? {
          temp_file_id: f.temp_file_id,
          mode: 'NEW_GROUP' as const,
          document_type_code: f.document_type_code ?? undefined,
          title: f.original_filename,
        },
      )
      const result =
        decision === 'CREATE_NEW'
          ? await resolveUploadSession(sessionId, {
              mode: 'CREATE_NEW',
              identity: {
                name: identity.name.trim(),
                company: identity.company || null,
                phone: identity.phone || null,
                email: identity.email || null,
              },
              document_resolution,
            })
          : await resolveUploadSession(sessionId, {
              mode: 'LINK_EXISTING',
              person_id: selectedPersonId!,
              document_resolution,
            })
      message.success(
        decision === 'CREATE_NEW'
          ? '인력이 등록되었습니다. 문서 분석을 시작합니다.'
          : '문서가 기존 인력에 추가되었습니다. 문서 분석을 시작합니다.',
      )
      setStep(5)
      navigate(`/people/${result.data.person_id}`)
    } catch (error) {
      message.error(apiErrorMessage(error, '등록에 실패했습니다.'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <div>
        <Title level={3} style={{ marginBottom: 4 }}>
          신규 인력 등록
        </Title>
        <Paragraph type="secondary" style={{ marginBottom: 0 }}>
          문서를 업로드하고 최소 식별 정보로 신규 등록 또는 기존 인력 연결을 진행합니다.
        </Paragraph>
      </div>

      <Steps current={step} items={STEP_ITEMS} size="small" />

      {step <= 1 && (
        <Card title="1. 문서 업로드 / 2. 문서종류 확인">
          <Upload.Dragger
            multiple
            fileList={fileList}
            beforeUpload={() => false}
            disabled={filesLocked}
            onChange={({ fileList: next }) => setFileList(next)}
          >
            <p className="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p className="ant-upload-text">파일을 드래그하거나 클릭하여 선택</p>
          </Upload.Dragger>
          <Space style={{ marginTop: 16 }}>
            <Button
              type="primary"
              loading={uploadMutation.isPending}
              disabled={!fileList.length || filesLocked}
              onClick={() => {
                const files = fileList
                  .map((f) => f.originFileObj as File | undefined)
                  .filter(Boolean) as File[]
                uploadMutation.mutate(files)
              }}
            >
              업로드
            </Button>
            <Button disabled={!session?.files.length || filesLocked} onClick={() => setStep(2)}>
              다음: 인력 식별
            </Button>
            <Button onClick={onCancelWizard}>취소</Button>
          </Space>
          <Table
            style={{ marginTop: 16 }}
            rowKey="temp_file_id"
            size="small"
            pagination={false}
            columns={fileColumns}
            dataSource={session?.files ?? []}
          />
        </Card>
      )}

      {step === 2 && (
        <Card title="3. 인력 식별">
          {identifyError && <Alert type="error" showIcon message={identifyError} style={{ marginBottom: 16 }} />}
          <Paragraph>
            업로드된 문서를 분석하여 이름·회사·전화·이메일을 추출합니다. 상세 프로필 분석은 수행하지
            않습니다.
          </Paragraph>
          <Space>
            <Button type="primary" loading={busy || identifying} onClick={onStartIdentify}>
              인력 식별 실행
            </Button>
            <Button disabled={identifying} onClick={() => setStep(1)}>
              이전
            </Button>
            <Button onClick={onCancelWizard}>취소</Button>
          </Space>
          {(busy || identifying) && (
            <Alert
              style={{ marginTop: 16 }}
              type="info"
              showIcon
              message="인력 식별 중입니다. 파일 변경은 잠시 후 가능합니다."
            />
          )}
        </Card>
      )}

      {step === 3 && (
        <Card title="4. 식별 정보 / 중복 가능성">
          <Form layout="vertical" style={{ maxWidth: 480 }}>
            <Form.Item label="이름" required>
              <Input
                value={identity.name}
                onChange={(e) => setIdentity((s) => ({ ...s, name: e.target.value }))}
              />
            </Form.Item>
            <Form.Item label="소속회사">
              <Input
                value={identity.company}
                onChange={(e) => setIdentity((s) => ({ ...s, company: e.target.value }))}
              />
            </Form.Item>
            <Form.Item label="전화번호">
              <Input
                value={identity.phone}
                onChange={(e) => setIdentity((s) => ({ ...s, phone: e.target.value }))}
              />
            </Form.Item>
            <Form.Item label="이메일">
              <Input
                value={identity.email}
                onChange={(e) => setIdentity((s) => ({ ...s, email: e.target.value }))}
              />
            </Form.Item>
          </Form>

          <Title level={5}>중복 가능성</Title>
          <Paragraph type="secondary">
            점수는 참고용입니다. 동일 인물 여부는 사용자가 최종 결정합니다.
          </Paragraph>
          <Table
            rowKey="person_id"
            size="small"
            pagination={false}
            columns={candidateColumns}
            dataSource={session?.duplicate_candidates ?? []}
            locale={{ emptyText: '중복 후보가 없습니다.' }}
            rowSelection={{
              type: 'radio',
              selectedRowKeys: selectedPersonId ? [selectedPersonId] : [],
              onChange: (keys) => {
                const id = String(keys[0] ?? '')
                setSelectedPersonId(id || null)
                if (id) setDecision('LINK_EXISTING')
              },
            }}
          />

          <Radio.Group
            style={{ marginTop: 16 }}
            value={decision}
            onChange={(e) => {
              setDecision(e.target.value)
              if (e.target.value === 'CREATE_NEW') setSelectedPersonId(null)
            }}
          >
            <Radio value="CREATE_NEW">신규 인력으로 등록</Radio>
            <Radio value="LINK_EXISTING">기존 인력에 연결</Radio>
          </Radio.Group>

          <Space style={{ marginTop: 16 }}>
            <Button type="primary" onClick={onConfirmDecision}>
              다음: 문서 등록 방식
            </Button>
            <Button onClick={() => setStep(2)}>이전</Button>
          </Space>
        </Card>
      )}

      {step === 4 && (
        <Card title="5. 문서 등록 방식">
          <Space direction="vertical" style={{ width: '100%' }} size="middle">
            {(session?.files ?? []).map((file) => {
              const item = resolutions[file.temp_file_id]
              const typeCode = item?.document_type_code ?? file.document_type_code ?? undefined
              const groupOpts = typeCode ? groupOptionsByType.get(typeCode) ?? [] : []
              return (
                <Card key={file.temp_file_id} size="small" title={file.original_filename}>
                  <Space wrap>
                    <Text type="secondary">문서종류</Text>
                    <Select
                      style={{ width: 220 }}
                      value={typeCode}
                      options={docTypeOptions}
                      disabled={decision === 'LINK_EXISTING' && item?.mode === 'NEW_VERSION'}
                      onChange={(code) =>
                        setResolutions((prev) => ({
                          ...prev,
                          [file.temp_file_id]: {
                            ...prev[file.temp_file_id],
                            temp_file_id: file.temp_file_id,
                            document_type_code: code,
                            mode: 'NEW_GROUP',
                            document_group_id: undefined,
                            title: file.original_filename,
                          },
                        }))
                      }
                    />
                    {decision === 'LINK_EXISTING' && (
                      <>
                        <Select
                          style={{ width: 180 }}
                          value={item?.mode ?? 'NEW_GROUP'}
                          options={[
                            { value: 'NEW_GROUP', label: '새 문서 그룹' },
                            { value: 'NEW_VERSION', label: '기존 그룹 새 버전' },
                          ]}
                          onChange={(mode) =>
                            setResolutions((prev) => ({
                              ...prev,
                              [file.temp_file_id]: {
                                ...prev[file.temp_file_id],
                                temp_file_id: file.temp_file_id,
                                mode,
                                document_group_id:
                                  mode === 'NEW_GROUP'
                                    ? undefined
                                    : prev[file.temp_file_id]?.document_group_id,
                                document_type_code: typeCode,
                                title: file.original_filename,
                              },
                            }))
                          }
                        />
                        {item?.mode === 'NEW_VERSION' && (
                          <Select
                            style={{ width: 260 }}
                            placeholder="문서 그룹 선택"
                            options={groupOpts}
                            value={item.document_group_id}
                            onChange={(gid) =>
                              setResolutions((prev) => ({
                                ...prev,
                                [file.temp_file_id]: {
                                  ...prev[file.temp_file_id],
                                  document_group_id: gid,
                                },
                              }))
                            }
                          />
                        )}
                      </>
                    )}
                  </Space>
                </Card>
              )
            })}
          </Space>
          <Space style={{ marginTop: 16 }}>
            <Button type="primary" loading={busy} onClick={onResolve}>
              등록 완료
            </Button>
            <Button disabled={busy} onClick={() => setStep(3)}>
              이전
            </Button>
          </Space>
        </Card>
      )}
    </Space>
  )
}
