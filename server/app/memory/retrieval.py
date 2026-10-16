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
        from app.services.task_access import access as task_access

        await task_access(session, actor, task_id)
        task = await session.get(Task, task_id)
        if task is None:
            raise not_found()
    return actor


async def epoch(session, scope="library", owner: UUID | None = None, *, lock=False) -> int:
    if lock:
        await session.execute(
            insert(MemoryScopeEpoch)
            .values(scope=scope, owner_member_id=owner, epoch=0)
            .on_conflict_do_nothing()
        )
    query = select(MemoryScopeEpoch.epoch).where(
        MemoryScopeEpoch.scope == scope,
        MemoryScopeEpoch.owner_member_id.is_not_distinct_from(owner),
    )
    row = await session.scalar(query.with_for_update(read=True) if lock else query)
    return row if row is not None else 0


type EpochTable = dict[tuple[str, str | None], int]


async def epoch_table(session) -> EpochTable:
    """Every scope's current epoch; a scope without a row is at zero."""
    rows = await session.execute(
        select(MemoryScopeEpoch.scope, MemoryScopeEpoch.owner_member_id, MemoryScopeEpoch.epoch)
    )
    return {(scope, str(owner) if owner else None): value for scope, owner, value in rows.tuples()}


def current_epochs(manifest, table: EpochTable) -> list[dict]:
    """The manifest's epochs as they stand now, in the same order and shape."""
    return [
        {**item, "epoch": table.get((item.get("scope"), item.get("owner_member_id")), 0)}
        for item in manifest.get("epochs") or []
    ]


def epochs_current(manifest, table: EpochTable) -> bool:
    entries = manifest.get("epochs") or []
    scopes = [item.get("scope") for item in entries]
    return (
        scopes in (["library"], ["library", "private"])
        and manifest.get("scopes") == scopes
        and all(("owner_member_id" in item) == (item["scope"] == "private") for item in entries)
        and entries == current_epochs(manifest, table)
    )


def private_owner(data) -> str | None:
    for item in data.get("epochs") or []:
        if isinstance(item, dict) and item.get("scope") == "private":
            return item.get("owner_member_id")
    return None


def shown_refs(data, refs: list, viewer: UUID | None) -> list:
    """Memory references as `viewer` may see them.

    Another member's private memory is left out even by reference: a shared
    project shows that its drafts used public memory, never whose private
    memory or which.
    """
    owner = private_owner(data)
    if owner is None or owner == (str(viewer) if viewer else None):
        return refs
    return [ref for ref in refs if ref.get("scope") != "private"]


def requested_owner(actor, body: MemoryRetrievalRequest) -> UUID | None:
    """Retrieval reads library memory, plus the acting member's private memory if asked."""
    if sorted(body.scopes) == ["library"]:
        return None
    if sorted(body.scopes) == ["library", "private"] and actor.member_id is not None:
        return actor.member_id
    raise ServiceError("memory_scope_unavailable", "Requested memory scope is not enabled", 403, 4)


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
        retrieval_id = UUID(int=0)
    else:
        retrieval_id = output.data.retrieval_id
    return MemoryPromptContext(
        retrieval_id=retrieval_id,
        manifest_sha256=output.data.manifest_sha256,
        rules=[item for item in output.items if item.kind == "rule"],
        preferences=[item for item in output.items if item.kind == "preference"],
    )


