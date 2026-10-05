"""Score report projections preserve saved estimates, nulls and total status."""

from typing import Any
from uuid import UUID

from sqlalchemy import String, column, func, literal, select, true, tuple_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.core.config import Settings
from app.core.errors import ServiceError, not_found
from app.models.entities import Job, Requirement, Task
from app.models.score import (
    ScoreItemCitation,
    ScoreReport,
    ScoreReportItem,
    ScoreReportItemResponse,
    ScoreRubricItem,
    ScoreRubricSection,
)
from app.schemas.check_contracts import AssessmentListData
from app.schemas.console_assessments import (
    AssessmentHistoryQuery,
    Notice,
    PageData,
    ProjectionPage,
    ReportHeader,
    ScorePage,
    ScorePageRequest,
    ScoreSummaryData,
)
from app.schemas.score_contracts import ScoreItemView, ScoreSectionSummary
from app.services import score_execution, score_run_inputs
from app.services.assessment_bounds import (
    cursor,
    digest,
    fit_items,
    order_values,
    read_cursor,
    snapshot_token,
)
from app.services.assessment_reads import dated_anchor
from app.services.auth import Identity


async def get_run(
    session: AsyncSession, actor: Identity, task_id: UUID, report_id: UUID
) -> tuple[Identity, ScoreReport]:
    actor = await score_run_inputs.access(session, actor, "score:read")
    run = await session.scalar(
        select(ScoreReport)
        .options(defer(ScoreReport.sections))
        .where(
            ScoreReport.org_id == actor.org_id,
            ScoreReport.task_id == task_id,
            ScoreReport.id == report_id,
        )
    )
    if run is None:
        raise not_found()
    await score_run_inputs.require_dependencies(session, actor, task_id, run.input_manifest)
    return actor, run


async def summary(
    session: AsyncSession, actor: Identity, task_id: UUID, report_id: UUID, settings: Settings
) -> ScoreSummaryData:
    actor, run = await get_run(session, actor, task_id, report_id)
    value = await score_execution.run_view(session, actor, run, settings)
    section_count = await session.scalar(
        select(func.jsonb_array_length(ScoreReport.sections)).where(ScoreReport.id == run.id)
    )
    header = ReportHeader(
        **{
            key: value[key]
            for key in (
                "id",
                "org_id",
                "task_id",
                "job_id",
                "created_at",
                "completion",
                "validity",
                "invalidation_codes",
            )
        },
        extraction_job_id=run.extraction_job_id,
        document_id=run.document_id,
        draft_id=run.draft_id,
        input_hash=run.input_hash,
        assessment_date=run.assessment_date,
        notice_count=len(run.limitations),
    )
    fields = (
        "rubric_id",
        "rubric_version",
        "assessed_items",
        "unassessable_items",
        "overall_aggregation",
        "overall_aggregation_assessable",
        "overall_rule_text",
        "overall_cap",
        "assessed_subtotal",
        "total_status",
        "possible_range",
        "estimated_total",
    )
    return ScoreSummaryData(
        report=header, section_count=section_count or 0, **{key: value[key] for key in fields}
    )


