/**
 * Run: npx --yes tsx --tsconfig tsconfig.app.json scripts/check-employment-enddate-conflict.ts
 */
import {
  buildEmploymentEndDateModifiedDecision,
  employmentEndDateSelectionError,
  extractEmploymentEndDateConflictCandidates,
  isEmploymentEndDateConflictReview,
  isParseableEmploymentDate,
  parseEmploymentPartialDate,
  validateChosenEmploymentEndDate,
} from '../src/api/employmentEndDateConflict'

function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) throw new Error(msg)
}

const conflict = '2013.01.02 / 2014.09.30'
const candidates = extractEmploymentEndDateConflictCandidates(conflict)
assert(
  candidates != null &&
    candidates.length === 2 &&
    candidates[0] === '2013.01.02' &&
    candidates[1] === '2014.09.30',
  'normal conflict splits into 2 candidates',
)

assert(
  extractEmploymentEndDateConflictCandidates('2013.01.02 /  / 2014.09.30') ==
    null,
  'empty middle candidate → fallback',
)
assert(
  extractEmploymentEndDateConflictCandidates('2013.01.02 / ') == null,
  'trailing empty candidate → fallback',
)
assert(
  extractEmploymentEndDateConflictCandidates(' / 2014.09.30') == null,
  'leading empty candidate → fallback',
)
assert(
  extractEmploymentEndDateConflictCandidates('모름 / 2014.09.30') == null,
  'non-date token → fallback',
)

// Internal spaces: dotted forms must match Confirm (reject), Korean may keep spaces.
assert(
  !isParseableEmploymentDate('2014. 09. 30'),
  'dotted date with internal spaces rejected like Confirm',
)
assert(
  !isParseableEmploymentDate('2014- 09- 30'),
  'ISO-like date with internal spaces rejected',
)
assert(
  parseEmploymentPartialDate('2014.09.30')?.year === 2014 &&
    parseEmploymentPartialDate('2014.09.30')?.month === 9 &&
    parseEmploymentPartialDate('2014.09.30')?.day === 30,
  'compact dotted date accepted',
)
assert(
  parseEmploymentPartialDate('2014년 8월')?.year === 2014 &&
    parseEmploymentPartialDate('2014년 8월')?.month === 8,
  'Korean date with internal spaces accepted',
)

assert(
  validateChosenEmploymentEndDate({
    endDate: '2014.02.30',
    startDate: '2012.12.03',
  }).ok === false,
  '2014.02.30 blocked',
)
assert(
  validateChosenEmploymentEndDate({
    endDate: '2012.01.01',
    startDate: '2012.12.03',
  }).ok === false,
  'end < start blocked',
)
assert(
  validateChosenEmploymentEndDate({
    endDate: conflict,
    startDate: '2012.12.03',
  }).ok === false,
  'conflict string blocked',
)
assert(
  validateChosenEmploymentEndDate({
    endDate: '2014.09.30',
    startDate: '2012.12.03',
  }).ok === true,
  'single valid end accepted',
)

assert(
  employmentEndDateSelectionError({
    selected: null,
    customDate: '',
    startDate: '2012.12.03',
  }) != null,
  'unselected blocked',
)

const conflictDiff = {
  entity_type: 'EMPLOYMENT',
  change_type: 'REVIEW',
  field_name: null as string | null,
  candidate_path: 'employment_history[3]',
  new_value: {
    company_name: '(주)피앤드티컨설팅',
    start_date: '2012.12.03',
    end_date: conflict,
    department: '컨설팅',
    title: '컨설턴트',
    source_refs: [{ quote_text: 'x' }],
    confidence: 0.9,
  },
}
assert(
  isEmploymentEndDateConflictReview(conflictDiff),
  'normal conflict REVIEW uses dedicated UX',
)

const decided = buildEmploymentEndDateModifiedDecision(
  conflictDiff,
  '2014.09.30',
)
assert(decided.end_date === '2014.09.30', 'chosen end_date applied')
assert(decided.company_name === '(주)피앤드티컨설팅', 'company preserved')
assert(decided.department === '컨설팅', 'department preserved')
assert(decided.title === '컨설턴트', 'title preserved')
assert(decided.start_date === '2012.12.03', 'start_date preserved')
assert(!('source_refs' in decided), 'source_refs stripped')
assert(!('confidence' in decided), 'confidence stripped')

assert(
  !isEmploymentEndDateConflictReview({
    ...conflictDiff,
    new_value: { ...conflictDiff.new_value, end_date: '2014.09.30' },
  }),
  'single end_date REVIEW is not conflict UX',
)
assert(
  !isEmploymentEndDateConflictReview({
    entity_type: 'EMPLOYMENT',
    change_type: 'NEW',
    field_name: null,
    candidate_path: 'employment_history[0]',
    new_value: conflictDiff.new_value,
  }),
  'NEW employment not treated as conflict UX',
)
assert(
  !isEmploymentEndDateConflictReview({
    entity_type: 'JOB',
    change_type: 'REVIEW',
    field_name: null,
    candidate_path: 'jobs[0]',
    new_value: { code: null },
  }),
  'other entity REVIEW unaffected',
)

console.log('employmentEndDateConflict checks: OK')
