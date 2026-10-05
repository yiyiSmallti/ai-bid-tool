"""Score preview, durable submission and append-only report publication."""

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ServiceError, not_found
from app.core.security import Secrets, TokenSigner
from app.jobs.execution import job_cost
from app.models.entities import Job, OrgBalance, Task, UsageRecord, VendorCall
from app.models.score import (
    ScoreItemCitation,
    ScoreReport,
    ScoreReportItem,
    ScoreReportItemResponse,
)
from app.providers import llm as llm_providers
from app.providers.base import ProviderFailure
from app.providers.configured import model_identity
from app.providers.llm import HTTPExtractor, with_reasoning
from app.providers.scoring import (
    ADAPTER_VERSION,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    HTTPScoreProvider,
    score_provider,
    supports_score,
)
from app.schemas.check_contracts import AssessmentInput, AssessmentJobAccepted, AssessmentListData
from app.schemas.contracts import Cost
from app.schemas.score_contracts import (
    ScoreJobResult,
    ScorePreview,
    ScoreReportData,
    ScoreRequest,
    ScoreRunView,
)
from app.services import drafts, redaction, score_generation, score_run_inputs, score_semantic
from app.services import response_cards as cards
from app.services.score_run_inputs import RULE_VERSION, ScoreSnapshot
from app.services.versioned import audit

PARTIAL_STOPS = score_generation.PARTIAL_STOPS
HARD_STOPS = score_generation.HARD_STOPS | {
    "score_input_changed",
    "score_input_integrity",
    "score_usage_integrity",
    "score_stale_draft",
    "score_rubric_unconfirmed",
}


async def resolve(session, settings, job=None):
    return await llm_providers.resolve_llm(session, settings, job=job)


def safe_input(fixed):
    # Persist only the actual redacted provider request plus manifest hashes.
    outbound = fixed.secret.get("outbound")
    return {
        "manifest": fixed.manifest,
        "context": outbound["context"] if outbound else None,
        "provider_items": outbound["provider_items"] if outbound else [],
        "context_only_refs": outbound.get("context_only_refs", []) if outbound else [],
        "preflight": outbound["preflight"] if outbound else {},
    }


async def prepare(session, fixed, llm, requested_reasoning, settings):
    manifest = fixed.manifest
    manifest |= {
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "model": None,
        "reasoning": None,
        "price": None,
        "provider_source": None,
        "outbound_sha256": None,
        "redacted_counts": {},
    }
    if not manifest["model_redaction_enabled"]:
        fixed.input_hash = drafts.digest(manifest)
        return None
    llm, reasoning, _ = with_reasoning(llm, requested_reasoning)
    manifest |= {
        "model": model_identity(llm),
        "reasoning": reasoning,
        "price": score_generation.prices(llm),
        "provider_source": "org" if getattr(llm, "org_owned", False) else "platform",
    }
    fields, library = await score_generation.secret_library(session, fixed.task.id, settings)
    outbound = score_semantic.build_outbound(fixed.secret, fields, library)
    fixed.secret["outbound"] = outbound
    sections = fixed.secret["rubric"]["sections"]
    fixed.secret["display"] = {
        "sections": {
            row["key"]: {
                "title": redaction.redact(row["title"], True, library)[0],
                "aggregation_rule_text": redaction.redact(
                    row["aggregation_rule_text"], True, library
                )[0]
                if row["aggregation_rule_text"] is not None
                else None,
            }
            for row in sections
        },
        "overall_rule_text": redaction.redact(
            fixed.secret["rubric"]["rubric"]["overall_rule_text"], True, library
        )[0]
        if fixed.secret["rubric"]["rubric"]["overall_rule_text"] is not None
        else None,
    }
    manifest["sensitive_report_identifier"] = any(
        redaction.redact(row["key"], True, library)[0] != row["key"] for row in sections
    )
    manifest |= {
        "outbound_sha256": drafts.digest(outbound["context"]),
        "outbound_text_sha256": [
            {"ref": entry["ref"], "sha256": hashlib.sha256(entry["text"].encode()).hexdigest()}
            for entry in outbound["context"]["texts"]
        ],
        "redacted_counts": outbound["redacted_counts"],
        "confidential_hints_sha256": drafts.digest({"fields": fields}),
    }
    if getattr(llm, "org_owned", False):
        fixed.limitations.append("org_key_max_charge_does_not_limit_vendor_bill")
    if getattr(llm, "test_only", False):
        fixed.limitations.append("test_provider_not_real_assessment")
    fixed.input_hash = drafts.digest(manifest)
    return llm


