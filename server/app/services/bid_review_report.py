"""Frozen human decision reports, bounded projections and local render admission."""

import hashlib
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select, text

from app.core.errors import ServiceError, not_found
from app.core.security import TokenSigner
from app.jobs.execution import job_cost
from app.models.bid_review import BidSubmission, BidSubmissionDocument
from app.models.bid_review_findings import BidReviewFinding, BidReviewFindingEvent
from app.models.bid_review_report import BidReviewReportArtifact, BidReviewReportSnapshot
from app.models.bid_review_run import BidReviewObligation, BidReviewSigningRequirement
from app.models.bid_signature import BidPDFValidation
from app.models.entities import AuditLog, Job, Task, UsageRecord
from app.schemas import bid_review_report as c
from app.schemas.budget_contracts import BudgetPreflightData
from app.schemas.check_contracts import AssessmentJobAccepted
from app.schemas.contracts import Cost, ProviderUsage
from app.services import bid_review as bids
from app.services import bid_review_findings as findings
from app.services import bid_review_privacy as privacy
from app.services import bid_review_run as runs
from app.services import bid_signature_views, budget_preflight, task_workflow
from app.services.bid_review_report_renderer import RENDERER_IDENTITY
from app.services.versioned import audit


async def report_access(
    session, actor, task_id, *, scope="bid-review:report:read", write=False, bind_context=True
):
    await task_workflow.access(
        session, actor, task_id, scope=scope, write=False, lock=write, bind_context=bind_context
    )
    if scope != "bid-review:report:read" and not findings.protected(actor):
        bids.fail(
            "human_session_required",
            "Report artifacts require authorized human original access",
            403,
            4,
        )


async def lock_decisions(session, review_id):
    # Match the finding event guard locks, in stable order, before reading the set.
    ids = list(
        (
            await session.scalars(
                select(BidReviewFinding.id)
                .where(BidReviewFinding.review_id == review_id)
                .order_by(BidReviewFinding.id)
            )
        ).all()
    )
    for identifier in ids:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"bid-review-finding:{identifier}"},
        )


async def decision_hash(session, review_id):
    events = list(
        (
            await session.scalars(
                select(BidReviewFindingEvent)
                .where(BidReviewFindingEvent.review_id == review_id)
                .order_by(BidReviewFindingEvent.finding_id, BidReviewFindingEvent.revision)
            )
        ).all()
    )
    return bids.digest(
        [
            {
                "id": str(e.id),
                "finding_id": str(e.finding_id),
                "revision": e.revision,
                "action": e.action,
                "state": e.state,
                "reason_sha256": e.reason_sha256,
            }
            for e in events
        ]
    )


def notice(label, text_value, values=None):
    result = {"kind": "notice", "label": label, "text": text_value}
    if values is not None:
        result["values"] = values
    return result


