import { useRef, useState } from 'react'
import { Alert, Button, Modal, Space, Typography, message } from 'antd'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  checkAnalysisOrphan,
  recoverAnalysisOrphan,
  type AnalysisOrphanCheck,
} from '@/api/analysisRecovery'
import { apiErrorMessage } from '@/api/errors'
import { currentOrphanDiagnosis, canRequestOrphanRecovery } from '@/api/analysisRecoveryPolicy'

/** Mounted only for PROCESSING, keyed by run ID and the latest DB update. */
export function AnalysisRecoveryPanel({
  analysisId,
  updatedAt,
}: {
  analysisId: string
  updatedAt: string
}) {
  const queryClient = useQueryClient()
  const [diagnosis, setDiagnosis] = useState<AnalysisOrphanCheck | null>(null)
  const [checkedAt, setCheckedAt] = useState<number | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const busy = useRef(false)

  const refreshAnalyses = () =>
    queryClient.invalidateQueries({ queryKey: ['analyses'] })

  const checkMutation = useMutation({
    mutationFn: () => checkAnalysisOrphan(analysisId),
    retry: false,
    onSuccess: ({ data }) => {
      if (data.analysis_id !== analysisId) {
        message.error('분석 상태 확인 응답이 일치하지 않습니다.')
        return
      }
      setDiagnosis(data)
      setCheckedAt(Date.now())
      if (data.status !== 'PROCESSING' || data.updated_at !== updatedAt) {
        void refreshAnalyses()
      }
    },
    onError: (error) => {
      message.error(apiErrorMessage(error, '실행 상태를 확인할 수 없습니다.'))
    },
    onSettled: () => {
      busy.current = false
    },
  })

  const recoverMutation = useMutation({
    mutationFn: () => recoverAnalysisOrphan(analysisId),
    retry: false,
    onSuccess: ({ data }) => {
      if (data.analysis_id === analysisId && data.status === 'FAILED' && data.recovered === true) {
        message.success('중단된 분석을 실패 상태로 정리했습니다.')
      } else {
        message.warning('복구 결과를 확인할 수 없습니다. 분석 상태를 새로 확인해 주세요.')
      }
    },
    onError: (error) => {
      message.error(apiErrorMessage(error, '복구하지 못했습니다. 실행 상태를 다시 확인해 주세요.'))
    },
    onSettled: async () => {
      setConfirmOpen(false)
      setDiagnosis(null)
      setCheckedAt(null)
      try {
        await refreshAnalyses()
      } finally {
        busy.current = false
      }
    },
  })

  const currentDiagnosis = currentOrphanDiagnosis(diagnosis, analysisId, updatedAt)
  const canRecover = canRequestOrphanRecovery(currentDiagnosis)
  const pending = checkMutation.isPending || recoverMutation.isPending
  let explanation = '완료될 때까지 자동 갱신합니다. 분석이 오래 지속되면 실행 상태를 확인할 수 있습니다.'
  let kind: 'info' | 'warning' | 'error' = 'info'
  if (recoverMutation.isSuccess) {
    explanation = '복구 응답을 받았습니다. 최신 분석 상태를 불러오는 중입니다.'
  } else if (checkMutation.isError) {
    kind = 'warning'
    explanation = '실행 상태 확인에 실패했습니다. 복구할 수 없습니다. 다시 확인해 주세요.'
  } else if (diagnosis && !currentDiagnosis) {
    kind = 'warning'
    explanation = '확인 중 분석 상태가 변경되었습니다. 최신 상태를 다시 확인해 주세요.'
  } else if (currentDiagnosis) {
    if (canRecover) {
      kind = 'warning'
      explanation = '실행·대기 중인 작업이 발견되지 않아 중단이 의심됩니다. 복구 요청 시 서버가 실행 상태를 다시 검사합니다.'
    } else if (currentDiagnosis.worker_task_state === 'ACTIVE') {
      explanation = '실행 중이거나 대기 중인 작업이 확인되었습니다. 복구할 수 없습니다.'
    } else if (currentDiagnosis.worker_task_state === 'NOT_CHECKED') {
      explanation = '아직 서버의 중단 의심 검사 조건을 충족하지 않았습니다. 자동 갱신을 기다려 주세요.'
    } else {
      kind = 'warning'
      explanation = 'Worker 실행 상태를 확실하게 확인할 수 없어 복구할 수 없습니다.'
    }
  }

  const requestCheck = () => {
    if (busy.current || pending || confirmOpen || recoverMutation.isSuccess) return
    busy.current = true
    setDiagnosis(null)
    setCheckedAt(null)
    checkMutation.mutate()
  }

  const submitRecovery = () => {
    if (!confirmOpen || !canRecover || busy.current || pending || recoverMutation.isSuccess) return
    busy.current = true
    recoverMutation.mutate()
  }

  return (
    <>
      <Alert
        type={kind}
        showIcon
        style={{ marginBottom: 16 }}
        message="분석이 PROCESSING 상태입니다."
        description={
          <Space direction="vertical" size="small" style={{ width: '100%' }}>
            <span>{explanation}</span>
            {checkedAt !== null ? (
              <Typography.Text type="secondary">
                확인 시각: {new Date(checkedAt).toLocaleString('ko-KR')}
              </Typography.Text>
            ) : null}
            <Space wrap>
              <Button
                loading={checkMutation.isPending}
                disabled={pending || confirmOpen || recoverMutation.isSuccess}
                onClick={requestCheck}
              >
                실행 상태 확인
              </Button>
              {canRecover && !recoverMutation.isSuccess ? (
                <Button
                  danger
                  loading={recoverMutation.isPending}
                  disabled={pending || confirmOpen}
                  onClick={() => setConfirmOpen(true)}
                >
                  중단 분석 복구
                </Button>
              ) : null}
            </Space>
          </Space>
        }
      />
      <Modal
        title="중단된 분석을 실패 상태로 정리할까요?"
        open={confirmOpen && canRecover}
        okText="복구"
        cancelText="취소"
        okButtonProps={{ danger: true, disabled: !canRecover || pending }}
        cancelButtonProps={{ disabled: recoverMutation.isPending }}
        confirmLoading={recoverMutation.isPending}
        closable={!recoverMutation.isPending}
        maskClosable={!recoverMutation.isPending}
        keyboard={!recoverMutation.isPending}
        onCancel={() => {
          if (!busy.current) setConfirmOpen(false)
        }}
        onOk={submitRecovery}
      >
        해당 분석 한 건만 FAILED로 정리합니다. 서버에서 실행 여부를 다시 확인하며,
        실제 실행 중이거나 확인할 수 없으면 중단합니다. 확정 프로필·원본 문서는 변경하지
        않으며 AI 분석을 자동으로 다시 실행하지 않습니다.
      </Modal>
    </>
  )
}
