import { Button, Typography } from 'antd'

import type { DocumentBrief } from '@/api/analyses'
import { documentPreviewUrl } from '@/api/documents'
import type { PptxSourceLocation } from '@/api/projectReviewDisplay'

type ProjectPptxSourceLocationsProps = {
  locations: PptxSourceLocation[]
  documents: DocumentBrief[]
}

function documentLabel(
  documentId: string,
  documents: DocumentBrief[],
): string {
  const match = documents.find((doc) => doc.id === documentId)
  if (!match) return documentId
  return match.original_filename || match.id
}

/** Separate from citation Evidence — verified PPTX table-row coordinates only. */
export function ProjectPptxSourceLocations({
  locations,
  documents,
}: ProjectPptxSourceLocationsProps) {
  if (!locations.length) return null

  return (
    <div style={{ marginTop: 8 }}>
      <Typography.Text strong style={{ display: 'block', marginBottom: 2 }}>
        원본 PPTX 위치
      </Typography.Text>
      {locations.map((loc) => {
        const key = [
          loc.document_id,
          loc.page_no,
          loc.table_index,
          loc.row_index,
          loc.row_id,
        ].join('|')
        const title = documentLabel(loc.document_id, documents)
        return (
          <div key={key} style={{ marginBottom: 4 }}>
            <Typography.Text
              ellipsis
              title={title}
              style={{ display: 'block', maxWidth: '100%' }}
            >
              {title}
            </Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {`슬라이드 ${loc.page_no} · 표 ${loc.table_index} · 행 ${loc.row_index}`}
            </Typography.Text>
            <div>
              <Button
                type="link"
                size="small"
                style={{ paddingInline: 0, height: 'auto' }}
                href={documentPreviewUrl(loc.document_id, loc.page_no)}
                target="_blank"
                rel="noopener noreferrer"
              >
                {`문서 보기 p.${loc.page_no}`}
              </Button>
            </div>
          </div>
        )
      })}
    </div>
  )
}
