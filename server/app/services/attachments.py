"""Immutable attachment revisions, human decisions, and exact declaration/task pins.

Every public resolver rechecks live membership. Storage publication is deliberately
not compensated by deletion: the operational stage receipt identifies retained
ciphertext even when the enclosing database transaction cannot commit.
"""

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import NoReturn
from uuid import UUID, uuid4

from cryptography.fernet import InvalidToken
from sqlalchemy import String, cast, func, literal, or_, text, true, tuple_
from sqlalchemy import select as sql_select

from app.core.config import Settings
from app.core.errors import ServiceError, not_found
from app.core.pdf_process import run_pdf_operation_async
from app.core.security import Secrets, TokenSigner
from app.models.attachments import (
    AttachmentArchive,
    AttachmentFile,
    AttachmentReview,
    AttachmentRevision,
    ProfileAttachmentLink,
    TaskAttachment,
)
from app.models.entities import AuditLog, Membership, OrgProfileRevision, TaskOrgProfile, User
from app.schemas import attachment_contracts as c
from app.schemas.certificate_file_contracts import CertificateScanFile
from app.services import task_workflow
from app.services.auth import set_actor_context
from app.services.evidence_sources import RENDER_SLOTS
from app.services.versioned import audit

logger = logging.getLogger(__name__)
WARNINGS = ["User-supplied file; archive review does not confirm evidence"]


def fail(code: str, message: str, status=409, exit_code=2) -> NoReturn:
    raise ServiceError(code, message, status, exit_code)


def settings_for(session):
    return session.info.get("attachment_settings") or Settings.load()


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def view(model, **values):
    return model.model_validate(values).model_dump(mode="json")


async def access(session, actor, scope="attachment:read"):
    live = await task_workflow.live_actor(session, actor)
    live.require(scope)
    await set_actor_context(session, live)
    return live


async def required(session, model, identifier, *, lock=False):
    query = sql_select(model).where(model.id == identifier)
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query.execution_options(populate_existing=True))
    if row is None:
        raise not_found()
    return row


async def eligible(session, user_id):
    return bool(
        await session.scalar(
            sql_select(Membership.id)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.user_id == user_id,
                Membership.active.is_(True),
                Membership.role.in_(["admin", "bidder"]),
                User.active.is_(True),
            )
        )
    )


async def assigned(session, user_id):
    if not await eligible(session, user_id):
        fail("reviewer_unavailable", "Assign a live administrator or bidder")


def state(root, expected):
    if not root.active:
        fail("archive_inactive", "Archive is inactive")
    if root.state_version != expected:
        fail("stale_state_version", "Refresh the archive before changing it")


async def replay(session, actor, command, body, extra=None):
    payload_hash = digest({"body": body.model_dump(mode="json"), "binding": extra})
    # Covers an absent root as well as existing roots and serializes divergent replays.
    key = f"{actor.org_id}/{actor.user_id}/{command}/{body.request_id}"
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
    )
    old = await session.scalar(
        sql_select(AuditLog).where(
            AuditLog.actor_user_id == actor.user_id,
            AuditLog.action == command,
            AuditLog.details["request_id"].astext == str(body.request_id),
        )
    )
    if old is not None:
        if old.details.get("payload_hash") != payload_hash:
            fail("idempotency_conflict", "Request ID has different input")
        return payload_hash, old.details.get("receipt")
    return payload_hash, None


async def record(
    session, actor, command, identifier, body, payload_hash, receipt, *, duplicate=False
):
    details = {
        "request_id": str(body.request_id),
        "payload_hash": payload_hash,
        "receipt": receipt,
        "actor_kind": actor.actor_kind,
        "duplicate": duplicate,
    }
    if command in {"attachment.create", "attachment.revise"}:
        details.update(new_revision_id=receipt["revision_id"], revision=receipt["revision"])
    audit(session, actor, command, identifier, details)
    if not duplicate and command in {
        "attachment.create",
        "attachment.revise",
        "attachment.assign",
        "attachment.review.approve",
        "attachment.review.reject",
        "attachment.review.revoke",
        "attachment.deactivate",
        "profile.attachment.link",
        "profile.attachment.deactivate",
    }:
        from app.jobs.queue import Queue

        await session.flush()
        # No connection is opened: defer borrows the current transaction so queue
        # delivery cannot outlive a rolled-back mutation. Tests use the same SQL.
        queue = session.info.get("attachment_queue")
        if queue is None or not hasattr(queue, "enqueue_attachment_invalidation_in_transaction"):
            queue = Queue(settings_for(session))
        root_id = receipt.get("attachment_id") or receipt.get("attachment", {}).get("id")
        if root_id:
            await queue.enqueue_attachment_invalidation_in_transaction(
                session,
                org_id=str(actor.org_id),
                action=command,
                request_id=str(body.request_id),
                root_id=str(root_id),
                link_id=str(identifier) if command.startswith("profile.") else None,
            )


