"""Immutable upload and call-free admission for the independent uploaded-bid path."""

import hashlib
import json
import logging
from datetime import datetime, timedelta
from pathlib import PurePath
from typing import Literal, NoReturn
from uuid import UUID, uuid4

from sqlalchemy import func, select, text, tuple_
from sqlalchemy.exc import IntegrityError

from app.core.errors import ServiceError, not_found
from app.core.security import Secrets, TokenSigner
from app.models.bid_review import (
    BidDocumentPage,
    BidPreparation,
    BidPreparationPublication,
    BidPreparedDocument,
    BidSubmission,
    BidSubmissionDocument,
)
from app.models.bid_signature import BidPreparationTrust
from app.models.entities import AuditLog, Job
from app.schemas import bid_review as c
from app.schemas.budget_contracts import BudgetPreflightData
from app.schemas.check_contracts import AssessmentJobAccepted, AssessmentListData
from app.schemas.contracts import Cost
from app.services import budget_preflight, task_workflow
from app.services.versioned import audit

logger = logging.getLogger(__name__)


def fail(code, message, status=409, exit_code=2) -> NoReturn:
    raise ServiceError(code, message, status, exit_code)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def limits(settings) -> dict[Literal["files", "file_bytes", "submission_bytes"], int]:
    return {
        "files": c.FILE_LIMIT,
        "file_bytes": min(c.FILE_BYTE_LIMIT, settings.max_upload_bytes),
        "submission_bytes": min(c.SUBMISSION_BYTE_LIMIT, settings.max_upload_bytes),
    }


def seal(settings, org_id, identifier, value):
    return Secrets.for_data(settings).encrypt(
        json.dumps({"org_id": str(org_id), "id": str(identifier), "value": value})
    )


async def access(session, actor, task_id, scope="bid-review:read", *, write=False, lock=None):
    return await task_workflow.access(session, actor, task_id, scope=scope, write=write, lock=lock)


async def rows(session, submission_id):
    return list(
        (
            await session.scalars(
                select(BidSubmissionDocument)
                .where(BidSubmissionDocument.submission_id == submission_id)
                .order_by(BidSubmissionDocument.ordinal)
            )
        ).all()
    )


async def required(session, submission_id, task_id=None):
    root = await session.get(BidSubmission, submission_id)
    if root is None or (task_id is not None and root.task_id != task_id):
        raise not_found()
    return root


def uploaded_document(row):
    return c.BidUploadedDocument.model_validate(
        {
            key: getattr(row, key)
            for key in (
                "id",
                "org_id",
                "task_id",
                "submission_id",
                "file_id",
                "role",
                "kind",
                "media_type",
                "sha256",
                "size_bytes",
            )
        }
    )


async def submission_view(session, root):
    documents = await rows(session, root.id)
    base = {
        key: getattr(root, key)
        for key in (
            "id",
            "org_id",
            "task_id",
            "revision",
            "manifest_sha256",
            "created_by",
            "created_at",
        )
    }
    publication = await session.scalar(
        select(BidPreparationPublication).where(BidPreparationPublication.submission_id == root.id)
    )
    if publication is None:
        return c.BidSubmissionUploaded(
            **base, files=[uploaded_document(row) for row in documents], state=root.state
        )
    prepared = {
        row.document_id: row
        for row in (
            await session.scalars(
                select(BidPreparedDocument).where(
                    BidPreparedDocument.preparation_id == publication.preparation_id
                )
            )
        ).all()
    }
    views = []
    for row in documents:
        derived = prepared[row.id]
        views.append(
            c.BidDocumentView(
                **uploaded_document(row).model_dump(),
                **{
                    key: getattr(derived, key)
                    for key in (
                        "page_count",
                        "rendered_pdf_sha256",
                        "render_profile",
                        "renderer_identity",
                        "citation_mode",
                        "parsing_warnings",
                    )
                },
            )
        )
    return c.BidSubmissionView(
        **base,
        documents=views,
        state="prepared" if root.state == "uploaded" else "withdrawn",
        preparation_job_id=publication.job_id,
        preparation_input_hash=publication.input_hash,
    )


async def replay_lock(session, actor, operation, request_id):
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
        {"key": f"{actor.org_id}/{actor.user_id}/bid-review/{operation}/{request_id}"},
    )


