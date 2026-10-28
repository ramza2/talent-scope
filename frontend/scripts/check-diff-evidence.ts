/**
 * Run: npx --yes tsx --tsconfig tsconfig.app.json scripts/check-diff-evidence.ts
 */
import {
  diffHasOriginalEvidence,
  isNonEmptyEvidenceId,
  isNonEmptyQuoteText,
  isRootJobOrExpNew,
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
assert(isUngroundedRootJobOrExpNew(ungroundedJobMetaOnly), 'job meta-only ungrounded')
assert(!isUngroundedRootJobOrExpNew(grounded), 'grounded job')
assert(diffHasOriginalEvidence(grounded), 'grounded has evidence')
assert(!isRootJobOrExpNew(projectRelation), 'project relation not root')
assert(!isUngroundedRootJobOrExpNew(projectRelation), 'project relation not blocked')
assert(!isUngroundedRootJobOrExpNew(projectRoot), 'project root not blocked')

const linkedEvidence = {
  ...ungroundedExp,
  evidence: [{ id: 'ev-1', quote_text: null }],
} as DiffItem
assert(diffHasOriginalEvidence(linkedEvidence), 'linked evidence id counts')
assert(!isUngroundedRootJobOrExpNew(linkedEvidence), 'linked evidence grounded')

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