async def put_retained(session, actor, storage, key, content, identifier):
    # Stage means attempted publication, never success. It survives DB rollback in
    # operational logs and contains no filename, label, signed link or document text.
    logger.info(
        "attachment_object_staged org_id=%s object_id=%s key=%s sha256=%s",
        actor.org_id,
        identifier,
        key,
        hashlib.sha256(content).hexdigest(),
    )
    session.info.setdefault("attachment_staged_objects", []).append(
        {"object_id": str(identifier), "key": key, "sha256": hashlib.sha256(content).hexdigest()}
    )
    try:
        await storage.put(actor.org_id, key, content)
    except ServiceError:
        raise
    except Exception as exc:
        raise ServiceError(
            "attachment_storage_unavailable",
            "Encrypted object publication failed; retain the reconciliation ID",
            503,
            3,
        ) from exc


def seal(session, org_id, identifier, value):
    return Secrets.for_data(settings_for(session)).encrypt(
        json.dumps({"org_id": str(org_id), "id": str(identifier), "value": value})
    )


def unseal(session, org_id, identifier, ciphertext):
    try:
        value = json.loads(Secrets.for_data(settings_for(session)).decrypt(ciphertext))
        if value["org_id"] != str(org_id) or value["id"] != str(identifier):
            raise ValueError("binding")
        return value["value"]
    except (InvalidToken, ValueError, KeyError, TypeError) as exc:
        raise ServiceError(
            "source_integrity_failure", "Encrypted metadata failed integrity checks", 500, 4
        ) from exc


async def latest_reviews(session, revision_ids):
    if not revision_ids:
        return {}
    parents = (
        sql_select(AttachmentRevision.id, AttachmentRevision.org_id)
        .where(AttachmentRevision.id.in_(revision_ids))
        .subquery()
    )
    latest = (
        sql_select(AttachmentReview.id)
        .where(
            AttachmentReview.org_id == parents.c.org_id,
            AttachmentReview.attachment_revision_id == parents.c.id,
        )
        .correlate(parents)
        .order_by(AttachmentReview.reviewed_at.desc(), AttachmentReview.id.desc())
        .limit(1)
        .lateral()
    )
    rows = (
        await session.scalars(
            sql_select(AttachmentReview).select_from(
                parents.join(latest, true()).join(
                    AttachmentReview, AttachmentReview.id == latest.c.id
                )
            )
        )
    ).all()
    return {row.attachment_revision_id: row for row in rows}


def review_state(review):
    return (
        {"approve": "approved", "reject": "rejected", "revoke": "revoked"}.get(
            review.decision, "pending"
        )
        if review
        else "pending"
    )


def readiness(root, review, reviewer_live=True, blockers=None):
    reasons = list(blockers or [])
    decision = review_state(review)
    if not root.active:
        reasons.insert(0, "archive_inactive")
    if decision != "approved":
        reasons.append(f"archive_review_{decision}")
        if not reviewer_live:
            reasons.insert(0, "reviewer_unavailable")
    actions = {
        "archive_inactive": "inspect_history",
        "reviewer_unavailable": "assign_reviewer",
        "archive_review_pending": "review_uploaded_file",
        "archive_review_rejected": "upload_corrected_revision",
        "archive_review_revoked": "review_uploaded_file",
        "profile_link_inactive": "replace_source",
        "profile_selection_changed": "select_for_task",
        "attachment_selection_inactive": "select_for_task",
        "task_archived": "inspect_history",
        "privacy_pending": "review_page_privacy",
        "needs_redaction": "redact_page",
        "privacy_withdrawn": "review_page_privacy",
        "extraction_required": "review_page_privacy",
        "annotation_adapter_not_enabled": "wait_for_annotation_enablement",
        "source_not_archived": "archive_page",
        "profile_link_missing": "link_declaration",
    }
    action = actions.get(reasons[0], "link_declaration") if reasons else "link_declaration"
    return view(
        c.AttachmentReadiness,
        blockers=list(dict.fromkeys(reasons)),
        next_action=action,
        responsible_user_id=root.reviewer_user_id
        if action in {"review_uploaded_file", "review_page_privacy"}
        else root.custodian_user_id,
    )


async def revision_authors(session, revisions):
    ids = [r.id for r in revisions]
    if not ids:
        return {}
    rows = (
        await session.execute(
            sql_select(
                AttachmentRevision.id,
                func.count(AuditLog.id),
                func.min(cast(AuditLog.actor_user_id, String)),
            )
            .join(
                AuditLog,
                (AuditLog.org_id == AttachmentRevision.org_id)
                & (AuditLog.object_id == AttachmentRevision.attachment_id)
                & (AuditLog.resource_revision_id_text == cast(AttachmentRevision.id, String))
                & (AuditLog.details["revision"] == func.to_jsonb(AttachmentRevision.revision)),
            )
            .where(
                AttachmentRevision.id.in_(ids),
                AuditLog.resource_revision_id_text.in_([str(i) for i in ids]),
                text("audit_logs.action IN ('attachment.create','attachment.revise')"),
            )
            .group_by(AttachmentRevision.id)
        )
    ).all()
    authors: dict[UUID, UUID | None] = {i: None for i in ids}
    for identifier, count, user_id in rows:
        if count == 1 and user_id:
            authors[identifier] = UUID(user_id)
    return authors