async def upload(session, actor, task_id, body, files, storage, settings):
    await access(session, actor, task_id, "bid-review:upload", write=True, lock=not body.dry_run)
    cap = limits(settings)
    if (
        len(files) != len(body.files)
        or sum(len(content) for _, _, content in files) > cap["submission_bytes"]
    ):
        fail("bid_upload_limit", "Submission exceeds its upload bounds", 413)
    # Validate the complete set before the first object or row is created.
    from app.services.bid_preparation import validate_upload

    for expected, (name, media_type, content) in zip(body.files, files, strict=True):
        suffix = ".pdf" if expected.media_type == "application/pdf" else ".docx"
        if (
            PurePath(name).suffix.lower() != suffix
            or media_type != expected.media_type
            or not content
            or len(content) != expected.size_bytes
            or hashlib.sha256(content).hexdigest() != expected.sha256
        ):
            fail("bid_file_mismatch", "File descriptor does not match the supplied file", 422)
        if len(content) > cap["file_bytes"]:
            fail("bid_upload_limit", "File exceeds its upload bounds", 413)
        await validate_upload(content, expected.media_type, settings)
    manifest = {
        "org_id": str(actor.org_id),
        "task_id": str(task_id),
        "files": [item.model_dump(mode="json") for item in body.files],
    }
    manifest_hash = digest(manifest)
    payload_hash = digest(
        {
            "manifest": manifest,
            "names_sha256": [hashlib.sha256(name.encode()).hexdigest() for name, _, _ in files],
        }
    )
    if body.dry_run:
        return c.BidUploadPreview(
            files=body.files,
            payload_sha256=payload_hash,
            limits=cap,
            cost=Cost(billing_currency=settings.billing_currency),
        )
    await replay_lock(session, actor, "upload", body.request_id)
    previous = await session.scalar(
        select(BidSubmission).where(
            BidSubmission.created_by == actor.user_id, BidSubmission.request_id == body.request_id
        )
    )
    if previous is not None:
        if previous.payload_hash != payload_hash:
            fail("idempotency_conflict", "Request ID has different input")
        # An upload replay is the original receipt even after preparation completes.
        return c.BidSubmissionUploaded(
            **{
                key: getattr(previous, key)
                for key in (
                    "id",
                    "org_id",
                    "task_id",
                    "revision",
                    "manifest_sha256",
                    "created_by",
                    "created_at",
                    "state",
                )
            },
            files=[uploaded_document(row) for row in await rows(session, previous.id)],
        )
    revision = (
        await session.scalar(
            select(func.max(BidSubmission.revision)).where(BidSubmission.task_id == task_id)
        )
        or 0
    ) + 1
    root = BidSubmission(
        id=uuid4(),
        org_id=actor.org_id,
        task_id=task_id,
        revision=revision,
        manifest_sha256=manifest_hash,
        request_id=body.request_id,
        payload_hash=payload_hash,
        created_by=actor.user_id,
        state="uploaded",
        file_count=len(files),
        total_bytes=sum(len(content) for _, _, content in files),
    )
    session.add(root)
    await session.flush()
    for ordinal, (expected, (name, _, content)) in enumerate(
        zip(body.files, files, strict=True), 1
    ):
        identifier, file_id = uuid4(), uuid4()
        suffix = ".pdf" if expected.media_type == "application/pdf" else ".docx"
        key = (
            f"org/{actor.org_id}/bid-review/{root.id}/originals/{file_id}/{expected.sha256}{suffix}"
        )
        logger.info(
            "bid_review.object_staged org_id=%s submission_id=%s object_id=%s sha256=%s",
            actor.org_id,
            root.id,
            file_id,
            expected.sha256,
        )
        try:
            await storage.put(actor.org_id, key, content)
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                "bid_storage_unavailable", "Encrypted file publication failed", 503, 3
            ) from exc
        session.add(
            BidSubmissionDocument(
                id=identifier,
                org_id=actor.org_id,
                task_id=task_id,
                submission_id=root.id,
                file_id=file_id,
                ordinal=ordinal,
                **expected.model_dump(),
                storage_key=key,
                upload_name_encrypted=seal(settings, actor.org_id, identifier, name),
                created_by=actor.user_id,
            )
        )
    await session.flush()
    audit(
        session,
        actor,
        "bid_review.upload",
        root.id,
        {
            "task_id": str(task_id),
            "request_id": str(body.request_id),
            "manifest_sha256": manifest_hash,
            "payload_hash": payload_hash,
            "file_count": len(files),
        },
    )
    return await submission_view(session, root)