async def build_snapshot(session, actor, run, publication, settings):
    task = await session.get(Task, run.task_id)
    rows = {key: [] for key, _ in c.SECTIONS}
    obligations = list(
        (
            await session.scalars(
                select(BidReviewObligation)
                .where(BidReviewObligation.review_id == run.id)
                .order_by(BidReviewObligation.ordinal)
            )
        ).all()
    )
    obligation_details = {
        str(o.id): privacy.open_value(settings, o, "details_encrypted") for o in obligations
    }
    finding_rows = []
    for row in (
        await session.scalars(
            select(BidReviewFinding)
            .where(BidReviewFinding.review_id == run.id)
            .order_by(BidReviewFinding.ordinal)
        )
    ).all():
        value = await findings.view(session, row, settings)
        event = await findings.latest(session, row.id)
        finding_rows.append(
            {
                "kind": "finding",
                "obligation": obligation_details[str(row.obligation_id)],
                "finding": value.model_dump(mode="json"),
                "decision": (await findings.event_view(event, settings)).model_dump(mode="json")
                if event
                else None,
            }
        )
    open_rows = [
        r
        for r in finding_rows
        if r["finding"]["state"] == "open" and r["finding"]["outcome"] != "responded"
    ]
    risks = sorted(
        open_rows, key=lambda row: {"fatal": 0, "high": 1, "medium": 2}[row["finding"]["severity"]]
    )
    machine_risks = sorted(
        [r for r in finding_rows if r["finding"]["outcome"] != "responded"],
        key=lambda row: {"fatal": 0, "high": 1, "medium": 2}[row["finding"]["severity"]],
    )
    rejection = [r for r in machine_risks if r["finding"]["impact"] in {"rejection", "both"}]
    active_rejection = [r for r in rejection if r["finding"]["state"] != "dismissed"]
    active_risks = [r for r in machine_risks if r["finding"]["state"] != "dismissed"]
    rows["overall"] = [
        notice(
            "废标风险",
            f"机器发现 {len(rejection)} 项废标相关风险，其中 {len(active_rejection)} 项未被人工驳回；"
            "人工确认仅表示同意风险结论，最终由评标委员会认定。",
        ),
        notice(
            "最大风险",
            active_risks[0]["finding"]["title"]
            if active_risks
            else "已检查范围内未发现待处理风险；未覆盖项目仍为未知。",
        ),
        notice("补救摘要", f"逐项核查并完成 {len(risks)} 项补救动作。"),
        notice("得分区间", "未评分；第一阶段不包含评分。"),
        notice("完成范围", publication.completion, list(publication.uncovered_codes)),
    ]
    documents = list(
        (
            await session.scalars(
                select(BidSubmissionDocument)
                .where(BidSubmissionDocument.submission_id == run.submission_id)
                .order_by(BidSubmissionDocument.ordinal)
            )
        ).all()
    )
    inventory = [
        {
            "id": str(d.id),
            "role": d.role,
            "kind": d.kind,
            "sha256": d.sha256,
            "size_bytes": d.size_bytes,
            "name": privacy.open_value(settings, d, "upload_name_encrypted"),
        }
        for d in documents
    ]
    rows["basic_information"] = [
        {
            "kind": "basic_information",
            "task_name": task.name,
            "tender_number": task.tender_number,
            "task_id": str(task.id),
            "submission_id": str(run.submission_id),
            "assessment_date": run.manifest["assessment_date"],
            "review_id": str(run.id),
            "review_job_id": str(run.job_id),
            "preparation_id": str(run.preparation_id),
            "review_created_at": run.created_at.isoformat(),
            "report_input_hash": run.input_hash,
            "tender_documents": [d for d in inventory if d["role"] == "tender"],
            "bid_documents": [d for d in inventory if d["role"] == "bid"],
            "completion": publication.completion,
        }
    ]
    rows["compliance"] = list(finding_rows)
    covered = {r["finding"]["obligation_id"] for r in finding_rows}
    for obligation in (
        await session.scalars(
            select(BidReviewObligation)
            .where(BidReviewObligation.review_id == run.id)
            .order_by(BidReviewObligation.ordinal)
        )
    ).all():
        if str(obligation.id) not in covered:
            value = privacy.open_value(settings, obligation, "details_encrypted")
            rows["compliance"].append(
                {
                    "kind": "obligation",
                    "obligation": value,
                    "outcome": "unknown",
                    "reason_code": "compliance_unassessed",
                }
            )
    rows["compliance"].append(
        notice("报价瑕疵", "未评估；报价页面仅在本地保存，未参与第一阶段模型审查。")
    )
    for row in (
        await session.scalars(
            select(BidPDFValidation)
            .where(BidPDFValidation.preparation_id == run.preparation_id)
            .order_by(BidPDFValidation.document_id)
        )
    ).all():
        value = bid_signature_views.open_detail(settings, actor, row)
        rows["signatures"].append(
            {
                "kind": "pdf_validation",
                "validation": {
                    "document_id": str(row.document_id),
                    "validator_identity": row.validator_identity,
                    "trust_store_sha256": row.trust_store_sha256,
                    **value,
                },
            }
        )
    for row in (
        await session.scalars(
            select(BidReviewSigningRequirement)
            .where(BidReviewSigningRequirement.review_id == run.id)
            .order_by(BidReviewSigningRequirement.ordinal)
        )
    ).all():
        value = privacy.open_value(settings, row, "details_encrypted")
        # Presence is a distinct future assessment. Mapping never establishes presence.
        if any(location.get("status") != "unresolved" for location in value["required_locations"]):
            bids.fail(
                "bid_review_output_integrity",
                "Unassessed signing location claimed presence",
                409,
                4,
            )
        rows["signatures"].append({"kind": "signing_requirement", "requirement": value})
    rows["signatures"].append(
        notice(
            "签章位置",
            "所需位置均未核实（unresolved，presence_not_checked）；数字签名有效性与可见签章位置分别判断。",
        )
    )
    rows["risks"] = machine_risks or [
        notice("高风险缺陷", "已检查范围内没有待处理缺陷；未知和未覆盖范围见检验说明。")
    ]
    rows["scores"] = [notice("得分预估", "未评分；第一阶段不可用。")]
    rows["evidence"] = [notice("证据核对", "未执行；第一阶段不包含证据核对。")]
    rows["remediation"] = risks or [notice("补救清单", "当前没有待处理发现。")]
    rows["methodology"] = [
        notice("审查范围", "uploaded_bid；仅资格与符合性审查，报价页面保持本地。"),
        notice("检验声明", c.ADVISORY),
        notice("运行发布时间", publication.created_at.isoformat()),
        notice(
            "未覆盖项目",
            "未知项目不构成已满足结论。",
            sorted(
                set(
                    publication.uncovered_codes
                    + [
                        "quotation_not_assessed",
                        "scoring_not_requested",
                        "evidence_not_performed",
                        "signature_presence_not_checked",
                    ]
                )
            ),
        ),
        notice(
            "判断依据", "rule 为本地规则/PDF 校验；model 为所记录模型判断；人工决定保留机器原结论。"
        ),
    ]
    for usage in (
        await session.scalars(
            select(UsageRecord)
            .where(UsageRecord.job_id == run.job_id)
            .order_by(UsageRecord.created_at, UsageRecord.id)
        )
    ).all():
        value = ProviderUsage.model_validate(
            {key: getattr(usage, key) for key in ProviderUsage.model_fields if hasattr(usage, key)}
        ).model_dump(mode="json")
        rows["methodology"].append({"kind": "usage", "usage": value})
    rows["methodology"].append(
        {
            "kind": "cost",
            "cost": Cost.model_validate(
                await job_cost(session, run.job_id, settings.billing_currency)
            ).model_dump(mode="json"),
        }
    )
    last_decision_time = await session.scalar(
        select(func.max(BidReviewFindingEvent.created_at)).where(
            BidReviewFindingEvent.review_id == run.id
        )
    )
    rows["methodology"].append(
        notice(
            "人工决定截至时间",
            last_decision_time.isoformat() if last_decision_time else "尚无人工决定",
        )
    )
    decisions = await decision_hash(session, run.id)
    rows["basic_information"][0]["decisions_snapshot_sha256"] = decisions
    payload = {
        "review_id": str(run.id),
        "task_id": str(run.task_id),
        "report_input_hash": run.input_hash,
        "publication_id": str(publication.id),
        "publication_output_hash": publication.output_hash,
        "completion": publication.completion,
        "decisions_snapshot_sha256": decisions,
        "renderer_identity": RENDERER_IDENTITY,
        "advisory_statement": c.ADVISORY,
        "rows": rows,
    }
    payload["input_hash"] = bids.digest(payload)
    return payload