async def root_summaries(session, roots):
    if not roots:
        return []
    revisions = (
        await session.scalars(
            sql_select(AttachmentRevision).where(
                tuple_(AttachmentRevision.attachment_id, AttachmentRevision.revision).in_(
                    [(root.id, root.current_revision) for root in roots]
                )
            )
        )
    ).all()
    authors = await revision_authors(session, revisions)
    by_root = {r.attachment_id: r for r in revisions}
    reviews = await latest_reviews(session, [r.id for r in revisions])
    live = set(
        (
            await session.scalars(
                sql_select(Membership.user_id)
                .join(User, User.id == Membership.user_id)
                .where(
                    Membership.user_id.in_([r.reviewer_user_id for r in roots]),
                    Membership.active.is_(True),
                    Membership.role.in_(["admin", "bidder"]),
                    User.active.is_(True),
                )
            )
        ).all()
    )
    return [
        view(
            c.AttachmentSummary,
            id=r.id,
            org_id=r.org_id,
            current_revision_id=by_root[r.id].id,
            current_revision=r.current_revision,
            revised_at=by_root[r.id].created_at,
            revised_by=authors[by_root[r.id].id],
            state_version=r.state_version,
            kind=by_root[r.id].kind,
            active=r.active,
            custodian_user_id=r.custodian_user_id,
            reviewer_user_id=r.reviewer_user_id,
            review_state=review_state(reviews.get(by_root[r.id].id)),
            latest_review_id=getattr(reviews.get(by_root[r.id].id), "id", None),
            created_at=r.created_at,
            readiness=readiness(r, reviews.get(by_root[r.id].id), r.reviewer_user_id in live),
        )
        for r in roots
    ]


async def page_rows(session, actor, query, model, body, purpose, parent=None, *, task_id=None):
    binding = {
        "kind": "attachment_cursor",
        "org": str(actor.org_id),
        "actor": str(actor.user_id),
        "token": str(actor.token_id),
        "role": actor.role,
        "scopes": digest(sorted(actor.scopes)),
        "purpose": purpose,
        "parent": str(parent),
        "filters": body.model_dump(mode="json", exclude={"cursor"}),
    }
    membership_version = (
        await session.execute(
            text(
                "SELECT id, xmin::text FROM memberships WHERE org_id=:org AND user_id=:user AND active"
            ),
            {"org": actor.org_id, "user": actor.user_id},
        )
    ).first()
    if membership_version is None:
        raise not_found()
    binding["membership"] = str(membership_version[0])
    binding["membership_version"] = membership_version[1]
    if task_id:
        _, workflow, member = await task_workflow.access(session, actor, task_id)
        binding["access"] = str(workflow.access_epoch)
        binding["member"] = str(member.id) if member else None
    signer = TokenSigner.for_tokens(settings_for(session))
    if body.cursor:
        try:
            value = signer.open(body.cursor)
            if any(value.get(k) != v for k, v in binding.items()):
                raise ValueError("binding")
            when, ident = datetime.fromisoformat(value["at"]), UUID(value["id"])
        except (ServiceError, ValueError, KeyError, TypeError) as exc:
            raise ServiceError(
                "attachment_cursor_invalid", "Cursor expired or access/filter changed", 409, 2
            ) from exc
        query = query.where(
            tuple_(model.created_at, model.id) > tuple_(literal(when), literal(ident))
        )
    rows = list(
        (
            await session.scalars(query.order_by(model.created_at, model.id).limit(body.limit + 1))
        ).all()
    )
    more = len(rows) > body.limit
    rows = rows[: body.limit]
    cursor = (
        signer.issue(
            {**binding, "at": rows[-1].created_at.isoformat(), "id": str(rows[-1].id)}, 900
        )
        if more
        else None
    )
    return view(c.PageData, next_cursor=cursor, limit=body.limit, history=body.history), rows


async def show(session, actor, attachment_id):
    await access(session, actor)
    root = await required(session, AttachmentArchive, attachment_id)
    return {"attachment": (await root_summaries(session, [root]))[0]}


async def list_archives(session, actor, query):
    await access(session, actor)
    stmt = sql_select(AttachmentArchive)
    if not query.history:
        stmt = stmt.where(AttachmentArchive.active.is_(True))
    if query.kind:
        stmt = stmt.join(
            AttachmentRevision,
            (AttachmentRevision.attachment_id == AttachmentArchive.id)
            & (AttachmentRevision.revision == AttachmentArchive.current_revision),
        ).where(AttachmentRevision.kind == query.kind)
    prefix = getattr(query, "q", None)
    if prefix:
        normalized = prefix.strip().lower()
        if not normalized or any(
            ch not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for ch in normalized
        ):
            fail("invalid_attachment_search", "Use a category or archive ID prefix", 422)
        if not query.kind:
            stmt = stmt.join(
                AttachmentRevision,
                (AttachmentRevision.attachment_id == AttachmentArchive.id)
                & (AttachmentRevision.revision == AttachmentArchive.current_revision),
            )
        escaped = normalized.replace("_", r"\_")
        stmt = stmt.where(
            or_(
                cast(AttachmentArchive.id, String).like(escaped + "%", escape="\\"),
                AttachmentRevision.kind.like(escaped + "%", escape="\\"),
            )
        )
    data, rows = await page_rows(session, actor, stmt, AttachmentArchive, query, "archives")
    return data, await root_summaries(session, rows)


