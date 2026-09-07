"""Document / upload-session HTTP endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.auth.dependencies import (
    AuthenticatedContext,
    require_admin,
    require_authenticated_user,
    require_csrf,
)
from app.modules.documents.schemas import (
    DocumentDetailResponse,
    DocumentListResponse,
    DocumentVersionListResponse,
    ResolveRequest,
    ResolveResponse,
    ResolveResponseData,
    TempFileListResponse,
    TempFilePatchRequest,
    UploadSessionCreateRequest,
    UploadSessionResponse,
)
from app.modules.documents.service import (
    DocumentService,
    content_disposition_attachment,
    content_disposition_inline,
)
from app.storage.s3 import build_object_storage

upload_sessions_router = APIRouter(prefix="/upload-sessions", tags=["upload-sessions"])
documents_router = APIRouter(prefix="/documents", tags=["documents"])
document_groups_router = APIRouter(prefix="/document-groups", tags=["documents"])
person_documents_router = APIRouter(prefix="/people", tags=["documents"])


def get_document_service(db: Session = Depends(get_db)) -> DocumentService:
    return DocumentService(db, storage=build_object_storage())


# --- Upload sessions ---


@upload_sessions_router.post(
    "", response_model=UploadSessionResponse, status_code=status.HTTP_201_CREATED
)
def create_upload_session(
    payload: UploadSessionCreateRequest,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: DocumentService = Depends(get_document_service),
) -> UploadSessionResponse:
    return UploadSessionResponse(data=service.create_session(payload, ctx.user.id))


@upload_sessions_router.get("/{session_id}", response_model=UploadSessionResponse)
def get_upload_session(
    session_id: UUID,
    _admin: AuthenticatedContext = Depends(require_admin),
    service: DocumentService = Depends(get_document_service),
) -> UploadSessionResponse:
    return UploadSessionResponse(data=service.get_session(session_id))


@upload_sessions_router.post(
    "/{session_id}/files",
    response_model=TempFileListResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_session_files(
    session_id: UUID,
    files: list[UploadFile] = File(...),
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: DocumentService = Depends(get_document_service),
) -> TempFileListResponse:
    return TempFileListResponse(
        data=service.upload_files(session_id, files, ctx.user.id)
    )


@upload_sessions_router.patch(
    "/{session_id}/files/{file_id}", response_model=TempFileListResponse
)
def patch_temp_file(
    session_id: UUID,
    file_id: UUID,
    payload: TempFilePatchRequest,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: DocumentService = Depends(get_document_service),
) -> TempFileListResponse:
    item = service.patch_temp_file(session_id, file_id, payload, ctx.user.id)
    return TempFileListResponse(data=[item])


@upload_sessions_router.delete(
    "/{session_id}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_temp_file(
    session_id: UUID,
    file_id: UUID,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: DocumentService = Depends(get_document_service),
) -> Response:
    service.delete_temp_file(session_id, file_id, ctx.user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@upload_sessions_router.post("/{session_id}/identify", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def identify_upload_session(
    session_id: UUID,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: DocumentService = Depends(get_document_service),
) -> None:
    service.identify(session_id)


@upload_sessions_router.post(
    "/{session_id}/resolve",
    response_model=ResolveResponse,
    status_code=status.HTTP_201_CREATED,
)
def resolve_upload_session(
    session_id: UUID,
    payload: ResolveRequest,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: DocumentService = Depends(get_document_service),
) -> ResolveResponse:
    data = service.resolve(session_id, payload, ctx.user.id)
    return ResolveResponse(
        data=ResolveResponseData(
            person_id=data["person_id"],
            document_ids=data["document_ids"],
            upload_session_id=data["upload_session_id"],
        )
    )


@upload_sessions_router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_upload_session(
    session_id: UUID,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: DocumentService = Depends(get_document_service),
) -> Response:
    service.cancel_session(session_id, ctx.user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Person documents ---


@person_documents_router.get(
    "/{person_id}/documents", response_model=DocumentListResponse
)
def list_person_documents(
    person_id: UUID,
    ctx: AuthenticatedContext = Depends(require_authenticated_user),
    service: DocumentService = Depends(get_document_service),
    include_deleted: bool = False,
) -> DocumentListResponse:
    return DocumentListResponse(
        data=service.list_person_documents(
            person_id,
            is_admin=ctx.user.role == "ADMIN",
            include_deleted=include_deleted,
        )
    )


# --- Documents ---


@documents_router.get("/{document_id}", response_model=DocumentDetailResponse)
def get_document(
    document_id: UUID,
    ctx: AuthenticatedContext = Depends(require_authenticated_user),
    service: DocumentService = Depends(get_document_service),
) -> DocumentDetailResponse:
    return DocumentDetailResponse(
        data=service.get_document(document_id, is_admin=ctx.user.role == "ADMIN")
    )


@documents_router.get("/{document_id}/download")
def download_document(
    document_id: UUID,
    ctx: AuthenticatedContext = Depends(require_authenticated_user),
    service: DocumentService = Depends(get_document_service),
) -> StreamingResponse:
    doc, _group, obj = service.open_download(
        document_id,
        is_admin=ctx.user.role == "ADMIN",
        actor_user_id=ctx.user.id,
    )
    headers = {
        "Content-Disposition": content_disposition_attachment(doc.original_filename),
        "Accept-Ranges": "bytes",
    }
    if obj.content_length is not None:
        headers["Content-Length"] = str(obj.content_length)

    def stream():
        try:
            yield from obj.iter_chunks()
        finally:
            obj.close()

    return StreamingResponse(
        stream(),
        media_type=doc.mime_type or obj.content_type or "application/octet-stream",
        headers=headers,
        status_code=200,
    )


@documents_router.get("/{document_id}/preview")
def preview_document(
    document_id: UUID,
    request: Request,
    ctx: AuthenticatedContext = Depends(require_authenticated_user),
    service: DocumentService = Depends(get_document_service),
) -> StreamingResponse:
    is_admin = ctx.user.role == "ADMIN"
    # Peek size for Range parsing when needed
    range_header = request.headers.get("range")
    byte_range = None
    if range_header:
        # Use storage head via a lightweight get_document + storage head
        detail = service.get_document(document_id, is_admin=is_admin)
        from app.modules.documents.constants import INLINE_PREVIEW_EXTENSIONS

        doc_row = service.repo.get_document(document_id, include_deleted=is_admin)
        assert doc_row is not None
        key = doc_row.preview_storage_key
        if not key:
            ext = (doc_row.extension or "").lower()
            if ext not in INLINE_PREVIEW_EXTENSIONS:
                from app.core.exceptions import PreviewUnavailableError

                raise PreviewUnavailableError()
            key = doc_row.storage_key
        head = service.storage.head(key)
        byte_range = service.parse_range_header(range_header, head.content_length)

    doc, filename, obj = service.open_preview(
        document_id, is_admin=is_admin, byte_range=byte_range
    )
    headers = {
        "Content-Disposition": content_disposition_inline(filename),
        "Accept-Ranges": "bytes",
        "Content-Length": str(obj.content_length),
    }
    if obj.content_range:
        headers["Content-Range"] = obj.content_range

    def stream():
        try:
            yield from obj.iter_chunks()
        finally:
            obj.close()

    return StreamingResponse(
        stream(),
        media_type=doc.mime_type or obj.content_type or "application/octet-stream",
        headers=headers,
        status_code=obj.status_code,
    )


@documents_router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: UUID,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: DocumentService = Depends(get_document_service),
) -> Response:
    service.soft_delete(document_id, ctx.user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@documents_router.post(
    "/{document_id}/restore", response_model=DocumentDetailResponse
)
def restore_document(
    document_id: UUID,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: DocumentService = Depends(get_document_service),
) -> DocumentDetailResponse:
    return DocumentDetailResponse(data=service.restore(document_id, ctx.user.id))


@document_groups_router.get(
    "/{group_id}/versions", response_model=DocumentVersionListResponse
)
def list_document_versions(
    group_id: UUID,
    ctx: AuthenticatedContext = Depends(require_authenticated_user),
    service: DocumentService = Depends(get_document_service),
) -> DocumentVersionListResponse:
    return DocumentVersionListResponse(
        data=service.list_versions(group_id, is_admin=ctx.user.role == "ADMIN")
    )