def open_snapshot(row, settings):
    payload = privacy.open_value(settings, row, "details_encrypted")
    if (
        bids.digest(payload) != row.details_sha256
        or payload["input_hash"] != row.input_hash
        or payload["decisions_snapshot_sha256"] != row.decisions_snapshot_sha256
    ):
        bids.fail("bid_review_output_integrity", "Report snapshot integrity mismatch", 409, 4)
    return payload


def artifact_view(row, snapshot):
    return c.BidReviewReportArtifact(
        id=row.id,
        org_id=row.org_id,
        task_id=row.task_id,
        report_id=row.review_id,
        snapshot_id=row.snapshot_id,
        format=row.format,
        sha256=row.sha256,
        size_bytes=row.size_bytes,
        report_input_hash=snapshot.report_input_hash,
        decisions_snapshot_sha256=snapshot.decisions_snapshot_sha256,
        renderer_identity=snapshot.renderer_identity,
        created_at=row.created_at,
    )


async def artifacts(session, snapshot):
    values = list(
        (
            await session.scalars(
                select(BidReviewReportArtifact)
                .where(BidReviewReportArtifact.snapshot_id == snapshot.id)
                .order_by(BidReviewReportArtifact.format)
            )
        ).all()
    )
    if values and {r.format for r in values} != {"console", "docx"}:
        bids.fail("bid_review_output_integrity", "Report artifact pair is incomplete", 409, 4)
    return [artifact_view(r, snapshot) for r in values]