async def upload(session, actor, body, uploads, storage, *, attachment_id=None):
    actor = await access(session, actor, "attachment:write")
    root = (
        await required(session, AttachmentArchive, attachment_id, lock=True)
        if attachment_id
        else None
    )
    await assigned(session, body.reviewer_user_id)
    if (
        len(uploads) != 1
        or any(option.rotation != 0 for option in body.parts)
        or len(body.parts) > 1
    ):
        fail("attachment_upload_mode_not_enabled", "Only one unchanged PDF is enabled")
    upload = uploads[0]
    if not upload.name.lower().endswith(".pdf") or "/" in upload.name or "\\" in upload.name:
        fail("attachment_upload_mode_not_enabled", "Only one unchanged PDF is enabled")
    if len(upload.content) > min(c.FILE_BYTE_LIMIT, settings_for(session).max_upload_bytes):
        fail("attachment_file_limit", "Upload exceeds the file limit", 413)
    if not RENDER_SLOTS.acquire(blocking=False):
        fail("source_render_busy", "PDF processing is busy", 503, 3)
    try:
        values = await run_pdf_operation_async(
            upload.content, "attachment_validate", {"name": "attachment.pdf"}, settings_for(session)
        )
    finally:
        RENDER_SLOTS.release()
    descriptor = CertificateScanFile.model_validate(values[0])
    if descriptor.sha256 != hashlib.sha256(
        upload.content
    ).hexdigest() or descriptor.size_bytes != len(upload.content):
        fail("pdf_resource_limits", "PDF processor returned an inconsistent descriptor", 502, 4)
    if descriptor.page_count > min(c.PAGE_LIMIT, settings_for(session).max_pages):
        fail("attachment_page_limit", "Upload exceeds the page limit", 413)
    meta_hash = digest(body.metadata.model_dump(mode="json"))
    if body.dry_run:
        return view(
            c.AttachmentUploadPreview,
            original=descriptor,
            metadata_sha256=meta_hash,
            enabled_upload_mode="single_pdf",
        )
    command = "attachment.revise" if root else "attachment.create"
    payload_hash, duplicate = await replay(
        session,
        actor,
        command,
        body,
        {
            "attachment_id": str(attachment_id),
            "sha256": descriptor.sha256,
            "name_hash": digest(upload.name),
        },
    )
    if duplicate:
        return {**duplicate, "duplicate": True}
    if root:
        state(root, body.expected_state_version)
        root.current_revision += 1
        root.state_version += 1
        root.reviewer_user_id = body.reviewer_user_id
    else:
        root = AttachmentArchive(
            id=uuid4(),
            org_id=actor.org_id,
            created_by=actor.user_id,
            current_revision=1,
            state_version=1,
            active=True,
            custodian_user_id=actor.user_id,
            reviewer_user_id=body.reviewer_user_id,
        )
        session.add(root)
        await session.flush()
    revision_id, file_id = uuid4(), uuid4()
    descriptor = descriptor.model_copy(update={"name": f"attachment-{revision_id}.pdf"})
    revision = AttachmentRevision(
        id=revision_id,
        org_id=actor.org_id,
        attachment_id=root.id,
        revision=root.current_revision,
        kind=body.metadata.kind,
        label_encrypted=seal(session, actor.org_id, revision_id, body.metadata.label),
        metadata_sha256=meta_hash,
        created_by=actor.user_id,
        request_id=body.request_id,
        payload_hash=payload_hash,
    )
    key = f"org/{actor.org_id}/attachment/{root.id}/{revision_id}/{descriptor.sha256}.pdf"
    await put_retained(session, actor, storage, key, upload.content, revision_id)
    session.add(revision)
    await session.flush()
    session.add(
        AttachmentFile(
            id=file_id,
            org_id=actor.org_id,
            attachment_id=root.id,
            attachment_revision_id=revision_id,
            file=descriptor.model_dump(mode="json"),
            storage_key=key,
            upload_name_encrypted=seal(session, actor.org_id, file_id, upload.name),
            created_by=actor.user_id,
        )
    )
    await session.flush()
    receipt = view(
        c.AttachmentWriteReceipt,
        attachment_id=root.id,
        revision_id=revision_id,
        file_id=file_id,
        revision=root.current_revision,
        state_version=root.state_version,
        review_state="pending",
        duplicate=False,
        next_action="review_uploaded_file",
    )
    await record(session, actor, command, root.id, body, payload_hash, receipt)
    return receipt


async def revision_parts(session, revision_id):
    revision = await required(session, AttachmentRevision, revision_id)
    root = await required(session, AttachmentArchive, revision.attachment_id)
    file = await session.scalar(
        sql_select(AttachmentFile).where(AttachmentFile.attachment_revision_id == revision_id)
    )
    if file is None:
        fail("source_integrity_failure", "Revision file is unavailable", 500, 4)
    reviews = await latest_reviews(session, [revision_id])
    return root, revision, file, reviews.get(revision_id)


def revision_summary(revision, file, review, revised_by=None):
    return view(
        c.AttachmentRevisionSummary,
        id=revision.id,
        org_id=revision.org_id,
        attachment_id=revision.attachment_id,
        revision=revision.revision,
        revised_at=revision.created_at,
        revised_by=revised_by,
        file_id=file.id,
        kind=revision.kind,
        original_sha256=file.file["sha256"],
        metadata_sha256=revision.metadata_sha256,
        page_count=file.file["page_count"],
        size_bytes=file.file["size_bytes"],
        review_state=review_state(review),
        latest_review_id=review.id if review else None,
        created_by=revision.created_by,
        created_at=revision.created_at,
    )