def assessment_input(manifest, input_hash):
    return AssessmentInput.model_validate(
        {
            **{
                key: manifest[key]
                for key in (
                    "org_id",
                    "task_id",
                    "draft_id",
                    "extraction_job_id",
                    "document_id",
                    "draft_input_hash",
                    "assessment_date",
                )
            },
            "input_hash": input_hash,
        }
    ).model_dump(mode="json")


async def preview_cost(
    session: AsyncSession,
    fixed: ScoreSnapshot,
    llm,
    body: ScoreRequest,
    settings: Settings,
) -> dict:
    unknown = {
        "estimated_cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": None},
        "estimated_charge": None,
        "cost_basis": "unknown",
        "cost_basis_reason": "model_unavailable",
        "admission_blocker": None,
    }
    if not fixed.manifest["model_redaction_enabled"]:
        return unknown | {
            "admission_blocker": "redaction_required",
            "cost_basis_reason": "redaction_required",
        }
    if fixed.manifest.get("sensitive_report_identifier"):
        return unknown | {
            "admission_blocker": "sensitive_report_identifier",
            "cost_basis_reason": "fixed_identifier_cannot_be_persisted",
        }
    request = score_semantic.provider_request(fixed.secret["outbound"])
    if request is None:
        return {
            "estimated_cost": Cost().model_dump(mode="json"),
            "estimated_charge": Decimal(0),
            "cost_basis": "known",
            "cost_basis_reason": "no_assessable_items",
            "admission_blocker": None,
        }
    if not supports_score(llm):
        return unknown | {"admission_blocker": "score_capability_unavailable"}
    adapter = score_provider(llm)
    request = score_semantic.provider_request(fixed.secret["outbound"])
    assert request is not None
    if not isinstance(adapter, HTTPScoreProvider):
        if getattr(adapter, "test_only", False):
            return {
                "estimated_cost": Cost().model_dump(mode="json"),
                "estimated_charge": Decimal(0),
                "cost_basis": "known",
                "cost_basis_reason": "test_provider",
                "admission_blocker": None,
            }
        return unknown | {"admission_blocker": "billing_bound_unavailable"}
    try:
        requests = adapter._groups(request)
        bodies = [adapter.request_body(entry) for entry in requests]
        if adapter.llm.platform_model_id is None and not adapter.llm.org_owned:
            return unknown | {
                "admission_blocker": "billing_price_unavailable",
                "cost_basis_reason": "prices_unavailable",
            }
        bounds = [adapter.reservation(entry) for entry in requests]
    except ProviderFailure as error:
        return unknown | {
            "admission_blocker": error.code,
            "cost_basis_reason": "context_limit"
            if error.code == "score_context_limit"
            else "prices_unavailable",
        }
    inputs = sum(len(json.dumps(value, ensure_ascii=False).encode()) + 4096 for value in bodies)
    outputs = sum(adapter.llm.output_token_bound(value) for value in bodies)
    input_price = adapter.llm.settings.llm_input_usd_per_mtok
    output_price = adapter.llm.settings.llm_output_usd_per_mtok
    usd = (
        None
        if input_price is None or output_price is None
        else (inputs * input_price + outputs * output_price) / 1_000_000
    )
    first = bounds[0] if bounds else Decimal(0)
    blocker = None
    if first > settings.job_max_charge:
        blocker = "job_charge_limit_exceeded"
    elif body.max_charge is not None and first > body.max_charge:
        blocker = "spend_cap_below_first_call"
    if adapter.llm.platform_model_id is not None:
        balance = await session.scalar(select(OrgBalance))
        held = await session.scalar(
            select(func.coalesce(func.sum(VendorCall.reserved_charge), 0)).where(
                VendorCall.state != "completed"
            )
        )
        if balance is not None and balance.currency != settings.billing_currency:
            blocker = "billing_currency_mismatch"
        elif balance is None or balance.balance <= 0 or balance.balance - (held or 0) < first:
            blocker = "insufficient_balance"
    return {
        "estimated_cost": {"llm_tokens": inputs + outputs, "ocr_pages": 0, "usd": usd},
        "estimated_charge": sum(bounds, Decimal(0)),
        "cost_basis": "known",
        "cost_basis_reason": (
            "org_key_platform_charge_zero_vendor_cost_estimate"
            if adapter.llm.org_owned
            else "first_pass_token_bound"
        ),
        "admission_blocker": blocker,
    }