def build_manifest(root, documents, settings, trust_store_sha256):
    from app.services.bid_preparation import preparation_identity

    return {
        "preparation_identity": preparation_identity(settings),
        "org_id": str(root.org_id),
        "task_id": str(root.task_id),
        "submission_id": str(root.id),
        "manifest_sha256": root.manifest_sha256,
        "preparation_version": "bid-prepare-v2",
        "trust_store_sha256": trust_store_sha256,
        "render_profile": "bid-pages-v1",
        "documents": [
            {
                key: str(getattr(row, key)) if key in {"id", "file_id"} else getattr(row, key)
                for key in (
                    "id",
                    "file_id",
                    "ordinal",
                    "role",
                    "kind",
                    "media_type",
                    "sha256",
                    "size_bytes",
                )
            }
            for row in documents
        ],
        "limits": {
            "pages": c.PAGE_LIMIT,
            **{key: value for key, value in limits(settings).items() if key != "files"},
        },
        "converter_identity_sha256": hashlib.sha256(
            f"gotenberg:{settings.converter_url}".encode()
        ).hexdigest()
        if any(row.media_type != "application/pdf" for row in documents)
        else None,
    }


def receipt_binding(actor, task_id, submission_id, input_hash):
    return {
        "kind": "bid-review-prepare",
        "org_id": str(actor.org_id),
        "actor_user_id": str(actor.user_id),
        "actor_kind": actor.actor_kind,
        "task_id": str(task_id),
        "submission_id": str(submission_id),
        "input_hash": input_hash,
    }


