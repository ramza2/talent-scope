/**
 * Run: npx --yes tsx --tsconfig tsconfig.app.json scripts/check-diff-evidence.ts
 */
import {
  diffHasOriginalEvidence,
  isNonEmptyEvidenceId,
  isNonEmptyQuoteText,
  isRootJobOrExpNew,
  isUngroundedNewDiff,
  isUngroundedRootJobOrExpNew,
} from '../src/api/diffEvidence'
import type { DiffItem } from '../src/api/analyses'

function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) throw new Error(msg)
}

assert(isNonEmptyQuoteText('원문'), 'string quote ok')
assert(!isNonEmptyQuoteText('   '), 'whitespace quote rejected')
assert(!isNonEmptyQuoteText(123), 'number quote rejected')
assert(!isNonEmptyQuoteText({ text: 'x' }), 'object quote rejected')
assert(!isNonEmptyQuoteText(null), 'null quote rejected')
assert(isNonEmptyEvidenceId('ev-1'), 'string id ok')
assert(!isNonEmptyEvidenceId(''), 'empty id rejected')
assert(!isNonEmptyEvidenceId('  '), 'whitespace id rejected')
assert(!isNonEmptyEvidenceId(42), 'number id rejected')

const ungroundedExp = {
  id: '1',
  entity_type: 'EXP',
  candidate_path: 'expertise[0]',
  change_type: 'NEW',
  review_status: 'PENDING',
  new_value: { code: 'EXP-RAG', source_refs: [] },
  evidence: [],
} as DiffItem

const ungroundedJobMetaOnly = {
  id: '2',
  entity_type: 'JOB',
  candidate_path: 'jobs[0]',
  change_type: 'NEW',
  review_status: 'PENDING',
  new_value: {
    code: 'JOB-DEV-GEN',
    source_refs: [{ document_id: 'd', page_no: 1 }],
  },
  evidence: [],
} as DiffItem

const grounded = {
  id: '3',
  entity_type: 'JOB',
  candidate_path: 'jobs[1]',
  change_type: 'NEW',
  review_status: 'PENDING',
  new_value: {
    code: 'JOB-MGT-PM',
    source_refs: [{ quote_text: 'PM 역할' }],
  },
  evidence: [],
} as DiffItem

const projectRelation = {
  id: '4',
  entity_type: 'JOB',
  candidate_path: 'projects[0].jobs[0]',
  field_name: 'jobs',
  change_type: 'NEW',
  review_status: 'PENDING',
  new_value: { code: 'JOB-X', source_refs: [] },
  evidence: [],
} as DiffItem

const projectRoot = {
  id: '5',
  entity_type: 'PROJECT',
  candidate_path: 'projects[0]',
  change_type: 'NEW',
  review_status: 'PENDING',
  new_value: { project_name: 'A', source_refs: [] },
  evidence: [],
} as DiffItem

assert(isRootJobOrExpNew(ungroundedExp), 'exp root new')
assert(isUngroundedRootJobOrExpNew(ungroundedExp), 'exp ungrounded')
assert(isUngroundedNewDiff(ungroundedExp), 'any new exp ungrounded')
assert(isUngroundedRootJobOrExpNew(ungroundedJobMetaOnly), 'job meta-only ungrounded')
assert(!isUngroundedRootJobOrExpNew(grounded), 'grounded job')
assert(diffHasOriginalEvidence(grounded), 'grounded has evidence')
assert(!isRootJobOrExpNew(projectRelation), 'project relation not root')
assert(!isUngroundedRootJobOrExpNew(projectRelation), 'project relation is not a root JOB/EXP')
assert(!isUngroundedRootJobOrExpNew(projectRoot), 'project root not root job/exp')
assert(isUngroundedNewDiff(projectRoot), 'project new without evidence blocked')
assert(isUngroundedNewDiff(projectRelation), 'project relation NEW without evidence blocked')

const profileScalar = {
  id: '6',
  entity_type: 'PROFILE',
  candidate_path: 'profile.birth_year',
  field_name: 'birth_year',
  change_type: 'NEW',
  review_status: 'PENDING',
  new_value: 2007,
  evidence: [],
} as DiffItem
const educationNoRefs = {
  id: '7',
  entity_type: 'EDUCATION',
  candidate_path: 'education[1]',
  change_type: 'NEW',
  review_status: 'PENDING',
  new_value: { school_name: 'OO대학교', source_refs: [] },
  evidence: [],
} as DiffItem
assert(isUngroundedNewDiff(profileScalar), 'profile scalar NEW without evidence')
assert(isUngroundedNewDiff(educationNoRefs), 'education NEW without evidence')
assert(!isUngroundedNewDiff(grounded), 'grounded NEW can bulk accept')
assert(
  !isUngroundedNewDiff({ ...profileScalar, change_type: 'UPDATE' }),
  'UPDATE without evidence unaffected',
)

const linkedEvidence = {
  ...ungroundedExp,
  evidence: [{ id: 'ev-1', quote_text: null }],
} as DiffItem
assert(diffHasOriginalEvidence(linkedEvidence), 'linked evidence id counts')
assert(!isUngroundedRootJobOrExpNew(linkedEvidence), 'linked evidence grounded')
assert(!isUngroundedNewDiff(linkedEvidence), 'linked evidence can bulk accept')

const badQuoteRefs = {
  ...ungroundedJobMetaOnly,
  new_value: {
    code: 'JOB-DEV-GEN',
    source_refs: [
      { quote_text: 123 },
      { quote_text: { t: 'x' } },
      { quote_text: '   ' },
    ],
  },
} as DiffItem
assert(
  isUngroundedRootJobOrExpNew(badQuoteRefs),
  'non-string/whitespace quote_text ungrounded',
)

console.log('diffEvidence checks: OK')
