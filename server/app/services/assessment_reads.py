"""Read-only assessment discovery and bounded check projections.

Counts and keyset anchors are selected in SQL before child narratives/citations
are hydrated. Every collection first validates its complete dependency graph.
"""

import asyncio
from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import case, func, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ServiceError, not_found
from app.models.check import (
    CheckCertificate,
    CheckCertificateItem,
    CheckDecision,
    CheckFinding,
    CheckFindingCitation,
    CheckItem,
    CheckRun,
)
from app.models.entities import Chunk, Document, Job, Requirement, Task
from app.models.response_cards import (
    CardEvidenceLink,
    DraftRun,
    Evidence,
    ResponseCard,
    ResponseCardRevision,
    ResponseItem,
)
from app.providers.storage import Storage
from app.schemas.check_contracts import (
    AssessmentListData,
    CheckCertificateView,
    CheckItemView,
    FindingView,
)
from app.schemas.console_assessments import (
    ActionAvailability,
    AssessmentHistoryQuery,
    AssessmentInputsData,
    AssessmentJobPage,
    AssessmentJobPageData,
    AssessmentJobQuery,
    AssessmentJobView,
    CheckPage,
    CheckPageRequest,
    CheckSummaryData,
    CitationContextData,
    CitationRequest,
    DraftChoice,
    FindingGroup,
    FixTarget,
    Notice,
    PageData,
    ProjectionPage,
    ReportHeader,
    SubjectActions,
)
from app.schemas.contracts import Source
from app.services import budgets, check, check_inputs, drafts
from app.services import response_cards as cards
from app.services.assessment_bounds import (
    cursor,
    digest,
    fit_items,
    principal,
    read_cursor,
    snapshot_token,
    text_window,
)
from app.services.auth import Identity
from app.services.extraction import source_text
from app.services.task_authorization import task_authorized
from app.services.task_workflow import access as task_access

type ActionChecks = dict[tuple[str, UUID, str, str | None], tuple[str, ...]]


async def action(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    name: Any,
    scope: str,
    *,
    role: Any = None,
    domain: Any = None,
    valid: bool = True,
    human: bool = False,
    checks: ActionChecks | None = None,
) -> ActionAvailability:
    key = (digest(principal(actor)), task_id, scope, domain)
    cached = checks.get(key) if checks is not None else None
    if cached is None:
        blockers = []
        # Reuse only task-policy outcomes within this projection. Row state and
        # human/role hints stay uncached; every write independently reauthorizes.
        try:
            await task_access(
                session, actor, task_id, scope=scope, write=True, domain=domain, lock=False
            )
        except ServiceError as error:
            if error.status == 404:
                raise
            if error.code not in {"forbidden", "task_archived"}:
                raise
            blockers.append(error.code)
        if checks is not None:
            # access may refresh the actor's live role/scopes.
            key = (digest(principal(actor)), task_id, scope, domain)
            checks[key] = tuple(blockers)
    else:
        blockers = list(cached)
    if scope not in actor.scopes and "forbidden" not in blockers:
        blockers.append("forbidden")
    if actor.role == "viewer" or (role is not None and actor.role != role):
        blockers.append("wrong_role")
    if human and (actor.actor_kind != "session" or actor.token_id is not None):
        blockers.append("human_required")
    if not valid:
        blockers.append("check_input_changed")
    return ActionAvailability(
        action=name,
        allowed=not blockers,
        required_role=role,
        review_domain=domain,
        blocker_codes=blockers,
    )