async def submit(session, actor, review_id, body, queue, settings):
    if body.report_id != review_id:
        bids.fail("invalid_input", "Report parent does not match route", 422, 2)
    run, publication = await findings.required_run(session, actor, review_id)
    await report_access(
        session, actor, run.task_id, scope="bid-review:report:render", write=not body.dry_run
    )
    await lock_decisions(session, run.id)
    payload = await build_snapshot(session, actor, run, publication, settings)
    if body.expected_decisions_snapshot_sha256 != payload["decisions_snapshot_sha256"]:
        bids.fail(
            "bid_review_decisions_changed", "Human decisions changed; read the report again", 409, 2
        )
    attached = await budget_preflight.attach(
        session,
        {},
        command="review report",
        task_id=run.task_id,
        input_hash=payload["input_hash"],
        currency=settings.billing_currency,
        settings=settings,
        quotes=[],
        planned_calls=0,
    )
    budget = BudgetPreflightData.model_validate(attached["budget_preflight"])
    budget.as_of = budget.as_of.replace(microsecond=0)
    expires = budget.as_of + timedelta(seconds=900)
    binding = {
        "purpose": "bid_review_report",
        "org_id": str(actor.org_id),
        "task_id": str(run.task_id),
        "actor_user_id": str(actor.user_id),
        "request_id": str(body.request_id),
        "review_id": str(run.id),
        "input_hash": payload["input_hash"],
    }
    signer = TokenSigner.for_tokens(settings)
    if body.dry_run:
        return c.BidReportRenderPreview(
            report_id=run.id,
            input_hash=payload["input_hash"],
            report_input_hash=run.input_hash,
            decisions_snapshot_sha256=payload["decisions_snapshot_sha256"],
            renderer_identity=RENDERER_IDENTITY,
            budget=budget,
            expires_at=expires,
            preflight_token=signer.issue(binding, 900, expires_at=int(expires.timestamp())),
        )
    if body.expected_input_hash != payload["input_hash"]:
        bids.fail("bid_review_input_changed", "Report inputs changed; preview again", 409, 2)
    try:
        receipt = signer.open(body.preflight_token)
    except ServiceError:
        bids.fail("bid_preflight_expired", "Report receipt expired; preview again", 409, 2)
    if any(receipt.get(k) != v for k, v in binding.items()):
        bids.fail("bid_preflight_mismatch", "Report receipt does not match this request", 409, 2)
    root = await session.get(BidSubmission, run.submission_id)
    if root.state != "uploaded":
        bids.fail("bid_submission_withdrawn", "Submission was withdrawn", 409, 4)
    await bids.replay_lock(session, actor, "bid_review_report", body.request_id)
    prior = await session.scalar(
        select(BidReviewReportSnapshot).where(
            BidReviewReportSnapshot.created_by == actor.user_id,
            BidReviewReportSnapshot.request_id == body.request_id,
        )
    )
    if prior and (prior.input_hash != payload["input_hash"] or prior.review_id != run.id):
        bids.fail("idempotency_conflict", "Report request ID has different input", 409, 2)
    payload_hash = bids.digest(binding | {"retry": body.retry})
    replay = await session.scalar(
        select(AuditLog).where(
            AuditLog.action == "bid_review.report_requested",
            AuditLog.actor_user_id == actor.user_id,
            AuditLog.details["request_id"].astext == str(body.request_id),
        )
    )
    if replay is not None and replay.details.get("payload_hash") != payload_hash:
        bids.fail("idempotency_conflict", "Report request ID has different input", 409, 2)
    if prior is None:
        prior = await session.scalar(
            select(BidReviewReportSnapshot)
            .where(
                BidReviewReportSnapshot.created_by == actor.user_id,
                BidReviewReportSnapshot.review_id == run.id,
                BidReviewReportSnapshot.input_hash == payload["input_hash"],
            )
            .order_by(BidReviewReportSnapshot.created_at.desc())
            .limit(1)
        )
    cached = prior is not None
    if prior:
        job = await session.scalar(select(Job).where(Job.id == prior.job_id).with_for_update())
        if body.retry and replay is None:
            if job.status not in {"failed", "cancelled"}:
                bids.fail(
                    "bid_retry_not_terminal",
                    "Only failed or cancelled report work may be retried",
                    409,
                    2,
                )
            job.status, job.error, job.finished_at, job.queue_id, job.run_id, job.lease_until = (
                "queued",
                None,
                None,
                None,
                None,
                None,
            )
    else:
        if body.retry:
            bids.fail("bid_retry_missing", "No previous report exists", 409, 2)
        identifier = uuid4()
        job = Job(
            id=uuid4(),
            org_id=actor.org_id,
            task_id=run.task_id,
            document_id=None,
            bid_submission_document_id=run.tender_document_id,
            kind="bid_review_report",
            cache_key=bids.digest(
                {key: value for key, value in binding.items() if key != "request_id"}
            ),
            status="queued",
            actor_user_id=actor.user_id,
            actor_token_id=None,
            actor_kind="session",
            actor_scopes=sorted(actor.scopes),
            result={
                "submission": {
                    "snapshot_id": str(identifier),
                    "review_id": str(run.id),
                    "input_hash": payload["input_hash"],
                }
            },
        )
        session.add(job)
        await session.flush()
        snapshot = BidReviewReportSnapshot(
            id=identifier,
            org_id=actor.org_id,
            task_id=run.task_id,
            submission_id=run.submission_id,
            review_id=run.id,
            publication_id=publication.id,
            job_id=job.id,
            created_by=actor.user_id,
            request_id=body.request_id,
            input_hash=payload["input_hash"],
            report_input_hash=run.input_hash,
            decisions_snapshot_sha256=payload["decisions_snapshot_sha256"],
            renderer_identity=RENDERER_IDENTITY,
            details_sha256=bids.digest(payload),
            details_encrypted=bids.seal(settings, actor.org_id, identifier, payload),
        )
        session.add(snapshot)
        await session.flush()
        audit(
            session,
            actor,
            "bid_review.report_submitted",
            snapshot.id,
            {
                "task_id": str(run.task_id),
                "review_id": str(run.id),
                "job_id": str(job.id),
                "input_hash": snapshot.input_hash,
                "decisions_snapshot_sha256": snapshot.decisions_snapshot_sha256,
            },
        )
    if replay is None:
        audit(
            session,
            actor,
            "bid_review.report_requested",
            job.id,
            {
                "task_id": str(run.task_id),
                "review_id": str(run.id),
                "job_id": str(job.id),
                "request_id": str(body.request_id),
                "payload_hash": payload_hash,
                "input_hash": payload["input_hash"],
                "retry": body.retry,
            },
        )
    await bids.enqueue(session, queue, job)
    return AssessmentJobAccepted.model_validate(
        {"job_id": job.id, "status": job.status, "cached": cached}
    )