async def _submit_score(session, actor, task_id, body, settings, storage):
    actor = await score_run_inputs.access(session, actor, "score:run")
    # The task lock serializes preview inputs with all human rubric/card changes.
    await cards.task_lock(session, task_id)
    fixed = await score_run_inputs.snapshot(
        session, actor, task_id, body.draft_id, body.rubric_id, body.assessment_date
    )
    await score_run_inputs.lock_inputs(session, actor, task_id, fixed.extraction.id)
    fixed = await score_run_inputs.snapshot(
        session, actor, task_id, body.draft_id, body.rubric_id, body.assessment_date
    )
    llm = await resolve(session, settings) if fixed.manifest["model_redaction_enabled"] else None
    llm = await prepare(session, fixed, llm, body.reasoning, settings)
    estimate = await preview_cost(session, fixed, llm, body, settings)
    if body.dry_run:
        model = fixed.manifest["model"]
        data = ScorePreview.model_validate(
            {
                "input": assessment_input(fixed.manifest, fixed.input_hash),
                "selected_item_ids": [entry["id"] for entry in fixed.secret["rubric"]["items"]],
                "rubric_id": fixed.rubric.id,
                "rubric_version": fixed.rubric.version,
                "rubric_input_hash": fixed.rubric.input_hash,
                "prompt_version": PROMPT_VERSION,
                "schema_version": SCHEMA_VERSION,
                "scoring_rule_version": RULE_VERSION,
                "preflight_unassessable_item_ids": list(
                    fixed.secret.get("outbound", {}).get("preflight", {})
                ),
                "limitations": fixed.limitations,
                "billing_currency": settings.billing_currency,
                "redaction_revision": fixed.manifest["model_redaction_revision"],
                "redaction_rule_version": fixed.manifest["redaction_rule_version"],
                "redacted_counts": fixed.manifest["redacted_counts"],
                "max_charge": body.max_charge,
                **(
                    {
                        "provider_config_id": getattr(llm, "provider_config_id", None),
                        "provider_source": fixed.manifest["provider_source"],
                        "platform_model_id": model.get("platform_model_id"),
                        "model_revision": model.get("model_revision"),
                        "model": model["model"],
                        "reasoning": fixed.manifest["reasoning"],
                    }
                    if model
                    else {}
                ),
                **estimate,
            }
        ).model_dump(mode="json")
        from app.services import budget_preflight

        quotes = []
        if (
            isinstance(llm, HTTPExtractor)
            and estimate.get("estimated_charge") is not None
            and estimate.get("cost_basis_reason") not in {"no_assessable_items", "test_provider"}
        ):
            adapter = score_provider(llm)
            request = score_semantic.provider_request(fixed.secret["outbound"])
            if isinstance(adapter, HTTPScoreProvider) and request is not None:
                quotes = [
                    llm.quote(adapter.request_body(entry)) for entry in adapter._groups(request)
                ]
        data = await budget_preflight.attach(
            session,
            data,
            command="score run",
            task_id=task_id,
            input_hash=fixed.input_hash,
            currency=settings.billing_currency,
            settings=settings,
            quotes=quotes,
            planned_calls=len(quotes)
            if quotes
            or estimate.get("cost_basis_reason") in {"no_assessable_items", "test_provider"}
            else None,
            max_charge=body.max_charge,
        )
        return data, None
    if estimate["admission_blocker"] == "redaction_required":
        cards.fail("redaction_required", "Scoring requires redaction", 409, 4)
    if fixed.input_hash != body.expected_input_hash:
        cards.fail("score_input_changed", "Inputs changed since preview; preview again", 409)
    cache_key = drafts.digest(
        {
            "kind": "score",
            "org_id": str(actor.org_id),
            "task_id": str(task_id),
            "actor_user_id": str(actor.user_id),
            "actor_token_id": str(actor.token_id) if actor.token_id else None,
            "actor_kind": actor.actor_kind,
            "input_hash": fixed.input_hash,
        }
    )
    job = await session.scalar(select(Job).where(Job.cache_key == cache_key).with_for_update())
    cached = job is not None
    if job is None:
        if estimate["admission_blocker"]:
            cards.fail(
                estimate["admission_blocker"],
                "Scoring admission was refused; inspect a new preview",
                409,
                4,
            )
        job = Job(
            id=uuid4(),
            org_id=actor.org_id,
            task_id=task_id,
            document_id=fixed.document.id,
            kind="score",
            cache_key=cache_key,
            status="queued",
            reasoning=fixed.manifest["reasoning"],
            provider_config_id=getattr(llm, "provider_config_id", None),
            provider_identity=fixed.manifest["model"],
            result={
                "submission": {
                    "input_manifest": fixed.manifest,
                    "input_hash": fixed.input_hash,
                    "encrypted_input": Secrets.for_data(settings).encrypt(
                        json.dumps(safe_input(fixed), ensure_ascii=False)
                    ),
                    "actor_user_id": str(actor.user_id),
                    "actor_token_id": str(actor.token_id) if actor.token_id else None,
                    "actor_kind": actor.actor_kind,
                    "scopes": sorted(actor.scopes),
                    "provider_source": fixed.manifest["provider_source"],
                    "max_charge": str(body.max_charge) if body.max_charge is not None else None,
                }
            },
        )
        session.add(job)
        await session.flush()
        audit(
            session,
            actor,
            "score.submitted",
            job.id,
            {
                "task_id": str(task_id),
                "job_id": str(job.id),
                "input_hash": fixed.input_hash,
                "rubric_id": str(fixed.rubric.id),
                "actor_kind": actor.actor_kind,
            },
        )
    elif body.retry and (
        job.status in {"failed", "cancelled"}
        or (job.status == "running" and job.lease_until and job.lease_until < datetime.now(UTC))
    ):
        if estimate["admission_blocker"]:
            cards.fail(estimate["admission_blocker"], "Scoring admission was refused", 409, 4)
        job.status, job.error, job.queue_id, job.attempts = "queued", None, None, 0
        job.lease_until, job.finished_at, job.run_id = None, None, None
        if body.max_charge is not None:
            job.result = {
                **job.result,
                "submission": {**job.result["submission"], "max_charge": str(body.max_charge)},
            }
    elif body.max_charge is not None and job.status in {"queued", "running"}:
        cap = job.result["submission"].get("max_charge")
        if cap is None or Decimal(cap) > body.max_charge:
            cards.fail("score_cap_conflict", "Existing score job has a higher charge cap", 409)
    await session.flush()
    return AssessmentJobAccepted.model_validate(
        {"job_id": job.id, "status": job.status, "cached": cached}
    ).model_dump(mode="json"), job


