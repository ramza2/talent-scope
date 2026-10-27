import type { DocumentBrief, EvidenceLite } from '@/api/analyses'

export type EvidenceDocGroup = {
  key: string
  documentId: string | null
  filename: string
  typeLabel: string | null
  known: boolean
  items: EvidenceLite[]
}

/** Group evidence by document_id. Missing / unknown docs stay visible as separate groups. */
export function groupEvidenceByDocument(
  items: EvidenceLite[],
  documents: DocumentBrief[],
): EvidenceDocGroup[] {
  const docById = new Map(documents.map((d) => [d.id, d]))
  const groups = new Map<string, EvidenceDocGroup>()
  const order: string[] = []

  for (const ev of items) {
    const rawId = ev.document_id?.trim() || ''
    let key: string
    if (!rawId) {
      key = '__missing__'
    } else if (docById.has(rawId)) {
      key = rawId
    } else {
      key = `__unknown__:${rawId}`
    }

    let group = groups.get(key)
    if (!group) {
      if (key === '__missing__') {
        group = {
          key,
          documentId: null,
          filename: '문서 미지정',
          typeLabel: null,
          known: false,
          items: [],
        }
      } else if (key.startsWith('__unknown__:')) {
        group = {
          key,
          documentId: rawId,
          filename: '알 수 없는 문서',
          typeLabel: null,
          known: false,
          items: [],
        }
      } else {
        const doc = docById.get(rawId)!
        group = {
          key,
          documentId: rawId,
          filename: doc.original_filename,
          typeLabel: doc.document_type_name || doc.document_type_code || null,
          known: true,
          items: [],
        }
      }
      groups.set(key, group)
      order.push(key)
    }
    // Preserve every evidence row (no quote dedupe / merge).
    group.items.push(ev)
  }

  return order.map((k) => groups.get(k)!)
}

export function groupTitle(group: EvidenceDocGroup): string {
  const type = group.typeLabel ? ` (${group.typeLabel})` : ''
  return `${group.filename}${type}`
}

/** Per-group visible counts when collapsing to `limit` evidence lines total. */
export function visibleCountsForGroups(
  groups: EvidenceDocGroup[],
  limit: number | null,
): number[] {
  if (limit == null) {
    return groups.map((g) => g.items.length)
  }
  let left = limit
  return groups.map((g) => {
    const take = Math.max(0, Math.min(g.items.length, left))
    left -= take
    return take
  })
}
