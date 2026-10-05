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
from app.services.versioned import audit

RULE_VERSION = "response-draft-v3"
TABLES = ("substantive", "commercial", "technical")


def digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def evidence_dependency(row: dict) -> dict:
    fixed = {
        "id": row["id"],
        "active": row["active_selection"],
        "confirmed_by": row["confirmed_by"],
    }
    if row["input"]["kind"] == "image_region":
        fixed |= {
            "asset_id": row["screenshot_asset_id"],
            "rendition_id": row["screenshot_rendition_id"],
            "image_sha256": row["image_sha256"],
            "region": row["region"],
            "claim_scope": row["claim_scope"],
            "visual_observation_sha256": hashlib.sha256(
                row["visual_observation"].encode()
            ).hexdigest(),
            "material_kind": row["material_kind"],
            "quote_check": row["quote_check"],
            "image_plan_sha256": row["image_rendition"]["plan_sha256"],
            "image_mapping": row["image_rendition"]["mapping"],
            "image_profile": row["image_rendition"]["profile"],
            "privacy_review_id": row["image_rendition"]["privacy_review_id"],
        }
    return fixed


async def assemble(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    job_id: UUID,
    storage: Storage | None = None,
):
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
            view = await cards.card_view(session, actor, card, revision, requirement, storage)
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
                if (
                    eligibility == "stale_material"
                    or await cards.generation_materials_stale(session, actor, revision)
                    or any(not material["active_selection"] for material in view["evidence"])
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
                "evidence": [evidence_dependency(row) for row in view["evidence"]] if view else [],
                **(
                    {"memory_lineage": view["memory_lineage"]}
                    if view and view.get("memory_lineage")
                    else {}
                ),
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
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    extraction_job_id: UUID,
    items: list[dict],
    storage: Storage,
):
    from pydantic import TypeAdapter

    from app.schemas.response_card_contracts import EvidenceInput

    adapter = TypeAdapter(EvidenceInput)
    for item in items:
        if item["kind"] == "row":
            for evidence in await cards.linked_evidence(session, UUID(item["card_revision_id"])):
                if evidence.kind == "image_region":
                    from app.services.screenshots import validate_image_evidence

                    await validate_image_evidence(session, actor, evidence, storage=storage)
                    continue
                view = await cards.evidence_view(session, actor, evidence)
                await cards.resolve_material(
                    session,
                    actor,
                    task_id,
                    adapter.validate_python(view["input"]),
                    storage,
                    extraction_job_id=extraction_job_id,
                )


async def submit_draft(
    session: AsyncSession, actor: Identity, task_id: UUID, body: DraftRequest, storage: Storage
):
    actor = await cards.access(session, actor, "draft:run")
    actor.require("card:read")
    await cards.task_lock(session, task_id)
    extraction, items, manifest, input_hash, negatives = await assemble(
        session, actor, task_id, body.extraction_job_id, storage
    )
    await validate_materials(session, actor, task_id, body.extraction_job_id, items, storage)
    if body.dry_run:
        counts = {
            kind: sum(item["kind"] == kind for item in items)
            for kind in ("row", "comply_only", "gap")
        }
        reasons = {}
        for item in items:
            for reason in item.get("gap_reasons", []):
                reasons[reason] = reasons.get(reason, 0) + 1
        data = DraftPreview.model_validate(
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
        ).model_dump(mode="json")
        from app.services import budget_preflight

        return await budget_preflight.attach(
            session,
            data,
            command="draft",
            task_id=task_id,
            input_hash=input_hash,
            currency=session.info["memory_settings"].billing_currency
            if session.info.get("memory_settings")
            else "USD",
            planned_calls=0,
        ), None
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
    assert job.task_id is not None  # Only provider_test jobs may omit the task.
    await cards.task_lock(session, job.task_id)
    extraction_id = UUID(submitted["extraction_job_id"])
    _, items, manifest, input_hash, negatives = await assemble(
        session, actor, job.task_id, extraction_id, storage
    )
    if input_hash != submitted["input_hash"] or manifest != submitted["input_manifest"]:
        cards.fail(
            "draft_input_changed", "Inputs changed; read current inputs and submit again", 409, 3
        )
    await validate_materials(session, actor, job.task_id, extraction_id, items, storage)
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
        "cost": Cost().model_dump(mode="json"),
        "exit_code": 5 if completion == "partial" else 0,
    }


def current_draft_inputs(requirements: list[Requirement], batch: cards.CardReadBatch) -> dict:
    """Recreate the assembly manifest without per-item database round trips.

    Full card projection still checks evidence and uncited model-input grants
    before eligibility precedence (even when comply-only ignores stale inputs).
    Source and material hashes use the unchanged assembly manifest shape.
    """
    current = {}
    for requirement in requirements:
        card = batch.by_requirement.get(requirement.id)
        view = None
        if card is not None:
            revision = batch.revisions.get(card.current_revision_id)
            if revision is None:
                raise not_found()
            view = batch.card_view(card, revision, requirement)
        # Preserve assembly's source checks for missing cards as well as rows.
        batch.citation_valid(requirement)
        cards.location_label_for(requirement, batch.documents.get(requirement.document_id))
        eligibility = view["eligibility"] if view else "missing_card"
        kind = {"eligible": "row", "comply_only": "comply_only"}.get(eligibility, "gap")
        current[str(requirement.id)] = {
            "requirement_id": str(requirement.id),
            "card_revision_id": view["revision_id"] if view else None,
            "source_hash": digest(cards.source(requirement)),
            "category": requirement.category,
            "starred": requirement.starred,
            "kind": kind,
            "eligibility": eligibility,
            "evidence": [evidence_dependency(row) for row in view["evidence"]] if view else [],
            **(
                {"memory_lineage": view["memory_lineage"]}
                if view and view.get("memory_lineage")
                else {}
            ),
        }
    return current


