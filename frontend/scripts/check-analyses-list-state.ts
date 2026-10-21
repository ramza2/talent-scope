/**
 * Regression checks for analyses list URL state helpers.
 * Run from frontend/: npx --yes tsx --tsconfig tsconfig.app.json scripts/check-analyses-list-state.ts
 */
import {
  ADMIN_ANALYSES_DEFAULTS,
  ANALYSES_QUEUE_DEFAULTS,
  buildAnalysisDetailPath,
  parseAnalysesListFilters,
  readAnalysesReturnTo,
  sanitizeAnalysesReturnTo,
  toAnalysesListSearchParams,
} from '../src/pages/analysesListState'

function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) throw new Error(msg)
}

assert(
  JSON.stringify(
    parseAnalysesListFilters(new URLSearchParams(), ANALYSES_QUEUE_DEFAULTS),
  ) === JSON.stringify(ANALYSES_QUEUE_DEFAULTS),
  'queue defaults',
)
assert(
  JSON.stringify(
    parseAnalysesListFilters(new URLSearchParams(), ADMIN_ANALYSES_DEFAULTS),
  ) === JSON.stringify(ADMIN_ANALYSES_DEFAULTS),
  'admin defaults',
)

{
  const filters = parseAnalysesListFilters(
    new URLSearchParams('status=CANCELLED&page=2&page_size=50'),
    ANALYSES_QUEUE_DEFAULTS,
  )
  assert(filters.status === 'CANCELLED', 'cancelled status')
  assert(filters.page === 2, 'page 2')
  assert(filters.page_size === 50, 'page size 50')
  const roundTrip = toAnalysesListSearchParams(filters)
  assert(roundTrip.get('status') === 'CANCELLED', 'roundtrip status')
  assert(roundTrip.get('page') === '2', 'roundtrip page')
  assert(roundTrip.get('page_size') === '50', 'roundtrip page_size')
}

{
  const filters = parseAnalysesListFilters(
    new URLSearchParams('status=CONFIRMED&page=3&page_size=20'),
    ANALYSES_QUEUE_DEFAULTS,
  )
  assert(filters.status === 'CONFIRMED', 'confirmed status')
}

{
  const filters = parseAnalysesListFilters(
    new URLSearchParams('status=FAILED&page=1&page_size=20'),
    ADMIN_ANALYSES_DEFAULTS,
  )
  assert(filters.status === 'FAILED', 'admin failed status')
}

{
  const filters = parseAnalysesListFilters(
    new URLSearchParams('status=NOPE&page=0&page_size=999'),
    ANALYSES_QUEUE_DEFAULTS,
  )
  assert(filters.status === 'REVIEWING', 'invalid status → default')
  assert(filters.page === 1, 'invalid page → default')
  assert(filters.page_size === 20, 'invalid page_size → default')
}

assert(
  sanitizeAnalysesReturnTo('/analyses?status=FAILED&page=2&page_size=50') ===
    '/analyses?status=FAILED&page=2&page_size=50',
  'analyses return',
)
assert(
  sanitizeAnalysesReturnTo(
    '/admin/analyses?status=CONFIRMED&page=1&page_size=20',
  ) === '/admin/analyses?status=CONFIRMED&page=1&page_size=20',
  'admin return',
)
assert(sanitizeAnalysesReturnTo('/analyses/abc') === '/analyses', 'detail rejected')
assert(
  sanitizeAnalysesReturnTo('https://evil.example/') === '/analyses',
  'absolute rejected',
)
assert(sanitizeAnalysesReturnTo('//evil.example') === '/analyses', 'proto-rel rejected')
assert(sanitizeAnalysesReturnTo('/people') === '/analyses', 'other path rejected')

{
  const path = buildAnalysisDetailPath(
    'id-1',
    '/admin/analyses?status=FAILED&page=2&page_size=50',
  )
  const u = new URL(path, 'http://local.test')
  assert(u.pathname === '/analyses/id-1', 'detail path')
  assert(
    u.searchParams.get('from') ===
      '/admin/analyses?status=FAILED&page=2&page_size=50',
    'from query preserved',
  )
  assert(
    readAnalysesReturnTo(u.searchParams) ===
      '/admin/analyses?status=FAILED&page=2&page_size=50',
    'read from query',
  )
}

console.log('analysesListState checks: OK')