@task_authorized("check:read")
async def inputs(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    extraction_job_id: UUID,
    storage: Storage,
    settings: Settings,
) -> AssessmentInputsData:
    checks: ActionChecks = {}
    actor = await check_inputs.access(session, actor, "check:read")
    actor.require("score:read")
    task = await session.get(Task, task_id)
    extraction, requirements = await cards.extraction_scope(session, task_id, extraction_job_id)
    if task is None or extraction.document_id is None:
        raise not_found()
    # Draft bodies are read only for the two advertised choices, never an entire
    # historical list. Current input metadata is computed once for this extraction.
    batch = await cards.CardReadBatch.load(
        session,
        actor,
        requirements,
        historical_card_ids=set(),
        historical_revision_ids=set(),
        storage=storage,
    )
    current = drafts.current_draft_inputs(requirements, batch)
    latest = await session.scalar(
        select(DraftRun)
        .where(DraftRun.task_id == task_id, DraftRun.extraction_job_id == extraction_job_id)
        .order_by(DraftRun.created_at.desc(), DraftRun.id.desc())
        .limit(1)
    )
    selected = None
    async for candidate in await session.stream_scalars(
        select(DraftRun)
        .where(DraftRun.task_id == task_id, DraftRun.extraction_job_id == extraction_job_id)
        .order_by(DraftRun.created_at.desc(), DraftRun.id.desc())
        .execution_options(yield_per=50)
    ):
        if all(
            current.get(entry["requirement_id"]) == entry
            for entry in candidate.input_manifest["requirements"]
        ):
            selected = candidate
            break
    choices: dict[UUID, DraftChoice] = {}
    for run in {row.id: row for row in (latest, selected) if row is not None}.values():
        loaded, rows, current_inputs = await drafts.load_draft_reads(
            session, actor, [run], requirements, storage
        )
        view = drafts.draft_view(run, rows[run.id], loaded, current_inputs)
        choices[run.id] = DraftChoice(
            draft_id=run.id,
            extraction_job_id=run.extraction_job_id,
            created_at=run.created_at,
            validity=view["validity"],
            completion=view["completion"],
            input_hash=run.input_hash,
        )
    selected_choice = choices.get(selected.id) if selected else None
    if selected_choice is not None and selected_choice.validity != "current":
        selected_choice = None
    available = selected_choice is not None
    return AssessmentInputsData(
        org_id=actor.org_id,
        task_id=task_id,
        extraction_job_id=extraction_job_id,
        document_id=extraction.document_id,
        latest_draft=choices.get(latest.id) if latest else None,
        current_draft=selected_choice,
        redaction_enabled=task.model_redaction_enabled,
        redaction_revision=task.model_redaction_revision,
        task_budget=await budgets.view(session, task, settings),
        actions=[
            await action(
                session, actor, task_id, "check_run", "check:run", valid=available, checks=checks
            ),
            await action(
                session, actor, task_id, "rubric_generate", "score:rubric:generate", checks=checks
            ),
            await action(
                session, actor, task_id, "score_run", "score:run", valid=available, checks=checks
            ),
        ],
    )


async def job_access(session: AsyncSession, actor: Identity, job: Job, storage: Storage) -> None:
    if job.kind == "check":
        await check.job_access(session, actor, job, storage)
    elif job.kind == "score_rubric":
        from app.services.score_generation import job_access as rubric_access

        await rubric_access(session, actor, job)
    elif job.kind == "score":
        from app.services.score_execution import job_access as score_access

        await score_access(session, actor, job, storage)
    else:
        raise not_found()


def extraction_id(job: Job) -> UUID:
    submission = job.result.get("submission", {})
    manifest = submission.get("input_manifest", {})
    value = manifest.get("extraction_job_id") or submission.get("extraction_job_id")
    if value is None:
        raise ServiceError(
            "assessment_input_integrity", "Assessment job has no fixed extraction", 409, 4
        )
    return UUID(value)


def dated_anchor(anchor: dict[str, Any] | None) -> tuple[datetime, UUID] | None:
    if anchor is None:
        return None
    try:
        created = datetime.fromisoformat(anchor["created_at"])
        if created.tzinfo is None:
            raise ValueError("timezone")
        return created, UUID(anchor["id"])
    except (ValueError, KeyError, TypeError):
        raise ServiceError(
            "invalid_cursor", "Cursor has an invalid keyset position", 400, 2
        ) from None


