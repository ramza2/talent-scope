"""In-memory ObjectStorage for unit/integration tests."""

from __future__ import annotations

import io
from typing import BinaryIO

from app.core.exceptions import NotFoundError, StorageError
from app.storage.base import ObjectHead, StoredObject


class MemoryObjectStorage:
    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, str | None]] = {}

    def clear(self) -> None:
        self._objects.clear()

    def keys(self) -> list[str]:
        return list(self._objects.keys())

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
    ) -> None:
        self._objects[key] = (data, content_type)

    def put_fileobj(
        self,
        key: str,
        fileobj: BinaryIO,
        *,
        length: int,
        content_type: str | None = None,
    ) -> None:
        data = fileobj.read(length)
        if len(data) != length:
            raise StorageError("업로드 스트림 길이가 일치하지 않습니다.")
        self.put_bytes(key, data, content_type=content_type)

    def head(self, key: str) -> ObjectHead:
        if key not in self._objects:
            raise NotFoundError("객체를 찾을 수 없습니다.")
        data, content_type = self._objects[key]
        return ObjectHead(content_length=len(data), content_type=content_type)

    def get(
        self,
        key: str,
        *,
        byte_range: tuple[int, int] | None = None,
    ) -> StoredObject:
        if key not in self._objects:
            raise NotFoundError("객체를 찾을 수 없습니다.")
        data, content_type = self._objects[key]
        total = len(data)
        if byte_range is None:
            return StoredObject(
                body=io.BytesIO(data),
                content_length=total,
                content_type=content_type,
                status_code=200,
            )
        start, end = byte_range
        if start < 0 or end < start or start >= total:
            raise StorageError("잘못된 byte range입니다.")
        end = min(end, total - 1)
        chunk = data[start : end + 1]
        return StoredObject(
            body=io.BytesIO(chunk),
            content_length=len(chunk),
            content_type=content_type,
            content_range=f"bytes {start}-{end}/{total}",
            status_code=206,
        )

    def delete(self, key: str) -> None:
        self._objects.pop(key, None)

    def copy(self, source_key: str, dest_key: str) -> None:
        if source_key not in self._objects:
            raise NotFoundError("원본 객체를 찾을 수 없습니다.")
        data, content_type = self._objects[source_key]
        self._objects[dest_key] = (data, content_type)

    def exists(self, key: str) -> bool:
        return key in self._objects
