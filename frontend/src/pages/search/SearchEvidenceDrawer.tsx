import { Alert, Button, Descriptions, Drawer, Empty, Space, Spin, Typography } from 'antd'
import { FileTextOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'

import { documentPreviewUrl } from '@/api/documents'
import { apiErrorMessage } from '@/api/errors'
import { getEvidence } from '@/api/evidence'

type Props = {
  evidenceId: string | null
  open: boolean
  onClose: () => void
}

export function SearchEvidenceDrawer({ evidenceId, open, onClose }: Props) {
  const query = useQuery({
    queryKey: ['evidence', evidenceId],
    queryFn: () => getEvidence(evidenceId!),
    enabled: open && Boolean(evidenceId),
  })

  const evidence = query.data?.data
  const previewHref =
    evidence?.document?.id != null
      ? evidence.page_no != null
        ? `${documentPreviewUrl(evidence.document.id)}#page=${evidence.page_no}`
        : documentPreviewUrl(evidence.document.id)
      : null

  return (
    <Drawer title="근거 상세" width={520} open={open} onClose={onClose} destroyOnHidden>
      {query.isLoading ? <Spin /> : null}
      {query.isError ? (
        <Alert
          type="error"
          showIcon
          message={apiErrorMessage(query.error, '근거 정보를 불러오지 못했습니다.')}
        />
      ) : null}
      {!query.isLoading && !query.isError && !evidence ? (
        <Empty description="근거 정보가 없습니다." />
      ) : null}
      {evidence ? (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label="문서 제목">
              {evidence.document?.title || '—'}
            </Descriptions.Item>
            <Descriptions.Item label="원본 파일명">
              {evidence.document?.original_filename || '—'}
            </Descriptions.Item>
            <Descriptions.Item label="버전">
              {evidence.document?.version_no != null
                ? `v${evidence.document.version_no}`
                : '—'}
            </Descriptions.Item>
            <Descriptions.Item label="페이지">
              {evidence.page_no != null ? evidence.page_no : '—'}
            </Descriptions.Item>
            <Descriptions.Item label="추출 방식">
              {evidence.extraction_method || '—'}
            </Descriptions.Item>
            <Descriptions.Item label="인용 문구">
              <Typography.Paragraph style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}>
                {evidence.quote_text || '—'}
              </Typography.Paragraph>
            </Descriptions.Item>
            <Descriptions.Item label="근거 대상">
              {(evidence.links ?? []).length === 0
                ? '—'
                : evidence.links.map((link, idx) => (
                    <div key={`${link.target_type}-${idx}`}>
                      {link.target_type}
                      {link.field_name ? ` · ${link.field_name}` : ''}
                      {link.relation_type ? ` (${link.relation_type})` : ''}
                    </div>
                  ))}
            </Descriptions.Item>
          </Descriptions>
          {previewHref ? (
            <Button
              icon={<FileTextOutlined />}
              href={previewHref}
              target="_blank"
              rel="noopener noreferrer"
            >
              원문 보기
            </Button>
          ) : null}
        </Space>
      ) : null}
    </Drawer>
  )
}