async def revisions(session, actor, attachment_id, query):
    await access(session, actor)
    await required(session, AttachmentArchive, attachment_id)
    data, rows = await page_rows(
        session,
        actor,
        sql_select(AttachmentRevision).where(AttachmentRevision.attachment_id == attachment_id),
        AttachmentRevision,
        query,
        "revisions",
        attachment_id,
    )
    files = (
        await session.scalars(
            sql_select(AttachmentFile).where(
                AttachmentFile.attachment_revision_id.in_([r.id for r in rows])
            )
        )
    ).all()
    by_revision = {f.attachment_revision_id: f for f in files}
    decisions = await latest_reviews(session, [r.id for r in rows])
    authors = await revision_authors(session, rows)
    return data, [
        revision_summary(r, by_revision[r.id], decisions.get(r.id), authors[r.id]) for r in rows
    ]


async def revision(session, actor, revision_id):
    await access(session, actor, "attachment:original:read")
    _, row, file, review = await revision_parts(session, revision_id)
    authors = await revision_authors(session, [row])
    return view(
        c.AttachmentRevisionView,
        **revision_summary(row, file, review, authors[row.id]),
        metadata={
            "kind": row.kind,
            "label": unseal(session, actor.org_id, row.id, row.label_encrypted),
        },
        original=file.file,
    )


async def assign(session, actor, attachment_id, body):
    await access(session, actor, "attachment:manage")
    root = await required(session, AttachmentArchive, attachment_id, lock=True)
    hash_, old = await replay(session, actor, "attachment.assign", body, str(attachment_id))
    if old:
        return await show(session, actor, attachment_id)
    state(root, body.expected_state_version)
    await assigned(session, body.custodian_user_id)
    await assigned(session, body.reviewer_user_id)
    root.custodian_user_id, root.reviewer_user_id = body.custodian_user_id, body.reviewer_user_id
    root.state_version += 1
    await session.flush()
    receipt = await show(session, actor, attachment_id)
    await record(session, actor, "attachment.assign", root.id, body, hash_, receipt)
    return receipt


def review_view(row):
    return c.AttachmentReviewView.model_validate(row, from_attributes=True).model_dump(mode="json")


async def review(session, actor, revision_id, body):
    await access(
        session, actor, "attachment:manage" if body.decision == "revoke" else "attachment:review"
    )
    root, revision, file, _ = await revision_parts(session, revision_id)
    root = await required(session, AttachmentArchive, root.id, lock=True)
    command = f"attachment.review.{body.decision}"
    hash_, old = await replay(session, actor, command, body, str(revision_id))
    if body.decision != "revoke" and (
        root.reviewer_user_id != actor.user_id or not await eligible(session, root.reviewer_user_id)
    ):
        fail("forbidden", "Only the assigned live reviewer may decide", 403, 4)
    if old:
        return old
    prior = (await latest_reviews(session, [revision_id])).get(revision_id)
    if (body.file_id, body.original_sha256, body.metadata_sha256) != (
        file.id,
        file.file["sha256"],
        revision.metadata_sha256,
    ):
        fail("attachment_review_binding_changed", "Review must name the exact file and metadata")
    if prior and prior.decision == "approve" and body.decision == "approve":
        if not root.active:
            fail("archive_inactive", "Archive is inactive")
        receipt = review_view(prior)
        await record(session, actor, command, prior.id, body, hash_, receipt, duplicate=True)
        return receipt
    state(root, body.expected_state_version)
    if body.expected_review_id != (prior.id if prior else None):
        fail("stale_review", "Review decision changed; refresh before deciding")
    current = review_state(prior)
    if (
        current
        not in {
            "approve": {"pending", "rejected", "revoked"},
            "reject": {"pending"},
            "revoke": {"approved"},
        }[body.decision]
    ):
        fail("invalid_review_transition", "Decision is not valid in the current review state")
    row = AttachmentReview(
        org_id=actor.org_id,
        attachment_id=root.id,
        attachment_revision_id=revision_id,
        file_id=file.id,
        original_sha256=body.original_sha256,
        metadata_sha256=body.metadata_sha256,
        prior_review_id=body.expected_review_id,
        decision=body.decision,
        reason=body.reason,
        reviewed_by=actor.user_id,
        reviewed_at=datetime.now(UTC),
        request_id=body.request_id,
        payload_hash=hash_,
    )
    root.state_version += 1
    session.add(row)
    await session.flush()
    receipt = review_view(row)
    await record(session, actor, command, row.id, body, hash_, receipt)
    return receipt


async def reviews(session, actor, revision_id, query):
    await access(session, actor)
    await required(session, AttachmentRevision, revision_id)
    data, rows = await page_rows(
        session,
        actor,
        sql_select(AttachmentReview).where(AttachmentReview.attachment_revision_id == revision_id),
        AttachmentReview,
        query,
        "reviews",
        revision_id,
    )
    return data, [review_view(r) for r in rows]


async def deactivate_archive(session, actor, attachment_id, body):
    await access(session, actor, "attachment:manage")
    root = await required(session, AttachmentArchive, attachment_id, lock=True)
    hash_, old = await replay(session, actor, "attachment.deactivate", body, str(attachment_id))
    if old:
        return await show(session, actor, attachment_id)
    state(root, body.expected_state_version)
    root.active = False
    root.state_version += 1
    await session.flush()
    receipt = await show(session, actor, attachment_id)
    await record(session, actor, "attachment.deactivate", root.id, body, hash_, receipt)
    return receipt


