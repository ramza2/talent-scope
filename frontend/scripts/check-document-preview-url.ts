/** Run: npx tsx --tsconfig tsconfig.app.json scripts/check-document-preview-url.ts */
import { documentPreviewUrl } from '../src/api/documents'

function check(condition: unknown, label: string): asserts condition {
  if (!condition) throw new Error(label)
}

const docId = '11111111-1111-4111-8111-111111111111'
const base = `/api/v1/documents/${docId}/preview`

check(documentPreviewUrl(docId) === base, 'no page keeps base url')
check(documentPreviewUrl(docId, undefined) === base, 'undefined page keeps base')
check(documentPreviewUrl(docId, null) === base, 'null page keeps base')
check(documentPreviewUrl(docId, 1) === `${base}#page=1`, 'page 1 anchors')
check(documentPreviewUrl(docId, 12) === `${base}#page=12`, 'page 12 anchors')
check(documentPreviewUrl(docId, 0) === base, 'zero page ignored')
check(documentPreviewUrl(docId, -3) === base, 'negative page ignored')
check(documentPreviewUrl(docId, 1.5) === base, 'non-integer page ignored')
check(documentPreviewUrl(docId, Number.NaN) === base, 'NaN page ignored')

console.log('documentPreviewUrl checks: OK')
