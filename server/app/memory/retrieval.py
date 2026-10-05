"""Bounded PostgreSQL keyword retrieval and exact, encrypted call lineage."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import and_, func, literal_column, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.errors import ServiceError, not_found
from app.core.security import Secrets
from app.memory.access import normalize
from app.memory.safety import SANITIZER_VERSION
from app.models.entities import Job, Task
from app.models.memory import (
    Memory,
    MemoryCallInput,
    MemoryRetrieval,
    MemoryRetrievalItem,
    MemoryRevision,
    MemoryScopeEpoch,
)
from app.schemas.memory_contracts import (
    MemoryCallData,
    MemoryCallInputView,
    MemoryEpochView,
    MemoryHit,
    MemoryOmission,
    MemoryPromptContext,
    MemoryRef,
    MemoryRetrievalData,
    MemoryRetrievalOutput,
    MemoryRetrievalRequest,
)
from app.services.auth import Identity
from app.services.versioned import audit

RETRIEVAL_VERSION = "keyword-v1"
PRIORITY_VERSION = "memory-priority-v1"
MAX_SEARCH_ROWS = 5000


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def access(session, actor, *, task_id=None):
    from app.memory.access import access as memory_access

    actor = await memory_access(session, actor, "memory:read")
    actor.require("memory:retrieve")
    if task_id is not None:
        from app.services.task_workflow import access as task_access

        await task_access(session, actor, task_id)
        task = await session.get(Task, task_id)
        if task is None or task.org_id != actor.org_id:
            raise not_found()
    return actor


async def epoch(session, org_id, *, lock=False) -> int:
    if lock:
        await session.execute(
            insert(MemoryScopeEpoch)
            .values(org_id=org_id, scope="org", owner_id=org_id, epoch=0)
            .on_conflict_do_nothing()
        )
    query = select(MemoryScopeEpoch.epoch).where(
        MemoryScopeEpoch.org_id == org_id,
        MemoryScopeEpoch.scope == "org",
        MemoryScopeEpoch.owner_id == org_id,
    )
    row = await session.scalar(query.with_for_update(read=True) if lock else query)
    return row if row is not None else 0


def public_manifest(output: MemoryRetrievalOutput) -> dict:
    data = output.data.model_dump(
        mode="json", exclude={"retrieval_id", "preview", "currently_valid", "stale_reasons"}
    )
    return {
        **data,
        "sanitizer_version": SANITIZER_VERSION,
        "memories": [item.memory.model_dump(mode="json") for item in output.items],
    }


def prompt_context(output: MemoryRetrievalOutput) -> MemoryPromptContext:
    if output.data.retrieval_id is None:
        # Previews are never admitted; this stable zero ID only sizes their wire input.
        retrieval_id = UUID(int=0)
    else:
        retrieval_id = output.data.retrieval_id
    return MemoryPromptContext(
        org_id=output.data.org_id,
        retrieval_id=retrieval_id,
        manifest_sha256=output.data.manifest_sha256,
        rules=[item for item in output.items if item.kind == "rule"],
        preferences=[item for item in output.items if item.kind == "preference"],
    )


async def retrieve(session, actor, body: MemoryRetrievalRequest, settings, *, preview=False):
    if body.org_id != actor.org_id:
        raise not_found()
    if body.scopes != ["org"]:
        raise ServiceError(
            "memory_scope_unavailable", "Only organization memory is enabled", 403, 4
        )
    if body.user_id is not None:
        raise ServiceError("memory_scope_unavailable", "User memory is unavailable", 403, 4)
    if body.mode != "keyword":
        raise ServiceError(
            "embedding_unconfigured", "Embedding retrieval is not configured", 403, 4
        )
    actor = await access(session, actor, task_id=body.task_id)
    current_epoch = await epoch(session, actor.org_id, lock=not preview)
    now = datetime.now(UTC)
    base = (
        select(MemoryRevision)
        .join(
            Memory,
            and_(
                Memory.org_id == MemoryRevision.org_id,
                Memory.current_revision_id == MemoryRevision.id,
                Memory.id == MemoryRevision.memory_id,
            ),
        )
        .where(
            Memory.org_id == actor.org_id,
            MemoryRevision.org_id == actor.org_id,
            Memory.scope == "org",
            Memory.user_id.is_(None),
            Memory.task_id.is_(None),
            Memory.deleted_at.is_(None),
            MemoryRevision.status == "active",
            or_(MemoryRevision.expires_at.is_(None), MemoryRevision.expires_at > now),
        )
    )
    # Expiry covers the entire accessible active set, even zero-score/top-k omissions.
    active = base.subquery()
    valid_until = await session.scalar(select(func.min(active.c.expires_at)))
    query = normalize(body.query)
    terms = list(dict.fromkeys([*query.split(), *(normalize(x) for x in body.keywords)]))[:20]
    tags = set(normalize(x) for x in body.tags)
    predicates = [MemoryRevision.normalized_text == query]
    predicates.extend(
        MemoryRevision.normalized_text.contains(term, autoescape=True) for term in terms
    )
    if tags:
        predicates.append(MemoryRevision.normalized_tags.overlap(sorted(tags)))
    rows = list(
        await session.scalars(
            base.where(or_(*predicates))
            .order_by(MemoryRevision.memory_id)
            .limit(MAX_SEARCH_ROWS + 1)
        )
    )
    if len(rows) > MAX_SEARCH_ROWS:
        raise ServiceError("memory_query_limit", "Narrow the memory query", 422, 2)
    scored = []
    # Literal JSON keys: bound keys render as different parameters in SELECT and GROUP BY,
    # which PostgreSQL then refuses as ungrouped columns.
    kind = active.c.content.op("->>")(literal_column("'kind'"))
    conflict_key = active.c.content.op("->>")(literal_column("'conflict_key'"))
    conflict_rows = await session.execute(
        select(kind, conflict_key, func.count()).group_by(kind, conflict_key)
    )
    conflicts = {(kind, key): count for kind, key, count in conflict_rows}
    for row in rows:
        content = row.content
        normalized = normalize(content["text"])
        exact = normalized == query
        keyword_hits = sum(term in normalized for term in terms)
        tag_hits = len(tags & {normalize(tag) for tag in content["tags"]})
        score = 100 * exact + 10 * keyword_hits + 5 * tag_hits
        if not score:
            continue
        matched: list[Literal["exact", "keyword", "tag", "vector"]] = []
        if exact:
            matched.append("exact")
        if keyword_hits:
            matched.append("keyword")
        if tag_hits:
            matched.append("tag")
        hit = MemoryHit(
            memory=MemoryRef(
                memory_id=row.memory_id,
                revision_id=row.id,
                revision=row.revision,
                scope="org",
                content_sha256=row.content_sha256,
                sent_sha256=text_hash(content["text"]),
                expires_at=row.expires_at,
            ),
            kind=content["kind"],
            conflict_key=content["conflict_key"],
            text=content["text"],
            rank=1,
            priority=2 if content["kind"] == "rule" else 1,
            relevance=score,
            matched_by=matched,
        )
        scored.append(hit)
    omitted, selected, chars = [], [], 0
    scored.sort(
        key=lambda hit: (
            hit.kind != "rule",
            -hit.priority,
            -hit.relevance,
            str(hit.memory.memory_id),
        )
    )
    for hit in scored:
        reason = None
        if conflicts[(hit.kind, hit.conflict_key)] > 1:
            reason = "same_priority_conflict"
        elif len(selected) >= body.top_k:
            reason = "top_k_limit"
        elif chars + len(hit.text) > body.max_context_chars:
            reason = "context_limit"
        if reason:
            omitted.append(
                MemoryOmission(
                    memory_id=hit.memory.memory_id,
                    revision_id=hit.memory.revision_id,
                    reason=reason,
                )
            )
        else:
            hit.rank = len(selected) + 1
            selected.append(hit)
            chars += len(hit.text)
    epochs = [
        MemoryEpochView(
            org_id=actor.org_id, scope="org", owner_id=actor.org_id, epoch=current_epoch
        )
    ]
    query_hash = digest(body.model_dump(mode="json"))
    manifest_hash = digest(
        {
            "org_id": str(actor.org_id),
            "query_sha256": query_hash,
            "epochs": [x.model_dump(mode="json") for x in epochs],
            "valid_until": valid_until.isoformat() if valid_until else None,
            "memories": [x.memory.model_dump(mode="json") for x in selected],
            "omitted": [x.model_dump(mode="json") for x in omitted],
            "retrieval_version": RETRIEVAL_VERSION,
            "priority_version": PRIORITY_VERSION,
            "sanitizer_version": SANITIZER_VERSION,
        }
    )
    output = MemoryRetrievalOutput(
        data=MemoryRetrievalData(
            retrieval_id=None,
            org_id=actor.org_id,
            mode="keyword",
            retrieval_version=RETRIEVAL_VERSION,
            priority_version=PRIORITY_VERSION,
            query_sha256=query_hash,
            manifest_sha256=manifest_hash,
            scopes=["org"],
            epochs=epochs,
            valid_until=valid_until,
            omitted=omitted,
            context_chars=chars,
            preview=True,
            currently_valid=True,
        ),
        items=selected,
        warnings=["memory_semantic_conflicts_require_review"] if selected else [],
    )
    if not preview:
        await persist(session, actor, body, output, settings, all_hits=scored)
    return output


async def persist(session, actor, body, output, settings, *, all_hits=None):
    await require_current(session, actor.org_id, public_manifest(output))
    rid = uuid4()
    output.data.retrieval_id, output.data.preview = rid, False
    crypto = Secrets.for_data(settings)

    def encrypted(payload):
        return crypto.encrypt(
            json.dumps(
                {"org_id": str(actor.org_id), "id": str(rid), "payload": payload},
                ensure_ascii=False,
            )
        )

    row = MemoryRetrieval(
        id=rid,
        org_id=actor.org_id,
        user_id=actor.user_id,
        task_id=body.task_id,
        actor_token_id=actor.token_id,
        data=output.data.model_dump(mode="json"),
        encrypted_query=encrypted(body.model_dump(mode="json")),
        encrypted_snapshot=encrypted(output.model_dump(mode="json")),
    )
    session.add(row)
    await session.flush()
    omitted = {x.revision_id: x.reason for x in output.data.omitted}
    hits = list(all_hits or output.items)
    if all_hits is None:
        for item in output.data.omitted:
            revision = await session.get(MemoryRevision, item.revision_id)
            if revision is None:
                raise not_found()
            hits.append(
                MemoryHit(
                    memory=MemoryRef(
                        memory_id=revision.memory_id,
                        revision_id=revision.id,
                        revision=revision.revision,
                        scope="org",
                        content_sha256=revision.content_sha256,
                        sent_sha256=text_hash(revision.content["text"]),
                        expires_at=revision.expires_at,
                    ),
                    kind=revision.content["kind"],
                    conflict_key=revision.content["conflict_key"],
                    text=revision.content["text"],
                    rank=1,
                    priority=2,
                    relevance=0,
                    matched_by=["keyword"],
                )
            )
    for rank, hit in enumerate(hits, 1):
        ref = hit.memory
        session.add(
            MemoryRetrievalItem(
                id=uuid4(),
                org_id=actor.org_id,
                retrieval_id=rid,
                memory_id=ref.memory_id,
                memory_revision_id=ref.revision_id,
                revision=ref.revision,
                content_sha256=ref.content_sha256,
                sent_sha256=ref.sent_sha256,
                rank=hit.rank if ref.revision_id not in omitted else rank,
                selected=ref.revision_id not in omitted,
                omission_reason=omitted.get(ref.revision_id),
            )
        )
    audit(
        session,
        actor,
        "memory.retrieve",
        rid,
        {
            "query_sha256": output.data.query_sha256,
            "manifest_sha256": output.data.manifest_sha256,
            "count": len(output.items),
        },
    )
    await session.flush()


def manifest_epoch_changed(manifest, org_id, current):
    return (
        manifest.get("retrieval_version") != RETRIEVAL_VERSION
        or manifest.get("priority_version") != PRIORITY_VERSION
        or manifest.get("sanitizer_version") != SANITIZER_VERSION
        or manifest.get("epochs")
        != [{"org_id": str(org_id), "scope": "org", "owner_id": str(org_id), "epoch": current}]
        or bool(
            manifest.get("valid_until")
            and datetime.fromisoformat(manifest["valid_until"]) <= datetime.now(UTC)
        )
    )


async def stale_reasons(
    session, org_id, manifest, *, lock=False
) -> list[Literal["epoch_changed", "expired", "policy_changed", "memory_changed"]]:
    if not manifest:
        return ["policy_changed"]
    reasons: list[Literal["epoch_changed", "expired", "policy_changed", "memory_changed"]] = []
    if (
        manifest.get("retrieval_version") != RETRIEVAL_VERSION
        or manifest.get("priority_version") != PRIORITY_VERSION
        or manifest.get("sanitizer_version", SANITIZER_VERSION) != SANITIZER_VERSION
    ):
        reasons.append("policy_changed")
    current = await epoch(session, org_id, lock=lock)
    if manifest.get("epochs") != [
        {"org_id": str(org_id), "scope": "org", "owner_id": str(org_id), "epoch": current}
    ]:
        reasons.append("epoch_changed")
    expiry = manifest.get("valid_until")
    if expiry and datetime.fromisoformat(expiry) <= datetime.now(UTC):
        reasons.append("expired")
    for ref in manifest.get("memories", []):
        memory = await session.get(Memory, UUID(ref["memory_id"]))
        if (
            memory is None
            or memory.org_id != org_id
            or memory.deleted_at is not None
            or str(memory.current_revision_id) != ref["revision_id"]
        ):
            reasons.append("memory_changed")
            break
    return list(dict.fromkeys(reasons))


async def require_current(session, org_id, manifest, *, lock=False):
    if await stale_reasons(session, org_id, manifest, lock=lock):
        raise ServiceError(
            "memory_input_changed", "Fixed memory input changed; submit again", 409, 4
        )


async def show_retrieval(session, actor, retrieval_id, settings):
    row = await session.get(MemoryRetrieval, retrieval_id)
    if row is None or row.org_id != actor.org_id:
        raise not_found()
    await access(session, actor, task_id=row.task_id)
    envelope = json.loads(Secrets.for_data(settings).decrypt(row.encrypted_snapshot))
    if envelope["org_id"] != str(actor.org_id) or envelope["id"] != str(row.id):
        raise ServiceError("memory_snapshot_invalid", "Memory snapshot binding is invalid", 500, 4)
    output = MemoryRetrievalOutput.model_validate(envelope["payload"])
    reasons = await stale_reasons(session, actor.org_id, public_manifest(output))
    output.data.stale_reasons = reasons
    output.data.currently_valid = not reasons
    return output


async def used(session, actor, job_id, storage):
    from app.services.jobs import status

    await status(session, actor, job_id, storage)
    actor.require("memory:read")
    actor.require("card:read")
    actor.require("task:read")
    job = await session.get(Job, job_id)
    if job is None or job.org_id != actor.org_id:
        raise not_found()
    rows = await session.scalars(
        select(MemoryCallInput)
        .where(MemoryCallInput.org_id == actor.org_id, MemoryCallInput.job_id == job_id)
        .order_by(MemoryCallInput.created_at, MemoryCallInput.id)
    )
    return MemoryCallData(
        job_id=job_id,
        calls=[
            MemoryCallInputView.model_validate(
                {field: getattr(row, field) for field in MemoryCallInputView.model_fields}
            )
            for row in rows
        ],
    )


async def attach_call(session, execution, call_id, payload):
    job = await session.get(Job, execution.job_id)
    if job is None or job.kind != "card_generate":
        return
    if payload is None:
        raise ServiceError("memory_call_missing", "Drafting call manifest is missing", 500, 4)
    context = MemoryPromptContext.model_validate(payload["memory"])
    if any(
        text_hash(item.text) != item.memory.sent_sha256
        for item in (*context.rules, *context.preferences)
    ):
        raise ServiceError(
            "memory_snapshot_invalid", "Sent memory text does not match its fixed hash", 409, 4
        )
    submitted = job.result["submission"]
    await require_current(session, job.org_id, submitted["input_manifest"]["memory"], lock=True)
    if (
        context.org_id != job.org_id
        or str(context.retrieval_id) != submitted["memory_retrieval_id"]
    ):
        raise not_found()
    row_id = uuid4()
    session.add(
        MemoryCallInput(
            id=row_id,
            org_id=job.org_id,
            task_id=job.task_id,
            job_id=job.id,
            run_id=execution.run_id,
            call_id=call_id,
            retrieval_id=context.retrieval_id,
            requirement_ids=payload["requirement_ids"],
            memories=[
                x.memory.model_dump(mode="json") for x in (*context.rules, *context.preferences)
            ],
            manifest_sha256=context.manifest_sha256,
            prompt_sha256=digest(payload["body"]),
            prompt_version=submitted["input_manifest"]["prompt_version"],
            state="admitted",
            encrypted_prompt=Secrets.for_data(execution.settings).encrypt(
                json.dumps(
                    {"org_id": str(job.org_id), "id": str(row_id), "payload": payload["body"]},
                    ensure_ascii=False,
                )
            ),
        )
    )
    actor = Identity(
        UUID(submitted["actor_user_id"]),
        job.org_id,
        set(submitted["scopes"]),
        "viewer",
        UUID(submitted["actor_token_id"]) if submitted["actor_token_id"] else None,
        "worker",
    )
    audit(
        session,
        actor,
        "memory.call.attach",
        row_id,
        {
            "job_id": str(job.id),
            "run_id": str(execution.run_id),
            "call_id": str(call_id),
            "prompt_sha256": digest(payload["body"]),
            "manifest_sha256": context.manifest_sha256,
        },
    )
    await session.flush()


async def settle_call(session, org_id, job_id, run_id, call_id, state):
    await session.execute(
        update(MemoryCallInput)
        .where(
            MemoryCallInput.org_id == org_id,
            MemoryCallInput.job_id == job_id,
            MemoryCallInput.run_id == run_id,
            MemoryCallInput.call_id == call_id,
            MemoryCallInput.state != "completed",
        )
        .values(state=state, usage_record_id=call_id if state == "completed" else None)
    )


async def recover_unsettled_calls(session, job):
    """A replacement attempt cannot know whether its predecessor reached the vendor."""
    from app.models.entities import VendorCall

    await session.execute(
        update(VendorCall)
        .where(
            VendorCall.org_id == job.org_id,
            VendorCall.job_id == job.id,
            VendorCall.state == "pending",
        )
        .values(state="unknown")
    )
    await session.execute(
        update(MemoryCallInput)
        .where(
            MemoryCallInput.org_id == job.org_id,
            MemoryCallInput.job_id == job.id,
            MemoryCallInput.state == "admitted",
        )
        .values(state="unknown")
    )
