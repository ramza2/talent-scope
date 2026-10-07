/**
 * Focused behavior checks for per-file DOC_TYPE upload (no vitest / esbuild).
 * Mirrors promoteExistingPersonDocuments + mergeFileDocTypeState algorithms;
 * source contract tests lock the TypeScript implementation to the same shape.
 *
 * Run: node scripts/test-per-file-doc-type-upload.mjs
 */
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(__dirname, '..')
const documentsTs = fs.readFileSync(
  path.join(root, 'src/api/documents.ts'),
  'utf8',
)
const documentsTabTsx = fs.readFileSync(
  path.join(root, 'src/pages/people/DocumentsTab.tsx'),
  'utf8',
)

function suggestDocTypeFromFilename(filename) {
  const lower = filename.toLowerCase()
  const mapping = [
    [['이력서', 'resume', 'cv'], 'DOC-RESUME'],
    [['경력기술', 'career'], 'DOC-CAREER'],
    [['프로필', 'profile'], 'DOC-PROFILE'],
    [['자격', 'cert'], 'DOC-CERT'],
    [['kosa', '경력증명'], 'DOC-KOSA'],
    [['포트폴리오', 'portfolio'], 'DOC-PORTFOLIO'],
    [['학력', 'diploma', '졸업'], 'DOC-EDU'],
  ]
  for (const [keys, code] of mapping) {
    if (keys.some((k) => lower.includes(k))) return code
  }
  return undefined
}

function mergeFileDocTypeState(files, prev, activeCodes) {
  const next = {}
  for (const file of files) {
    const existing = prev[file.uid]
    if (existing?.source === 'manual') {
      next[file.uid] = existing
      continue
    }
    const name = file.name || file.originFileObj?.name || ''
    const suggested = suggestDocTypeFromFilename(name)
    if (suggested && activeCodes.has(suggested)) {
      next[file.uid] = { documentTypeCode: suggested, source: 'suggested' }
    } else {
      next[file.uid] = { documentTypeCode: undefined, source: null }
    }
  }
  return next
}

/** Same flow as frontend/src/api/documents.ts promoteExistingPersonDocuments. */
async function promoteExistingPersonDocuments(opts, deps) {
  const session = await deps.createUploadSession(opts.personId)
  const sessionId = session.data.id
  try {
    const files = opts.items.map((item) => item.file)
    const uploaded = await deps.uploadSessionFiles(sessionId, files)
    if (uploaded.data.length !== opts.items.length) {
      throw new Error(
        '업로드된 파일 수가 선택한 파일 수와 일치하지 않습니다.',
      )
    }
    for (let i = 0; i < uploaded.data.length; i += 1) {
      await deps.patchTempFile(
        sessionId,
        uploaded.data[i].temp_file_id,
        opts.items[i].documentTypeCode,
      )
    }
    return deps.resolveUploadSession(sessionId, {
      mode: 'LINK_EXISTING',
      person_id: opts.personId,
      document_resolution: uploaded.data.map((file, i) => ({
        temp_file_id: file.temp_file_id,
        mode: opts.mode ?? 'NEW_GROUP',
        document_type_code: opts.items[i].documentTypeCode,
        title: opts.items[i].title ?? file.original_filename,
      })),
    })
  } catch (error) {
    try {
      await deps.cancelUploadSession(sessionId)
    } catch {
      /* best-effort cleanup */
    }
    throw error
  }
}

// Source locked to the mirrored algorithms.
assert.match(documentsTs, /items:\s*PromoteExistingPersonDocumentItem\[\]/)
assert.match(documentsTs, /uploaded\.data\.length !== opts\.items\.length/)
assert.match(documentsTs, /opts\.items\[i\]\.documentTypeCode/)
assert.match(documentsTs, /opts\.items\[i\]\.title \?\? file\.original_filename/)
assert.match(documentsTs, /cancelUploadSession/)
assert.doesNotMatch(
  documentsTabTsx,
  /consensusDocType|effectiveDocType|docTypeSource/,
)
assert.match(documentsTabTsx, /fileDocTypes/)
assert.match(documentsTabTsx, /mergeFileDocTypeState/)
assert.match(documentsTabTsx, /filesMissingDocType/)

