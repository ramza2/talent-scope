/**
 * Regression checks for upload 413 / non-JSON error handling.
 * Run: npx --yes tsx --tsconfig tsconfig.app.json scripts/check-upload-413.ts
 */
import { ApiError } from '../src/api/client'
import { apiErrorMessage } from '../src/api/errors'
import {
  UPLOAD_PAYLOAD_TOO_LARGE_MESSAGE,
  buildUploadSessionFilesError,
  parseUploadResponseBody,
} from '../src/api/documents'

function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) throw new Error(msg)
}

assert(parseUploadResponseBody('') === null, 'empty → null')
assert(
  JSON.stringify(parseUploadResponseBody('{"data":[]}')) ===
    JSON.stringify({ data: [] }),
  'json ok',
)

{
  const html =
    '<html><head><title>413 Request Entity Too Large</title></head><body>413</body></html>'
  const body = parseUploadResponseBody(html) as { detail: string }
  assert(typeof body.detail === 'string', 'html → detail string')
  assert(body.detail.length <= 200, 'html detail truncated')
  assert(body.detail.startsWith('<html>'), 'keeps text without JSON.parse throw')
}

{
  const err = buildUploadSessionFilesError(413, '<html>413</html>')
  assert(err instanceof ApiError, '413 → ApiError')
  assert(err.status === 413, '413 status')
  assert(
    apiErrorMessage(err, '업로드에 실패했습니다.') ===
      UPLOAD_PAYLOAD_TOO_LARGE_MESSAGE,
    '413 shows Korean capacity message (no JSON.parse throw)',
  )
}

{
  const err = buildUploadSessionFilesError(
    400,
    JSON.stringify({ detail: '파일 형식이 지원되지 않습니다.', code: 'BAD' }),
  )
  assert(
    apiErrorMessage(err, 'fallback') === '파일 형식이 지원되지 않습니다.',
    'JSON API errors still surface detail',
  )
}

{
  const err = buildUploadSessionFilesError(502, '<html>Bad Gateway</html>')
  assert(err.status === 502, '502 status')
  assert(
    typeof (err.body as { detail?: unknown }).detail === 'string',
    'non-json error body is object with detail',
  )
}

console.log('upload-413 checks: OK')
