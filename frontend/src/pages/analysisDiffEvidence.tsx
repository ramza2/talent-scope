import { useMemo, useState } from 'react'
import { Button, Typography } from 'antd'

import type { DocumentBrief, EvidenceLite } from '@/api/analyses'
import { documentPreviewUrl } from '@/api/documents'
import {
  groupEvidenceByDocument,
  groupTitle,
  visibleCountsForGroups,
} from '@/pages/analysisDiffEvidenceGroups'

const PREVIEW_LIMIT = 3

type DiffEvidenceGroupsProps = {
  evidence: EvidenceLite[]
  documents: DocumentBrief[]
  rowId: string
  onOpenEvidence: (item: EvidenceLite) => void
}

export function DiffEvidenceGroups({
  evidence,
  documents,
  rowId,
  onOpenEvidence,
}: DiffEvidenceGroupsProps) {
  const [expanded, setExpanded] = useState(false)
  const groups = useMemo(
    () => groupEvidenceByDocument(evidence, documents),
    [evidence, documents],
  )
  const total = evidence.length
  const visibleCounts = useMemo(
    () =>
      visibleCountsForGroups(
        groups,
        expanded || total <= PREVIEW_LIMIT ? null : PREVIEW_LIMIT,
      ),
    [groups, expanded, total],
  )

  if (!total) {
    return <Typography.Text type="secondary">—</Typography.Text>
  }

  return (
    <div style={{ maxWidth: 280, minWidth: 0 }}>
      {groups.map((group, groupIndex) => {
        const take = visibleCounts[groupIndex] ?? 0
        const visible = group.items.slice(0, take)
        const hiddenInGroup = group.items.length - visible.length
        return (
          <div key={group.key} style={{ marginBottom: 6 }}>
            <Typography.Text
              strong
              ellipsis
              title={groupTitle(group)}
              style={{ display: 'block', maxWidth: '100%' }}
            >
              {groupTitle(group)}
              <Typography.Text type="secondary" style={{ fontWeight: 400 }}>
                {` · ${group.items.length}건`}
              </Typography.Text>
            </Typography.Text>
            {visible.map((ev, idx) => {
              const key = ev.id || `${rowId}-${group.key}-${idx}`
              const quote = ev.quote_text || '근거'
              const pagePrefix = ev.page_no != null ? `p.${ev.page_no} ` : ''
              return (
                <div key={key} style={{ marginTop: 2 }}>
                  <Typography.Text
                    ellipsis
                    style={{ maxWidth: '100%', display: 'block' }}
                    title={`${pagePrefix}${quote}`}
                  >
                    {pagePrefix}
                    {quote}
                  </Typography.Text>
                  {ev.id ? (
                    <Button
                      type="link"
                      size="small"
                      style={{ paddingInline: 0, height: 'auto' }}
                      onClick={() => onOpenEvidence(ev)}
                    >
                      근거 보기
                    </Button>
                  ) : ev.document_id ? (
                    <Button
                      type="link"
                      size="small"
                      style={{ paddingInline: 0, height: 'auto' }}
                      href={documentPreviewUrl(ev.document_id)}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {ev.page_no != null
                        ? `문서 보기 p.${ev.page_no}`
                        : '문서 보기'}
                    </Button>
                  ) : null}
                </div>
              )
            })}
            {!expanded && hiddenInGroup > 0 ? (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {`외 ${hiddenInGroup}건`}
              </Typography.Text>
            ) : null}
          </div>
        )
      })}
      {total > PREVIEW_LIMIT ? (
        <Button
          type="link"
          size="small"
          style={{ paddingInline: 0, height: 'auto' }}
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? '접기' : `외 ${total - PREVIEW_LIMIT}건 펼치기`}
        </Button>
      ) : null}
    </div>
  )
}
