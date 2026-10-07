"""Frontend per-file DOC_TYPE upload wiring (no vitest in this repo)."""

from __future__ import annotations

from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"


def _read(rel: str) -> str:
    return (FRONTEND / rel).read_text(encoding="utf-8")


def test_documents_tab_has_no_global_single_type_logic() -> None:
    src = _read("pages/people/DocumentsTab.tsx")
    assert "consensusDocType" not in src
    assert "effectiveDocType" not in src
    assert "docTypeSource" not in src
    assert "fileDocTypes" in src
    assert "mergeFileDocTypeState" in src
    assert "suggestDocTypeFromFilename" in src
    assert "filesMissingDocType" in src
    assert "문서 종류를 선택하세요:" in src
    # Per-row Select, not a single global DOC_TYPE Select above Upload.
    assert "fileDocTypes[file.uid]" in src
    assert "source: 'manual'" in src or 'source: "manual"' in src
    assert "source: 'suggested'" in src or 'source: "suggested"' in src


def test_documents_tab_cleanup_and_suggestion_helpers() -> None:
    src = _read("pages/people/DocumentsTab.tsx")
    assert "existing?.source === 'manual'" in src or 'existing?.source === "manual"' in src
    assert "activeCodes.has(suggested)" in src
    # Removing a file drops its uid from merged state (only current files kept).
    assert "for (const file of files)" in src
    assert "prev[file.uid]" in src


def test_promote_existing_person_documents_items_signature_and_index_mapping() -> None:
    src = _read("api/documents.ts")
    assert "PromoteExistingPersonDocumentItem" in src
    assert "items: PromoteExistingPersonDocumentItem[]" in src
    assert "documentTypeCode: string" in src
    assert "uploaded.data.length !== opts.items.length" in src
    assert "opts.items[i].documentTypeCode" in src
    assert "opts.items[i].title ?? file.original_filename" in src
    # Must not map by filename when patching/resolving.
    assert "original_filename ===" not in src.split("promoteExistingPersonDocuments")[1].split(
        "export function listPersonDocuments"
    )[0]
    # Single-session flow markers.
    assert "createUploadSession(opts.personId)" in src
    assert "uploadSessionFiles(sessionId, files)" in src
    assert "resolveUploadSession(sessionId," in src
    assert "cancelUploadSession(sessionId)" in src
    # Index loop for patch (not for-of over uploaded only with a shared type).
    assert "for (let i = 0; i < uploaded.data.length; i += 1)" in src