@task_authorized("job:read")
async def jobs(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    query: AssessmentJobQuery,
    storage: Storage,
    settings: Settings,
) -> AssessmentJobPage:
    checks: ActionChecks = {}
    actor = await cards.access(session, actor, "job:read")
    if await session.get(Task, task_id) is None:
        raise not_found()
    if query.extraction_job_id is not None:
        extraction = await session.get(Job, query.extraction_job_id)
        if extraction is None or extraction.task_id != task_id or extraction.kind != "extract":
            raise not_found()
    conditions: list[Any] = [
        Job.org_id == actor.org_id,
        Job.task_id == task_id,
        Job.kind == query.kind,
    ]
    if query.extraction_job_id is not None:
        conditions.append(
            func.coalesce(
                Job.result["submission"]["input_manifest"]["extraction_job_id"].astext,
                Job.result["submission"]["extraction_job_id"].astext,
            )
            == str(query.extraction_job_id)
        )
    # All matching dependencies are validated even beyond the visible page.
    signatures = []
    async for job in await session.stream_scalars(
        select(Job).where(*conditions).order_by(Job.id).execution_options(yield_per=50)
    ):
        await job_access(session, actor, job, storage)
        signatures.append(
            (str(job.id), job.status, job.attempts, job.result.get("completion"), job.finished_at)
        )
    binding = {
        "task": task_id,
        "kind": query.kind,
        "extraction": query.extraction_job_id,
        "snapshot": digest(signatures),
    }
    anchor = dated_anchor(read_cursor(settings, actor, binding, query.cursor))
    statement = select(Job).where(*conditions)
    if anchor is not None:
        statement = statement.where(
            tuple_(Job.created_at, Job.id) > tuple_(literal(anchor[0]), literal(anchor[1]))
        )
    rows = list(
        (
            await session.scalars(statement.order_by(Job.created_at, Job.id).limit(query.limit + 1))
        ).all()
    )
    items = []
    for job in rows[: query.limit]:
        cancel_scope = {
            "check": "check:run",
            "score": "score:run",
            "score_rubric": "score:rubric:generate",
        }[job.kind]
        cancel = await action(session, actor, task_id, "job_cancel", cancel_scope, checks=checks)
        if "job:cancel" not in actor.scopes or job.status not in {"queued", "running", "cancelled"}:
            cancel = cancel.model_copy(
                update={
                    "allowed": False,
                    "blocker_codes": [
                        "forbidden" if "job:cancel" not in actor.scopes else "terminal_job"
                    ],
                }
            )
        result_id = job.result.get("report_id") or job.result.get("rubric_id")
        error = job.result.get("error")
        items.append(
            AssessmentJobView(
                id=job.id,
                task_id=task_id,
                extraction_job_id=extraction_id(job),
                kind=cast(Any, job.kind),
                status=cast(Any, job.status),
                created_at=job.created_at,
                finished_at=job.finished_at,
                attempts=job.attempts,
                result_id=UUID(result_id) if result_id else None,
                completion=cast(Any, job.result.get("completion")),
                progress=job.result.get("progress"),
                error_code=error.get("code") if isinstance(error, dict) else None,
                stop_reason=job.result.get("stop_reason"),
                cancel=cancel,
            )
        )
    more = (
        cursor(
            settings,
            actor,
            binding,
            {
                "id": str(rows[query.limit - 1].id),
                "created_at": rows[query.limit - 1].created_at.isoformat(),
            },
        )
        if len(rows) > query.limit
        else None
    )
    data = AssessmentJobPageData(
        task_id=task_id, kind=query.kind, total=len(signatures), next_cursor=more
    )
    data, items = fit_items(
        "assessment jobs",
        data,
        items,
        lambda index: cursor(
            settings,
            actor,
            binding,
            {"id": str(rows[index].id), "created_at": rows[index].created_at.isoformat()},
        ),
    )
    return AssessmentJobPage(data=data, items=items)


async def check_summary(
    session: AsyncSession, actor: Identity, report_id: UUID, storage: Storage, settings: Settings
) -> CheckSummaryData:
    actor, run = await check.get_run(session, actor, report_id, storage)
    invalidated = await check.validity(session, actor, run, storage, settings)
    latest = (
        select(CheckDecision.action)
        .where(CheckDecision.finding_id == CheckFinding.id)
        .order_by(CheckDecision.revision.desc())
        .limit(1)
        .scalar_subquery()
    )
    state = func.coalesce(latest, "reopen")
    grouped = (
        await session.execute(
            select(
                CheckFinding.severity,
                CheckFinding.review_domain,
                state.label("state"),
                func.count(),
            )
            .where(CheckFinding.org_id == actor.org_id, CheckFinding.report_id == run.id)
            .group_by(CheckFinding.severity, CheckFinding.review_domain, state)
        )
    ).all()
    groups = [
        FindingGroup(
            severity=severity,
            review_domain=domain,
            status="dismissed" if status == "dismiss" else "open",
            count=count,
        )
        for severity, domain, status, count in grouped
    ]
    item_count = await session.scalar(
        select(func.count()).select_from(CheckItem).where(CheckItem.report_id == run.id)
    )
    certificate_count = await session.scalar(
        select(func.count())
        .select_from(CheckCertificate)
        .where(CheckCertificate.report_id == run.id)
    )
    return CheckSummaryData(
        report=ReportHeader(
            id=run.id,
            org_id=run.org_id,
            task_id=run.task_id,
            job_id=run.job_id,
            extraction_job_id=run.extraction_job_id,
            document_id=run.document_id,
            draft_id=run.draft_id,
            input_hash=run.input_hash,
            assessment_date=run.assessment_date,
            created_at=run.created_at,
            completion=cast(Any, run.completion),
            validity="stale" if invalidated else "current",
            invalidation_codes=invalidated,
            notice_count=len(run.limitations),
        ),
        mode=cast(Any, run.mode),
        item_count=item_count or 0,
        finding_count=sum(group.count for group in groups),
        unassessed_count=run.summary.get("unassessed_count", 0),
        certificate_count=certificate_count or 0,
        groups=groups,
    )


async def check_snapshot(session: AsyncSession, run: CheckRun) -> str:
    revisions = (
        await session.execute(
            select(CheckDecision.finding_id, func.max(CheckDecision.revision))
            .where(CheckDecision.report_id == run.id)
            .group_by(CheckDecision.finding_id)
            .order_by(CheckDecision.finding_id)
        )
    ).all()
    return digest([run.id, run.input_hash, [tuple(row) for row in revisions]])