async def item_view(session: AsyncSession, row: ScoreReportItem) -> ScoreItemView:
    section = await session.get(ScoreRubricSection, row.section_id)
    if section is None or section.rubric_id != row.rubric_id or section.task_id != row.task_id:
        raise not_found()
    response_ids = list(
        (
            await session.scalars(
                select(ScoreReportItemResponse.response_item_id)
                .where(
                    ScoreReportItemResponse.score_item_id == row.id,
                    ScoreReportItemResponse.report_id == row.report_id,
                )
                .order_by(ScoreReportItemResponse.response_item_id)
            )
        ).all()
    )
    citations = []
    for saved in await session.scalars(
        select(ScoreItemCitation)
        .where(
            ScoreItemCitation.score_item_id == row.id, ScoreItemCitation.report_id == row.report_id
        )
        .order_by(ScoreItemCitation.created_at, ScoreItemCitation.id)
    ):
        if saved.kind == "tender":
            citations.append({"kind": "tender", "source": saved.source})
        else:
            citations.append(
                {
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
    keys = (
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
    return ScoreItemView.model_validate(
        {
            **{key: getattr(row, key) for key in keys},
            "section_key": section.key,
            "response_item_ids": response_ids,
            "citations": citations,
        }
    )


async def page(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    report_id: UUID,
    query: ScorePageRequest,
    settings: Settings,
) -> ScorePage:
    actor, run = await get_run(session, actor, task_id, report_id)
    snapshot = digest([run.id, run.input_hash])
    binding = {
        "parent": report_id,
        "snapshot": snapshot,
        **query.model_dump(mode="json", exclude={"cursor", "limit"}),
    }
    anchor = read_cursor(settings, actor, binding, query.cursor)
    more = None
    values: list[Any] = []
    anchors: list[dict[str, Any]] = []
    if query.part == "items":
        conditions: list[Any] = [
            ScoreReportItem.org_id == actor.org_id,
            ScoreReportItem.report_id == run.id,
            ScoreReportItem.task_id == task_id,
        ]
        total = (
            await session.scalar(
                select(func.count()).select_from(ScoreReportItem).where(*conditions)
            )
            or 0
        )
        if query.entry_id:
            if (
                await session.scalar(
                    select(ScoreReportItem.id).where(
                        *conditions, ScoreReportItem.id == query.entry_id
                    )
                )
                is None
            ):
                raise not_found()
            conditions.append(ScoreReportItem.id == query.entry_id)
        if query.requirement_id:
            requirement = await session.get(Requirement, query.requirement_id)
            if (
                requirement is None
                or requirement.task_id != task_id
                or requirement.job_id != run.extraction_job_id
            ):
                raise not_found()
            conditions.append(ScoreReportItem.requirement_id == query.requirement_id)
        if query.outcome:
            conditions.append(ScoreReportItem.outcome == query.outcome)
        if query.section_key:
            conditions.append(
                ScoreReportItem.section_id.in_(
                    select(ScoreRubricSection.id).where(
                        ScoreRubricSection.rubric_id == run.rubric_id,
                        ScoreRubricSection.key == query.section_key,
                    )
                )
            )
        filtered = (
            await session.scalar(
                select(func.count()).select_from(ScoreReportItem).where(*conditions)
            )
            or 0
        )
        section_order = (
            select(ScoreRubricSection.order)
            .where(ScoreRubricSection.id == ScoreReportItem.section_id)
            .scalar_subquery()
        )
        item_order = (
            select(ScoreRubricItem.order)
            .where(ScoreRubricItem.id == ScoreReportItem.rubric_item_id)
            .scalar_subquery()
        )
        ordering = [section_order, item_order]
        statement = select(ScoreReportItem.id, *ordering).where(*conditions)
        if anchor:
            order, id_ = order_values(anchor, len(ordering))
            statement = statement.where(
                tuple_(*ordering, ScoreReportItem.id)
                > tuple_(*(literal(value) for value in order), literal(id_))
            )
        selected = (
            await session.execute(
                statement.order_by(*ordering, ScoreReportItem.id).limit(query.limit + 1)
            )
        ).all()
        ids = [entry[0] for entry in selected[: query.limit]]
        anchors = [
            {"id": str(entry[0]), "order": list(entry[1:])} for entry in selected[: query.limit]
        ]
        if len(selected) > query.limit:
            more = cursor(settings, actor, binding, anchors[-1])
        rows = {
            row.id: row
            for row in (
                await session.scalars(select(ScoreReportItem).where(ScoreReportItem.id.in_(ids)))
            ).all()
        }
        values = [await item_view(session, rows[id_]) for id_ in ids]
    else:
        # Stored section/notice JSON is expanded and keyset-paged in PostgreSQL;
        # no full sections array is loaded or sliced by the application.
        json_column = ScoreReport.sections if query.part == "sections" else ScoreReport.limitations
        expand = (
            func.jsonb_array_elements(json_column)
            if query.part == "sections"
            else func.jsonb_array_elements_text(json_column)
        )
        entries = (
            expand.table_valued(
                column("value", JSONB if query.part == "sections" else String),
                with_ordinality="position",
            )
            .render_derived(name="assessment_entries")
            .lateral()
        )
        source = (
            select(entries.c.value, entries.c.position)
            .select_from(ScoreReport)
            .join(entries, true())
            .where(ScoreReport.id == run.id)
        )
        total = (
            await session.scalar(
                select(func.jsonb_array_length(json_column)).where(ScoreReport.id == run.id)
            )
            or 0
        )
        if query.section_key:
            source = source.where(entries.c.value["section_key"].astext == query.section_key)
        filtered = await session.scalar(select(func.count()).select_from(source.subquery())) or 0
        if anchor:
            try:
                position = int(anchor["position"])
                if position < 1:
                    raise ValueError("position")
            except (ValueError, KeyError, TypeError):
                raise ServiceError(
                    "invalid_cursor", "Cursor has an invalid JSON position", 400, 2
                ) from None
            source = source.where(entries.c.position > position)
        rows = (
            await session.execute(source.order_by(entries.c.position).limit(query.limit + 1))
        ).all()
        if len(rows) > query.limit:
            more = cursor(settings, actor, binding, {"position": rows[query.limit - 1].position})
        anchors = [{"position": row.position} for row in rows[: query.limit]]
        values = [
            ScoreSectionSummary.model_validate(row.value)
            if query.part == "sections"
            else Notice(code=row.value, message="评分结果存在限制或未完成项，请根据提示代码复核。")
            for row in rows[: query.limit]
        ]
    report = await score_execution.run_view(session, actor, run, settings)
    projected = ProjectionPage[ScoreSectionSummary | ScoreItemView | Notice](
        data=PageData(
            task_id=task_id,
            parent_id=report_id,
            part=query.part,
            snapshot=snapshot_token(settings, actor, {"parent": report_id, "snapshot": snapshot}),
            total=total,
            filtered_total=filtered,
            returned=len(values),
            next_cursor=more,
            validity=report["validity"],
        ),
        items=values,
    )

    partial = (
        run.completion == "partial"
        or run.summary.get("unassessable_items", 0) > 0
        or run.summary.get("total_status") != "estimated"
    )
    warnings = ["score_input_changed"] if report["validity"] == "stale" else []
    data, items = fit_items(
        "score show",
        projected.data,
        projected.items,
        lambda index: cursor(settings, actor, binding, anchors[index]),
        ok=not partial,
        warnings=warnings,
    )
    return projected.model_copy(update={"data": data, "items": items})


async def history(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    query: AssessmentHistoryQuery,
    settings: Settings,
) -> tuple[AssessmentListData, list[ScoreSummaryData]]:
    actor = await score_run_inputs.access(session, actor, "score:read")
    if await session.get(Task, task_id) is None:
        raise not_found()
    conditions: list[Any] = [ScoreReport.task_id == task_id, ScoreReport.org_id == actor.org_id]
    if query.extraction_job_id:
        extraction = await session.get(Job, query.extraction_job_id)
        if extraction is None or extraction.task_id != task_id or extraction.kind != "extract":
            raise not_found()
        conditions.append(ScoreReport.extraction_job_id == query.extraction_job_id)
    signatures = []
    for id_, input_hash, manifest in await session.execute(
        select(ScoreReport.id, ScoreReport.input_hash, ScoreReport.input_manifest)
        .where(*conditions)
        .order_by(ScoreReport.id)
    ):
        await score_run_inputs.require_dependencies(session, actor, task_id, manifest)
        signatures.append((str(id_), input_hash))
    binding = {
        "task": task_id,
        "extraction": query.extraction_job_id,
        "snapshot": digest(signatures),
    }
    anchor = dated_anchor(read_cursor(settings, actor, binding, query.cursor))
    statement = select(ScoreReport.id, ScoreReport.created_at).where(*conditions)
    if anchor:
        statement = statement.where(
            tuple_(ScoreReport.created_at, ScoreReport.id)
            > tuple_(literal(anchor[0]), literal(anchor[1]))
        )
    rows = (
        await session.execute(
            statement.order_by(ScoreReport.created_at, ScoreReport.id).limit(query.limit + 1)
        )
    ).all()
    items = [
        await summary(session, actor, task_id, row.id, settings) for row in rows[: query.limit]
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
    partial = any(
        row.report.completion == "partial"
        or row.unassessable_items > 0
        or row.total_status != "estimated"
        for row in items
    )
    data, items = fit_items(
        "score list",
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