def safe_row(row):
    if row["kind"] == "finding":
        from app.schemas.bid_review_findings import BidReviewSafeFinding

        full = row["finding"]
        return {
            "kind": "finding",
            "finding": {key: full[key] for key in BidReviewSafeFinding.model_fields},
            "decision": None,
        }
    # Token metadata carries no document names, source coordinates or content.
    if row["kind"] == "notice" and row["label"] in {
        "得分预估",
        "证据核对",
        "得分区间",
        "完成范围",
        "审查范围",
        "签章位置",
        "报价瑕疵",
    }:
        return row
    return None


def clear_row(row, project):
    """Build a purpose-specific cleared shape before applying privacy masking."""
    kind = row["kind"]
    if kind == "finding":
        result = safe_row(row)
        assert result is not None
        result["summary"] = {
            "responded": "已识别响应",
            "deviation": "发现偏离，需复核",
            "missing": "检索范围内缺失",
            "unknown": "未能确定响应情况",
        }[row["finding"]["outcome"]]
        result["basis_type"] = row["finding"]["basis"]["kind"]
        return result
    if kind == "notice":
        # These are fixed scope statements and explicit uncovered codes. A risk
        # title or task metadata is protected content and has no cleared counterpart.
        if row["label"] in {"最大风险", "补救摘要", "废标风险"}:
            return {
                "kind": "notice",
                "label": row["label"],
                "text": "请按发现状态核查；原文详情仅向获授权的审查人员提供。",
            }
        return {
            "kind": "notice",
            "label": row["label"],
            "text": project(row["text"]),
            "values": row.get("values", []),
        }
    if kind == "basic_information":
        fields = (
            "task_id",
            "submission_id",
            "assessment_date",
            "review_id",
            "review_job_id",
            "preparation_id",
            "review_created_at",
            "report_input_hash",
            "decisions_snapshot_sha256",
            "completion",
        )
        return {"kind": kind, **{field: row[field] for field in fields}}
    if kind == "obligation":
        obligation = row["obligation"]
        return {
            "kind": "notice",
            "label": "未覆盖条款",
            "text": "未完成符合性审查",
            "values": [obligation["id"], row["reason_code"]],
        }
    if kind == "signing_requirement":
        requirement = row["requirement"]
        fields = (
            "id",
            "applicability",
            "mark_types",
            "owner_roles",
            "date_required",
            "location_rule",
            "required_locations",
            "reason_code",
        )
        return {
            "kind": kind,
            "requirement": {field: requirement[field] for field in fields if field in requirement},
        }
    if kind == "pdf_validation":
        validation = row["validation"]
        fields = (
            "document_id",
            "validator_identity",
            "trust_store_sha256",
            "status",
            "validation_time",
        )
        return {
            "kind": kind,
            "validation": {field: validation[field] for field in fields if field in validation},
        }
    if kind in {"cost", "usage"}:
        return {"kind": kind, kind: row[kind]}
    bids.fail("bid_review_output_integrity", "Unknown report row has no cleared projection", 409, 4)


