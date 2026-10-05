"""Durable assessment reports and human-only, append-only false-positive decisions."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ServiceError, not_found
from app.core.security import Secrets, TokenSigner
from app.jobs.execution import job_cost
from app.models.check import (
    CheckCertificate,
    CheckCertificateItem,
    CheckDecision,
    CheckFinding,
    CheckFindingCitation,
    CheckItem,
    CheckRun,
)
from app.models.entities import Job, Task, UsageRecord
from app.providers.base import ProviderFailure
from app.providers.storage import Storage
from app.schemas.check_contracts import (
    AssessmentInput,
    AssessmentJobAccepted,
    AssessmentListData,
    CheckCertificateView,
    CheckItemView,
    CheckJobResult,
    CheckPreview,
    CheckReportData,
    CheckRequest,
    CheckRunView,
    FindingCitationView,
    FindingDecisionData,
    FindingDecisionRequest,
    FindingDecisionView,
    FindingView,
)
from app.schemas.contracts import Cost
from app.services import check_inputs, check_rules, check_semantic, drafts
from app.services import response_cards as cards
from app.services.auth import Identity
from app.services.check_inputs import ADAPTER_VERSION, RULE_VERSION, SCHEMA_VERSION, CheckSnapshot
from app.services.versioned import audit


def assessment_input(manifest: dict, input_hash: str) -> dict:
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


async def submit_check(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    body: CheckRequest,
    storage: Storage,
    settings: Settings,
) -> tuple[dict, Job | None]:
    actor = await check_inputs.access(session, actor, "check:run")
    await check_inputs.lock_inputs(session, actor, task_id)
    try:
        fixed = await check_inputs.snapshot(
            session,
            actor,
            task_id,
            body.draft_id,
            body.assessment_date,
            storage,
            semantic=body.mode == "combined",
        )
    except ServiceError as error:
        if not body.dry_run and error.code == "check_stale_draft":
            cards.fail(
                "check_input_changed",
                "Draft inputs changed since preview; assemble and preview again",
                409,
            )
        raise
    # Bound output limits are checked at preview as well as execution. This is
    # deterministic local analysis; it creates no report, audit or usage row.
    check_rules.evaluate(fixed.secret, str(fixed.draft.id))
    llm, semantic_preview = None, {}
    if body.mode == "combined":
        llm = (
            await check_semantic.resolve(session, settings)
            if fixed.manifest["model_redaction_enabled"]
            else None
        )
        llm = await check_semantic.prepare(session, fixed, llm, body.reasoning, settings)
        semantic_preview = await check_semantic.preview(session, fixed, llm, body, settings)
    if body.dry_run:
        return CheckPreview.model_validate(
            {
                "input": assessment_input(fixed.manifest, fixed.input_hash),
                "selected_item_ids": [item["requirement_id"] for item in fixed.secret["items"]],
                "estimated_cost": Cost(),
                "estimated_charge": Decimal(0),
                "billing_currency": settings.billing_currency,
                "cost_basis": "known",
                "cost_basis_reason": "no_model_calls",
                "redaction_revision": fixed.manifest["model_redaction_revision"],
                "redaction_rule_version": fixed.manifest["redaction_rule_version"],
                "redacted_counts": fixed.manifest.get("redacted_counts", {}),
                "mode": body.mode,
                "rule_version": RULE_VERSION,
                "prompt_version": fixed.manifest["prompt_version"],
                "schema_version": fixed.manifest["schema_version"],
                "rules_applicable": len(fixed.secret["items"]),
                "semantic_items": len(fixed.secret["items"]) if body.mode == "combined" else 0,
                "gap_requirements": sum(
                    item["partition"] == "gap" for item in fixed.secret["items"]
                ),
                "limitations": fixed.limitations,
                "max_charge": body.max_charge,
                **(
                    {
                        "provider_config_id": getattr(llm, "provider_config_id", None),
                        "provider_source": fixed.manifest["provider_source"],
                        "platform_model_id": fixed.manifest["model"].get("platform_model_id"),
                        "model_revision": fixed.manifest["model"].get("model_revision"),
                        "model": fixed.manifest["model"]["model"],
                        "reasoning": fixed.manifest["reasoning"],
                    }
                    if llm is not None
                    else {}
                ),
                **semantic_preview,
            }
        ).model_dump(mode="json"), None
    if semantic_preview.get("admission_blocker") == "redaction_required":
        cards.fail("redaction_required", "Combined checks require redaction to be enabled", 409, 4)
    if fixed.input_hash != body.expected_input_hash:
        cards.fail(
            "check_input_changed",
            "Inputs changed since preview; preview the current draft again",
            409,
        )
    cache_key = drafts.digest(
        {
            "kind": "check",
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
        if semantic_preview.get("admission_blocker"):
            cards.fail(
                semantic_preview["admission_blocker"],
                "Combined check admission was refused; inspect a new preview",
                409,
                4,
            )
        job = Job(
            id=uuid4(),
            org_id=actor.org_id,
            task_id=task_id,
            document_id=fixed.extraction.document_id,
            kind="check",
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
                        json.dumps(fixed.secret, ensure_ascii=False)
                    ),
                    "actor_user_id": str(actor.user_id),
                    "actor_token_id": str(actor.token_id) if actor.token_id else None,
                    "actor_kind": actor.actor_kind,
                    "scopes": sorted(actor.scopes),
                    "limitations": fixed.limitations,
                    "provider_source": fixed.manifest.get("provider_source"),
                    "max_charge": str(body.max_charge) if body.max_charge is not None else None,
                }
            },
        )
        session.add(job)
        await session.flush()
        audit(
            session,
            actor,
            "check.submit",
            job.id,
            {
                "task_id": str(task_id),
                "job_id": str(job.id),
                "draft_id": str(fixed.draft.id),
                "input_hash": fixed.input_hash,
                "rule_version": RULE_VERSION,
                "schema_version": SCHEMA_VERSION,
                "item_count": len(fixed.secret["items"]),
            },
        )
    elif body.retry and (
        job.status in {"failed", "cancelled"}
        or (job.status == "running" and job.lease_until and job.lease_until < datetime.now(UTC))
    ):
        if semantic_preview.get("admission_blocker"):
            cards.fail(
                semantic_preview["admission_blocker"],
                "Combined check admission was refused",
                409,
                4,
            )
        job.status, job.error, job.queue_id, job.attempts = "queued", None, None, 0
        if body.max_charge is not None:
            job.result = {
                **job.result,
                "submission": {**job.result["submission"], "max_charge": str(body.max_charge)},
            }
        job.lease_until, job.finished_at, job.run_id = None, None, None
    elif body.max_charge is not None and job.status in {"queued", "running"}:
        cap = job.result["submission"].get("max_charge")
        if cap is None or Decimal(cap) > body.max_charge:
            cards.fail("check_cap_conflict", "The existing check has a higher charge cap", 409, 2)
    await session.flush()
    return AssessmentJobAccepted.model_validate(
        {"job_id": job.id, "status": job.status, "cached": cached}
    ).model_dump(mode="json"), job


def worker(job: Job) -> Identity:
    submission = job.result["submission"]
    return Identity(
        UUID(submission["actor_user_id"]),
        job.org_id,
        set(submission["scopes"]),
        "viewer",
        UUID(submission["actor_token_id"]) if submission["actor_token_id"] else None,
        "worker",
    )


async def job_access(
    session: AsyncSession,
    actor: Identity,
    job: Job,
    storage: Storage,
    *,
    cancel: bool = False,
) -> None:
    actor = await check_inputs.access(session, actor, "check:run" if cancel else "check:read")
    if job.kind != "check" or job.task_id is None:
        raise not_found()
    await check_inputs.require_dependencies(
        session, actor, job.task_id, job.result["submission"]["input_manifest"], storage
    )


def store_citation(session, job, run, citation, *, finding_id=None, check_item_id=None):
    if citation["kind"] == "tender":
        source = citation["source"]
        fields = {
            "document_id": UUID(source["document_id"]),
            "chunk_id": UUID(source["chunk_id"]),
            "source": source,
            "quote": source["quote"],
        }
    elif citation["kind"] == "draft":
        fields = {
            key: UUID(citation[key]) for key in ("draft_id", "response_item_id", "card_revision_id")
        }
        fields |= {"field": citation["field"], "quote": citation["quote"]}
    else:
        fields = {"evidence_id": UUID(citation["evidence_id"]), "quote": citation["quote"]}
    session.add(
        CheckFindingCitation(
            id=uuid4(),
            org_id=job.org_id,
            task_id=job.task_id,
            report_id=run.id,
            finding_id=finding_id,
            check_item_id=check_item_id,
            kind=citation["kind"],
            **fields,
        )
    )


async def publish(
    session: AsyncSession,
    actor: Identity,
    job: Job,
    fixed: CheckSnapshot,
    evaluated: list[dict],
    settings: Settings,
    stop_reason: str | None = None,
) -> dict:
    submitted = job.result["submission"]
    if job.task_id is None or job.run_id is None:
        check_inputs.integrity()
    unassessed = sum(item["unassessed"] for item in evaluated)
    finding_count = sum(len(item["findings"]) for item in evaluated)
    completion = "partial" if unassessed else "complete"
    run = CheckRun(
        id=uuid4(),
        org_id=job.org_id,
        task_id=job.task_id,
        job_id=job.id,
        run_id=job.run_id,
        draft_id=fixed.draft.id,
        extraction_job_id=fixed.extraction.id,
        document_id=fixed.extraction.document_id,
        input_hash=fixed.input_hash,
        draft_input_hash=fixed.draft.input_hash,
        assessment_date=date.fromisoformat(fixed.manifest["assessment_date"]),
        mode=fixed.manifest["mode"],
        rule_version=RULE_VERSION,
        prompt_version=fixed.manifest["prompt_version"],
        schema_version=fixed.manifest["schema_version"],
        input_manifest=fixed.manifest,
        encrypted_input=submitted["encrypted_input"],
        completion=completion,
        limitations=fixed.limitations,
        summary={
            "item_count": len(evaluated),
            "finding_count": finding_count,
            "unassessed_count": unassessed,
        },
        actor_user_id=actor.user_id,
        actor_token_id=actor.token_id,
        actor_kind="worker",
    )
    session.add(run)
    await session.flush()
    by_requirement: dict[str, CheckItem] = {}
    for result in evaluated:
        item = result["item"]
        coverage = CheckItem(
            id=uuid4(),
            org_id=job.org_id,
            task_id=job.task_id,
            report_id=run.id,
            draft_id=fixed.draft.id,
            extraction_job_id=fixed.extraction.id,
            requirement_id=UUID(item["requirement_id"]),
            response_item_id=UUID(item["response_item_id"]),
            card_revision_id=UUID(item["card_revision_id"]) if item["card_revision_id"] else None,
            partition=item["partition"],
            source=item["source"],
            rules=result["rules"],
            semantic_status=result.get("semantic_status", "not_requested"),
            semantic_reason_code=result.get("semantic_reason_code"),
            semantic_outcome=result.get("semantic_outcome"),
        )
        session.add(coverage)
        await session.flush()
        by_requirement[item["requirement_id"]] = coverage
        for finding in result["findings"]:
            row = CheckFinding(
                id=uuid4(),
                org_id=job.org_id,
                task_id=job.task_id,
                report_id=run.id,
                check_item_id=coverage.id,
                requirement_id=coverage.requirement_id,
                method=finding.get("method", "deterministic"),
                code=finding["code"],
                severity=finding["severity"],
                review_domain=item["review_domain"],
                reason=finding["reason"],
                source=item["source"],
            )
            session.add(row)
            await session.flush()
            for citation in finding["citations"]:
                store_citation(session, job, run, citation, finding_id=row.id)
        for citation in result.get("semantic_citations", []):
            store_citation(session, job, run, citation, check_item_id=coverage.id)
    await session.flush()
    for item in fixed.secret["certificates"]:
        certificate = CheckCertificate(
            id=uuid4(),
            org_id=job.org_id,
            task_id=job.task_id,
            report_id=run.id,
            task_certificate_id=UUID(item["task_certificate_id"]),
            certificate_revision_id=UUID(item["certificate_revision_id"]),
            assessment_date=run.assessment_date,
            date_status=item["date_status"],
        )
        session.add(certificate)
        await session.flush()
        for requirement_id in item["requirement_ids"]:
            session.add(
                CheckCertificateItem(
                    org_id=job.org_id,
                    task_id=job.task_id,
                    report_id=run.id,
                    certificate_id=certificate.id,
                    check_item_id=by_requirement[requirement_id].id,
                )
            )
    await session.flush()
    audit(
        session,
        actor,
        "check.publish",
        run.id,
        {
            "task_id": str(job.task_id),
            "job_id": str(job.id),
            "run_id": str(job.run_id),
            "report_id": str(run.id),
            "input_hash": run.input_hash,
            "completion": completion,
            **run.summary,
        },
    )
    usage_rows = list(
        (await session.scalars(select(UsageRecord).where(UsageRecord.job_id == job.id))).all()
    )
    if run.mode == "rules" and usage_rows:
        cards.fail("check_usage_integrity", "Rules checks cannot have model usage records", 500, 4)
    return {
        **CheckJobResult.model_validate(
            {
                "report_id": run.id,
                "job_id": job.id,
                "completion": completion,
                "usage_record_ids": [row.id for row in usage_rows],
                "charge": sum(
                    (Decimal(str(row.charge)) for row in usage_rows if row.charge is not None),
                    Decimal(0),
                ),
                "stop_reason": stop_reason,
                "billing_currency": settings.billing_currency,
                "checked_requirements": len(evaluated),
                "finding_count": finding_count,
                "unassessed_requirements": unassessed,
            }
        ).model_dump(mode="json"),
        "cost": await job_cost(session, job.id),
        "warnings": fixed.limitations,
        "exit_code": 5 if unassessed else 0,
    }


async def get_run(
    session: AsyncSession, actor: Identity, report_id: UUID, storage: Storage
) -> tuple[Identity, CheckRun]:
    actor = await check_inputs.access(session, actor, "check:read")
    run = await session.get(CheckRun, report_id)
    if run is None:
        raise not_found()
    await check_inputs.require_dependencies(
        session, actor, run.task_id, run.input_manifest, storage
    )
    return actor, run


async def validity(
    session: AsyncSession, actor: Identity, run: CheckRun, storage: Storage, settings: Settings
) -> list[str]:
    combined = run.mode == "combined"
    if (
        run.rule_version != RULE_VERSION
        or run.schema_version != (check_semantic.SCHEMA_VERSION if combined else SCHEMA_VERSION)
        or run.input_manifest["adapter_version"]
        != (check_semantic.ADAPTER_VERSION if combined else ADAPTER_VERSION)
        or run.prompt_version != (check_semantic.PROMPT_VERSION if combined else None)
    ):
        return ["check_input_changed"]
    try:
        fixed = await check_inputs.snapshot(
            session,
            actor,
            run.task_id,
            run.draft_id,
            run.assessment_date,
            storage,
            semantic=combined,
        )
        if combined:
            # The immutable model/price identity belongs to this job, never today's default.
            job = await session.get(Job, run.job_id)
            if job is None:
                raise not_found()
            llm = await check_semantic.resolve(session, settings, job)
            await check_semantic.prepare(session, fixed, llm, job.reasoning, settings)
    except ProviderFailure:
        return ["check_input_changed", "provider_model_changed"]
    except ServiceError as error:
        if error.code not in {
            "check_stale_draft",
            "invalid_input_citation",
            "check_input_integrity",
            "empty_requirements",
            "invalid_extraction_job",
            "provider_unavailable",
        }:
            raise
        return ["check_input_changed", error.code]
    return [] if fixed.input_hash == run.input_hash else ["check_input_changed"]


async def run_view(
    session: AsyncSession, actor: Identity, run: CheckRun, storage: Storage, settings: Settings
) -> dict:
    invalidated = await validity(session, actor, run, storage, settings)
    usage_ids = list(
        (
            await session.scalars(select(UsageRecord.id).where(UsageRecord.job_id == run.job_id))
        ).all()
    )
    if run.mode == "rules" and usage_ids:
        cards.fail("check_usage_integrity", "Rules checks cannot have model usage records", 500, 4)
    return CheckRunView.model_validate(
        {
            "id": run.id,
            "org_id": run.org_id,
            "task_id": run.task_id,
            "job_id": run.job_id,
            "run_id": run.run_id,
            "input": assessment_input(run.input_manifest, run.input_hash),
            "mode": run.mode,
            "rule_version": run.rule_version,
            "prompt_version": run.prompt_version,
            "schema_version": run.schema_version,
            "created_at": run.created_at,
            "completion": run.completion,
            "validity": "stale" if invalidated else "current",
            "invalidation_codes": invalidated,
            **run.summary,
            "limitations": run.limitations,
            "usage_record_ids": usage_ids,
        }
    ).model_dump(mode="json")


def citation_payload(row: CheckFindingCitation) -> dict:
    citation: dict = {"kind": row.kind}
    if row.kind == "tender":
        citation["source"] = row.source
    elif row.kind == "draft":
        citation |= {
            key: getattr(row, key)
            for key in ("draft_id", "response_item_id", "card_revision_id", "field", "quote")
        }
    else:
        citation |= {"evidence_id": row.evidence_id, "quote": row.quote}
    return citation


def citation_view(row: CheckFindingCitation) -> dict:
    return FindingCitationView.model_validate(
        {
            **{
                key: getattr(row, key)
                for key in ("id", "org_id", "task_id", "report_id", "finding_id")
            },
            "citation": citation_payload(row),
        }
    ).model_dump(mode="json")


async def finding_view(session: AsyncSession, finding: CheckFinding) -> dict:
    latest = await session.scalar(
        select(CheckDecision)
        .where(CheckDecision.finding_id == finding.id)
        .order_by(CheckDecision.revision.desc())
        .limit(1)
    )
    citations = (
        await session.scalars(
            select(CheckFindingCitation)
            .where(CheckFindingCitation.finding_id == finding.id)
            .order_by(CheckFindingCitation.created_at, CheckFindingCitation.id)
        )
    ).all()
    return FindingView.model_validate(
        {
            **{
                key: getattr(finding, key)
                for key in (
                    "id",
                    "org_id",
                    "task_id",
                    "report_id",
                    "check_item_id",
                    "requirement_id",
                    "method",
                    "code",
                    "severity",
                    "review_domain",
                    "reason",
                    "source",
                )
            },
            "citations": [citation_view(row) for row in citations],
            "status": "dismissed" if latest and latest.action == "dismiss" else "open",
            "revision": latest.revision if latest else 1,
            "latest_decision_id": latest.id if latest else None,
        }
    ).model_dump(mode="json")


async def show_check(
    session: AsyncSession,
    actor: Identity,
    report_id: UUID,
    storage: Storage,
    settings: Settings,
) -> tuple[dict, list[dict]]:
    actor, run = await get_run(session, actor, report_id, storage)
    report = await run_view(session, actor, run, storage, settings)
    findings = [
        await finding_view(session, row)
        for row in (
            await session.scalars(
                select(CheckFinding)
                .where(CheckFinding.report_id == run.id)
                .order_by(CheckFinding.created_at, CheckFinding.id)
            )
        ).all()
    ]
    coverage = [
        CheckItemView.model_validate(
            {
                **{
                    key: getattr(row, key)
                    for key in (
                        "id",
                        "org_id",
                        "task_id",
                        "report_id",
                        "requirement_id",
                        "response_item_id",
                        "card_revision_id",
                        "partition",
                        "source",
                        "rules",
                        "semantic_status",
                        "semantic_reason_code",
                        "semantic_outcome",
                    )
                },
                "semantic_citations": [
                    citation_payload(citation)
                    for citation in (
                        await session.scalars(
                            select(CheckFindingCitation)
                            .where(CheckFindingCitation.check_item_id == row.id)
                            .order_by(CheckFindingCitation.created_at, CheckFindingCitation.id)
                        )
                    ).all()
                ],
                "finding_ids": [
                    finding["id"] for finding in findings if finding["check_item_id"] == str(row.id)
                ],
            }
        ).model_dump(mode="json")
        for row in (
            await session.scalars(
                select(CheckItem)
                .where(CheckItem.report_id == run.id)
                .order_by(CheckItem.created_at, CheckItem.id)
            )
        ).all()
    ]
    positions = {
        item["requirement_id"]: index for index, item in enumerate(run.input_manifest["items"])
    }
    coverage.sort(key=lambda item: positions[item["requirement_id"]])
    certificates = []
    for row in (
        await session.scalars(
            select(CheckCertificate)
            .where(CheckCertificate.report_id == run.id)
            .order_by(CheckCertificate.id)
        )
    ).all():
        ids = list(
            (
                await session.scalars(
                    select(CheckItem.requirement_id)
                    .join(CheckCertificateItem, CheckCertificateItem.check_item_id == CheckItem.id)
                    .where(CheckCertificateItem.certificate_id == row.id)
                )
            ).all()
        )
        certificates.append(
            CheckCertificateView.model_validate(
                {
                    **{
                        key: getattr(row, key)
                        for key in (
                            "id",
                            "org_id",
                            "task_id",
                            "report_id",
                            "task_certificate_id",
                            "certificate_revision_id",
                            "assessment_date",
                            "date_status",
                        )
                    },
                    "requirement_ids": sorted(ids, key=lambda value: positions[str(value)]),
                }
            ).model_dump(mode="json")
        )
    data = CheckReportData.model_validate(
        {"report": report, "coverage": coverage, "certificates": certificates}
    ).model_dump(mode="json")
    if run.mode == "rules":
        # Preserve the phase-one JSON while adding outcome/support only to combined.
        for item in data["coverage"]:
            item.pop("semantic_outcome")
            item.pop("semantic_citations")
    return data, findings


def page_cursor(
    settings: Settings, actor: Identity, task_id: UUID, finding_id: UUID | None, anchor: dict
) -> str:
    return TokenSigner.for_tokens(settings).issue(
        {
            "kind": "check_cursor",
            "org_id": str(actor.org_id),
            "task_id": str(task_id),
            "finding_id": str(finding_id) if finding_id else None,
            "sort": "revision_asc" if finding_id else "created_at_id_asc",
            **anchor,
        },
        7 * 86400,
    )


def read_cursor(
    settings: Settings, actor: Identity, task_id: UUID, finding_id: UUID | None, cursor: str | None
) -> dict | None:
    if cursor is None:
        return None
    try:
        payload = TokenSigner.for_tokens(settings).open(cursor)
        if any(
            payload.get(key) != expected
            for key, expected in {
                "kind": "check_cursor",
                "org_id": str(actor.org_id),
                "task_id": str(task_id),
                "finding_id": str(finding_id) if finding_id else None,
                "sort": "revision_asc" if finding_id else "created_at_id_asc",
            }.items()
        ):
            raise ValueError("cursor binding")
        if finding_id is not None:
            revision = payload["revision"]
            if not isinstance(revision, int) or revision < 2:
                raise ValueError("cursor revision")
            return {"revision": revision}
        return {
            "created_at": datetime.fromisoformat(payload["created_at"]),
            "id": UUID(payload["id"]),
        }
    except (ServiceError, ValueError, KeyError, TypeError):
        cards.fail("invalid_cursor", "Cursor does not match this authorized query")


def page_limit(limit: int) -> None:
    if limit < 1 or limit > 200:
        cards.fail("invalid_input", "Limit must be between 1 and 200")


async def list_checks(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    storage: Storage,
    settings: Settings,
    *,
    cursor: str | None = None,
    limit: int = 50,
) -> tuple[dict, list[dict]]:
    actor = await check_inputs.access(session, actor, "check:read")
    if await session.get(Task, task_id) is None:
        raise not_found()
    page_limit(limit)
    anchor = read_cursor(settings, actor, task_id, None, cursor)
    # Reports can depend on different materials. Authorize the full query before
    # exposing its total or a cursor, including dependencies outside this page.
    for manifest in await session.scalars(
        select(CheckRun.input_manifest).where(CheckRun.task_id == task_id)
    ):
        await check_inputs.require_dependencies(session, actor, task_id, manifest, storage)
    query = select(CheckRun).where(CheckRun.task_id == task_id)
    total = await session.scalar(
        select(func.count()).select_from(CheckRun).where(CheckRun.task_id == task_id)
    )
    if anchor is not None:
        query = query.where(
            tuple_(CheckRun.created_at, CheckRun.id) > tuple_(anchor["created_at"], anchor["id"])
        )
    rows = list(
        (
            await session.scalars(query.order_by(CheckRun.created_at, CheckRun.id).limit(limit + 1))
        ).all()
    )
    items = []
    for run in rows[:limit]:
        await check_inputs.require_dependencies(
            session, actor, task_id, run.input_manifest, storage
        )
        items.append(await run_view(session, actor, run, storage, settings))
    next_cursor = (
        page_cursor(
            settings,
            actor,
            task_id,
            None,
            {"id": str(rows[limit - 1].id), "created_at": rows[limit - 1].created_at.isoformat()},
        )
        if len(rows) > limit
        else None
    )
    return AssessmentListData(
        task_id=task_id, total=total or 0, next_cursor=next_cursor
    ).model_dump(mode="json"), items


async def require_finding(
    session: AsyncSession, run: CheckRun, finding_id: UUID, *, lock: bool = False
) -> CheckFinding:
    query = select(CheckFinding).where(
        CheckFinding.id == finding_id,
        CheckFinding.report_id == run.id,
        CheckFinding.task_id == run.task_id,
    )
    if lock:
        query = query.with_for_update()
    finding = await session.scalar(query)
    if finding is None:
        raise not_found()
    return finding


def decision_view(row: CheckDecision) -> dict:
    return FindingDecisionView.model_validate(row).model_dump(mode="json")


async def decide_finding(
    session: AsyncSession,
    actor: Identity,
    report_id: UUID,
    finding_id: UUID,
    body: FindingDecisionRequest,
    storage: Storage,
    settings: Settings,
) -> dict:
    actor, run = await get_run(session, actor, report_id, storage)
    await require_finding(session, run, finding_id)
    if actor.actor_kind != "session" or actor.token_id is not None:
        cards.fail(
            "human_session_required", "A human session is required for check decisions", 403, 4
        )
    actor = await check_inputs.access(session, actor, "check:decide")
    await check_inputs.lock_inputs(session, actor, run.task_id)
    await session.refresh(run)
    actor = await check_inputs.access(session, actor, "check:decide")
    finding = await require_finding(session, run, finding_id, lock=True)
    if finding.review_domain is None:
        cards.fail(
            "unclassified", "Assign the card review domain and generate a new current report"
        )
    if actor.role != {"commercial": "bidder", "technical": "technical"}.get(finding.review_domain):
        cards.fail("forbidden", "Review role does not match the finding domain", 403, 4)
    if (
        await validity(session, actor, run, storage, settings)
        or body.expected_input_hash != run.input_hash
    ):
        cards.fail(
            "check_input_changed", "Only the current report input can receive a decision", 409
        )
    current = await finding_view(session, finding)
    if current["revision"] != body.expected_revision:
        cards.fail("revision_conflict", "Read the current finding revision before deciding", 409)
    if (body.action == "dismiss") != (current["status"] == "open"):
        cards.fail(
            "invalid_transition",
            "Only open findings may be dismissed and dismissed findings reopened",
            409,
        )
    row = CheckDecision(
        id=uuid4(),
        org_id=actor.org_id,
        task_id=run.task_id,
        report_id=run.id,
        finding_id=finding.id,
        revision=body.expected_revision + 1,
        action=body.action,
        reason=body.reason,
        reason_sha256=cards.quote_hash(body.reason),
        expected_input_hash=body.expected_input_hash,
        decided_by=actor.user_id,
        actor_kind="session",
    )
    session.add(row)
    await session.flush()
    audit(
        session,
        actor,
        f"check.{body.action}",
        finding.id,
        {
            "task_id": str(run.task_id),
            "job_id": str(run.job_id),
            "run_id": str(run.run_id),
            "report_id": str(run.id),
            "finding_id": str(finding.id),
            "decision_id": str(row.id),
            "revision": row.revision,
            "input_hash": run.input_hash,
            "reason_sha256": row.reason_sha256,
        },
    )
    return FindingDecisionData.model_validate(
        {"finding": await finding_view(session, finding), "decision": decision_view(row)}
    ).model_dump(mode="json")


async def decision_history(
    session: AsyncSession,
    actor: Identity,
    report_id: UUID,
    finding_id: UUID,
    storage: Storage,
    settings: Settings,
    *,
    cursor: str | None = None,
    limit: int = 50,
) -> tuple[dict, list[dict]]:
    actor, run = await get_run(session, actor, report_id, storage)
    await require_finding(session, run, finding_id)
    page_limit(limit)
    anchor = read_cursor(settings, actor, run.task_id, finding_id, cursor)
    query = select(CheckDecision).where(
        CheckDecision.finding_id == finding_id, CheckDecision.report_id == run.id
    )
    total = await session.scalar(
        select(func.count())
        .select_from(CheckDecision)
        .where(CheckDecision.finding_id == finding_id, CheckDecision.report_id == run.id)
    )
    if anchor is not None:
        query = query.where(CheckDecision.revision > anchor["revision"])
    rows = list(
        (await session.scalars(query.order_by(CheckDecision.revision).limit(limit + 1))).all()
    )
    next_cursor = (
        page_cursor(
            settings,
            actor,
            run.task_id,
            finding_id,
            {"revision": rows[limit - 1].revision},
        )
        if len(rows) > limit
        else None
    )
    return AssessmentListData(
        task_id=run.task_id, total=total or 0, next_cursor=next_cursor
    ).model_dump(mode="json"), [decision_view(row) for row in rows[:limit]]
