import { apiFetch } from '@/api/client'

export type EvidenceDocumentBrief = {
  id: string
  title?: string | null
  original_filename?: string | null
  version_no?: number | null
}

export type EvidenceListItem = {
  id: string
  document: EvidenceDocumentBrief
  page_no?: number | null
  quote_text?: string | null
  bbox?: Record<string, unknown> | null
  char_start?: number | null
  char_end?: number | null
  extraction_method?: string | null
  relation_type: string
}

export type EvidenceLinkItem = {
  target_type: string
  target_id: string
  field_name?: string | null
  relation_type: string
}

export type EvidenceDetail = {
  id: string
  document: EvidenceDocumentBrief
  page_no?: number | null
  quote_text?: string | null
  bbox?: Record<string, unknown> | null
  char_start?: number | null
  char_end?: number | null
  extraction_method?: string | null
  created_at?: string | null
  links: EvidenceLinkItem[]
}

export function getEvidence(evidenceId: string) {
  return apiFetch<{ data: EvidenceDetail }>(`/evidence/${evidenceId}`)
}

export function listEvidence(params: {
  target_type: string
  target_id: string
  field_name?: string
}) {
  const qs = new URLSearchParams({
    target_type: params.target_type,
    target_id: params.target_id,
  })
  if (params.field_name) qs.set('field_name', params.field_name)
  return apiFetch<{ data: EvidenceListItem[] }>(`/evidence?${qs.toString()}`)
}