async def check_page(
    session: AsyncSession,
    actor: Identity,
    report_id: UUID,
    query: CheckPageRequest,
    storage: Storage,
    settings: Settings,
) -> CheckPage:
    checks: ActionChecks = {}
    actor, run = await check.get_run(session, actor, report_id, storage)
    snapshot = await check_snapshot(session, run)
    binding = {
        "parent": report_id,
        "snapshot": snapshot,
        **query.model_dump(mode="json", exclude={"cursor", "limit"}),
    }
    anchor = read_cursor(settings, actor, binding, query.cursor)
    token = snapshot_token(settings, actor, {"parent": report_id, "snapshot": snapshot})
    values: list[Any] = []
    anchors: list[dict[str, Any]] = []
    next_value = None
    subject_actions = []
    if query.part == "notices":
        messages = {
            "confirmed_draft_only": "仅检查已保存且已确认的初稿内容。",
            "semantic_not_checked": "本次未请求语义检查。",
            "coverage_relative_to_saved_requirements": "覆盖情况仅相对于所选提取中保存的要求。",
            "comply_only_is_not_material_proof": "须遵守决定不代表已提供证明材料。",
            "certificate_dates_are_declarations": "证照日期来自保存的声明，不能替代真实性核验。",
            "document_layout_signatures_and_attachments_not_checked": "本次未检查完整投标文件的版式、签章和附件。",
        }
        notices = [
            Notice(code=code, message=messages.get(code, "检查存在限制，请结合对应要求核对。"))
            for code in run.limitations
        ]
        try:
            start = int(anchor["position"]) if anchor else 0
            if start < 0 or start > len(notices):
                raise ValueError("position")
        except (ValueError, KeyError, TypeError):
            raise ServiceError(
                "invalid_cursor", "Cursor has an invalid notice position", 400, 2
            ) from None
        values = notices[start : start + query.limit]
        anchors = [{"position": start + index + 1} for index in range(len(values))]
        total = filtered = len(notices)
        if start + len(values) < total:
            next_value = cursor(settings, actor, binding, anchors[-1])
    else:
        model = {"findings": CheckFinding, "coverage": CheckItem, "certificates": CheckCertificate}[
            query.part
        ]
        conditions: list[Any] = [
            model.org_id == actor.org_id,
            model.report_id == run.id,
            model.task_id == run.task_id,
        ]
        total = (
            await session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0
        )
        if query.entry_id:
            if (
                await session.scalar(
                    select(model.id).where(*conditions, model.id == query.entry_id)
                )
                is None
            ):
                raise not_found()
            conditions.append(model.id == query.entry_id)
        if query.requirement_id:
            requirement = await session.get(Requirement, query.requirement_id)
            if (
                requirement is None
                or requirement.task_id != run.task_id
                or requirement.job_id != run.extraction_job_id
            ):
                raise not_found()
            if query.part == "certificates":
                conditions.append(
                    model.id.in_(
                        select(CheckCertificateItem.certificate_id)
                        .join(CheckItem, CheckItem.id == CheckCertificateItem.check_item_id)
                        .where(
                            CheckItem.report_id == run.id,
                            CheckItem.requirement_id == query.requirement_id,
                        )
                    )
                )
            else:
                conditions.append(cast(Any, model).requirement_id == query.requirement_id)
        if query.part == "findings":
            if query.severity:
                conditions.append(CheckFinding.severity == query.severity)
            if query.domain:
                conditions.append(
                    CheckFinding.review_domain.is_(None)
                    if query.domain == "unclassified"
                    else CheckFinding.review_domain == query.domain
                )
            if query.status:
                decision = (
                    select(CheckDecision.action)
                    .where(CheckDecision.finding_id == CheckFinding.id)
                    .order_by(CheckDecision.revision.desc())
                    .limit(1)
                    .scalar_subquery()
                )
                conditions.append(
                    func.coalesce(decision, "reopen") == "dismiss"
                    if query.status == "dismissed"
                    else func.coalesce(decision, "reopen") != "dismiss"
                )
        filtered = (
            await session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0
        )
        # The keyset sorts identifiers/metadata before loading any quote or reason.
        order: list[Any]
        if query.part == "findings":
            order = [
                case(
                    {"disqualification_risk": 0, "deduction_risk": 1, "info": 2},
                    value=CheckFinding.severity,
                    else_=3,
                ),
                case({"commercial": 0, "technical": 1}, value=CheckFinding.review_domain, else_=2),
                model.id,
            ]
        elif query.part == "coverage":
            positions = {
                UUID(entry["requirement_id"]): index
                for index, entry in enumerate(run.input_manifest["items"])
            }
            order = [
                case(positions, value=CheckItem.requirement_id, else_=len(positions)),
                CheckItem.requirement_id,
                model.id,
            ]
        else:
            order = [model.id]
        statement = select(
            model.id, *[value.label(f"order_{index}") for index, value in enumerate(order)]
        ).where(*conditions)
        if anchor:
            try:
                position = anchor["order"]
                if not isinstance(position, list) or len(position) != len(order):
                    raise ValueError("order")
                converted = [
                    UUID(value)
                    if index == len(order) - 1 or (query.part == "coverage" and index == 1)
                    else int(value)
                    for index, value in enumerate(position)
                ]
                statement = statement.where(
                    tuple_(*order) > tuple_(*[literal(value) for value in converted])
                )
            except (ValueError, KeyError, TypeError):
                raise ServiceError(
                    "invalid_cursor", "Cursor has an invalid row anchor", 400, 2
                ) from None
        keys = (await session.execute(statement.order_by(*order).limit(query.limit + 1))).all()
        selected_keys = keys[: query.limit]
        ids = [entry[0] for entry in selected_keys]
        anchors = [
            {
                "id": str(entry[0]),
                "order": [str(value) if isinstance(value, UUID) else value for value in entry[1:]],
            }
            for entry in selected_keys
        ]
        if len(keys) > query.limit:
            next_value = cursor(settings, actor, binding, anchors[-1])
        loaded = (
            (
                await session.scalars(
                    select(model).where(
                        model.org_id == actor.org_id, model.report_id == run.id, model.id.in_(ids)
                    )
                )
            ).all()
            if ids
            else []
        )
        by_id = {row.id: row for row in loaded}
        rows = [by_id[id_] for id_ in ids]
        if query.part == "findings":
            latest = (
                (
                    await session.execute(
                        select(
                            CheckDecision.finding_id,
                            CheckDecision.id,
                            CheckDecision.revision,
                            CheckDecision.action,
                        )
                        .where(CheckDecision.report_id == run.id, CheckDecision.finding_id.in_(ids))
                        .distinct(CheckDecision.finding_id)
                        .order_by(CheckDecision.finding_id, CheckDecision.revision.desc())
                    )
                ).all()
                if ids
                else []
            )
            decisions = {row.finding_id: row for row in latest}
            citations: dict[UUID, list[Any]] = {}
            for citation in (
                (
                    await session.scalars(
                        select(CheckFindingCitation)
                        .where(
                            CheckFindingCitation.report_id == run.id,
                            CheckFindingCitation.finding_id.in_(ids),
                        )
                        .order_by(CheckFindingCitation.created_at, CheckFindingCitation.id)
                    )
                ).all()
                if ids
                else []
            ):
                if citation.finding_id is not None:
                    citations.setdefault(citation.finding_id, []).append(
                        check.citation_view(citation)
                    )
            for row in rows:
                decision = decisions.get(row.id)
                values.append(
                    FindingView.model_validate(
                        {
                            **{
                                key: getattr(row, key)
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
                            "citations": citations.get(row.id, []),
                            "status": "dismissed"
                            if decision is not None and decision.action == "dismiss"
                            else "open",
                            "revision": decision.revision if decision is not None else 1,
                            "latest_decision_id": decision.id if decision is not None else None,
                        }
                    )
                )
        elif query.part == "coverage":
            semantic: dict[UUID, list[Any]] = {}
            finding_ids: dict[UUID, list[UUID]] = {}
            for citation in (
                (
                    await session.scalars(
                        select(CheckFindingCitation)
                        .where(
                            CheckFindingCitation.report_id == run.id,
                            CheckFindingCitation.check_item_id.in_(ids),
                        )
                        .order_by(CheckFindingCitation.created_at, CheckFindingCitation.id)
                    )
                ).all()
                if ids
                else []
            ):
                if citation.check_item_id is not None:
                    semantic.setdefault(citation.check_item_id, []).append(
                        check.citation_payload(citation)
                    )
            for item_id, finding_id in await session.execute(
                select(CheckFinding.check_item_id, CheckFinding.id)
                .where(CheckFinding.report_id == run.id, CheckFinding.check_item_id.in_(ids))
                .order_by(CheckFinding.id)
            ):
                finding_ids.setdefault(item_id, []).append(finding_id)
            for row in rows:
                values.append(
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
                                    "semantic_outcome",
                                    "semantic_reason_code",
                                )
                            },
                            "finding_ids": finding_ids.get(row.id, []),
                            "semantic_citations": semantic.get(row.id, []),
                        }
                    )
                )
        else:
            requirements: dict[UUID, list[UUID]] = {}
            for certificate_id, requirement_id in await session.execute(
                select(CheckCertificateItem.certificate_id, CheckItem.requirement_id)
                .join(CheckItem, CheckItem.id == CheckCertificateItem.check_item_id)
                .where(
                    CheckCertificateItem.report_id == run.id,
                    CheckCertificateItem.certificate_id.in_(ids),
                    CheckItem.report_id == run.id,
                )
                .order_by(CheckItem.requirement_id)
            ):
                requirements.setdefault(certificate_id, []).append(requirement_id)
            for row in rows:
                values.append(
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
                            "requirement_ids": requirements.get(row.id, []),
                        }
                    )
                )
    invalidated = await check.validity(session, actor, run, storage, settings)
    if query.part == "findings":
        for value in values:
            role = {"commercial": "bidder", "technical": "technical"}.get(value.review_domain)
            availability = await action(
                session,
                actor,
                run.task_id,
                "finding_reopen" if value.status == "dismissed" else "finding_dismiss",
                "check:decide",
                role=role,
                domain=value.review_domain,
                checks=checks,
                valid=not invalidated,
                human=True,
            )
            if role is None:
                availability = availability.model_copy(
                    update={"allowed": False, "blocker_codes": ["unclassified"]}
                )
            subject_actions.append(SubjectActions(subject_id=value.id, actions=[availability]))
    data = PageData(
        task_id=run.task_id,
        parent_id=run.id,
        part=query.part,
        snapshot=token,
        total=total,
        filtered_total=filtered,
        returned=len(values),
        next_cursor=next_value,
        validity="stale" if invalidated else "current",
        subject_actions=subject_actions,
    )
    data, values = fit_items(
        "check show",
        data,
        values,
        lambda index: cursor(settings, actor, binding, anchors[index]),
        ok=run.completion != "partial",
        warnings=["check_input_changed"] if invalidated else [],
    )
    return ProjectionPage[FindingView | CheckItemView | CheckCertificateView | Notice](
        data=data, items=values
    )