async def approval(session, revision_id, approval_id, *, lock=False):
    root, revision, file, decision = await revision_parts(session, revision_id)
    requested = await required(session, AttachmentReview, approval_id)
    if requested.attachment_revision_id != revision_id:
        raise not_found()
    if lock:
        root = await required(session, AttachmentArchive, root.id, lock=True)
        decision = (await latest_reviews(session, [revision_id])).get(revision_id)
    if not root.active:
        fail("archive_inactive", "Archive is inactive")
    if not decision or decision.id != approval_id or decision.decision != "approve":
        code = (
            "archive_review_pending"
            if decision is None
            else (
                "archive_review_revoked"
                if decision.id != approval_id
                else f"archive_review_{review_state(decision)}"
            )
        )
        fail(code, "The exact approval is no longer current")
    return root, revision, file, decision


def link_view(row):
    return c.ProfileAttachmentLinkView.model_validate(row, from_attributes=True).model_dump(
        mode="json"
    )


async def link_profile(session, actor, profile_revision_id, body):
    await access(session, actor, "attachment:write")
    actor.require("profile:read")
    actor.require("profile:write")
    profile = await required(session, OrgProfileRevision, profile_revision_id)
    root, _, file, _ = await approval(
        session, body.attachment_revision_id, body.approval_id, lock=True
    )
    hash_, old = await replay(
        session, actor, "profile.attachment.link", body, str(profile_revision_id)
    )
    if old:
        return link_view(await required(session, ProfileAttachmentLink, UUID(old["id"])))
    if not str(profile.data.get(body.field) or "").strip():
        fail("empty_declaration", "The exact declaration field must be nonempty")
    row = ProfileAttachmentLink(
        org_id=actor.org_id,
        profile_id=profile.profile_id,
        profile_revision_id=profile.id,
        field=body.field,
        attachment_id=root.id,
        attachment_revision_id=body.attachment_revision_id,
        file_id=file.id,
        approval_id=body.approval_id,
        active=True,
        state_version=1,
        created_by=actor.user_id,
        request_id=body.request_id,
        payload_hash=hash_,
    )
    session.add(row)
    await session.flush()
    receipt = link_view(row)
    await record(session, actor, "profile.attachment.link", row.id, body, hash_, receipt)
    return receipt


async def profile_links(session, actor, profile_revision_id, query):
    await access(session, actor)
    actor.require("profile:read")
    await required(session, OrgProfileRevision, profile_revision_id)
    stmt = sql_select(ProfileAttachmentLink).where(
        ProfileAttachmentLink.profile_revision_id == profile_revision_id
    )
    if not query.history:
        stmt = stmt.where(ProfileAttachmentLink.active.is_(True))
    data, rows = await page_rows(
        session, actor, stmt, ProfileAttachmentLink, query, "links", profile_revision_id
    )
    return data, [link_view(r) for r in rows]


async def deactivate_link(session, actor, link_id, body):
    await access(session, actor, "attachment:write")
    actor.require("profile:write")
    row = await required(session, ProfileAttachmentLink, link_id)
    await required(session, AttachmentArchive, row.attachment_id, lock=True)
    row = await required(session, ProfileAttachmentLink, link_id, lock=True)
    hash_, old = await replay(session, actor, "profile.attachment.deactivate", body, str(link_id))
    if old:
        return link_view(row)
    state(row, body.expected_state_version)
    row.active = False
    row.state_version += 1
    await session.flush()
    receipt = link_view(row)
    await record(session, actor, "profile.attachment.deactivate", row.id, body, hash_, receipt)
    return receipt


async def selection_context(
    session, actor, selection_id, *, write=False, lock=False, require_active=True
):
    selected = await required(session, TaskAttachment, selection_id)
    _, workflow, _ = await task_workflow.access(
        session,
        actor,
        selected.task_id,
        scope="task:attachment" if write else "attachment:read",
        write=write,
        lock=lock,
    )
    link = await required(session, ProfileAttachmentLink, selected.profile_attachment_link_id)
    profile = await required(session, TaskOrgProfile, selected.task_org_profile_id)
    root, rev, file, decision = await revision_parts(session, selected.attachment_revision_id)
    if lock:
        root = await required(session, AttachmentArchive, root.id, lock=True)
        selected = await required(session, TaskAttachment, selection_id, lock=True)
        link = await required(session, ProfileAttachmentLink, link.id, lock=True)
        profile = await required(session, TaskOrgProfile, profile.id, lock=True)
        decision = (await latest_reviews(session, [rev.id])).get(rev.id)
    reasons = []
    if not selected.active:
        reasons.append("attachment_selection_inactive")
    if not link.active:
        reasons.append("profile_link_inactive")
    if not profile.active or profile.profile_revision_id != link.profile_revision_id:
        reasons.append("profile_selection_changed")
    if not decision or decision.id != selected.approval_id or decision.decision != "approve":
        reasons.append("archive_review_revoked")
    if not root.active:
        reasons.append("archive_inactive")
    if require_active and reasons:
        fail(reasons[0], "The exact attachment selection is no longer usable")
    if workflow.state == "archived":
        reasons.append("task_archived")
    return selected, link, profile, root, rev, file, decision, reasons