async def prepare(session, actor, task_id, body, queue, settings):
    root = await required(session, body.submission_id, task_id)
    await access(session, actor, task_id, "bid-review:prepare", write=True, lock=not body.dry_run)
    documents = await rows(session, root.id)
    from app.services.platform_trust_anchors import get_snapshot

    pinned = await session.scalar(
        select(BidPreparationTrust).where(BidPreparationTrust.submission_id == root.id)
    )
    trust = (
        {"sha256": pinned.trust_store_sha256, "anchors": pinned.anchors}
        if pinned is not None
        else await get_snapshot(session)
    )
    manifest = build_manifest(root, documents, settings, trust["sha256"])
    input_hash = digest(manifest)
    blockers = []
    if root.state != "uploaded":
        blockers.append("bid_submission_withdrawn")
    if any(row.media_type != "application/pdf" for row in documents) and not settings.converter_url:
        blockers.append("converter_unavailable")
    if (
        any(row.media_type != "application/pdf" for row in documents)
        and not settings.review_office_profile
    ):
        blockers.append("converter_profile_unavailable")
    if (
        any(row.size_bytes > limits(settings)["file_bytes"] for row in documents)
        or sum(row.size_bytes for row in documents) > limits(settings)["submission_bytes"]
    ):
        blockers.append("bid_upload_limit")
    signer = TokenSigner.for_tokens(settings)
    binding = receipt_binding(actor, task_id, root.id, input_hash)
    if body.dry_run:
        attached = await budget_preflight.attach(
            session,
            {},
            command="review prepare",
            task_id=task_id,
            input_hash=input_hash,
            currency=settings.billing_currency,
            quotes=[],
            planned_calls=0,
        )
        budget = BudgetPreflightData.model_validate(attached["budget_preflight"])
        budget.maximum_calls = 0
        budget.as_of = budget.as_of.replace(microsecond=0)
        expiry = budget.as_of + timedelta(seconds=c.PREFLIGHT_TTL_SECONDS)
        token = signer.issue(
            {**binding, "issued_at": budget.as_of.isoformat()},
            c.PREFLIGHT_TTL_SECONDS,
            expires_at=int(expiry.timestamp()),
        )
        return c.BidPreparePreview(
            submission_id=root.id,
            input_hash=input_hash,
            budget=budget,
            expires_at=expiry,
            preflight_token=token,
            admission_blockers=blockers,
        )
    if body.expected_input_hash != input_hash:
        fail("bid_input_changed", "Submission inputs changed; preview again")
    try:
        signed = signer.open(body.preflight_token)
    except ServiceError:
        fail("bid_preflight_expired", "Preparation receipt is invalid or expired; preview again")
    if any(signed.get(key) != value for key, value in binding.items()):
        fail("bid_preflight_mismatch", "Preparation receipt does not match the actor or input")
    if blockers:
        fail(blockers[0], "Preparation is blocked; inspect the current preview", 409, 4)
    payload_hash = digest({"binding": binding, "retry": body.retry})
    await replay_lock(session, actor, "prepare", body.request_id)
    prior_receipt = await session.scalar(
        select(AuditLog).where(
            AuditLog.actor_user_id == actor.user_id,
            AuditLog.action == "bid_review.prepare",
            AuditLog.details["request_id"].astext == str(body.request_id),
        )
    )
    if prior_receipt is not None and prior_receipt.details.get("payload_hash") != payload_hash:
        fail("idempotency_conflict", "Request ID has different input")
    previous = await session.scalar(
        select(BidPreparation).where(
            BidPreparation.created_by == actor.user_id, BidPreparation.request_id == body.request_id
        )
    )
    if previous is not None and previous.payload_hash != payload_hash:
        fail("idempotency_conflict", "Request ID has different input")
    existing = previous or await session.scalar(
        select(BidPreparation)
        .where(BidPreparation.submission_id == root.id)
        .order_by(BidPreparation.created_at.desc())
        .limit(1)
    )
    if existing is not None:
        if existing.input_hash != input_hash:
            fail("bid_input_changed", "Existing preparation is bound to different inputs")
        job = await session.get(Job, existing.job_id, with_for_update=True)
        assert job is not None
        if body.retry and prior_receipt is None:
            if job.status not in {"failed", "cancelled"}:
                fail(
                    "bid_retry_not_terminal", "Only failed or cancelled preparation can be retried"
                )
            # Keep the original initiator: another authorized user cannot take over its grant.
            if existing.created_by != actor.user_id:
                fail("forbidden", "Only the preparation initiator may retry", 403, 4)
            job.status, job.error, job.finished_at = "queued", None, None
            job.run_id, job.lease_until, job.queue_id, job.attempts = None, None, None, 0
        await enqueue(session, queue, job)
        if prior_receipt is None:
            audit(
                session,
                actor,
                "bid_review.prepare",
                existing.id,
                {
                    "task_id": str(task_id),
                    "submission_id": str(root.id),
                    "job_id": str(job.id),
                    "request_id": str(body.request_id),
                    "input_hash": input_hash,
                    "payload_hash": payload_hash,
                },
            )
        return AssessmentJobAccepted.model_validate(
            {"job_id": job.id, "status": job.status, "cached": True}
        )
    if body.retry:
        fail("bid_retry_missing", "No previous preparation exists")
    prep_id = uuid4()
    job = Job(
        id=uuid4(),
        org_id=actor.org_id,
        task_id=task_id,
        document_id=None,
        kind="bid_review_prepare",
        cache_key=digest(
            {"kind": "bid_review_prepare", "submission_id": str(root.id), "input_hash": input_hash}
        ),
        status="queued",
        actor_user_id=actor.user_id,
        actor_kind="session",
        actor_scopes=sorted(actor.scopes),
        result={
            "submission": {
                **binding,
                "submission_id": str(root.id),
                "preparation_id": str(prep_id),
                "input_manifest": manifest,
                "scopes": sorted(actor.scopes),
            }
        },
    )
    session.add(job)
    await session.flush()
    session.add(
        BidPreparation(
            id=prep_id,
            org_id=actor.org_id,
            task_id=task_id,
            submission_id=root.id,
            job_id=job.id,
            request_id=body.request_id,
            payload_hash=payload_hash,
            input_hash=input_hash,
            created_by=actor.user_id,
        )
    )
    await session.flush()
    session.add(
        BidPreparationTrust(
            id=uuid4(),
            org_id=actor.org_id,
            task_id=task_id,
            submission_id=root.id,
            preparation_id=prep_id,
            created_by=actor.user_id,
            trust_store_sha256=trust["sha256"],
            anchors=trust["anchors"],
        )
    )
    try:
        await session.flush()
    except IntegrityError as exc:
        if (
            getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            == "bid_signing_trust_snapshot"
        ):
            fail("bid_input_changed", "Trust anchors changed; preview again")
        raise
    await enqueue(session, queue, job)
    audit(
        session,
        actor,
        "bid_review.prepare",
        prep_id,
        {
            "task_id": str(task_id),
            "submission_id": str(root.id),
            "job_id": str(job.id),
            "request_id": str(body.request_id),
            "input_hash": input_hash,
            "payload_hash": payload_hash,
        },
    )
    return AssessmentJobAccepted.model_validate(
        {"job_id": job.id, "status": job.status, "cached": False}
    )


async def enqueue(session, queue, job):
    if job.status == "queued" and job.queue_id is None:
        try:
            job.queue_id = await queue.enqueue_in_transaction(session, str(job.org_id), str(job.id))
        except Exception as exc:
            raise ServiceError(
                "queue_unavailable", "Preparation could not be queued; retry the request", 503, 3
            ) from exc


