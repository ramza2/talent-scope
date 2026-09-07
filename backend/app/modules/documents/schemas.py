"""Document / upload-session API schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class UploadSessionCreateRequest(BaseModel):
    """Create upload session.

    ``target_person_id`` is accepted for API compatibility with docs/15 but is
    **not persisted** — ``upload_session`` has no such column. Existing-person
    attach uses ``POST /resolve`` with ``LINK_EXISTING``.
    """

    target_person_id: UUID | None = None


class TempFileItem(BaseModel):
    temp_file_id: UUID
    original_filename: str
    file_size: int
    mime_type: str | None = None
    extension: str | None = None
    document_type_code: str | None = None
    document_type_suggested: bool = False
    validation_status: str
    validation_message: str | None = None
    sha256: str
    duplicate: dict | None = None
    created_at: datetime


class UploadSessionDetail(BaseModel):
    id: UUID
    status: str
    resolved_person_id: UUID | None = None
    target_person_id: UUID | None = None  # always null (not in schema); echo only
    created_by: UUID | None = None
    created_at: datetime
    expires_at: datetime | None = None
    files: list[TempFileItem] = Field(default_factory=list)
    identity: dict | None = None
    duplicate_candidates: list = Field(default_factory=list)


class UploadSessionResponse(BaseModel):
    data: UploadSessionDetail


class TempFileListResponse(BaseModel):
    data: list[TempFileItem]


class TempFilePatchRequest(BaseModel):
    document_type_code: str = Field(min_length=1, max_length=100)


class DocumentResolutionItem(BaseModel):
    temp_file_id: UUID
    mode: Literal["NEW_GROUP", "NEW_VERSION"]
    document_group_id: UUID | None = None
    document_type_code: str | None = None
    title: str | None = Field(default=None, max_length=500)


class ResolveRequest(BaseModel):
    mode: Literal["LINK_EXISTING", "CREATE_NEW"]
    person_id: UUID | None = None
    identity: dict | None = None
    document_resolution: list[DocumentResolutionItem] = Field(min_length=1)


class ResolveResponseData(BaseModel):
    person_id: UUID
    document_ids: list[UUID]
    upload_session_id: UUID


class ResolveResponse(BaseModel):
    data: ResolveResponseData


class DocumentListItem(BaseModel):
    document_id: UUID
    document_group_id: UUID
    document_type_code: str
    document_type_name: str | None = None
    title: str
    version_no: int
    is_latest: bool
    document_date: date | None = None
    original_filename: str
    extension: str | None = None
    mime_type: str | None = None
    file_size: int
    processing_status: str
    uploaded_at: datetime
    deleted_at: datetime | None = None


class DocumentListResponse(BaseModel):
    data: list[DocumentListItem]


class DocumentDetail(DocumentListItem):
    sha256: str
    preview_storage_key: str | None = None
    preview_page_count: int | None = None
    uploaded_by: UUID | None = None
    person_id: UUID


class DocumentDetailResponse(BaseModel):
    data: DocumentDetail


class DocumentVersionListResponse(BaseModel):
    data: list[DocumentListItem]
