import { useState } from 'react'
import { Button, Drawer, Empty, Space, Spin, Typography } from 'antd'
import { FileTextOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'

import { documentPreviewUrl } from '@/api/documents'
import { listEvidence, type EvidenceListItem } from '@/api/evidence'

type Props = {
  targetType: string
  targetId: string
  fieldName?: string
}

function previewHref(item: EvidenceListItem): string | null {
  if (!item.document?.id) return null
  const base = documentPreviewUrl(item.document.id)
  return item.page_no != null ? `${base}#page=${item.page_no}` : base
}

export function TargetEvidenceButton({ targetType, targetId, fieldName }: Props) {
  const [open, setOpen] = useState(false)
  const query = useQuery({
    queryKey: ['evidence', 'by-target', targetType, targetId, fieldName ?? null],
    queryFn: () =>
      listEvidence({
        target_type: targetType,
        target_id: targetId,
        field_name: fieldName,
      }),
    enabled: Boolean(targetType && targetId),
  })

  const items = query.data?.data ?? []
  if (!query.isSuccess || items.length === 0) {
    return null
  }

  return (
    <>
      <Button type="link" size="small" onClick={() => setOpen(true)} style={{ paddingInline: 4 }}>
        근거 {items.length}
      </Button>
      <Drawer
        title="원문 근거"
        width={520}
        open={open}
        onClose={() => setOpen(false)}
        destroyOnHidden
      >
        {query.isFetching ? <Spin /> : null}
        {!query.isFetching && items.length === 0 ? (
          <Empty description="근거 정보가 없습니다." />
        ) : null}
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          {items.map((item) => {
            const href = previewHref(item)
            const docLabel =
              item.document?.title || item.document?.original_filename || '문서'
            return (
              <div key={item.id} style={{ borderBottom: '1px solid #f0f0f0', paddingBottom: 12 }}>
                <Typography.Text strong>{docLabel}</Typography.Text>
                <div>
                  <Typography.Text type="secondary">
                    {item.document?.original_filename && item.document?.title
                      ? item.document.original_filename
                      : null}
                    {item.document?.version_no != null
                      ? ` · v${item.document.version_no}`
                      : ''}
                    {item.page_no != null ? ` · p.${item.page_no}` : ''}
                    {item.extraction_method ? ` · ${item.extraction_method}` : ''}
                  </Typography.Text>
                </div>
                <Typography.Paragraph
                  style={{ marginTop: 8, marginBottom: 8, whiteSpace: 'pre-wrap' }}
                >
                  {item.quote_text || '—'}
                </Typography.Paragraph>
                {href ? (
                  <Button
                    type="link"
                    size="small"
                    icon={<FileTextOutlined />}
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ paddingInline: 0 }}
                  >
                    원문 보기
                  </Button>
                ) : null}
              </div>
            )
          })}
        </Space>
      </Drawer>
    </>
  )
}
