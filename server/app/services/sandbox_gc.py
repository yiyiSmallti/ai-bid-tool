"""Age-gated cleanup of unreferenced sandbox ciphertext; retained archives are excluded."""

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select

from app.core.errors import ServiceError
from app.models.entities import Job
from app.models.sandbox import SandboxArtifact, SandboxInput, SandboxRun
from app.providers.storage import LocalStorage, S3Storage, Storage
from app.services.sandbox import org_lock


@dataclass(frozen=True)
class Candidate:
    key: str
    changed_at: datetime
    identity: str


def local_candidates(storage: LocalStorage, org_id: UUID, limit: int) -> list[Candidate]:
    candidates = []
    for purpose in ("sandbox-inputs", "sandbox-artifacts"):
        root = storage.root / "org" / str(org_id) / purpose
        if root.is_symlink():
            raise ServiceError("sandbox_cleanup_path", "Sandbox cleanup path is invalid", 409, 4)
        for path in root.rglob("*"):
            if path.is_symlink():
                continue
            info = path.lstat()
            if path.is_file():
                key = path.relative_to(storage.root).as_posix()
                storage.path(org_id, key)
                candidates.append(
                    Candidate(
                        key,
                        datetime.fromtimestamp(info.st_mtime, UTC),
                        f"{info.st_dev}:{info.st_ino}:{info.st_mtime_ns}",
                    )
                )
                if len(candidates) >= limit:
                    return candidates
    return candidates


def s3_candidates(storage: S3Storage, org_id: UUID, limit: int) -> list[Candidate]:
    result = []
    for purpose in ("sandbox-inputs", "sandbox-artifacts"):
        options = {
            "Bucket": storage.bucket,
            "Prefix": f"org/{org_id}/{purpose}/",
            "MaxKeys": min(limit, 1000),
        }
        while True:
            response = storage.client.list_objects_v2(**options)
            for item in response.get("Contents", []):
                result.append(Candidate(item["Key"], item["LastModified"], item["ETag"]))
                if len(result) >= limit:
                    return result
            if not response["IsTruncated"]:
                break
            options["ContinuationToken"] = response["NextContinuationToken"]
    return result


def remove_candidate(storage: LocalStorage | S3Storage, org_id: UUID, candidate: Candidate) -> None:
    if isinstance(storage, LocalStorage):
        path = storage.path(org_id, candidate.key)
        info = path.lstat()
        if (
            path.is_symlink()
            or f"{info.st_dev}:{info.st_ino}:{info.st_mtime_ns}" != candidate.identity
        ):
            raise ServiceError("sandbox_cleanup_changed", "Sandbox candidate changed", 409, 4)
        path.unlink()
    else:
        # Stores without conditional deletion fail explicitly; never erase a replaced object.
        storage.client.delete_object(
            Bucket=storage.bucket, Key=candidate.key, IfMatch=candidate.identity
        )


async def reap_orphans(
    db, storage: Storage, org_id: UUID, *, delete: bool = False, limit: int = 1000
) -> dict:
    if not 1 <= limit <= 10000:
        raise ValueError("Cleanup limit must be between 1 and 10000")
    if not isinstance(storage, LocalStorage | S3Storage):
        raise ServiceError(
            "sandbox_cleanup_backend", "Storage backend has no sandbox cleanup adapter", 503, 4
        )
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    deleted = eligible = 0
    uuid = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    prefix = f"org/{org_id}/"
    input_pattern = re.compile(prefix + rf"sandbox-inputs/{uuid}/[a-f0-9]{{64}}$")
    artifact_pattern = re.compile(
        prefix + rf"sandbox-artifacts/({uuid})/{uuid}/{uuid}/[a-f0-9]{{64}}$"
    )
    async with db.transaction(org_id) as session:
        # Submission holds this same lock from input object creation through commit.
        await org_lock(session, org_id)
        if isinstance(storage, LocalStorage):
            candidates = await asyncio.to_thread(local_candidates, storage, org_id, limit)
        else:
            candidates = await asyncio.to_thread(s3_candidates, storage, org_id, limit)
        for candidate in candidates:
            if candidate.changed_at > cutoff:
                continue
            artifact = artifact_pattern.fullmatch(candidate.key)
            if input_pattern.fullmatch(candidate.key):
                reference = await session.scalar(
                    select(SandboxInput.id).where(SandboxInput.object_key == candidate.key)
                )
            elif artifact:
                reference = await session.scalar(
                    select(SandboxArtifact.id).where(SandboxArtifact.object_key == candidate.key)
                )
                active_job = await session.scalar(
                    select(Job.id)
                    .join(SandboxRun, SandboxRun.job_id == Job.id)
                    .where(
                        SandboxRun.id == UUID(artifact[1]), Job.status.in_(["queued", "running"])
                    )
                )
                if active_job is not None:
                    continue
            else:
                continue
            if reference is not None:
                continue
            eligible += 1
            if delete:
                await asyncio.to_thread(remove_candidate, storage, org_id, candidate)
                deleted += 1
    return {
        "org_id": str(org_id),
        "dry_run": not delete,
        "scanned": len(candidates),
        "eligible": eligible,
        "deleted": deleted,
        "limit_reached": len(candidates) == limit,
        "minimum_age_hours": 24,
    }