// --- suggestion / merge state ---
{
  const active = new Set(['DOC-RESUME', 'DOC-PROFILE', 'DOC-CAREER'])
  assert.equal(suggestDocTypeFromFilename('이력서.pdf'), 'DOC-RESUME')
  assert.equal(suggestDocTypeFromFilename('프로필.pptx'), 'DOC-PROFILE')
  assert.equal(suggestDocTypeFromFilename('notes.txt'), undefined)

  const files = [
    { uid: 'a', name: '이력서.pdf' },
    { uid: 'b', name: '프로필.pptx' },
    { uid: 'c', name: 'notes.txt' },
  ]
  let state = mergeFileDocTypeState(files, {}, active)
  assert.equal(state.a.documentTypeCode, 'DOC-RESUME')
  assert.equal(state.a.source, 'suggested')
  assert.equal(state.b.documentTypeCode, 'DOC-PROFILE')
  assert.equal(state.c.documentTypeCode, undefined)

  state = {
    ...state,
    a: { documentTypeCode: 'DOC-CAREER', source: 'manual' },
  }
  state = mergeFileDocTypeState(files, state, active)
  assert.equal(state.a.documentTypeCode, 'DOC-CAREER')
  assert.equal(state.a.source, 'manual')
  assert.equal(state.b.documentTypeCode, 'DOC-PROFILE')

  state = mergeFileDocTypeState([files[1], files[2]], state, active)
  assert.equal(state.a, undefined)
  assert.equal(state.b.documentTypeCode, 'DOC-PROFILE')
}

// --- promoteExistingPersonDocuments call sequence ---
{
  const calls = []
  const fileA = { name: '이력서.pdf' }
  const fileB = { name: '프로필.pptx' }
  const deps = {
    async createUploadSession(personId) {
      calls.push(['createUploadSession', personId])
      return { data: { id: 'sess-1' } }
    },
    async uploadSessionFiles(sessionId, files) {
      calls.push([
        'uploadSessionFiles',
        sessionId,
        files.map((f) => f.name),
      ])
      return {
        data: files.map((f, i) => ({
          temp_file_id: `tf-${i}`,
          original_filename: f.name,
        })),
      }
    },
    async patchTempFile(sessionId, fileId, documentTypeCode) {
      calls.push(['patchTempFile', sessionId, fileId, documentTypeCode])
      return { data: [] }
    },
    async resolveUploadSession(sessionId, body) {
      calls.push(['resolveUploadSession', sessionId, body])
      return {
        data: {
          person_id: body.person_id,
          document_ids: ['d1', 'd2'],
          profile_version: 1,
          upload_session_id: sessionId,
        },
      }
    },
    async cancelUploadSession(sessionId) {
      calls.push(['cancelUploadSession', sessionId])
    },
  }

  const result = await promoteExistingPersonDocuments(
    {
      personId: 'person-1',
      items: [
        { file: fileA, documentTypeCode: 'DOC-RESUME' },
        { file: fileB, documentTypeCode: 'DOC-PROFILE' },
      ],
      mode: 'NEW_GROUP',
    },
    deps,
  )

  assert.equal(result.data.document_ids.length, 2)
  assert.equal(calls.filter((c) => c[0] === 'createUploadSession').length, 1)
  assert.equal(calls.filter((c) => c[0] === 'uploadSessionFiles').length, 1)
  assert.equal(calls.filter((c) => c[0] === 'patchTempFile').length, 2)
  assert.equal(calls.filter((c) => c[0] === 'resolveUploadSession').length, 1)
  assert.deepEqual(
    calls.filter((c) => c[0] === 'patchTempFile').map((c) => c.slice(2)),
    [
      ['tf-0', 'DOC-RESUME'],
      ['tf-1', 'DOC-PROFILE'],
    ],
  )
  const resolveBody = calls.find((c) => c[0] === 'resolveUploadSession')[2]
  assert.deepEqual(
    resolveBody.document_resolution.map((r) => r.document_type_code),
    ['DOC-RESUME', 'DOC-PROFILE'],
  )
  assert.deepEqual(
    resolveBody.document_resolution.map((r) => r.temp_file_id),
    ['tf-0', 'tf-1'],
  )
  assert.equal(resolveBody.document_resolution[0].title, '이력서.pdf')
  assert.equal(resolveBody.document_resolution[1].title, '프로필.pptx')
}

console.log('test-per-file-doc-type-upload: PASS')
