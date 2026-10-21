/**
 * Run: npx --yes tsx --tsconfig tsconfig.app.json scripts/check-diff-value-display.ts
 */
import {
  formatDiffValueSummary,
  formatValue,
} from '../src/api/diffValueDisplay'

function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) throw new Error(msg)
}

const employment = {
  company_name: 'Alpha',
  start_date: '2020.07',
  end_date: '현재',
  title: 'Dev',
  source_refs: [{ quote_text: 'x' }],
  confidence: 0.9,
}

const summary = formatDiffValueSummary(employment)
assert(summary.includes('company_name: Alpha'), 'summary company')
assert(summary.includes('end_date: 현재'), 'summary end preserves AI original')
assert(!summary.includes('source_refs'), 'summary skips nested metadata')
assert(formatValue(employment).includes('source_refs'), 'full JSON keeps refs')

assert(formatDiffValueSummary(null) === '—', 'null')
assert(formatDiffValueSummary('현재') === '현재', 'scalar')

console.log('diffValueDisplay checks: OK')
