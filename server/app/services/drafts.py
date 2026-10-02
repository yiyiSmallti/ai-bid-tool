"""Deterministic background assembly; confirmed content is copied verbatim."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import not_found
from app.models.entities import Job, Requirement
from app.models.response_cards import DraftRun, ResponseCard, ResponseCardRevision, ResponseItem
from app.providers.storage import Storage
from app.schemas.contracts import Cost
from app.schemas.response_card_contracts import DraftPreview, DraftRequest, DraftView
from app.services import response_cards as cards
from app.services.auth import Identity
from app.services.resources import audit

RULE_VERSION = "response-draft-v3"
TABLES = ("substantive", "commercial", "technical")


def digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


async def assemble(session: AsyncSession, actor: Identity, task_id: UUID, job_id: UUID):
    job, requirements = await cards.extraction_scope(session, task_id, job_id)
    items, manifest, negatives = [], [], 0
    for requirement in requirements:
        card = await session.scalar(
            select(ResponseCard).where(ResponseCard.requirement_id == requirement.id)
        )
        view = None
        if card:
            revision = await session.get(ResponseCardRevision, card.current_revision_id)
            if revision is None:
                raise not_found()
            view = await cards.card_view(session, actor, card, revision, requirement)
        valid_citation = await cards.citation_valid(session, requirement)
        entry = {
            "requirement_id": str(requirement.id),
            "category": requirement.category,
            "starred": requirement.starred,
            "card_id": str(card.id) if card else None,
            "card_revision_id": view["revision_id"] if view else None,
            "source": cards.source(requirement),
            "location_label": await cards.location_label(session, requirement),
        }
        eligibility = view["eligibility"] if view else "missing_card"
        if view and eligibility == "comply_only":
            entry |= {
                "kind": "comply_only",
                "disposition_by": view["disposition_by"],
                "disposition_at": view["disposition_at"],
            }
        elif view and eligibility == "eligible":
            table = (
                "substantive"
                if requirement.starred or requirement.category == "substantive"
                else view["review_domain"]
            )
            entry |= {
                "kind": "row",
                "table": table,
                **{key: view["content"][key] for key in cards.CONTENT_FIELDS},
            }
            negatives += entry["deviation"] == "negative"
        else:
            reasons = []
            if not view:
                reasons.append("missing_card")
            else:
                if view["state"] != "confirmed":
                    reasons.append(
                        view["state"]
                        if view["state"] in {"rejected", "needs_material"}
                        else "unconfirmed"
                    )
                if view["review_domain"] is None:
                    reasons.append("unclassified")
                if await cards.generation_materials_stale(session, actor, revision) or any(
                    not material["active_selection"] for material in view["evidence"]
                ):
                    reasons.append("stale_material")
                if eligibility == "needs_reconfirmation":
                    reasons.append("needs_reconfirmation")
            if not valid_citation:
                reasons.append("invalid_citation")
            entry |= {"kind": "gap", "gap_reasons": reasons}
        items.append(entry)
        manifest.append(
            {
                "requirement_id": str(requirement.id),
                "card_revision_id": view["revision_id"] if view else None,
                "source_hash": digest(entry["source"]),
                "category": requirement.category,
                "starred": requirement.starred,
                "kind": entry["kind"],
                "eligibility": eligibility,
                "evidence": [
                    {
                        "id": row["id"],
                        "active": row["active_selection"],
                        "confirmed_by": row["confirmed_by"],
                    }
                    for row in view["evidence"]
                ]
                if view
                else [],
            }
        )
    fixed = {
        "rule_version": RULE_VERSION,
        "org_id": str(actor.org_id),
        "task_id": str(task_id),
        "extraction_job_id": str(job_id),
        "requirements": manifest,
    }
    return job, items, fixed, digest(fixed), negatives


async def validate_materials(
    session: AsyncSession, actor: Identity, task_id: UUID, items: list[dict], storage: Storage
):
    from pydantic import TypeAdapter

    from app.schemas.response_card_contracts import EvidenceInput

    adapter = TypeAdapter(EvidenceInput)
    for item in items:
        if item["kind"] == "row":
            for evidence in await cards.linked_evidence(session, UUID(item["card_revision_id"])):
                view = await cards.evidence_view(session, actor, evidence)
                await cards.resolve_material(
                    session, actor, task_id, adapter.validate_python(view["input"]), storage
                )


async def submit_draft(
    session: AsyncSession, actor: Identity, task_id: UUID, body: DraftRequest, storage: Storage
):
    actor = await cards.access(session, actor, "draft:run")
    actor.require("card:read")
    await cards.task_lock(session, task_id)
    extraction, items, manifest, input_hash, negatives = await assemble(
        session, actor, task_id, body.extraction_job_id
    )
    await validate_materials(session, actor, task_id, items, storage)
    if body.dry_run:
        counts = {
            kind: sum(item["kind"] == kind for item in items)
            for kind in ("row", "comply_only", "gap")
        }
        reasons = {}
        for item in items:
            for reason in item.get("gap_reasons", []):
                reasons[reason] = reasons.get(reason, 0) + 1
        return DraftPreview.model_validate(
            {
                "task_id": task_id,
                "extraction_job_id": body.extraction_job_id,
                "input_hash": input_hash,
                "response_requirements": counts["row"],
                "comply_only_requirements": counts["comply_only"],
                "gap_requirements": counts["gap"],
                "table_rows": {
                    table: sum(item.get("table") == table for item in items) for table in TABLES
                },
                "gap_reasons": reasons,
                "negative_deviations": negatives,
                "estimated_cost": Cost(),
            }
        ).model_dump(mode="json"), None
    cache_key = digest({"kind": "draft", "input_hash": input_hash})
    job = await session.scalar(select(Job).where(Job.cache_key == cache_key).with_for_update())
    cached = job is not None
    if job is None:
        job = Job(
            id=uuid4(),
            org_id=actor.org_id,
            task_id=task_id,
            document_id=extraction.document_id,
            kind="draft",
            cache_key=cache_key,
            status="queued",
            result={},
        )
        session.add(job)
    if body.retry and (
        job.status in {"failed", "cancelled"}
        or (job.status == "running" and job.lease_until and job.lease_until < datetime.now(UTC))
    ):
        job.status, job.error, job.queue_id, job.attempts = "queued", None, None, 0
        job.lease_until, job.finished_at, job.run_id = None, None, None
    if not cached:
        # Only IDs, hashes and authorisation grants; candidate/response text is never a job log.
        job.result = {
            "submission": {
                "extraction_job_id": str(body.extraction_job_id),
                "input_hash": input_hash,
                "input_manifest": manifest,
                "actor_user_id": str(actor.user_id),
                "actor_token_id": str(actor.token_id) if actor.token_id else None,
                "actor_kind": actor.actor_kind,
                "scopes": sorted(actor.scopes),
            }
        }
    await session.flush()
    if not cached:
        audit(
            session,
            actor,
            "draft.submit",
            job.id,
            {
                "task_id": str(task_id),
                "extraction_job_id": str(body.extraction_job_id),
                "input_hash": input_hash,
                "actor_kind": actor.actor_kind,
                "correlation_id": str(job.id),
            },
        )
    return {
        "job_id": str(job.id),
        "generation_job_id": str(job.id),
        "status": job.status,
        "cached": cached,
    }, job


async def complete_draft(session: AsyncSession, job: Job, storage: Storage):
    submitted = job.result["submission"]
    actor = Identity(
        UUID(submitted["actor_user_id"]),
        job.org_id,
        set(submitted["scopes"]),
        "viewer",
        UUID(submitted["actor_token_id"]) if submitted["actor_token_id"] else None,
        "worker",
    )
    actor = await cards.access(session, actor, "draft:run")
    actor.require("card:read")
    await cards.task_lock(session, job.task_id)
    extraction_id = UUID(submitted["extraction_job_id"])
    _, items, manifest, input_hash, negatives = await assemble(
        session, actor, job.task_id, extraction_id
    )
    if input_hash != submitted["input_hash"] or manifest != submitted["input_manifest"]:
        cards.fail(
            "draft_input_changed", "Inputs changed; read current inputs and submit again", 409, 3
        )
    await validate_materials(session, actor, job.task_id, items, storage)
    completion = "partial" if any(item["kind"] == "gap" for item in items) else "complete"
    run = DraftRun(
        id=uuid4(),
        org_id=job.org_id,
        task_id=job.task_id,
        extraction_job_id=extraction_id,
        generation_job_id=job.id,
        generation_run_id=job.run_id,
        input_hash=input_hash,
        actor_user_id=actor.user_id,
        actor_token_id=actor.token_id,
        actor_kind="worker",
        input_manifest=manifest,
        completion=completion,
        summary={
            "rows": sum(item["kind"] == "row" for item in items),
            "comply_only": sum(item["kind"] == "comply_only" for item in items),
            "gaps": sum(item["kind"] == "gap" for item in items),
            "negative_deviations": negatives,
        },
    )
    session.add(run)
    await session.flush()
    for item in items:
        values = dict(item)
        for key in ("requirement_id", "card_id", "card_revision_id", "disposition_by"):
            if values.get(key):
                values[key] = UUID(values[key])
        if values.get("disposition_at"):
            values["disposition_at"] = datetime.fromisoformat(
                values["disposition_at"].replace("Z", "+00:00")
            )
        session.add(ResponseItem(org_id=job.org_id, draft_id=run.id, **values))
    await session.flush()
    audit(
        session,
        actor,
        "draft.complete",
        run.id,
        {
            "generation_job_id": str(job.id),
            "completion": completion,
            "input_hash": input_hash,
            "actor_kind": actor.actor_kind,
            "correlation_id": str(job.id),
        },
    )
    warnings = [
        f"negative_deviation:{item['requirement_id']}"
        for item in items
        if item.get("deviation") == "negative"
    ]
    warnings.extend(
        f"{reason}:{item['requirement_id']}"
        for item in items
        for reason in item.get("gap_reasons", [])
    )
    warnings.extend(await cards.scope_warnings(session, extraction_id))
    return {
        "draft_id": str(run.id),
        "completion": completion,
        "warnings": warnings,
        "cost": Cost().model_dump(),
        "exit_code": 5 if completion == "partial" else 0,
    }


async def show_draft(session: AsyncSession, actor: Identity, draft_id: UUID):
    actor = await cards.access(session, actor, "draft:read")
    run = await session.get(DraftRun, draft_id)
    if run is None:
        raise not_found()
    rows = list(
        (await session.scalars(select(ResponseItem).where(ResponseItem.draft_id == run.id))).all()
    )
    positions = {
        entry["requirement_id"]: index
        for index, entry in enumerate(run.input_manifest["requirements"])
    }
    rows.sort(key=lambda item: positions[str(item.requirement_id)])
    tables = {key: [] for key in TABLES}
    _, _, current_manifest, _, _ = await assemble(
        session, actor, run.task_id, run.extraction_job_id
    )
    current_inputs = {entry["requirement_id"]: entry for entry in current_manifest["requirements"]}
    invalidated = [
        entry["requirement_id"]
        for entry in run.input_manifest["requirements"]
        if current_inputs.get(entry["requirement_id"]) != entry
    ]
    comply_only, gaps = [], []
    for item in rows:
        requirement = await session.get(Requirement, item.requirement_id)
        if requirement is None:
            raise not_found()
        card = await session.get(ResponseCard, item.card_id) if item.card_id else None
        if card is None:
            current = await session.scalar(
                select(ResponseCard).where(ResponseCard.requirement_id == item.requirement_id)
            )
            if current is not None:
                invalidated.append(str(item.requirement_id))
        elif card.current_revision_id != item.card_revision_id:
            invalidated.append(str(item.requirement_id))
        elif item.kind != "gap":
            revision = await session.get(ResponseCardRevision, item.card_revision_id)
            if revision is None:
                raise not_found()
            view = await cards.card_view(session, actor, card, revision, requirement)
            if view["eligibility"] != ("eligible" if item.kind == "row" else "comply_only"):
                invalidated.append(str(item.requirement_id))
        # A citation already listed as an invalid_citation gap is not a change since generation.
        already_reported = item.kind == "gap" and "invalid_citation" in (item.gap_reasons or [])
        if (
            requirement
            and not already_reported
            and not await cards.citation_valid(session, requirement)
        ):
            invalidated.append(str(item.requirement_id))
        entry = {
            "requirement_id": str(item.requirement_id),
            "card_id": str(item.card_id) if item.card_id else None,
            "card_revision_id": str(item.card_revision_id) if item.card_revision_id else None,
            "tender_clause": item.source,
            "location_label": item.location_label,
        }
        if item.kind == "row":
            if item.card_revision_id is None or item.table is None:
                cards.fail("invalid_draft", "Draft row is incomplete", 500, 4)
            entry |= {
                "category": item.category,
                "starred": item.starred,
                "table": item.table,
                **{key: getattr(item, key) for key in cards.CONTENT_FIELDS},
                "evidence": [
                    await cards.evidence_view(session, actor, row)
                    for row in await cards.linked_evidence(session, item.card_revision_id)
                ],
            }
            tables[item.table].append(entry)
        elif item.kind == "comply_only":
            entry |= {"disposition_by": item.disposition_by, "disposition_at": item.disposition_at}
            comply_only.append(entry)
        else:
            entry["reasons"] = item.gap_reasons
            gaps.append(entry)
    return DraftView.model_validate(
        {
            "id": run.id,
            "org_id": run.org_id,
            "task_id": run.task_id,
            "extraction_job_id": run.extraction_job_id,
            "generation_job_id": run.generation_job_id,
            "completion": run.completion,
            "validity": "stale" if invalidated else "current",
            "input_hash": run.input_hash,
            "tables": tables,
            "comply_only": comply_only,
            "gaps": gaps,
            "invalidated_requirements": list(dict.fromkeys(invalidated)),
        }
    ).model_dump(mode="json")


async def list_drafts(session: AsyncSession, actor: Identity, task_id: UUID, job_id: UUID):
    actor = await cards.access(session, actor, "draft:read")
    await cards.extraction_scope(session, task_id, job_id)
    runs = (
        await session.scalars(
            select(DraftRun)
            .where(DraftRun.task_id == task_id, DraftRun.extraction_job_id == job_id)
            .order_by(DraftRun.created_at, DraftRun.id)
        )
    ).all()
    items = []
    for run in runs:
        view = await show_draft(session, actor, run.id)
        items.append(
            {
                key: view[key]
                for key in (
                    "id",
                    "task_id",
                    "extraction_job_id",
                    "generation_job_id",
                    "status",
                    "completion",
                    "validity",
                    "input_hash",
                    "invalidated_requirements",
                )
            }
            | {"summary": run.summary}
        )
    return {"task_id": str(task_id), "extraction_job_id": str(job_id)}, items
