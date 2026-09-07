"""Document / upload constants."""

from __future__ import annotations

# MVP upload targets
ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {
        "hwp",
        "hwpx",
        "docx",
        "pdf",
        "pptx",
        "jpg",
        "jpeg",
        "png",
        # Additional compatible (LibreOffice conversion later)
        "doc",
        "ppt",
        "xls",
        "xlsx",
    }
)

# Inline preview without conversion worker
INLINE_PREVIEW_EXTENSIONS: frozenset[str] = frozenset({"pdf", "jpg", "jpeg", "png"})

# Extensions that need LibreOffice/PDF conversion (TODO next PR)
CONVERSION_PENDING_EXTENSIONS: frozenset[str] = frozenset(
    {"hwp", "hwpx", "docx", "doc", "pptx", "ppt", "xls", "xlsx"}
)

BLOCKED_EXTENSIONS: frozenset[str] = frozenset(
    {
        "exe",
        "bat",
        "cmd",
        "com",
        "msi",
        "scr",
        "js",
        "jse",
        "vbs",
        "vbe",
        "wsf",
        "wsh",
        "ps1",
        "sh",
        "bash",
        "jar",
        "dll",
        "so",
        "dmg",
        "apk",
        "zip",
        "rar",
        "7z",
        "tar",
        "gz",
        "tgz",
        "bz2",
    }
)

EXTENSION_MIME: dict[str, str] = {
    "pdf": "application/pdf",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "hwp": "application/x-hwp",
    "hwpx": "application/hwp+zip",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

DOC_TYPE_PREFIX = "DOC-"

# Magic-byte checks for formats we may serve inline without conversion.
FILE_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "pdf": (b"%PDF-",),
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
}


def canonical_mime(extension: str | None) -> str:
    ext = (extension or "").lower().strip()
    return EXTENSION_MIME.get(ext, "application/octet-stream")


def validate_inline_signature(extension: str, header: bytes) -> bool:
    """Return True when signature matches, or when no signature rule applies."""
    ext = extension.lower().strip()
    expected = FILE_SIGNATURES.get(ext)
    if expected is None:
        return True
    return any(header.startswith(sig) for sig in expected)