async def selection_contexts(session, selection_ids):
    from app.models.team_workflow import TaskWorkflow

    rows = (
        await session.execute(
            sql_select(
                TaskAttachment,
                ProfileAttachmentLink,
                TaskOrgProfile,
                AttachmentArchive,
                AttachmentRevision,
                AttachmentFile,
                TaskWorkflow.state,
            )
            .select_from(TaskAttachment)
            .join(
                ProfileAttachmentLink,
                ProfileAttachmentLink.id == TaskAttachment.profile_attachment_link_id,
            )
            .join(TaskOrgProfile, TaskOrgProfile.id == TaskAttachment.task_org_profile_id)
            .join(AttachmentArchive, AttachmentArchive.id == TaskAttachment.attachment_id)
            .join(
                AttachmentRevision, AttachmentRevision.id == TaskAttachment.attachment_revision_id
            )
            .join(AttachmentFile, AttachmentFile.id == TaskAttachment.file_id)
            .join(TaskWorkflow, TaskWorkflow.task_id == TaskAttachment.task_id)
            .where(TaskAttachment.id.in_(selection_ids))
            .execution_options(populate_existing=True)
        )
    ).all()
    decisions = await latest_reviews(session, [r[4].id for r in rows])
    contexts = {}
    for selected, link, profile, root, revision, file, task_state in rows:
        decision = decisions.get(revision.id)
        reasons = []
        if not root.active:
            reasons.append("archive_inactive")
        if not selected.active:
            reasons.append("attachment_selection_inactive")
        if not link.active:
            reasons.append("profile_link_inactive")
        if not profile.active:
            reasons.append("profile_selection_changed")
        if not decision or decision.id != selected.approval_id or decision.decision != "approve":
            reasons.append("archive_review_revoked")
        if task_state == "archived":
            reasons.append("task_archived")
        contexts[selected.id] = (selected, link, profile, root, revision, file, decision, reasons)
    return contexts


def selection_view(selected, link, root, file, review, reasons, *, pinned_revision):
    fields = {
        k: getattr(selected, k) for k in c.TaskAttachmentView.model_fields if hasattr(selected, k)
    }
    fields.update(
        current_library_revision=root.current_revision,
        library_updated=root.current_revision != pinned_revision,
        field=link.field,
        original_sha256=file.file["sha256"],
        lot=selected.lot or None,
        readiness=readiness(root, review, blockers=reasons or ["source_not_archived"]),
    )
    return view(c.TaskAttachmentView, **fields)


async def select(session, actor, task_id, body):
    await task_workflow.access(session, actor, task_id, scope="task:attachment", write=True)
    actor.require("task:profile")
    actor.require("profile:read")
    profile = await required(session, TaskOrgProfile, body.task_org_profile_id, lock=True)
    link = await required(session, ProfileAttachmentLink, body.profile_attachment_link_id)
    if profile.task_id != task_id or profile.profile_revision_id != link.profile_revision_id:
        raise not_found()
    root, pinned_revision, file, decision = await approval(
        session, link.attachment_revision_id, link.approval_id, lock=True
    )
    if not profile.active or not link.active:
        fail("profile_selection_changed", "Select an active exact declaration link")
    hash_, old = await replay(session, actor, "task.attachment.select", body, str(task_id))
    if old:
        current = await selection_context(session, actor, UUID(old["id"]), require_active=False)
        return selection_view(
            current[0],
            current[1],
            current[3],
            current[5],
            current[6],
            current[7],
            pinned_revision=current[4].revision,
        )
    previous = await session.scalar(
        sql_select(TaskAttachment).where(
            TaskAttachment.task_id == task_id,
            TaskAttachment.task_org_profile_id == profile.id,
            TaskAttachment.attachment_id == root.id,
            TaskAttachment.active.is_(True),
        )
    )
    if body.expected_selection_id != (previous.id if previous else None):
        fail("stale_selection", "Active attachment selection changed")
    if previous:
        previous.active = False
        previous.state_version += 1
        await session.flush()
    row = TaskAttachment(
        org_id=actor.org_id,
        task_id=task_id,
        task_org_profile_id=profile.id,
        profile_revision_id=profile.profile_revision_id,
        profile_attachment_link_id=link.id,
        attachment_id=root.id,
        attachment_revision_id=link.attachment_revision_id,
        file_id=file.id,
        approval_id=link.approval_id,
        field=link.field,
        original_sha256=file.file["sha256"],
        lot=profile.lot,
        active=True,
        state_version=1,
        selected_by=actor.user_id,
        selected_at=datetime.now(UTC),
        request_id=body.request_id,
        payload_hash=hash_,
    )
    session.add(row)
    await session.flush()
    receipt = selection_view(
        row, link, root, file, decision, [], pinned_revision=pinned_revision.revision
    )
    await record(session, actor, "task.attachment.select", row.id, body, hash_, receipt)
    return receipt