@task_authorized("check:read")
async def check_history(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    query: AssessmentHistoryQuery,
    storage: Storage,
    settings: Settings,
) -> tuple[AssessmentListData, list[CheckSummaryData]]:
    actor = await check_inputs.access(session, actor, "check:read")
    if await session.get(Task, task_id) is None:
        raise not_found()
    conditions: list[Any] = [CheckRun.org_id == actor.org_id, CheckRun.task_id == task_id]
    if query.extraction_job_id:
        extraction = await session.get(Job, query.extraction_job_id)
        if extraction is None or extraction.task_id != task_id or extraction.kind != "extract":
            raise not_found()
        conditions.append(CheckRun.extraction_job_id == query.extraction_job_id)
    signatures = []
    for id_, input_hash, manifest in await session.execute(
        select(CheckRun.id, CheckRun.input_hash, CheckRun.input_manifest)
        .where(*conditions)
        .order_by(CheckRun.id)
    ):
        await check_inputs.require_dependencies(session, actor, task_id, manifest, storage)
        signatures.append((str(id_), input_hash))
    revisions = (
        await session.execute(
            select(CheckDecision.id, CheckDecision.revision)
            .where(CheckDecision.task_id == task_id)
            .order_by(CheckDecision.id)
        )
    ).all()
    binding = {
        "task": task_id,
        "extraction": query.extraction_job_id,
        "snapshot": digest([signatures, [tuple(row) for row in revisions]]),
    }
    anchor = dated_anchor(read_cursor(settings, actor, binding, query.cursor))
    statement = select(CheckRun.id, CheckRun.created_at).where(*conditions)
    if anchor:
        statement = statement.where(
            tuple_(CheckRun.created_at, CheckRun.id)
            > tuple_(literal(anchor[0]), literal(anchor[1]))
        )
    rows = (
        await session.execute(
            statement.order_by(CheckRun.created_at, CheckRun.id).limit(query.limit + 1)
        )
    ).all()
    items = [
        await check_summary(session, actor, row.id, storage, settings)
        for row in rows[: query.limit]
    ]
    more = (
        cursor(
            settings,
            actor,
            binding,
            {
                "id": str(rows[query.limit - 1].id),
                "created_at": rows[query.limit - 1].created_at.isoformat(),
            },
        )
        if len(rows) > query.limit
        else None
    )
    data = AssessmentListData(task_id=task_id, total=len(signatures), next_cursor=more)
    partial = any(row.report.completion == "partial" for row in items)
    data, items = fit_items(
        "check list",
        data,
        items,
        lambda index: cursor(
            settings,
            actor,
            binding,
            {"id": str(rows[index].id), "created_at": rows[index].created_at.isoformat()},
        ),
        ok=not partial,
    )
    return data, items


