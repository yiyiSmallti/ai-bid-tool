import asyncio
import os
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings
from app.core.errors import ServiceError


class Storage(Protocol):
    async def put(self, org_id: UUID, key: str, content: bytes) -> None: ...
    async def read(self, org_id: UUID, key: str) -> bytes: ...


class FileCipher:
    """Authenticated file encryption bound to the exact tenant storage key."""

    marker = b"BIDFILE1\n"

    def __init__(self, key: str):
        self.cipher = Fernet(key.encode())

    def encrypt(self, key: str, content: bytes) -> bytes:
        return self.marker + self.cipher.encrypt(key.encode() + b"\0" + content)

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
    def __init__(self, root: Path, encryption_key: str):
        self.root = root.resolve()
        self.cipher = FileCipher(encryption_key)

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


class S3Storage:
    def __init__(self, settings: Settings):
        import boto3

        if not settings.s3_access_key or not settings.s3_secret_key:
            raise ValueError("S3 credentials are required for S3 mode")
        self.bucket = settings.s3_bucket
        self.cipher = FileCipher(settings.encryption_key.get_secret_value())
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key.get_secret_value(),
            aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
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


def create_storage(settings: Settings) -> Storage:
    return (
        S3Storage(settings)
        if settings.storage == "s3"
        else LocalStorage(settings.data_dir, settings.encryption_key.get_secret_value())
    )