async def submit_score(session, actor, task_id, body, settings, storage):
    try:
        return await _submit_score(session, actor, task_id, body, settings, storage)
    except (ServiceError, ProviderFailure) as failure:
        error = (
            failure
            if isinstance(failure, ServiceError)
            else ServiceError(
                failure.code,
                "Scoring provider configuration cannot be used; inspect the error code",
                503 if failure.retryable else 409,
                3 if failure.retryable else 4,
            )
        )
        if (
            not body.dry_run
            and error.status != 404
            and error.code not in {"forbidden", "human_required"}
        ):
            error.__dict__["score_audit"] = {
                "action": "score.failed",
                "object_id": str(task_id),
                "task_id": str(task_id),
                "input_hash": body.expected_input_hash,
                "error_code": error.code,
                "actor_kind": actor.actor_kind,
            }
        raise error from None


def worker(job):
    return score_generation.worker(job)


async def job_access(session, actor, job, storage, *, cancel=False):
    actor = await score_run_inputs.access(session, actor, "score:run" if cancel else "score:read")
    if job.kind != "score" or job.task_id is None:
        raise not_found()
    await score_run_inputs.require_dependencies(
        session, actor, job.task_id, job.result["submission"]["input_manifest"]
    )


async def publish(session, actor, job, fixed, evaluated, settings, stop_reason=None):
    if job.task_id is None or job.run_id is None:
        score_run_inputs.integrity()
    sections, summary = score_semantic.aggregate(
        fixed.secret["rubric"], evaluated, provider_complete=stop_reason is None
    )
    # Decimal stays exact through aggregation; encode as strings for immutable JSON.
    sections = jsonable_encoder(sections, custom_encoder={Decimal: str})
    summary = jsonable_encoder(summary, custom_encoder={Decimal: str})
    for section in sections:
        section.update(fixed.secret["display"]["sections"][section["section_key"]])
    summary["overall_rule_text"] = fixed.secret["display"]["overall_rule_text"]
    completion = summary.pop("completion")
    limitations = list(dict.fromkeys([*fixed.limitations, *summary.pop("limitations", [])]))
    run = ScoreReport(
        id=uuid4(),
        org_id=job.org_id,
        task_id=job.task_id,
        job_id=job.id,
        run_id=job.run_id,
        draft_id=fixed.draft.id,
        extraction_job_id=fixed.extraction.id,
        document_id=fixed.document.id,
        rubric_id=fixed.rubric.id,
        rubric_version=fixed.rubric.version,
        rubric_input_hash=fixed.rubric.input_hash,
        rubric_revision=fixed.manifest["rubric_revision"],
        input_hash=fixed.input_hash,
        draft_input_hash=fixed.draft.input_hash,
        assessment_date=date.fromisoformat(fixed.manifest["assessment_date"]),
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        scoring_rule_version=RULE_VERSION,
        input_manifest=fixed.manifest,
        encrypted_input=job.result["submission"]["encrypted_input"],
        completion=completion,
        limitations=limitations,
        summary=summary,
        sections=sections,
        actor_user_id=actor.user_id,
        actor_token_id=actor.token_id,
        actor_kind="worker",
    )
    session.add(run)
    await session.flush()
    rubric_items = {entry["id"]: entry for entry in fixed.secret["rubric"]["items"]}
    response_items = {entry["response_item_id"]: entry for entry in fixed.secret["items"]}
    for item in evaluated:
        item = jsonable_encoder(item, custom_encoder={Decimal: str})
        row = ScoreReportItem(
            id=uuid4(),
            org_id=job.org_id,
            task_id=job.task_id,
            report_id=run.id,
            draft_id=run.draft_id,
            extraction_job_id=run.extraction_job_id,
            rubric_id=run.rubric_id,
            rubric_item_id=UUID(str(item["rubric_item_id"])),
            section_id=UUID(rubric_items[str(item["rubric_item_id"])]["section_id"]),
            requirement_id=UUID(str(item["requirement_id"])),
            anchor_response_item_id=UUID(str(item["anchor_response_item_id"])),
            **{
                key: item[key]
                for key in (
                    "anchor_partition",
                    "outcome",
                    "score_range",
                    "estimated_score",
                    "reason_code",
                    "reason",
                    "deduction_reasons",
                    "strengthening_actions",
                )
            },
        )
        session.add(row)
        await session.flush()
        for response_id in item["response_item_ids"]:
            response = response_items[str(response_id)]
            session.add(
                ScoreReportItemResponse(
                    id=uuid4(),
                    org_id=job.org_id,
                    task_id=job.task_id,
                    report_id=run.id,
                    score_item_id=row.id,
                    draft_id=run.draft_id,
                    response_item_id=UUID(str(response_id)),
                    requirement_id=UUID(response["requirement_id"]),
                    card_revision_id=UUID(response["card_revision_id"]),
                )
            )
        await session.flush()
        for citation in item["citations"]:
            if citation["kind"] == "tender":
                source = citation["source"]
                fields = {
                    "document_id": UUID(source["document_id"]),
                    "chunk_id": UUID(source["chunk_id"]),
                    "source": source,
                    "quote": source["quote"],
                }
            else:
                fields = {
                    key: UUID(str(citation[key]))
                    for key in ("draft_id", "response_item_id", "card_revision_id")
                }
                fields |= {"field": citation["field"], "quote": citation["quote"]}
            session.add(
                ScoreItemCitation(
                    id=uuid4(),
                    org_id=job.org_id,
                    task_id=job.task_id,
                    report_id=run.id,
                    score_item_id=row.id,
                    kind=citation["kind"],
                    **fields,
                )
            )
    await session.flush()
    usage_rows = list(
        (await session.scalars(select(UsageRecord).where(UsageRecord.job_id == job.id))).all()
    )
    audit(
        session,
        actor,
        "score.completed",
        run.id,
        {
            "task_id": str(job.task_id),
            "job_id": str(job.id),
            "run_id": str(job.run_id),
            "report_id": str(run.id),
            "input_hash": run.input_hash,
            "actor_kind": "worker",
            "usage_ids": [str(row.id) for row in usage_rows],
        },
    )
    result = ScoreJobResult.model_validate(
        {
            "report_id": run.id,
            "job_id": job.id,
            "completion": completion,
            "usage_record_ids": [row.id for row in usage_rows],
            "charge": sum(
                (Decimal(str(row.charge)) for row in usage_rows if row.charge is not None),
                Decimal(0),
            ),
            "billing_currency": settings.billing_currency,
            "stop_reason": stop_reason,
            **{
                key: summary[key]
                for key in (
                    "assessed_items",
                    "unassessable_items",
                    "assessed_subtotal",
                    "total_status",
                    "estimated_total",
                )
            },
        }
    ).model_dump(mode="json")
    return {
        **result,
        "cost": await job_cost(session, job.id),
        "warnings": limitations,
        "exit_code": 0
        if completion == "complete" and summary["total_status"] == "estimated"
        else 5,
    }


