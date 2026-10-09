"""Explicit paid review stages; every dispatch and publication repeats live privacy."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select

from app.jobs.execution import ADMISSION_STOPS, JobExecution
from app.models.bid_review_run import (
    BidReviewObligation,
    BidReviewPublication,
    BidReviewRequiredLocation,
    BidReviewRun,
    BidReviewSigningRequirement,
)
from app.models.entities import UsageRecord
from app.providers.base import ProviderFailure
from app.providers.bid_reviewing import review_provider
from app.schemas.bid_review_run import BidReviewRequest
from app.services import bid_review as bids
from app.services import bid_review_clef, bid_review_findings, bid_review_text, check_semantic
from app.services import bid_review_compliance as compliance
from app.services import bid_review_run as review
from app.services.auth import set_actor_context
from app.services.versioned import audit

PARTIAL_STOPS = ADMISSION_STOPS | {
    "provider_unavailable",
    "provider_refused",
    "provider_quota_exhausted",
    "invalid_provider_output",
    "provider_output_truncated",
    "invalid_provider_model",
    "provider_timeout",
}


async def current(session, execution, job):
    actor = review.worker(job)
    await review.run_access(session, actor, job.task_id)
    submitted = job.result["submission"]
    request = BidReviewRequest.model_validate(
        {
            **submitted["request"],
            "dry_run": True,
            "retry": False,
            "expected_input_hash": None,
            "preflight_token": None,
        }
    )
    fixed = await review.snapshot(session, actor, job.task_id, request, execution.settings)
    if (
        fixed.input_hash != submitted["input_hash"]
        or fixed.manifest != submitted["input_manifest"]
        or fixed.blockers
    ):
        bids.fail("bid_review_input_changed", "Review inputs or outbound authority changed", 409, 4)
    return actor, fixed


async def process(execution: JobExecution, storage) -> None:
    async with execution.db.transaction(execution.org_id) as session:
        job = await execution.owned_job(session)
        actor, fixed = await current(session, execution, job)
        submitted = job.result["submission"]
        review_id = UUID(submitted["review_id"])
    execution.absolute_call_ceiling = fixed.manifest["limits"]["llm_calls"]
    execution.plan(fixed.manifest["planned_calls"])

    async def before_admit(session):
        live = await execution.owned_job(session)
        await current(session, execution, live)

    execution.before_admit = before_admit
    obligations, signing = [], bid_review_text.initial_signing(fixed.candidates)
    rejected, completed, reported = [], [], []
    stop_reason = None
    adapter = review_provider(fixed.llm)
    for request in fixed.requests:
        try:
            output = await adapter.review(request)
        except ProviderFailure as error:
            if error.code not in PARTIAL_STOPS:
                raise
            reported.extend(error.usage)
            stop_reason = error.code
            break
        reported.extend(output.usages)
        rejected += bid_review_text.accept(
            request,
            output.wire,
            fixed.refs,
            fixed.candidate_refs,
            obligations,
            signing,
            fixed.bid_pages,
        )
        completed.extend(fixed.refs[t.ref]["page_id"] for t in request.texts)
    findings, compliance_assessed = [], []
    for obligation in obligations:
        requests, refs, excluded = compliance.batches(
            obligation, fixed.authorized_bid_pages, fixed.llm.settings.llm_batch_chars
        )
        results, limits = [], []
        if len(fixed.authorized_bid_pages) < len(fixed.bid_pages):
            limits.append("bid_pages_excluded")
        if excluded:
            limits.append("bid_review_page_context_limit")
        for request in requests:
            if stop_reason is not None:
                limits.append(stop_reason)
                break
            try:
                output = await adapter.review(request)
            except ProviderFailure as error:
                if error.code not in PARTIAL_STOPS:
                    raise
                reported.extend(error.usage)
                stop_reason = error.code
                limits.append(error.code)
                break
            reported.extend(output.usages)
            results.append(compliance.accept(request, output.wire, obligation, refs))
        if stop_reason:
            limits.append(stop_reason)
        result = compliance.summarize(
            obligation,
            results,
            fixed.manifest,
            fixed.bid_pages,
            fixed.manifest["provider_binding"],
            limits,
        )
        findings.append(result)
        if result["outcome"] != "unknown":
            compliance_assessed.append(obligation["id"])
    rule_findings = compliance.rules(
        obligations,
        findings,
        fixed.manifest,
        fixed.bid_pages,
        fixed.documents,
        fixed.validations,
    )
    # LLM retains its independent stage ceiling; only after it stops making calls
    # may the visual stage use its additional fixed 40-call envelope.
    execution.absolute_call_ceiling = min(
        140, fixed.manifest["limits"]["llm_calls"] + fixed.manifest["limits"]["visual_calls"]
    )
    clef_coverage, clef_usages, clef_errors = await bid_review_clef.triage(
        execution, fixed, signing, storage
    )
    reported.extend(clef_usages)
    rejected.extend(clef_errors)
    if any(
        value["applicability"] == "applies" and value["required_locations"]
        for value in signing.values()
    ):
        rejected.append("signing_presence_human_confirmation_required")
    if compliance.invalid_signatures(fixed.validations)[0] and not any(
        finding["code"] == "signature_validation_invalid" for finding in rule_findings
    ):
        rejected.append("signature_defect_without_cited_obligation")
    # Reject an overflowing obligation group as a whole and expose the uncovered
    # suffix. Never prune individual anchors or low-confidence clauses to fit.
    grouped, accepted = findings, []
    limited_obligations = []
    for obligation in obligations:
        group = [f for f in grouped + rule_findings if f["obligation_id"] == obligation["id"]]
        if (
            limited_obligations
            or len(group) > compliance.PER_OBLIGATION_LIMIT
            or len(accepted) + len(group) > compliance.FINDING_LIMIT
        ):
            limited_obligations.append(obligation["id"])
        else:
            accepted.extend(group)
    findings = accepted
    if limited_obligations:
        rejected.append("bid_review_finding_limit")
        compliance_assessed = [i for i in compliance_assessed if i not in limited_obligations]
    compliance.enforce_limits(findings)
    if execution.stopped is not None and execution.stopped.code not in ADMISSION_STOPS:
        raise execution.stopped
    unknown_signing = sum(value["applicability"] == "unknown" for value in signing.values())
    for value in signing.values():
        if value["applicability"] == "unknown" and not value["required_locations"]:
            value["required_locations"] = [
                {"status": "unresolved", "reason_code": "required_location_unmapped"}
            ]
    if sum(len(v["required_locations"]) for v in signing.values()) > 10000:
        bids.fail("bid_review_output_limit", "Signing locations exceed approved bounds", 400, 4)
    if any(
        v["applicability"] != "not_applicable" and v["location_rule"] in {"unknown", "specified"}
        for v in signing.values()
    ):
        rejected.append("signing_locations_unmapped")
    uncovered = sorted(
        set(
            fixed.uncovered
            + rejected
            + ([stop_reason] if stop_reason else [])
            + (["signing_applicability_unknown"] if unknown_signing else [])
            + [code for finding in findings for code in finding["limitation_codes"]]
        )
    )
    coverage = {
        "tender_pages_total": fixed.manifest["tender_page_count"],
        "tender_pages_authorized": len(fixed.requests),
        "tender_pages_assessed": len(completed),
        "assessed_page_ids": completed,
        "unassessed_page_ids": [
            p["page_id"]
            for p in fixed.manifest["privacy_manifest"]["pages"]
            if p["role"] == "tender" and p["page_id"] not in completed
        ],
        "obligations": len(obligations),
        "signing_candidates": len(fixed.candidates),
        "signing_requirements": len(signing),
        "signing_unknown": unknown_signing,
        "required_locations": sum(len(v["required_locations"]) for v in signing.values()),
        "bid_compliance": "assessed" if len(compliance_assessed) == len(obligations) else "partial",
        "bid_pages_authorized": len(fixed.authorized_bid_pages),
        "compliance_obligations_total": len(obligations),
        "compliance_obligations_assessed": len(compliance_assessed),
        "compliance_assessed_obligation_ids": compliance_assessed,
        "compliance_unassessed_obligation_ids": [
            o["id"] for o in obligations if o["id"] not in compliance_assessed
        ],
        "findings": len(findings),
        "finding_outcomes": {
            outcome: sum(f["outcome"] == outcome for f in findings)
            for outcome in ("responded", "deviation", "missing", "unknown")
        },
        "signature_presence": "triage_only" if clef_coverage["clef_calls"] else "not_checked",
        "scoring": "not_requested",
        **clef_coverage,
    }
    completion = (
        "partial"
        if uncovered or len(completed) < fixed.manifest["tender_page_count"]
        else "complete"
    )
    async with execution.db.transaction(execution.org_id) as session:
        job = await execution.owned_job(session)
        actor, live = await current(session, execution, job)
        await check_semantic.usage_integrity(session, job, execution.run_id, reported)
        await set_actor_context(session, actor)
        run = await session.get(BidReviewRun, review_id)
        if run is None or run.job_id != job.id or run.input_hash != live.input_hash:
            bids.fail("bid_review_input_changed", "Review parent binding changed", 409, 4)
        common = {
            "org_id": job.org_id,
            "task_id": job.task_id,
            "submission_id": run.submission_id,
            "review_id": run.id,
        }
        for ordinal, value in enumerate(obligations, 1):
            session.add(
                BidReviewObligation(
                    id=UUID(value["id"]),
                    **common,
                    page_id=UUID(value["citation"]["page_id"]),
                    ordinal=ordinal,
                    details_encrypted=bids.seal(execution.settings, job.org_id, value["id"], value),
                )
            )
        for ordinal, value in enumerate(signing.values(), 1):
            session.add(
                BidReviewSigningRequirement(
                    id=UUID(value["id"]),
                    **common,
                    page_id=UUID(value["source_page_id"]),
                    candidate_id=UUID(value["candidate_id"]) if value["candidate_id"] else None,
                    applicability=value["applicability"],
                    ordinal=ordinal,
                    details_encrypted=bids.seal(execution.settings, job.org_id, value["id"], value),
                )
            )
        await session.flush()
        location_index = 0
        for value in signing.values():
            for location in value["required_locations"]:
                location_index += 1
                session.add(
                    BidReviewRequiredLocation(
                        id=uuid4(),
                        **common,
                        requirement_id=UUID(value["id"]),
                        page_id=UUID(location["page_id"]) if location.get("page_id") else None,
                        ordinal=location_index,
                        group_id=location.get("group_id"),
                        status=location["status"],
                    )
                )
        await session.flush()
        await bid_review_findings.publish_bid_review_findings(
            session, run, findings, settings=execution.settings
        )
        session.add(
            BidReviewPublication(
                id=uuid4(),
                **common,
                run_id=execution.run_id,
                completion=completion,
                coverage=coverage,
                uncovered_codes=uncovered,
                output_hash=bids.digest(
                    {
                        "obligations": obligations,
                        "signing": list(signing.values()),
                        "findings": findings,
                        "coverage": coverage,
                    }
                ),
            )
        )
        await session.flush()
        usages = list(
            (
                await session.scalars(select(UsageRecord.id).where(UsageRecord.job_id == job.id))
            ).all()
        )
        job.result = {
            "submission": submitted,
            "review_id": str(run.id),
            "completion": completion,
            "coverage": coverage,
            "uncovered_codes": uncovered,
            "stop_reason": stop_reason,
            "usage_record_ids": [str(u) for u in usages],
        }
        audit(
            session,
            actor,
            "bid_review.published",
            run.id,
            {
                "task_id": str(job.task_id),
                "job_id": str(job.id),
                "run_id": str(execution.run_id),
                "input_hash": live.input_hash,
                "obligation_count": len(obligations),
                "completion": completion,
            },
        )
        await execution.owned_job(session)
        job.status, job.error, job.finished_at = "succeeded", None, datetime.now(UTC)


async def failure_audit(session, job, code):
    actor = review.worker(job)
    await review.run_access(session, actor, job.task_id)
    audit(
        session,
        actor,
        "bid_review.failed",
        job.id,
        {
            "task_id": str(job.task_id),
            "job_id": str(job.id),
            "run_id": str(job.run_id),
            "error_code": code,
            "input_hash": job.result["submission"]["input_hash"],
        },
    )