async def load_draft_reads(
    session: AsyncSession,
    actor: Identity,
    runs: list[DraftRun],
    requirements: list[Requirement],
    storage: Storage | None = None,
):
    rows = list(
        (
            await session.scalars(
                select(ResponseItem).where(ResponseItem.draft_id.in_(run.id for run in runs))
            )
        ).all()
    )
    batch = await cards.CardReadBatch.load(
        session,
        actor,
        requirements,
        historical_card_ids={row.card_id for row in rows if row.card_id is not None},
        historical_revision_ids={
            row.card_revision_id for row in rows if row.card_revision_id is not None
        },
        storage=storage,
    )
    grouped: dict[UUID, list[ResponseItem]] = {run.id: [] for run in runs}
    for row in rows:
        grouped[row.draft_id].append(row)
    for run in runs:
        positions = {
            entry["requirement_id"]: index
            for index, entry in enumerate(run.input_manifest["requirements"])
        }
        grouped[run.id].sort(key=lambda item: positions[str(item.requirement_id)])
    return batch, grouped, current_draft_inputs(requirements, batch)


def draft_view(
    run: DraftRun, rows: list[ResponseItem], batch: cards.CardReadBatch, current_inputs: dict
) -> dict:
    tables = {key: [] for key in TABLES}
    invalidated = [
        entry["requirement_id"]
        for entry in run.input_manifest["requirements"]
        if current_inputs.get(entry["requirement_id"]) != entry
    ]
    comply_only, gaps = [], []
    for item in rows:
        requirement = batch.requirements.get(item.requirement_id)
        if requirement is None:
            raise not_found()
        card = batch.cards.get(item.card_id) if item.card_id else None
        if card is None:
            current = batch.by_requirement.get(item.requirement_id)
            if current is not None:
                invalidated.append(str(item.requirement_id))
        elif card.current_revision_id != item.card_revision_id:
            invalidated.append(str(item.requirement_id))
        elif item.kind != "gap":
            revision = (
                batch.revisions.get(item.card_revision_id)
                if item.card_revision_id is not None
                else None
            )
            if revision is None:
                raise not_found()
            view = batch.card_view(card, revision, requirement)
            if view["eligibility"] != ("eligible" if item.kind == "row" else "comply_only"):
                invalidated.append(str(item.requirement_id))
        # A citation already listed as an invalid_citation gap is not a change since generation.
        already_reported = item.kind == "gap" and "invalid_citation" in (item.gap_reasons or [])
        if requirement and not already_reported and not batch.citation_valid(requirement):
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
            material_rows = batch.links.get(item.card_revision_id, [])
            if any(batch.image(row)[2] for row in material_rows if row.kind == "image_region"):
                invalidated.append(str(item.requirement_id))
            entry |= {
                "category": item.category,
                "starred": item.starred,
                "table": item.table,
                **{key: getattr(item, key) for key in cards.CONTENT_FIELDS},
                "evidence": [batch.evidence_view(row) for row in material_rows],
            }
            tables[item.table].append(entry)
        elif item.kind == "comply_only":
            entry |= {"disposition_by": item.disposition_by, "disposition_at": item.disposition_at}
            comply_only.append(entry)
        else:
            entry["reasons"] = item.gap_reasons
            gaps.append(entry)
    memory_warnings, memory_lineage = set(), []
    for item in rows:
        revision = batch.revisions.get(item.card_revision_id) if item.card_revision_id else None
        if revision is not None and revision.model_job_id:
            manifest = batch.generation_manifests.get(revision.model_job_id, {})
            warning = cards.memory_warning_for(revision, manifest, batch.memory_epoch)
            if warning:
                memory_warnings.add(warning + ":" + str(item.requirement_id))
            lineage = cards.memory_lineage_for(manifest, item.requirement_id)
            if lineage:
                memory_lineage.append({"requirement_id": str(item.requirement_id), **lineage})
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
            "memory_warnings": sorted(memory_warnings),
            "memory_lineage": memory_lineage,
        }
    ).model_dump(mode="json")


async def show_draft(
    session: AsyncSession, actor: Identity, draft_id: UUID, storage: Storage | None = None
):
    actor = await cards.access(session, actor, "draft:read")
    run = await session.get(DraftRun, draft_id)
    if run is None:
        raise not_found()
    _, requirements = await cards.extraction_scope(session, run.task_id, run.extraction_job_id)
    batch, grouped, current_inputs = await load_draft_reads(
        session, actor, [run], requirements, storage
    )
    return draft_view(run, grouped[run.id], batch, current_inputs)


async def list_drafts(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    job_id: UUID,
    storage: Storage | None = None,
):
    actor = await cards.access(session, actor, "draft:read")
    _, requirements = await cards.extraction_scope(session, task_id, job_id)
    runs = (
        await session.scalars(
            select(DraftRun)
            .where(DraftRun.task_id == task_id, DraftRun.extraction_job_id == job_id)
            .order_by(DraftRun.created_at, DraftRun.id)
        )
    ).all()
    if not runs:
        return {"task_id": str(task_id), "extraction_job_id": str(job_id)}, []
    batch, grouped, current_inputs = await load_draft_reads(
        session, actor, list(runs), requirements, storage
    )
    items = []
    for run in runs:
        view = draft_view(run, grouped[run.id], batch, current_inputs)
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
                    "memory_warnings",
                    "memory_lineage",
                )
            }
            | {"summary": run.summary}
        )
    return {"task_id": str(task_id), "extraction_job_id": str(job_id)}, items