async def selections(session, actor, task_id, query):
    await task_workflow.access(session, actor, task_id, scope="attachment:read")
    stmt = sql_select(TaskAttachment).where(TaskAttachment.task_id == task_id)
    if not query.history:
        stmt = stmt.where(TaskAttachment.active.is_(True))
    data, rows = await page_rows(
        session, actor, stmt, TaskAttachment, query, "selections", task_id, task_id=task_id
    )
    contexts = await selection_contexts(session, [s.id for s in rows])
    return data, [
        selection_view(
            values[0],
            values[1],
            values[3],
            values[5],
            values[6],
            values[7],
            pinned_revision=values[4].revision,
        )
        for values in (contexts[row.id] for row in rows)
    ]


async def deactivate_selection(session, actor, selection_id, body):
    selected, link, _, root, pinned, file, decision, reasons = await selection_context(
        session, actor, selection_id, write=True, lock=True, require_active=False
    )
    hash_, old = await replay(session, actor, "task.attachment.deactivate", body, str(selection_id))
    if old:
        return old
    state(selected, body.expected_state_version)
    selected.active = False
    selected.state_version += 1
    await session.flush()
    receipt = selection_view(
        selected,
        link,
        root,
        file,
        decision,
        ["attachment_selection_inactive", *reasons],
        pinned_revision=pinned.revision,
    )
    await record(session, actor, "task.attachment.deactivate", selected.id, body, hash_, receipt)
    return receipt


async def read_original(session, actor, revision_id, storage, *, part_ordinal=None):
    await access(session, actor, "attachment:original:read")
    _, _, file, _ = await revision_parts(session, revision_id)
    if part_ordinal is not None:
        fail("attachment_upload_mode_not_enabled", "Part uploads and downloads are not enabled")
    descriptor = CertificateScanFile.model_validate(file.file)
    content = await storage.read_bounded(
        actor.org_id,
        file.storage_key,
        min(descriptor.size_bytes, c.FILE_BYTE_LIMIT, settings_for(session).max_upload_bytes),
    )
    if (
        len(content) != descriptor.size_bytes
        or hashlib.sha256(content).hexdigest() != descriptor.sha256
        or not content.lstrip().startswith(b"%PDF-")
    ):
        fail("source_integrity_failure", "Original failed integrity checks", 502, 4)
    audit(
        session,
        actor,
        "attachment.download.read",
        revision_id,
        {"file_id": str(file.id), "sha256": descriptor.sha256},
    )
    await session.flush()
    return content, descriptor


async def preview_page(session, actor, revision_id, page, zoom, storage):
    from app.services.page_previews import render_pdf_page_async

    content, descriptor = await read_original(session, actor, revision_id, storage)
    if not 1 <= page <= descriptor.page_count:
        fail("invalid_source_page", "Page is outside the original", 400)
    if not RENDER_SLOTS.acquire(blocking=False):
        fail("source_render_busy", "PDF processing is busy", 503, 3)
    try:
        return await render_pdf_page_async(content, page, zoom, settings_for(session))
    finally:
        RENDER_SLOTS.release()


# Public service names match the runtime protocol while page work stays in the
# existing source/screenshot domain and does not create another image archive.
async def create_source(*args, **kwargs):
    from app.services.attachment_pages import create_source as operation

    return await operation(*args, **kwargs)


async def sources(*args, **kwargs):
    from app.services.attachment_pages import sources as operation

    return await operation(*args, **kwargs)


async def source(*args, **kwargs):
    from app.services.attachment_pages import source as operation

    return await operation(*args, **kwargs)


async def privacy_review(*args, **kwargs):
    from app.services.attachment_pages import privacy_review as operation

    return await operation(*args, **kwargs)


async def read_source(*args, **kwargs):
    from app.services.attachment_pages import read_source as operation

    return await operation(*args, **kwargs)


async def resolve_annotation(*args, **kwargs):
    from app.services.attachment_pages import resolve_annotation as operation

    return await operation(*args, **kwargs)


async def profile_candidates(session, actor, task_id, *, history=False, cursor=None, limit=50):
    from app.services.profiles import snapshot_data

    await task_workflow.access(session, actor, task_id, scope="profile:read")
    query = sql_select(TaskOrgProfile).where(TaskOrgProfile.task_id == task_id)
    if not history:
        query = query.where(TaskOrgProfile.active.is_(True))
    data, rows = await page_rows(
        session,
        actor,
        query,
        TaskOrgProfile,
        c.PageQuery(history=history, cursor=cursor, limit=limit),
        "profile-candidates",
        task_id,
        task_id=task_id,
    )
    revisions_ = {
        r.id: r
        for r in (
            await session.scalars(
                sql_select(OrgProfileRevision).where(
                    OrgProfileRevision.id.in_([r.profile_revision_id for r in rows])
                )
            )
        ).all()
    }
    return data, [snapshot_data(row, revisions_[row.profile_revision_id]) for row in rows]


async def resolve_link(session, actor, selection_id):
    from app.services.profiles import revision_data, snapshot_data

    actor.require("profile:read")
    selected, link, profile, root, pinned, file, decision, reasons = await selection_context(
        session, actor, selection_id
    )
    fixed = await required(session, OrgProfileRevision, profile.profile_revision_id)
    return c.AttachmentLinkContext.model_validate(
        {
            "profile": revision_data(fixed),
            "task_profile": snapshot_data(profile, fixed),
            "link": link_view(link),
            "selection": selection_view(
                selected, link, root, file, decision, reasons, pinned_revision=pinned.revision
            ),
        }
    )