async def retrieve(session, actor, body: MemoryRetrievalRequest, settings, *, preview=False):
    owner = requested_owner(actor, body)
    if body.mode != "keyword":
        raise ServiceError(
            "embedding_unconfigured", "Embedding retrieval is not configured", 403, 4
        )
    actor = await access(session, actor, task_id=body.task_id)
    epochs = [MemoryEpochView(scope="library", epoch=await epoch(session, lock=not preview))]
    scope = Memory.scope == "library"
    if owner is not None:
        epochs.append(
            MemoryEpochView(
                scope="private",
                owner_member_id=owner,
                epoch=await epoch(session, "private", owner, lock=not preview),
            )
        )
        scope = or_(scope, and_(Memory.scope == "private", Memory.owner_member_id == owner))
    now = datetime.now(UTC)
    base = (
        select(MemoryRevision)
        .join(
            Memory,
            and_(
                Memory.current_revision_id == MemoryRevision.id,
                Memory.id == MemoryRevision.memory_id,
            ),
        )
        .where(
            scope,
            Memory.task_id.is_(None),
            Memory.deleted_at.is_(None),
            MemoryRevision.status == "active",
            or_(MemoryRevision.expires_at.is_(None), MemoryRevision.expires_at > now),
        )
    )
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
    private = (
        set(
            await session.scalars(
                select(Memory.id).where(
                    Memory.id.in_([row.memory_id for row in rows]), Memory.scope == "private"
                )
            )
        )
        if owner is not None and rows
        else set()
    )
    scored = []
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
                scope="private" if row.memory_id in private else "library",
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
    omitted, selected, chars = ([], [], 0)
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
        if conflicts[hit.kind, hit.conflict_key] > 1:
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
    query_hash = digest(body.model_dump(mode="json"))
    manifest_hash = digest(
        {
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
            mode="keyword",
            retrieval_version=RETRIEVAL_VERSION,
            priority_version=PRIORITY_VERSION,
            query_sha256=query_hash,
            manifest_sha256=manifest_hash,
            scopes=[item.scope for item in epochs],
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
    await require_current(session, public_manifest(output))
    rid = uuid4()
    output.data.retrieval_id, output.data.preview = (rid, False)
    crypto = Secrets.for_data(settings)

    def encrypted(payload):
        return crypto.encrypt(json.dumps({"id": str(rid), "payload": payload}, ensure_ascii=False))

    row = MemoryRetrieval(
        id=rid,
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
            memory = await session.get(Memory, item.memory_id)
            if revision is None or memory is None:
                raise not_found()
            hits.append(
                MemoryHit(
                    memory=MemoryRef(
                        memory_id=revision.memory_id,
                        revision_id=revision.id,
                        revision=revision.revision,
                        scope=memory.scope,
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


def manifest_epoch_changed(manifest, table: EpochTable):
    return (
        manifest.get("retrieval_version") != RETRIEVAL_VERSION
        or manifest.get("priority_version") != PRIORITY_VERSION
        or manifest.get("sanitizer_version") != SANITIZER_VERSION
        or not epochs_current(manifest, table)
        or bool(
            manifest.get("valid_until")
            and datetime.fromisoformat(manifest["valid_until"]) <= datetime.now(UTC)
        )
    )


async def stale_reasons(
    session, manifest, *, lock=False
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
    table: EpochTable = {}
    for item in manifest.get("epochs") or []:
        if not isinstance(item, dict) or item.get("scope") not in {"library", "private"}:
            continue
        owner = item.get("owner_member_id")
        table[item["scope"], owner] = await epoch(
            session, item["scope"], UUID(owner) if owner else None, lock=lock
        )
    if not epochs_current(manifest, table):
        reasons.append("epoch_changed")
    expiry = manifest.get("valid_until")
    if expiry and datetime.fromisoformat(expiry) <= datetime.now(UTC):
        reasons.append("expired")
    for ref in manifest.get("memories", []):
        memory = await session.get(Memory, UUID(ref["memory_id"]))
        if (
            memory is None
            or memory.deleted_at is not None
            or str(memory.current_revision_id) != ref["revision_id"]
        ):
            reasons.append("memory_changed")
            break
    return list(dict.fromkeys(reasons))


async def require_current(session, manifest, *, lock=False):
    if await stale_reasons(session, manifest, lock=lock):
        raise ServiceError(
            "memory_input_changed", "Fixed memory input changed; submit again", 409, 4
        )


async def show_retrieval(session, actor, retrieval_id, settings):
    row = await session.get(MemoryRetrieval, retrieval_id)
    if row is None:
        raise not_found()
    actor = await access(session, actor, task_id=row.task_id)
    owner = private_owner(row.data)
    if owner is not None and owner != (str(actor.member_id) if actor.member_id else None):
        raise not_found()
    envelope = json.loads(Secrets.for_data(settings).decrypt(row.encrypted_snapshot))
    if envelope["id"] != str(row.id):
        raise ServiceError("memory_snapshot_invalid", "Memory snapshot binding is invalid", 500, 4)
    output = MemoryRetrievalOutput.model_validate(envelope["payload"])
    reasons = await stale_reasons(session, public_manifest(output))
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
    if job is None:
        raise not_found()
    rows = list(
        await session.scalars(
            select(MemoryCallInput)
            .where(MemoryCallInput.job_id == job_id)
            .order_by(MemoryCallInput.created_at, MemoryCallInput.id)
        )
    )
    retrievals = {
        row.id: row.data
        for row in await session.scalars(
            select(MemoryRetrieval).where(
                MemoryRetrieval.id.in_({row.retrieval_id for row in rows})
            )
        )
    }
    return MemoryCallData(
        job_id=job_id,
        calls=[
            MemoryCallInputView.model_validate(
                {field: getattr(row, field) for field in MemoryCallInputView.model_fields}
                | {
                    "memories": shown_refs(
                        retrievals.get(row.retrieval_id, {}), row.memories, actor.member_id
                    )
                }
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
    await require_current(session, submitted["input_manifest"]["memory"], lock=True)
    if str(context.retrieval_id) != submitted["memory_retrieval_id"]:
        raise not_found()
    row_id = uuid4()
    session.add(
        MemoryCallInput(
            id=row_id,
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
                json.dumps({"id": str(row_id), "payload": payload["body"]}, ensure_ascii=False)
            ),
        )
    )
    actor = Identity(
        scopes=set(submitted["scopes"]),
        token_id=job.actor_token_id,
        actor_kind="worker",
        principal_id=job.agent_principal_id,
        session_id=job.agent_session_id,
        step_id=job.agent_step_id,
        invocation_id=job.invocation_id,
        job_id=job.id,
        run_id=execution.run_id,
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


async def settle_call(session, job_id, run_id, call_id, state):
    await session.execute(
        update(MemoryCallInput)
        .where(
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
        .where(VendorCall.job_id == job.id, VendorCall.state == "pending")
        .values(state="unknown")
    )
    await session.execute(
        update(MemoryCallInput)
        .where(MemoryCallInput.job_id == job.id, MemoryCallInput.state == "admitted")
        .values(state="unknown")
    )
