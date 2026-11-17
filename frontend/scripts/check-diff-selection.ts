/** Run: npx tsx --tsconfig tsconfig.app.json scripts/check-diff-selection.ts */
import {
  allSelectablePendingSelected,
  isSelectablePendingDiff,
  selectablePendingDiffIds,
} from '../src/api/diffSelection'

function check(condition: unknown, label: string): asserts condition {
  if (!condition) throw new Error(label)
}

const rows = [
  { id: '1', review_status: 'PENDING', change_type: 'NEW' },
  { id: '2', review_status: 'PENDING', change_type: 'SAME' },
  { id: '3', review_status: 'ACCEPTED', change_type: 'NEW' },
  { id: '4', review_status: 'PENDING', change_type: 'UPDATE' },
  { id: '5', review_status: 'MERGED', change_type: 'REVIEW' },
  { id: '6', review_status: 'PENDING', change_type: 'CONFLICT' },
  { id: '7', review_status: 'PENDING', change_type: 'REVIEW' },
  { id: '4', review_status: 'PENDING', change_type: 'UPDATE' }, // dup
]

check(isSelectablePendingDiff(rows[0]), 'pending NEW selectable')
check(!isSelectablePendingDiff(rows[1]), 'SAME excluded')
check(!isSelectablePendingDiff(rows[2]), 'ACCEPTED excluded')
check(!isSelectablePendingDiff(rows[4]), 'MERGED excluded')

const ids = selectablePendingDiffIds(rows)
check(ids.join(',') === '1,4,6,7', `pending ids across types: ${ids.join(',')}`)
check(selectablePendingDiffIds([]).length === 0, 'empty list')
check(selectablePendingDiffIds(null).length === 0, 'null list')

// Simulate >50 pending rows (cross-page).
const many = Array.from({ length: 63 }, (_, i) => ({
  id: `p${i}`,
  review_status: i % 10 === 0 ? 'ACCEPTED' : 'PENDING',
  change_type: i % 17 === 0 ? 'SAME' : 'NEW',
}))
const manyIds = selectablePendingDiffIds(many)
check(manyIds.length > 50, `cross-page count ${manyIds.length}`)
check(
  manyIds.every((id) => {
    const row = many.find((r) => r.id === id)!
    return row.review_status === 'PENDING' && row.change_type !== 'SAME'
  }),
  'only pending non-SAME',
)

check(!allSelectablePendingSelected(rows, ['1']), 'partial not all')
check(allSelectablePendingSelected(rows, ['1', '4', '6', '7', 'x']), 'all pending selected')
check(!allSelectablePendingSelected(rows, []), 'empty selection')

console.log('diffSelection checks: OK')
