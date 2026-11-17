/** Run: npx tsx --tsconfig tsconfig.app.json scripts/check-confirm-policy.ts */
import {
  isSourceFirstPromptVersion,
  shouldSkipInvalidValuesOnConfirm,
  summarizeReviewDecisions,
} from '../src/api/confirmPolicy'
import { canConfirmAnalysis } from '../src/api/analyses'

function check(condition: unknown, label: string): asserts condition {
  if (!condition) throw new Error(label)
}

check(!isSourceFirstPromptVersion(null), 'null not source-first')
check(!isSourceFirstPromptVersion(''), 'empty not source-first')
check(!isSourceFirstPromptVersion('profile-extract-v25'), 'v25 not source-first')
check(
  isSourceFirstPromptVersion('profile-source-first-v30-native-direct'),
  'v30 source-first',
)
check(
  isSourceFirstPromptVersion('profile-source-first-v31-native-first'),
  'v31 source-first',
)
check(
  isSourceFirstPromptVersion('profile-source-first-v32-core-presplit'),
  'v32 source-first',
)
check(
  isSourceFirstPromptVersion('profile-source-first-v99-future'),
  'future prefix source-first',
)

check(shouldSkipInvalidValuesOnConfirm(null) === true, 'null skips invalid')
check(shouldSkipInvalidValuesOnConfirm('') === true, 'empty skips invalid')
check(
  shouldSkipInvalidValuesOnConfirm('profile-extract-v25') === true,
  'v25 skips invalid',
)
check(
  shouldSkipInvalidValuesOnConfirm('profile-source-first-v30-native-direct') ===
    false,
  'v30 no skip',
)
check(
  shouldSkipInvalidValuesOnConfirm('profile-source-first-v31-native-first') ===
    false,
  'v31 no skip',
)
check(
  shouldSkipInvalidValuesOnConfirm('profile-source-first-v32-core-presplit') ===
    false,
  'v32 no skip',
)

const summary = summarizeReviewDecisions([
  { review_status: 'ACCEPTED', change_type: 'NEW' },
  { review_status: 'MODIFIED', change_type: 'UPDATE' },
  { review_status: 'MERGED', change_type: 'REVIEW' },
  { review_status: 'REJECTED', change_type: 'NEW' },
  { review_status: 'PENDING', change_type: 'NEW' },
  { review_status: 'PENDING', change_type: 'SAME' },
  { review_status: 'ACCEPTED', change_type: 'SAME' },
])
check(summary.accepted === 1, `accepted=${summary.accepted}`)
check(summary.modified === 1, `modified=${summary.modified}`)
check(summary.merged === 1, `merged=${summary.merged}`)
check(summary.rejected === 1, `rejected=${summary.rejected}`)
check(summary.pendingActionable === 1, `pending=${summary.pendingActionable}`)
check(summary.same === 2, `same=${summary.same}`)

check(
  canConfirmAnalysis({
    status: 'REVIEWING',
    diffsQuerySuccess: true,
    totalDiffs: 5,
    pendingActionable: 0,
    baseProfileVersion: 1,
  }),
  'confirm allowed when no pending',
)
check(
  !canConfirmAnalysis({
    status: 'REVIEWING',
    diffsQuerySuccess: true,
    totalDiffs: 5,
    pendingActionable: 61,
    baseProfileVersion: 1,
  }),
  'confirm blocked when pending',
)
check(
  !canConfirmAnalysis({
    status: 'REVIEWING',
    diffsQuerySuccess: false,
    totalDiffs: 5,
    pendingActionable: 0,
    baseProfileVersion: 1,
  }),
  'confirm blocked while diffs loading/failed',
)

console.log('confirmPolicy checks: OK')
