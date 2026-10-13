"""Discard only this attempt's private image object after proving it has no DB reference."""

import asyncio

from app.core.errors import ServiceError
from app.providers.storage import LocalStorage, S3Storage, Storage, validate_key


async def discard(storage: Storage, key: str) -> None:
    validate_key(key)
    if not key.startswith("screenshots/"):
        raise ServiceError("invalid_storage_key", "Not a staged screenshot object", 400, 4)
    if isinstance(storage, LocalStorage):
        await asyncio.to_thread(storage.path(key).unlink, missing_ok=True)
    elif isinstance(storage, S3Storage):
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            await asyncio.to_thread(storage.client.delete_object, Bucket=storage.bucket, Key=key)
        except ClientError as exc:
            raise storage.failure(exc) from None
        except BotoCoreError:
            raise ServiceError(
                "storage_unavailable", "Staged screenshot cleanup failed", 503, 3
            ) from None
    else:
        raise ServiceError(
            "unsupported_storage", "Storage cannot discard staged image objects", 503, 4
        )