async def show(
    session,
    actor,
    review_id,
    settings,
    *,
    section: c.ReportSectionKey = "overall",
    snapshot_id=None,
    cursor=None,
    limit=50,
):
    run, publication = await findings.required_run(session, actor, review_id)
    safe = actor.token_id is not None or actor.actor_kind != "session"
    if not safe:
        await report_access(session, actor, run.task_id)
    current_hash = await decision_hash(session, run.id)
    retained = None
    output_artifacts = []
    if snapshot_id:
        retained = await session.scalar(
            select(BidReviewReportSnapshot).where(
                BidReviewReportSnapshot.id == snapshot_id,
                BidReviewReportSnapshot.review_id == run.id,
            )
        )
        if retained is None:
            raise not_found()
        output_artifacts = await artifacts(session, retained)
        if not output_artifacts:
            bids.fail(
                "bid_review_report_unpublished",
                "Report render has no published artifact pair",
                409,
                4,
            )
        payload = open_snapshot(retained, settings)
    else:
        # Advisory locks preserve an internally coherent live decision view. This
        # remains a live view until an explicit render retains the immutable snapshot.
        await lock_decisions(session, run.id)
        payload = await build_snapshot(session, actor, run, publication, settings)
        current_hash = payload["decisions_snapshot_sha256"]
    projection = "safe" if safe else "protected" if findings.protected(actor) else "cleared"
    project = None
    if projection == "cleared":
        if not await runs.privacy_validity(session, run, settings):
            bids.fail("bid_review_input_changed", "Cleared report privacy is stale", 409, 4)
        project = await findings.cleared_projector(session, run, settings)
    identity = f"report:{snapshot_id or 'live'}:{section}:{payload['input_hash']}:{projection}"
    start = runs.offset(actor, review_id, identity, cursor, settings)
    values = payload["rows"][section]
    if safe:
        values = [value for row in values if (value := safe_row(row)) is not None]
        output_artifacts = []
    if project:
        values = [clear_row(row, project) for row in values]
    items, size = [], 0
    for value in values[start : start + limit]:
        import json

        length = len(json.dumps(value, ensure_ascii=True).encode())
        if length > 950000:
            bids.fail("bid_review_output_limit", "Report row exceeds response bound", 400, 4)
        if size + length > 950000:
            break
        size += length
        items.append(value)
    data = c.BidReportSectionData(
        review_id=run.id,
        task_id=run.task_id,
        snapshot_id=retained.id if retained else None,
        input_hash=payload["input_hash"],
        report_input_hash=run.input_hash,
        decisions_snapshot_sha256=payload["decisions_snapshot_sha256"],
        current_decisions_snapshot_sha256=current_hash,
        renderer_identity=payload["renderer_identity"],
        completion=payload["completion"],
        sections=[{"key": k, "title": t} for k, t in c.SECTIONS],
        section=section,
        projection=projection,
        artifacts=output_artifacts if projection == "protected" else [],
        next_cursor=runs.next_cursor(actor, review_id, identity, start + len(items), settings)
        if start + len(items) < len(values)
        else None,
    )
    return data, items