async def run_view(session, actor, run, settings):
    invalidated = []
    try:
        fixed = await score_run_inputs.snapshot(
            session, actor, run.task_id, run.draft_id, run.rubric_id, run.assessment_date
        )
        job = await session.get(Job, run.job_id)
        if job is None:
            raise not_found()
        llm = (
            await resolve(session, settings, job)
            if fixed.manifest["model_redaction_enabled"]
            else None
        )
        await prepare(session, fixed, llm, job.reasoning, settings)
        if fixed.input_hash != run.input_hash:
            invalidated = ["score_input_changed"]
    except ProviderFailure:
        invalidated = ["score_input_changed", "provider_model_changed"]
    except ServiceError as error:
        if error.code not in {
            "not_found",
            "score_stale_draft",
            "score_rubric_unconfirmed",
            "score_input_integrity",
            "rubric_input_changed",
            "invalid_input_citation",
            "invalid_extraction_job",
            "provider_unavailable",
        }:
            raise
        invalidated = ["score_input_changed", error.code]
    usage_ids = list(
        (
            await session.scalars(select(UsageRecord.id).where(UsageRecord.job_id == run.job_id))
        ).all()
    )
    return ScoreRunView.model_validate(
        {
            "id": run.id,
            "org_id": run.org_id,
            "task_id": run.task_id,
            "job_id": run.job_id,
            "run_id": run.run_id,
            "input": assessment_input(run.input_manifest, run.input_hash),
            "rubric_id": run.rubric_id,
            "rubric_version": run.rubric_version,
            "rubric_input_hash": run.rubric_input_hash,
            "prompt_version": run.prompt_version,
            "schema_version": run.schema_version,
            "scoring_rule_version": run.scoring_rule_version,
            "assessment_date": run.assessment_date,
            "created_at": run.created_at,
            "completion": run.completion,
            "validity": "stale" if invalidated else "current",
            "invalidation_codes": invalidated,
            "limitations": run.limitations,
            "usage_record_ids": usage_ids,
            **run.summary,
        }
    ).model_dump(mode="json")


