import { apiFetch, ApiError } from '@/api/client'

export type UploadSession = {
  id: string
  status: string
  resolved_person_id?: string | null
  target_person_id?: string | null
  created_at: string
  expires_at?: string | null
  files: TempFileItem[]
}

export type TempFileItem = {
  temp_file_id: string
  original_filename: string
  file_size: number
  mime_type?: string | null
  extension?: string | null
  document_type_code?: string | null
  document_type_suggested?: boolean
  validation_status: string
  validation_message?: string | null
  sha256: string
}

export type DocumentListItem = {
  document_id: string
  document_group_id: string
  document_type_code: string
  document_type_name?: string | null
  title: string
  version_no: number
  is_latest: boolean
  document_date?: string | null
  original_filename: string
  extension?: string | null
  mime_type?: string | null
  file_size: number
  processing_status: string
  uploaded_at: string
  deleted_at?: string | null
}

function readCsrf(): string | undefined {
  const csrf = document.cookie
    .split(';')
    .map((p) => p.trim())
    .find((p) => p.startsWith('ts_csrf='))
    ?.slice('ts_csrf='.length)
  return csrf ? decodeURIComponent(csrf) : undefined
}

export function createUploadSession(targetPersonId?: string | null) {
  return apiFetch<{ data: UploadSession }>('/upload-sessions', {
    method: 'POST',
    body: JSON.stringify({ target_person_id: targetPersonId ?? null }),
  })
}

export function getUploadSession(sessionId: string) {
  return apiFetch<{ data: UploadSession }>(`/upload-sessions/${sessionId}`)
}

export async function uploadSessionFiles(sessionId: string, files: File[]) {
  const form = new FormData()
  for (const file of files) {
    form.append('files', file)
  }
  const csrf = readCsrf()
  const response = await fetch(`/api/v1/upload-sessions/${sessionId}/files`, {
    method: 'POST',
    credentials: 'include',
    headers: csrf ? { 'X-CSRF-Token': csrf } : undefined,
    body: form,
  })
  if (response.status === 204) {
    return { data: [] as TempFileItem[] }
  }
  const text = await response.text()
  const body = text ? JSON.parse(text) : null
  if (!response.ok) {
    throw new ApiError(`API ${response.status}`, response.status, body)
  }
  return body as { data: TempFileItem[] }
}

export function patchTempFile(sessionId: string, fileId: string, documentTypeCode: string) {
  return apiFetch<{ data: TempFileItem[] }>(
    `/upload-sessions/${sessionId}/files/${fileId}`,
    {
      method: 'PATCH',
      body: JSON.stringify({ document_type_code: documentTypeCode }),
    },
  )
}

export function deleteTempFile(sessionId: string, fileId: string) {
  return apiFetch<void>(`/upload-sessions/${sessionId}/files/${fileId}`, {
    method: 'DELETE',
  })
}

export function cancelUploadSession(sessionId: string) {
  return apiFetch<void>(`/upload-sessions/${sessionId}`, { method: 'DELETE' })
}

export function resolveUploadSession(
  sessionId: string,
  body: {
    mode: 'LINK_EXISTING'
    person_id: string
    document_resolution: Array<{
      temp_file_id: string
      mode: 'NEW_GROUP' | 'NEW_VERSION'
      document_group_id?: string
      document_type_code?: string
      title?: string
    }>
  },
) {
  return apiFetch<{
    data: { person_id: string; document_ids: string[]; upload_session_id: string }
  }>(`/upload-sessions/${sessionId}/resolve`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/** Existing-person attach: session → upload → type → LINK_EXISTING / NEW_GROUP. */
export async function promoteExistingPersonDocuments(opts: {
  personId: string
  files: File[]
  documentTypeCode: string
  mode?: 'NEW_GROUP'
  title?: string
}) {
  const session = await createUploadSession(opts.personId)
  const sessionId = session.data.id
  try {
    const uploaded = await uploadSessionFiles(sessionId, opts.files)
    for (const file of uploaded.data) {
      await patchTempFile(sessionId, file.temp_file_id, opts.documentTypeCode)
    }
    return resolveUploadSession(sessionId, {
      mode: 'LINK_EXISTING',
      person_id: opts.personId,
      document_resolution: uploaded.data.map((file) => ({
        temp_file_id: file.temp_file_id,
        mode: opts.mode ?? 'NEW_GROUP',
        document_type_code: opts.documentTypeCode,
        title: opts.title ?? file.original_filename,
      })),
    })
  } catch (error) {
    try {
      await cancelUploadSession(sessionId)
    } catch {
      /* best-effort cleanup */
    }
    throw error
  }
}

export function listPersonDocuments(
  personId: string,
  opts?: { includeDeleted?: boolean },
) {
  const q = opts?.includeDeleted ? '?include_deleted=true' : ''
  return apiFetch<{ data: DocumentListItem[] }>(`/people/${personId}/documents${q}`)
}

export function getDocument(documentId: string) {
  return apiFetch<{ data: DocumentListItem & { person_id: string; sha256: string } }>(
    `/documents/${documentId}`,
  )
}

export function deleteDocument(documentId: string) {
  return apiFetch<void>(`/documents/${documentId}`, { method: 'DELETE' })
}

export function restoreDocument(documentId: string) {
  return apiFetch<{ data: DocumentListItem }>(`/documents/${documentId}/restore`, {
    method: 'POST',
    body: JSON.stringify({}),
  })
}

export function documentDownloadUrl(documentId: string) {
  return `/api/v1/documents/${documentId}/download`
}

export function documentPreviewUrl(documentId: string) {
  return `/api/v1/documents/${documentId}/preview`
}

export async function downloadDocumentBlob(documentId: string): Promise<Blob> {
  const csrf = readCsrf()
  const response = await fetch(documentDownloadUrl(documentId), {
    method: 'GET',
    credentials: 'include',
    headers: csrf ? { 'X-CSRF-Token': csrf } : undefined,
  })
  if (!response.ok) {
    const text = await response.text()
    let body: unknown = null
    try {
      body = text ? JSON.parse(text) : null
    } catch {
      body = text
    }
    throw new ApiError(`API ${response.status}`, response.status, body)
  }
  return response.blob()
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
