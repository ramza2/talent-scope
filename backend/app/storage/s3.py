"""S3-compatible object storage (MinIO / AWS) via boto3."""

from __future__ import annotations

from functools import lru_cache
from typing import BinaryIO

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.core.config import Settings, get_settings
from app.core.exceptions import NotFoundError, StorageError
from app.storage.base import ObjectHead, StoredObject
from app.storage.memory import MemoryObjectStorage


class S3ObjectStorage:
    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        force_path_style: bool = True,
        region_name: str = "us-east-1",
    ) -> None:
        self.bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region_name,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path" if force_path_style else "auto"},
            ),
        )

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
    ) -> None:
        try:
            kwargs: dict = {"Bucket": self.bucket, "Key": key, "Body": data}
            if content_type:
                kwargs["ContentType"] = content_type
            self._client.put_object(**kwargs)
        except ClientError as exc:
            raise StorageError(f"객체 업로드 실패: {key}") from exc
        except Exception as exc:  # connection errors, etc.
            raise StorageError(f"객체 업로드 실패: {key}") from exc

    def put_fileobj(
        self,
        key: str,
        fileobj: BinaryIO,
        *,
        length: int,
        content_type: str | None = None,
    ) -> None:
        try:
            extra: dict = {}
            if content_type:
                extra["ContentType"] = content_type
            self._client.upload_fileobj(
                fileobj,
                self.bucket,
                key,
                ExtraArgs=extra or None,
            )
        except ClientError as exc:
            raise StorageError(f"객체 업로드 실패: {key}") from exc
        except Exception as exc:
            raise StorageError(f"객체 업로드 실패: {key}") from exc

    def head(self, key: str) -> ObjectHead:
        try:
            resp = self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise NotFoundError("객체를 찾을 수 없습니다.") from exc
            raise StorageError(f"객체 조회 실패: {key}") from exc
        return ObjectHead(
            content_length=int(resp["ContentLength"]),
            content_type=resp.get("ContentType"),
            etag=resp.get("ETag"),
        )

    def get(
        self,
        key: str,
        *,
        byte_range: tuple[int, int] | None = None,
    ) -> StoredObject:
        try:
            kwargs: dict = {"Bucket": self.bucket, "Key": key}
            if byte_range is not None:
                start, end = byte_range
                kwargs["Range"] = f"bytes={start}-{end}"
            resp = self._client.get_object(**kwargs)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"404", "NoSuchKey", "NotFound", "InvalidRange"}:
                if code == "InvalidRange":
                    raise StorageError("잘못된 byte range입니다.") from exc
                raise NotFoundError("객체를 찾을 수 없습니다.") from exc
            raise StorageError(f"객체 다운로드 실패: {key}") from exc

        body = resp["Body"]
        content_length = int(resp.get("ContentLength") or 0)
        content_range = resp.get("ContentRange")
        status = 206 if content_range else 200
        return StoredObject(
            body=body,
            content_length=content_length,
            content_type=resp.get("ContentType"),
            content_range=content_range,
            status_code=status,
        )

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            raise StorageError(f"객체 삭제 실패: {key}") from exc

    def copy(self, source_key: str, dest_key: str) -> None:
        try:
            self._client.copy_object(
                Bucket=self.bucket,
                Key=dest_key,
                CopySource={"Bucket": self.bucket, "Key": source_key},
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise NotFoundError("원본 객체를 찾을 수 없습니다.") from exc
            raise StorageError(f"객체 복사 실패: {source_key} → {dest_key}") from exc

    def exists(self, key: str) -> bool:
        try:
            self.head(key)
            return True
        except NotFoundError:
            return False


_memory_singleton: MemoryObjectStorage | None = None


def build_object_storage(settings: Settings | None = None):
    """Build storage backend.

    Uses in-memory storage when ``APP_ENV=test`` so pytest does not need MinIO.
    """
    global _memory_singleton
    cfg = settings or get_settings()
    if cfg.app_env.lower() == "test":
        if _memory_singleton is None:
            _memory_singleton = MemoryObjectStorage()
        return _memory_singleton
    return S3ObjectStorage(
        endpoint_url=cfg.s3_endpoint,
        access_key=cfg.s3_access_key,
        secret_key=cfg.s3_secret_key,
        bucket=cfg.s3_bucket,
        force_path_style=cfg.s3_force_path_style,
    )


@lru_cache
def get_object_storage():
    return build_object_storage()


def reset_object_storage_cache() -> None:
    global _memory_singleton
    get_object_storage.cache_clear()
    _memory_singleton = None