async def show_score(session, actor, task_id, report_id, settings, storage):
    actor = await score_run_inputs.access(session, actor, "score:read")
    run = await session.get(ScoreReport, report_id)
    if run is None or run.task_id != task_id:
        raise not_found()
    await score_run_inputs.require_dependencies(session, actor, task_id, run.input_manifest)
    from app.models.score import ScoreRubricItem, ScoreRubricSection

    items = []
    for row in (
        await session.scalars(
            select(ScoreReportItem)
            .where(ScoreReportItem.report_id == run.id)
            .order_by(ScoreReportItem.created_at, ScoreReportItem.id)
        )
    ).all():
        rubric_item = await session.get(ScoreRubricItem, row.rubric_item_id)
        section = await session.get(ScoreRubricSection, row.section_id)
        if rubric_item is None or section is None:
            raise not_found()
        response_ids = list(
            (
                await session.scalars(
                    select(ScoreReportItemResponse.response_item_id)
                    .where(ScoreReportItemResponse.score_item_id == row.id)
                    .order_by(ScoreReportItemResponse.response_item_id)
                )
            ).all()
        )
        citations = []
        for citation in (
            await session.scalars(
                select(ScoreItemCitation)
                .where(ScoreItemCitation.score_item_id == row.id)
                .order_by(ScoreItemCitation.created_at, ScoreItemCitation.id)
            )
        ).all():
            if citation.kind == "tender":
                citations.append({"kind": "tender", "source": citation.source})
            else:
                citations.append(
                    {
                        "kind": "draft",
                        **{
                            key: getattr(citation, key)
                            for key in (
                                "draft_id",
                                "response_item_id",
                                "card_revision_id",
                                "field",
                                "quote",
                            )
                        },
                    }
                )
        items.append(
            {
                **{
                    key: getattr(row, key)
                    for key in (
                        "id",
                        "org_id",
                        "task_id",
                        "report_id",
                        "rubric_item_id",
                        "requirement_id",
                        "anchor_response_item_id",
                        "anchor_partition",
                        "outcome",
                        "score_range",
                        "estimated_score",
                        "reason_code",
                        "reason",
                        "deduction_reasons",
                        "strengthening_actions",
                    )
                },
                "section_key": section.key,
                "response_item_ids": response_ids,
                "citations": citations,
            }
        )
    return ScoreReportData.model_validate(
        {
            "report": await run_view(session, actor, run, settings),
            "sections": run.sections,
            "items": items,
        }
    ).model_dump(mode="json")


