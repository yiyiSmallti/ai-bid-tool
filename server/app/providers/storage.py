import asyncio
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import Settings
from app.core.errors import ServiceError


class Storage(Protocol):
    async def put(self, org_id: UUID, key: str, content: bytes) -> None: ...
    async def read(self, org_id: UUID, key: str) -> bytes: ...
    async def read_bounded(self, org_id: UUID, key: str, max_bytes: int) -> bytes: ...


class FileCipher:
    """Authenticated file encryption bound to the exact tenant storage key."""

    marker = b"BIDFILE1\n"

    def __init__(self, key: str, previous: Sequence[str] = ()):
        self.current = Fernet(key.encode())
        self.cipher = MultiFernet([self.current, *(Fernet(item.encode()) for item in previous)])

    @classmethod
    def for_data(cls, settings: Settings) -> "FileCipher":
        return cls(
            settings.encryption_key.get_secret_value(),
            [item.get_secret_value() for item in settings.encryption_key_previous],
        )

    def encrypt(self, key: str, content: bytes) -> bytes:
        return self.marker + self.cipher.encrypt(key.encode() + b"\0" + content)

    def rewrap(self, key: str, stored: bytes) -> bytes | None:
        """The object re-encrypted under the current key, or None if it already is."""
        self.decrypt(key, stored)
        token = stored[len(self.marker) :]
        try:
            self.current.decrypt(token)
            return None
        except InvalidToken:
            return self.marker + self.cipher.rotate(token)

    def stored_limit(self, key: str, max_bytes: int) -> int:
        if max_bytes < 0:
            raise ValueError("A nonnegative plaintext limit is required")
        payload = len(key.encode()) + 1 + max_bytes
        encrypted = 16 * (payload // 16 + 1)
        return len(self.marker) + 4 * ((57 + encrypted + 2) // 3)

    def decrypt_bounded(self, key: str, stored: bytes, max_bytes: int) -> bytes:
        if len(stored) > self.stored_limit(key, max_bytes):
            raise ServiceError(
                "file_size_limit", "Stored file exceeds its authorized limit", 413, 4
            )
        content = self.decrypt(key, stored)
        if len(content) > max_bytes:
            raise ServiceError(
                "file_size_limit", "Stored file exceeds its authorized limit", 413, 4
            )
        return content

    def decrypt(self, key: str, stored: bytes) -> bytes:
        try:
            if not stored.startswith(self.marker):
                raise ValueError("unsupported file format")
            payload = self.cipher.decrypt(stored[len(self.marker) :])
            stored_key, content = payload.split(b"\0", 1)
            if stored_key != key.encode():
                raise ValueError("storage scope mismatch")
            return content
        except (InvalidToken, ValueError) as exc:
            raise ServiceError(
                "unreadable_file", "Stored file failed integrity or encryption checks", 500, 4
            ) from exc


def validate_key(org_id: UUID, key: str) -> None:
    if not key.startswith(f"org/{org_id}/") or ".." in Path(key).parts or Path(key).is_absolute():
        raise ServiceError("invalid_storage_key", "Invalid storage scope", 400, 2)


class LocalStorage:
    def __init__(self, root: Path, encryption_key: str, previous: Sequence[str] = ()):
        self.root = root.resolve()
        self.cipher = FileCipher(encryption_key, previous)

    def path(self, org_id: UUID, key: str) -> Path:
        validate_key(org_id, key)
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root / "org" / str(org_id)):
            raise ServiceError("invalid_storage_key", "Invalid storage scope", 400, 2)
        return path

    async def put(self, org_id: UUID, key: str, content: bytes) -> None:
        path = self.path(org_id, key)

        def write():
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Hash-addressed content is immutable, including concurrent duplicate uploads.
            temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
            try:
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(self.cipher.encrypt(key, content))
                os.link(temporary, path)
            except FileExistsError:
                if self.cipher.decrypt(key, path.read_bytes()) != content:
                    raise ServiceError(
                        "storage_conflict", "Stored content differs", 409, 4
                    ) from None
            finally:
                temporary.unlink(missing_ok=True)

        await asyncio.to_thread(write)

    async def read(self, org_id: UUID, key: str) -> bytes:
        try:
            stored = await asyncio.to_thread(self.path(org_id, key).read_bytes)
            return self.cipher.decrypt(key, stored)
        except FileNotFoundError as exc:
            raise ServiceError("missing_file", "Stored file is unavailable", 404, 4) from exc

    async def rotate(self, org_id: UUID) -> tuple[int, int]:
        """Re-encrypt this org's objects under the current key: (checked, rewritten)."""
        base = self.path(org_id, f"org/{org_id}/")

        def run():
            checked = rewritten = 0
            for path in sorted(base.rglob("*")) if base.is_dir() else []:
                if not path.is_file() or path.name.endswith(".tmp"):
                    continue
                key = path.relative_to(self.root).as_posix()
                checked += 1
                stored = self.cipher.rewrap(key, path.read_bytes())
                if stored is None:
                    continue
                temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
                try:
                    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(stored)
                    # Same plaintext under a new key; readers see either complete file.
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
                rewritten += 1
            return checked, rewritten

        return await asyncio.to_thread(run)

    async def read_bounded(self, org_id: UUID, key: str, max_bytes: int) -> bytes:
        path = self.path(org_id, key)
        bound = self.cipher.stored_limit(key, max_bytes)

        def read():
            with path.open("rb") as handle:
                return self.cipher.decrypt_bounded(key, handle.read(bound + 1), max_bytes)

        try:
            return await asyncio.to_thread(read)
        except FileNotFoundError as exc:
            raise ServiceError("missing_file", "Stored file is unavailable", 404, 4) from exc
        except OSError as exc:
            raise ServiceError("storage_unavailable", "Stored file is unavailable", 503, 3) from exc


class S3Storage:
    def __init__(self, settings: Settings, *, request_timeout_seconds: float | None = None):
        import boto3
        from botocore.config import Config

        if not settings.s3_access_key or not settings.s3_secret_key:
            raise ValueError("S3 credentials are required for S3 mode")
        self._annotation_storage: S3Storage | None = None
        self.bucket = settings.s3_bucket
        self.cipher = FileCipher.for_data(settings)
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key.get_secret_value(),
            aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
            **(
                {
                    "config": Config(
                        connect_timeout=request_timeout_seconds,
                        read_timeout=request_timeout_seconds,
                        retries={"total_max_attempts": 1},
                    )
                }
                if request_timeout_seconds is not None
                else {}
            ),
        )

    async def put(self, org_id: UUID, key: str, content: bytes) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        validate_key(org_id, key)
        try:
            await asyncio.to_thread(
                self.client.put_object,
                Bucket=self.bucket,
                Key=key,
                Body=self.cipher.encrypt(key, content),
                IfNoneMatch="*",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "PreconditionFailed":
                if await self.read(org_id, key) != content:
                    raise ServiceError(
                        "storage_conflict", "Stored content differs", 409, 4
                    ) from None
                return
            raise self.failure(exc) from None
        except BotoCoreError:
            raise ServiceError(
                "storage_unavailable", "Object storage is unavailable", 503, 3
            ) from None

    async def read(self, org_id: UUID, key: str) -> bytes:
        from botocore.exceptions import BotoCoreError, ClientError

        validate_key(org_id, key)

        def read():
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            try:
                return self.cipher.decrypt(key, response["Body"].read())
            finally:
                response["Body"].close()

        try:
            return await asyncio.to_thread(read)
        except ClientError as exc:
            raise self.failure(exc) from None
        except BotoCoreError:
            raise ServiceError(
                "storage_unavailable", "Object storage is unavailable", 503, 3
            ) from None

    async def read_bounded(self, org_id: UUID, key: str, max_bytes: int) -> bytes:
        from botocore.exceptions import BotoCoreError, ClientError

        validate_key(org_id, key)
        bound = self.cipher.stored_limit(key, max_bytes)

        def read():
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            try:
                if response["ContentLength"] > bound:
                    raise ServiceError(
                        "file_size_limit", "Stored file exceeds its authorized limit", 413, 4
                    )
                return self.cipher.decrypt_bounded(key, response["Body"].read(bound + 1), max_bytes)
            finally:
                response["Body"].close()

        try:
            return await asyncio.to_thread(read)
        except ClientError as exc:
            raise self.failure(exc) from None
        except BotoCoreError:
            raise ServiceError(
                "storage_unavailable", "Object storage is unavailable", 503, 3
            ) from None

    async def rotate(self, org_id: UUID) -> tuple[int, int]:
        """Re-encrypt this org's objects under the current key: (checked, rewritten)."""

        def run():
            checked = rewritten = 0
            pages = self.client.get_paginator("list_objects_v2").paginate(
                Bucket=self.bucket, Prefix=f"org/{org_id}/"
            )
            for page in pages:
                for item in page.get("Contents", []):
                    key = item["Key"]
                    validate_key(org_id, key)
                    response = self.client.get_object(Bucket=self.bucket, Key=key)
                    try:
                        stored = self.cipher.rewrap(key, response["Body"].read())
                    finally:
                        response["Body"].close()
                    checked += 1
                    if stored is None:
                        continue
                    # Objects are immutable by content, so an overwrite carries the
                    # same plaintext as any concurrent writer of this key.
                    self.client.put_object(Bucket=self.bucket, Key=key, Body=stored)
                    rewritten += 1
            return checked, rewritten

        return await asyncio.to_thread(run)

    @staticmethod
    def failure(error) -> ServiceError:
        code = error.response["Error"]["Code"]
        if code in {"NoSuchKey", "NotFound", "404"}:
            return ServiceError("missing_file", "Stored file is unavailable", 404, 4)
        if code in {"AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch"}:
            return ServiceError("storage_denied", "Object storage access is denied", 503, 4)
        if code in {
            "SlowDown",
            "RequestTimeout",
            "InternalError",
            "ServiceUnavailable",
            "ConditionalRequestConflict",
        }:
            return ServiceError("storage_unavailable", "Object storage is unavailable", 503, 3)
        return ServiceError("storage_failure", "Object storage request failed", 503, 4)


def create_storage(settings: Settings) -> "LocalStorage | S3Storage":
    return (
        S3Storage(settings)
        if settings.storage == "s3"
        else LocalStorage(
            settings.data_dir,
            settings.encryption_key.get_secret_value(),
            [item.get_secret_value() for item in settings.encryption_key_previous],
        )
    )


def annotation_storage(storage: Storage, settings: Settings) -> Storage:
    """Use short, non-retrying S3 requests within B05's attempt and orphan grace."""
    if not isinstance(storage, S3Storage):
        return storage
    scoped = getattr(storage, "_annotation_storage", None)
    if scoped is None:
        scoped = S3Storage(settings, request_timeout_seconds=10.0)
        storage._annotation_storage = scoped
    return scoped
