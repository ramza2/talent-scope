"""Object storage protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO, Iterator, Protocol


@dataclass(frozen=True)
class ObjectHead:
    content_length: int
    content_type: str | None = None
    etag: str | None = None


@dataclass
class StoredObject:
    """Streaming payload from object storage."""

    body: BinaryIO
    content_length: int
    content_type: str | None = None
    content_range: str | None = None
    status_code: int = 200

    def iter_chunks(self, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        while True:
            chunk = self.body.read(chunk_size)
            if not chunk:
                break
            yield chunk

    def close(self) -> None:
        close = getattr(self.body, "close", None)
        if callable(close):
            close()


class ObjectStorage(Protocol):
    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
    ) -> None: ...

    def put_fileobj(
        self,
        key: str,
        fileobj: BinaryIO,
        *,
        length: int,
        content_type: str | None = None,
    ) -> None: ...

    def head(self, key: str) -> ObjectHead: ...

    def get(
        self,
        key: str,
        *,
        byte_range: tuple[int, int] | None = None,
    ) -> StoredObject: ...

    def delete(self, key: str) -> None: ...

    def copy(self, source_key: str, dest_key: str) -> None: ...

    def exists(self, key: str) -> bool: ...