async def list_scores(session, actor, task_id, settings, storage, *, limit=50, cursor=None):
    actor = await score_run_inputs.access(session, actor, "score:read")
    if await session.get(Task, task_id) is None:
        raise not_found()
    if not 1 <= limit <= 200:
        cards.fail("invalid_input", "Limit must be between 1 and 200")
    binding = {
        "kind": "score_cursor",
        "org_id": str(actor.org_id),
        "task_id": str(task_id),
        "sort": "created_at_id_asc",
    }
    anchor = None
    if cursor:
        try:
            payload = TokenSigner.for_tokens(settings).open(cursor)
            if any(payload.get(key) != value for key, value in binding.items()):
                raise ValueError("binding")
            anchor = (datetime.fromisoformat(payload["created_at"]), UUID(payload["id"]))
        except (ServiceError, ValueError, KeyError, TypeError):
            cards.fail("invalid_cursor", "Cursor does not match this authorized query")
    rows = list(
        (
            await session.scalars(
                select(ScoreReport)
                .where(ScoreReport.task_id == task_id)
                .order_by(ScoreReport.created_at, ScoreReport.id)
            )
        ).all()
    )
    for row in rows:
        await score_run_inputs.require_dependencies(session, actor, task_id, row.input_manifest)
    selected = [row for row in rows if anchor is None or (row.created_at, row.id) > anchor]
    next_cursor = None
    if len(selected) > limit:
        last = selected[limit - 1]
        next_cursor = TokenSigner.for_tokens(settings).issue(
            {**binding, "created_at": last.created_at.isoformat(), "id": str(last.id)}, 7 * 86400
        )
    return AssessmentListData(task_id=task_id, total=len(rows), next_cursor=next_cursor).model_dump(
        mode="json"
    ), [await run_view(session, actor, row, settings) for row in selected[:limit]]