async def history(session, actor, review_id, settings, *, cursor=None, limit=50):
    run, _ = await findings.required_run(session, actor, review_id)
    await report_access(session, actor, run.task_id)
    if not findings.protected(actor):
        return {"review_id": str(run.id), "task_id": str(run.task_id), "next_cursor": None}, []
    start = runs.offset(actor, review_id, "report_history", cursor, settings)
    snapshots = list(
        (
            await session.scalars(
                select(BidReviewReportSnapshot)
                .where(BidReviewReportSnapshot.review_id == review_id)
                .order_by(BidReviewReportSnapshot.created_at.desc(), BidReviewReportSnapshot.id)
                .offset(start)
                .limit(limit + 1)
            )
        ).all()
    )
    items = []
    for snapshot in snapshots[:limit]:
        outputs = await artifacts(session, snapshot)
        job = await session.get(Job, snapshot.job_id)
        items.append(
            {
                "snapshot_id": str(snapshot.id),
                "input_hash": snapshot.input_hash,
                "can_retry": snapshot.created_by == actor.user_id
                and job.status in {"failed", "cancelled"},
                "created_at": snapshot.created_at.isoformat(),
                "decisions_snapshot_sha256": snapshot.decisions_snapshot_sha256,
                "report_input_hash": snapshot.report_input_hash,
                "artifact_status": "ready"
                if outputs
                else "failed"
                if job.status in {"failed", "cancelled"}
                else "pending",
                "artifacts": [v.model_dump(mode="json") for v in outputs],
            }
        )
    return {
        "review_id": str(run.id),
        "task_id": str(run.task_id),
        "next_cursor": runs.next_cursor(
            actor, review_id, "report_history", start + len(items), settings
        )
        if len(snapshots) > limit
        else None,
    }, items


async def download_gate(session, actor, artifact_id):
    row = await session.get(BidReviewReportArtifact, artifact_id)
    if row is None:
        raise not_found()
    await report_access(session, actor, row.task_id, scope="bid-review:report:download")
    snapshot = await session.get(BidReviewReportSnapshot, row.snapshot_id)
    root = await session.get(BidSubmission, snapshot.submission_id)
    if root.state != "uploaded":
        bids.fail("bid_submission_withdrawn", "Submission was withdrawn", 409, 4)
    values = await artifacts(session, snapshot)
    if len(values) != 2:
        raise not_found()
    return row, snapshot


async def read_artifact(session, actor, artifact_id, storage):
    row, snapshot = await download_gate(session, actor, artifact_id)
    content = await storage.read_bounded(actor.org_id, row.storage_key, row.size_bytes)
    if len(content) != row.size_bytes or hashlib.sha256(content).hexdigest() != row.sha256:
        bids.fail(
            "bid_review_output_integrity", "Report artifact bytes failed integrity checks", 409, 4
        )
    await download_gate(session, actor, artifact_id)
    audit(
        session,
        actor,
        "bid_review.report_download_served",
        row.id,
        {"task_id": str(row.task_id), "snapshot_id": str(row.snapshot_id), "sha256": row.sha256},
    )
    return content, artifact_view(row, snapshot)


async def job_access(session, actor, job, *, cancel=False):
    snapshot = await session.scalar(
        select(BidReviewReportSnapshot).where(BidReviewReportSnapshot.job_id == job.id)
    )
    if snapshot is None:
        raise not_found()
    await report_access(session, actor, snapshot.task_id, scope="bid-review:report:render")
    if cancel and job.actor_user_id != actor.user_id:
        raise not_found()