@task_authorized("task:read")
async def citation(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    query: CitationRequest,
    storage: Storage,
    settings: Settings,
) -> CitationContextData:
    if query.parent_kind == "rubric":
        from app.models.score import ScoreRubricCoverage, ScoreRubricItem, ScoreRubricSection
        from app.services import score

        actor, row = await score.get_set(session, actor, task_id, query.parent_id)
        model = {
            "rubric_section": ScoreRubricSection,
            "rubric_item": ScoreRubricItem,
            "coverage": ScoreRubricCoverage,
        }[query.part]
        child = await session.scalar(
            select(model).where(
                model.id == query.entry_id, model.rubric_id == row.id, model.task_id == task_id
            )
        )
        if child is None:
            raise not_found()
        if query.part == "rubric_section":
            sources = score.section_sources(child)
            if query.citation_index >= len(sources):
                raise not_found()
            selected = sources[query.citation_index]
            requirement_id = UUID(str(selected["requirement_id"]))
            requirement = await session.get(Requirement, requirement_id)
            if (
                requirement is None
                or requirement.task_id != task_id
                or requirement.job_id != row.extraction_job_id
                or selected["source"] != cards.source(requirement)
            ):
                raise not_found()
            payload = {"kind": "tender", "source": selected["source"], "quote": selected["quote"]}
        else:
            requirement_id = child.requirement_id
            payload = {"kind": "tender", "source": child.source}
        return await resolve_citation(
            session,
            actor,
            task_id,
            row.extraction_job_id,
            row.document_id,
            None,
            requirement_id,
            None,
            query,
            payload,
            storage,
            row.input_manifest,
        )
    if query.parent_kind == "score":
        from app.models.score import ScoreItemCitation, ScoreReportItem, ScoreReportItemResponse
        from app.services.assessment_scores import get_run

        actor, score_run = await get_run(session, actor, task_id, query.parent_id)
        score_item = await session.scalar(
            select(ScoreReportItem).where(
                ScoreReportItem.id == query.entry_id,
                ScoreReportItem.report_id == score_run.id,
                ScoreReportItem.task_id == task_id,
            )
        )
        if score_item is None:
            raise not_found()
        saved = await session.scalar(
            select(ScoreItemCitation)
            .where(
                ScoreItemCitation.score_item_id == score_item.id,
                ScoreItemCitation.report_id == score_run.id,
            )
            .order_by(ScoreItemCitation.created_at, ScoreItemCitation.id)
            .offset(query.citation_index)
            .limit(1)
        )
        if saved is None:
            raise not_found()
        payload = (
            {"kind": saved.kind, "source": saved.source}
            if saved.kind == "tender"
            else {
                "kind": "draft",
                **{
                    key: getattr(saved, key)
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
        requirement_id = score_item.requirement_id
        if saved.kind == "draft":
            support = await session.scalar(
                select(ScoreReportItemResponse).where(
                    ScoreReportItemResponse.score_item_id == score_item.id,
                    ScoreReportItemResponse.report_id == score_run.id,
                    ScoreReportItemResponse.response_item_id == saved.response_item_id,
                    ScoreReportItemResponse.card_revision_id == saved.card_revision_id,
                )
            )
            if support is None:
                raise not_found()
            requirement_id = support.requirement_id
        return await resolve_citation(
            session,
            actor,
            task_id,
            score_run.extraction_job_id,
            score_run.document_id,
            score_run.draft_id,
            requirement_id,
            saved.card_revision_id,
            query,
            payload,
            storage,
            score_run.input_manifest,
        )
    actor, run = await check.get_run(session, actor, query.parent_id, storage)
    if run.task_id != task_id:
        raise not_found()
    model = CheckFinding if query.part == "finding" else CheckItem
    entry = await session.scalar(
        select(model).where(
            model.id == query.entry_id, model.task_id == task_id, model.report_id == run.id
        )
    )
    if entry is None:
        raise not_found()
    item = (
        await session.get(CheckItem, entry.check_item_id)
        if isinstance(entry, CheckFinding)
        else entry
    )
    if item is None or item.report_id != run.id:
        raise not_found()
    payload: dict[str, Any]
    if query.origin == "source":
        payload = {"kind": "tender", "source": entry.source}
    else:
        condition = (
            CheckFindingCitation.finding_id == entry.id
            if query.part == "finding"
            else CheckFindingCitation.check_item_id == entry.id
        )
        saved = await session.scalar(
            select(CheckFindingCitation)
            .where(
                condition,
                CheckFindingCitation.report_id == run.id,
                CheckFindingCitation.task_id == task_id,
            )
            .order_by(CheckFindingCitation.created_at, CheckFindingCitation.id)
            .offset(query.citation_index)
            .limit(1)
        )
        if saved is None:
            raise not_found()
        payload = check.citation_payload(saved)
    return await resolve_citation(
        session,
        actor,
        task_id,
        run.extraction_job_id,
        run.document_id,
        run.draft_id,
        item.requirement_id,
        item.card_revision_id,
        query,
        payload,
        storage,
        run.input_manifest,
    )


async def resolve_citation(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    extraction_job_id: UUID,
    document_id: UUID,
    draft_id: UUID | None,
    requirement_id: UUID,
    historical_revision: UUID | None,
    query: CitationRequest,
    payload: dict[str, Any],
    storage: Storage,
    manifest: dict[str, Any],
) -> CitationContextData:
    kind = payload["kind"]
    metadata: dict[str, Any] = {}
    quote = payload.get("quote")
    if kind == "tender":
        source = Source.model_validate(payload["source"])
        chunk = await session.get(Chunk, source.chunk_id)
        if (
            chunk is None
            or chunk.task_id != task_id
            or chunk.document_id != document_id
            or source.document_id != document_id
        ):
            raise not_found()
        document = await session.get(Document, document_id)
        requirements = manifest.get("requirements", manifest.get("items", []))
        fixed = next(
            (entry for entry in requirements if entry.get("requirement_id") == str(requirement_id)),
            None,
        )
        if (
            document is None
            or document.sha256 != manifest.get("document_sha256")
            or fixed is None
            or fixed.get("chunk_id") != str(chunk.id)
        ):
            raise ServiceError(
                "invalid_input_citation", "The saved source binding has changed", 409, 4
            )
        chunk_hash = drafts.digest(
            {key: getattr(chunk, key) for key in ("text", "blocks", "page", "citation_verified")}
        )
        if chunk_hash != fixed.get("chunk_sha256"):
            raise ServiceError(
                "invalid_input_citation", "The saved source content has changed", 409, 4
            )
        original = source_text(
            source,
            {
                key: getattr(chunk, key)
                for key in ("document_id", "text", "page", "blocks", "citation_verified")
            },
        )
        if original is None:
            raise ServiceError(
                "invalid_input_citation", "Saved source location cannot be verified", 409, 4
            )
        quote = source.quote if quote is None else quote
        metadata = {
            "document_id": source.document_id,
            "page": source.page,
            "location": source.location,
        }
    elif kind == "draft":
        row = await session.get(ResponseItem, UUID(str(payload["response_item_id"])))
        revision = await session.get(ResponseCardRevision, UUID(str(payload["card_revision_id"])))
        if (
            row is None
            or row.draft_id != draft_id
            or row.requirement_id != requirement_id
            or str(draft_id) != str(payload["draft_id"])
            or revision is None
            or row.card_revision_id != revision.id
            or row.card_id != revision.card_id
        ):
            raise not_found()
        field = payload["field"]
        if field not in {"response_text", "deviation_note"}:
            raise ServiceError("invalid_input_citation", "Saved citation field is invalid", 409, 4)
        original = getattr(row, field)
        if original != getattr(revision, field) or not isinstance(original, str):
            raise ServiceError(
                "invalid_input_citation", "Saved response revision has changed", 409, 4
            )
        metadata = {
            "draft_id": draft_id,
            "response_item_id": row.id,
            "card_revision_id": revision.id,
            "field": field,
        }
    elif kind == "evidence":
        evidence = await session.get(Evidence, UUID(str(payload["evidence_id"])))
        if evidence is None or evidence.task_id != task_id:
            raise not_found()
        if evidence.confirmed_by is None or evidence.confirmed_at is None:
            raise ServiceError(
                "invalid_input_citation", "Saved evidence is no longer confirmed", 409, 4
            )
        if (
            historical_revision is None
            or await session.scalar(
                select(CardEvidenceLink.id).where(
                    CardEvidenceLink.revision_id == historical_revision,
                    CardEvidenceLink.evidence_id == evidence.id,
                )
            )
            is None
        ):
            raise not_found()
        await cards.evidence_view(session, actor, evidence)
        if evidence.kind == "certificate_pdf_page":
            from app.services.evidence_sources import require_source

            if evidence.evidence_source_id is None:
                raise not_found()
            archive, _, file = await require_source(session, actor, evidence.evidence_source_id)
            content = await storage.read(actor.org_id, file.storage_key)
            original = await asyncio.to_thread(cards.page_text, content, archive.page)
        elif evidence.kind in cards.MATERIALS:
            _, revision_model, _, revision_field, _, _ = cards.MATERIALS[evidence.kind]
            material = await session.get(revision_model, getattr(evidence, revision_field))
            if material is None:
                raise not_found()
            original = material.data.get(evidence.field_path)
        else:
            original = None
        if not isinstance(original, str):
            raise ServiceError(
                "invalid_input_citation", "Saved evidence has no verified text", 409, 4
            )
        metadata = {"evidence_id": evidence.id}
    else:
        raise ServiceError("invalid_input_citation", "Unknown saved citation kind", 409, 4)
    if not isinstance(quote, str):
        raise ServiceError("invalid_input_citation", "Saved citation has no quote", 409, 4)
    window, start, end = text_window(original, quote, query)
    card_id = await session.scalar(
        select(ResponseCard.id).where(
            ResponseCard.task_id == task_id,
            ResponseCard.extraction_job_id == extraction_job_id,
            ResponseCard.requirement_id == requirement_id,
        )
    )
    return CitationContextData(
        parent_id=query.parent_id,
        entry_id=query.entry_id,
        kind=cast(Literal["tender", "draft", "evidence"], kind),
        text_kind=query.text,
        window=window,
        quote_start=start,
        quote_end=end,
        fix=FixTarget(
            task_id=task_id,
            extraction_job_id=extraction_job_id,
            requirement_id=requirement_id,
            current_card_id=card_id,
            historical_card_revision_id=historical_revision,
        ),
        **metadata,
    )