async def show(session, actor, submission_id, settings):
    root = await required(session, submission_id)
    await access(session, actor, root.task_id)
    submission = await submission_view(session, root)
    preparation = await session.scalar(
        select(BidPreparation)
        .where(BidPreparation.submission_id == root.id)
        .order_by(BidPreparation.created_at.desc())
        .limit(1)
    )
    status, inventory = None, []
    if preparation is not None:
        job = await session.get(Job, preparation.job_id)
        status = {"job_id": job.id, "status": job.status, "input_hash": preparation.input_hash}
    if isinstance(submission, c.BidSubmissionView):
        counts = (
            await session.execute(
                select(BidDocumentPage.document_id, BidDocumentPage.page_kind, func.count())
                .where(BidDocumentPage.submission_id == root.id)
                .group_by(BidDocumentPage.document_id, BidDocumentPage.page_kind)
            )
        ).all()
        count_map = {(doc, kind): count for doc, kind, count in counts}
        prepared = (
            await session.scalars(
                select(BidPreparedDocument).where(BidPreparedDocument.submission_id == root.id)
            )
        ).all()
        inventory = [
            {
                "document_id": row.document_id,
                "page_count": row.page_count,
                "text_pages": count_map.get((row.document_id, "text"), 0),
                "image_pages": count_map.get((row.document_id, "image"), 0),
                "signature_fields": row.signature_field_count,
                "parsing_warnings": row.parsing_warnings,
            }
            for row in prepared
        ]
    from app.services import bid_signature_views

    signatures, candidate_page = [], c.BidSigningCandidatesPage(total=0)
    if isinstance(submission, c.BidSubmissionView):
        signatures = await bid_signature_views.validations(session, actor, root.id, settings)
        candidate_page = await bid_signature_views.candidates(session, actor, root.id, settings)
    detail = c.BidSubmissionDetail.model_validate(
        {
            "submission": submission,
            "preparation": status,
            "inventory": inventory,
            "signature_validations": signatures,
            "signing_candidates": candidate_page.items,
            "signing_candidate_count": candidate_page.total,
            "signing_candidates_next_cursor": candidate_page.next_cursor,
        }
    )
    if len(detail.model_dump_json().encode()) > 1024 * 1024:
        fail("bid_review_output_limit", "Submission detail exceeds output limit", 409, 4)
    return detail


async def list_submissions(session, actor, task_id, query, settings):
    _, workflow, member = await access(session, actor, task_id)
    binding = {
        "kind": "bid-review-list",
        "org_id": str(actor.org_id),
        "actor": str(actor.user_id),
        "token": str(actor.token_id) if actor.token_id else None,
        "task_id": str(task_id),
        "limit": query.limit,
        "authority": digest(
            {
                "scopes": sorted(actor.scopes),
                "role": actor.role,
                "workflow": workflow.revision,
                "epoch": workflow.access_epoch,
                "member": str(member.id) if member else None,
                "member_revision": member.revision if member else None,
            }
        ),
    }
    selector = select(BidSubmission).where(BidSubmission.task_id == task_id)
    signer = TokenSigner.for_tokens(settings)
    if query.cursor:
        try:
            cursor = signer.open(query.cursor)
            if any(cursor.get(key) != value for key, value in binding.items()):
                raise ValueError()
            before = (datetime.fromisoformat(cursor["created_at"]), UUID(cursor["id"]))
        except (ServiceError, ValueError, KeyError, TypeError):
            fail("invalid_cursor", "Cursor is expired or does not match this list", 422)
        selector = selector.where(tuple_(BidSubmission.created_at, BidSubmission.id) < before)
    found = list(
        (
            await session.scalars(
                selector.order_by(BidSubmission.created_at.desc(), BidSubmission.id.desc()).limit(
                    query.limit + 1
                )
            )
        ).all()
    )
    following = None
    if len(found) > query.limit:
        last = found[query.limit - 1]
        following = signer.issue(
            {**binding, "created_at": last.created_at.isoformat(), "id": str(last.id)},
            c.PREFLIGHT_TTL_SECONDS,
        )
    total = await session.scalar(
        select(func.count()).select_from(BidSubmission).where(BidSubmission.task_id == task_id)
    )
    return AssessmentListData(task_id=task_id, total=total or 0, next_cursor=following), [
        await submission_view(session, row) for row in found[: query.limit]
    ]
