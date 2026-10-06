"""Bounded rubric metadata, human capabilities and canonical source projections."""

from typing import Any
from uuid import UUID

from sqlalchemy import case, func, literal, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ServiceError, not_found
from app.models.entities import Job, Requirement, Task
from app.models.score import (
    ScoreRubricClassification,
    ScoreRubricCoverage,
    ScoreRubricCoverageDecision,
    ScoreRubricCoverageItem,
    ScoreRubricDecision,
    ScoreRubricItem,
    ScoreRubricRevisionEvent,
    ScoreRubricSection,
    ScoreRubricSet,
)
from app.schemas.check_contracts import AssessmentListData
from app.schemas.console_assessments import (
    AssessmentHistoryQuery,
    ConsoleRubricSectionView,
    Notice,
    PageData,
    ProjectionPage,
    RubricCompletenessSummary,
    RubricPage,
    RubricPageRequest,
    RubricReplacementData,
    RubricSummaryData,
    SubjectActions,
)
from app.schemas.score_contracts import (
    RubricCompletenessView,
    RubricItemView,
    RubricRequirementCoverageView,
    RubricReviseRequest,
)
from app.services import score, score_normalization
from app.services.assessment_bounds import (
    cursor,
    digest,
    fit_items,
    notice_positions,
    order_values,
    read_cursor,
    snapshot_token,
)
from app.services.assessment_reads import ActionChecks, action, dated_anchor
from app.services.auth import Identity
from app.services.task_authorization import task_authorized


def review_expression(model: Any, subject: str) -> tuple[Any, Any]:
    """Use the same decision/classification revision winner as score.review_state."""
    id_column = model.id
    decision_filters = [
        ScoreRubricDecision.rubric_id == model.rubric_id,
        getattr(ScoreRubricDecision, subject) == id_column,
    ]
    classification_filters = [
        ScoreRubricClassification.rubric_id == model.rubric_id,
        getattr(ScoreRubricClassification, subject) == id_column,
    ]
    decision_revision = (
        select(ScoreRubricDecision.revision)
        .where(*decision_filters)
        .order_by(ScoreRubricDecision.revision.desc())
        .limit(1)
        .scalar_subquery()
    )
    classified_revision = (
        select(ScoreRubricClassification.revision)
        .where(*classification_filters)
        .order_by(ScoreRubricClassification.revision.desc())
        .limit(1)
        .scalar_subquery()
    )
    decision_action = (
        select(ScoreRubricDecision.action)
        .where(*decision_filters)
        .order_by(ScoreRubricDecision.revision.desc())
        .limit(1)
        .scalar_subquery()
    )
    domain = (
        select(ScoreRubricClassification.review_domain)
        .where(*classification_filters)
        .order_by(ScoreRubricClassification.revision.desc())
        .limit(1)
        .scalar_subquery()
    )
    state = case(
        (func.coalesce(decision_revision, 1) < func.coalesce(classified_revision, 1), "candidate"),
        (decision_action == "confirm", "confirmed"),
        (decision_action == "reject", "rejected"),
        else_="candidate",
    )
    return state, domain


async def completeness(session: AsyncSession, row: ScoreRubricSet) -> RubricCompletenessView:
    """Batch count-relevant metadata and reuse authoritative normalization.

    Nonblank-text presence is only an internal normalization input; public
    narratives and source quotes are hydrated solely for the selected page.
    """
    decisions = (
        (
            await session.execute(
                select(
                    ScoreRubricDecision.section_id,
                    ScoreRubricDecision.item_id,
                    ScoreRubricDecision.revision,
                    ScoreRubricDecision.action,
                    ScoreRubricDecision.decided_by,
                    ScoreRubricDecision.decided_at,
                )
                .where(ScoreRubricDecision.rubric_id == row.id)
                .distinct(ScoreRubricDecision.section_id, ScoreRubricDecision.item_id)
                .order_by(
                    ScoreRubricDecision.section_id,
                    ScoreRubricDecision.item_id,
                    ScoreRubricDecision.revision.desc(),
                )
            )
        )
        .mappings()
        .all()
    )
    classifications = (
        (
            await session.execute(
                select(
                    ScoreRubricClassification.section_id,
                    ScoreRubricClassification.item_id,
                    ScoreRubricClassification.revision,
                    ScoreRubricClassification.review_domain,
                )
                .where(ScoreRubricClassification.rubric_id == row.id)
                .distinct(ScoreRubricClassification.section_id, ScoreRubricClassification.item_id)
                .order_by(
                    ScoreRubricClassification.section_id,
                    ScoreRubricClassification.item_id,
                    ScoreRubricClassification.revision.desc(),
                )
            )
        )
        .mappings()
        .all()
    )
    decision_by_subject = {(value["section_id"], value["item_id"]): value for value in decisions}
    domain_by_subject = {
        (value["section_id"], value["item_id"]): value for value in classifications
    }

    def review(section_id: UUID | None = None, item_id: UUID | None = None) -> dict[str, Any]:
        decision = decision_by_subject.get((section_id, item_id))
        classification = domain_by_subject.get((section_id, item_id))
        revision = max(
            decision["revision"] if decision else 1,
            classification["revision"] if classification else 1,
        )
        current = decision if decision and decision["revision"] == revision else None
        confirmed = current is not None and current["action"] == "confirm"
        return {
            "state": {"confirm": "confirmed", "reject": "rejected", "reopen": "candidate"}.get(
                current["action"], "candidate"
            )
            if current
            else "candidate",
            "review_domain": classification["review_domain"] if classification else None,
            "confirmed_by": current["decided_by"] if confirmed and current else None,
            "confirmed_at": current["decided_at"] if confirmed and current else None,
        }

    sections = []
    keys = (
        "id",
        "key",
        "order",
        "aggregation",
        "score_range",
        "weight",
        "cap",
        "included_in_overall_total",
        "fingerprint",
        "citation_valid",
    )
    for entry in (
        await session.execute(
            select(
                *(getattr(ScoreRubricSection, key) for key in keys),
                (func.length(func.trim(ScoreRubricSection.aggregation_rule_text)) > 0).label(
                    "has_rule"
                ),
                (func.length(func.trim(ScoreRubricSection.ambiguity_reason)) > 0).label(
                    "has_ambiguity"
                ),
            ).where(ScoreRubricSection.rubric_id == row.id)
        )
    ).mappings():
        value = dict(entry)
        value["aggregation_rule_text"] = "recorded" if value.pop("has_rule") else None
        value["ambiguity_reason"] = "recorded" if value.pop("has_ambiguity") else None
        value.update(review(section_id=value["id"]))
        sections.append(value)
    items = []
    keys = (
        "id",
        "key",
        "order",
        "section_id",
        "requirement_id",
        "assessment_mode",
        "score_range",
        "weight",
        "fingerprint",
        "citation_valid",
    )
    for entry in (
        await session.execute(
            select(
                *(getattr(ScoreRubricItem, key) for key in keys),
                (func.length(func.trim(ScoreRubricItem.ambiguity_reason)) > 0).label(
                    "has_ambiguity"
                ),
            ).where(ScoreRubricItem.rubric_id == row.id)
        )
    ).mappings():
        value = dict(entry)
        value["ambiguity_reason"] = "recorded" if value.pop("has_ambiguity") else None
        value.update(review(item_id=value["id"]))
        items.append(value)
    latest = (
        (
            await session.execute(
                select(
                    ScoreRubricCoverageDecision.id,
                    ScoreRubricCoverageDecision.coverage_id,
                    ScoreRubricCoverageDecision.action,
                    ScoreRubricCoverageDecision.canonical_requirement_id,
                    ScoreRubricCoverageDecision.decided_by,
                    ScoreRubricCoverageDecision.decided_at,
                    (func.length(func.trim(ScoreRubricCoverageDecision.reason)) > 0).label(
                        "has_reason"
                    ),
                )
                .where(ScoreRubricCoverageDecision.rubric_id == row.id)
                .distinct(ScoreRubricCoverageDecision.coverage_id)
                .order_by(
                    ScoreRubricCoverageDecision.coverage_id,
                    ScoreRubricCoverageDecision.revision.desc(),
                )
            )
        )
        .mappings()
        .all()
    )
    by_coverage = {value["coverage_id"]: value for value in latest}
    mappings: dict[UUID, list[UUID]] = {}
    for decision_id, item_id in await session.execute(
        select(
            ScoreRubricCoverageItem.coverage_decision_id, ScoreRubricCoverageItem.rubric_item_id
        ).where(ScoreRubricCoverageItem.coverage_decision_id.in_([value["id"] for value in latest]))
    ):
        mappings.setdefault(decision_id, []).append(item_id)
    coverage = []
    for coverage_id, requirement_id in await session.execute(
        select(ScoreRubricCoverage.id, ScoreRubricCoverage.requirement_id).where(
            ScoreRubricCoverage.rubric_id == row.id
        )
    ):
        saved = by_coverage.get(coverage_id)
        value: dict[str, Any] = {
            "requirement_id": requirement_id,
            "disposition": "pending",
            "rubric_item_ids": [],
        }
        if saved and saved["action"] != "reopen":
            value.update(
                disposition=saved["action"],
                canonical_requirement_id=saved["canonical_requirement_id"],
                decided_by=saved["decided_by"],
                decided_at=saved["decided_at"],
                reason="recorded" if saved["has_reason"] else None,
                rubric_item_ids=mappings.get(saved["id"], []),
            )
        coverage.append(value)
    result = score_normalization.completeness(
        {
            "rubric": score.fields(
                row,
                ("overall_aggregation", "overall_rule_text", "overall_score_range", "overall_cap"),
            ),
            "sections": sections,
            "items": items,
            "coverage": coverage,
            "expected_requirement_ids": [entry["requirement_id"] for entry in coverage],
        }
    )
    if row.normalization_errors:
        result = result.model_copy(
            update={
                "normalization_errors": sorted(
                    set(result.normalization_errors + row.normalization_errors)
                ),
                "complete": False,
            }
        )
    return result


def compact_completeness(value: RubricCompletenessView) -> RubricCompletenessSummary:
    return RubricCompletenessSummary(
        scoring_requirement_count=value.scoring_requirement_count,
        covered_requirement_count=value.covered_requirement_count,
        pending_requirements=len(value.pending_requirement_ids),
        duplicate_groups=len(value.unresolved_duplicate_fingerprint_groups),
        unconfirmed_sections=len(value.unconfirmed_section_ids),
        unconfirmed_items=len(value.unconfirmed_item_ids),
        normalization_errors=len(value.normalization_errors),
        section_aggregation_rules_confirmed=value.section_aggregation_rules_confirmed,
        overall_aggregation_rule_confirmed=value.overall_aggregation_rule_confirmed,
        complete=value.complete,
    )


async def summary(
    session: AsyncSession, actor: Identity, task_id: UUID, rubric_id: UUID
) -> RubricSummaryData:
    checks: ActionChecks = {}
    actor, row = await score.get_set(session, actor, task_id, rubric_id)
    state = await score.review_state(session, row)
    complete = await completeness(session, row)
    section_count = await session.scalar(
        select(func.count())
        .select_from(ScoreRubricSection)
        .where(ScoreRubricSection.rubric_id == row.id)
    )
    item_count = await session.scalar(
        select(func.count()).select_from(ScoreRubricItem).where(ScoreRubricItem.rubric_id == row.id)
    )
    active = state["state"] != "superseded"
    confirm = await action(
        session,
        actor,
        task_id,
        "rubric_confirm",
        "score:rubric:review",
        role="bidder",
        domain="commercial",
        human=True,
        checks=checks,
        valid=active and state["state"] == "candidate" and complete.complete,
    )
    reopen = await action(
        session,
        actor,
        task_id,
        "rubric_reopen",
        "score:rubric:review",
        role="bidder",
        domain="commercial",
        human=True,
        checks=checks,
        valid=active and state["state"] == "confirmed",
    )
    revise = await action(
        session,
        actor,
        task_id,
        "rubric_revise",
        "score:rubric:review",
        domain={"bidder": "commercial", "technical": "technical"}.get(actor.role),
        human=True,
        checks=checks,
        valid=active,
    )
    if actor.role not in {"bidder", "technical"}:
        revise = revise.model_copy(update={"allowed": False, "blocker_codes": ["wrong_role"]})
    return RubricSummaryData(
        **score.fields(
            row,
            (
                "id",
                "org_id",
                "task_id",
                "extraction_job_id",
                "document_id",
                "input_hash",
                "prior_rubric_id",
                "version",
                "created_at",
                "overall_aggregation",
                "overall_rule_text",
                "overall_score_range",
                "overall_cap",
            ),
        ),
        revision=state["revision"],
        state=state["state"],
        validity="current" if active else "stale",
        section_count=section_count or 0,
        item_count=item_count or 0,
        completeness=compact_completeness(complete),
        overall_aggregation_assessable=row.overall_aggregation
        in {"sum", "weighted_sum", "capped_sum"},
        actions=[confirm, reopen, revise],
    )


async def subject_view(session: AsyncSession, row: ScoreRubricSet, child: Any, part: str) -> Any:
    if part == "coverage":
        return RubricRequirementCoverageView.model_validate(
            await score.coverage_view(session, child)
        )
    if part == "sections":
        fields = (
            "id",
            "org_id",
            "task_id",
            "rubric_id",
            "key",
            "title",
            "order",
            "aggregation",
            "aggregation_rule_text",
            "score_range",
            "weight",
            "cap",
            "included_in_overall_total",
            "ambiguity_reason",
        )
        return ConsoleRubricSectionView.model_validate(
            {
                **score.fields(child, fields),
                "normalization_errors": sorted(
                    score_normalization.subject_errors("section", score.fields(child, fields))
                ),
                "sources": score.section_sources(child),
                "aggregation_assessable": child.aggregation
                in {"sum", "weighted_sum", "capped_sum"},
                **await score.review_state(session, row, section_id=child.id),
            }
        )
    fields = (
        "id",
        "org_id",
        "task_id",
        "rubric_id",
        "section_id",
        "requirement_id",
        "key",
        "title",
        "rule_text",
        "order",
        "assessment_mode",
        "score_range",
        "weight",
        "ambiguity_reason",
        "source",
        "fingerprint",
    )
    return RubricItemView.model_validate(
        {
            **score.fields(child, fields),
            "normalization_errors": sorted(
                score_normalization.subject_errors("item", score.fields(child, fields))
            ),
            **await score.review_state(session, row, item_id=child.id),
        }
    )


def blockers(value: RubricCompletenessView) -> list[Notice]:
    result = []
    for name, ids in (
        ("pending_requirement", value.pending_requirement_ids),
        ("unconfirmed_section", value.unconfirmed_section_ids),
        ("unconfirmed_item", value.unconfirmed_item_ids),
    ):
        for id_ in ids:
            result.append(
                Notice(
                    code=name,
                    message={
                        "pending_requirement": "该评分要求尚未完成覆盖确认。",
                        "unconfirmed_section": "评分分项尚未由对应职责人员确认。",
                        "unconfirmed_item": "评分细项尚未由对应职责人员确认。",
                    }[name],
                    requirement_id=id_ if name == "pending_requirement" else None,
                    subject_id=id_,
                )
            )
    for members in value.unresolved_duplicate_fingerprint_groups:
        group = digest(sorted(map(str, members)))
        for id_ in members:
            result.append(
                Notice(
                    code="duplicate_item_fingerprint",
                    message="存在重复评分内容，需要确认唯一对应关系。",
                    subject_id=id_,
                    group_id=group,
                    group_member_count=len(members),
                )
            )
    result.extend(
        Notice(code=code, message="评分规则存在待处理的校验问题，请根据错误代码修正。")
        for code in value.normalization_errors
    )
    return sorted(result, key=lambda notice: (notice.code, str(notice.subject_id or "")))


async def page(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    rubric_id: UUID,
    query: RubricPageRequest,
    settings: Settings,
) -> RubricPage:
    checks: ActionChecks = {}
    actor, row = await score.get_set(session, actor, task_id, rubric_id)
    state = await score.review_state(session, row)
    snapshot = digest([row.id, row.input_hash, state["revision"], state["state"]])
    binding = {
        "parent": rubric_id,
        "snapshot": snapshot,
        **query.model_dump(mode="json", exclude={"cursor", "limit"}),
    }
    anchor = read_cursor(settings, actor, binding, query.cursor)
    next_value = None
    actions = []
    values: list[Any] = []
    anchors: list[dict[str, Any]] = []
    if query.part == "blockers":
        requirements = {
            UUID(entry["requirement_id"]) for entry in row.input_manifest["requirements"]
        }
        if query.requirement_id is not None and query.requirement_id not in requirements:
            raise not_found()
        if query.entry_id is not None and query.entry_id not in requirements:
            subject_exists = False
            for model in (ScoreRubricSection, ScoreRubricItem, ScoreRubricCoverage):
                if (
                    await session.scalar(
                        select(model.id).where(
                            model.id == query.entry_id,
                            model.rubric_id == row.id,
                            model.task_id == task_id,
                        )
                    )
                    is not None
                ):
                    subject_exists = True
                    break
            if not subject_exists:
                raise not_found()
        all_notices = blockers(await completeness(session, row))
        filtered_notices = [
            notice
            for notice in all_notices
            if (query.group_id is None or notice.group_id == query.group_id)
            and (query.entry_id is None or notice.subject_id == query.entry_id)
            and (query.requirement_id is None or notice.requirement_id == query.requirement_id)
        ]
        total, filtered = len(all_notices), len(filtered_notices)
        start = int(anchor["position"]) if anchor else 0
        if start < 0 or start > filtered:
            raise ServiceError("invalid_cursor", "Cursor has an invalid blocker position", 400, 2)
        positions = await notice_positions(session, filtered, start, query.limit)
        values = [filtered_notices[position] for position in positions[: query.limit]]
        anchors = [{"position": position + 1} for position in positions[: query.limit]]
        if len(positions) > query.limit:
            next_value = cursor(settings, actor, binding, anchors[-1])
    else:
        model = {
            "sections": ScoreRubricSection,
            "items": ScoreRubricItem,
            "coverage": ScoreRubricCoverage,
        }[query.part]
        conditions: list[Any] = [
            model.org_id == actor.org_id,
            model.rubric_id == rubric_id,
            model.task_id == task_id,
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
                or requirement.task_id != task_id
                or requirement.job_id != row.extraction_job_id
            ):
                raise not_found()
            conditions.append(model.requirement_id == query.requirement_id)
        if query.section_id:
            section = await session.get(ScoreRubricSection, query.section_id)
            if section is None or section.rubric_id != row.id or section.task_id != task_id:
                raise not_found()
            conditions.append(ScoreRubricItem.section_id == query.section_id)
        if query.part in {"sections", "items"}:
            subject = "section_id" if query.part == "sections" else "item_id"
            child_state, domain = review_expression(model, subject)
            if query.state:
                conditions.append(child_state == query.state)
            if query.domain:
                conditions.append(
                    domain.is_(None) if query.domain == "unclassified" else domain == query.domain
                )
        filtered = (
            await session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0
        )
        if query.part == "sections":
            ordering = [ScoreRubricSection.order]
        elif query.part == "items":
            section_order = (
                select(ScoreRubricSection.order)
                .where(ScoreRubricSection.id == ScoreRubricItem.section_id)
                .scalar_subquery()
            )
            ordering = [section_order, ScoreRubricItem.order]
        else:
            positions = {
                UUID(entry["requirement_id"]): index
                for index, entry in enumerate(row.input_manifest["requirements"])
            }
            ordering = [case(positions, value=ScoreRubricCoverage.requirement_id, else_=2147483647)]
        statement = select(model.id, *ordering).where(*conditions)
        if anchor:
            order, id_ = order_values(anchor, len(ordering))
            statement = statement.where(
                tuple_(*ordering, model.id)
                > tuple_(*(literal(value) for value in order), literal(id_))
            )
        selected = (
            await session.execute(statement.order_by(*ordering, model.id).limit(query.limit + 1))
        ).all()
        ids = [entry[0] for entry in selected[: query.limit]]
        anchors = [
            {"id": str(entry[0]), "order": list(entry[1:])} for entry in selected[: query.limit]
        ]
        if len(selected) > query.limit:
            next_value = cursor(settings, actor, binding, anchors[-1])
        children = {
            child.id: child
            for child in (await session.scalars(select(model).where(model.id.in_(ids)))).all()
        }
        values = [await subject_view(session, row, children[id_], query.part) for id_ in ids]
        for value in values:
            if query.part == "coverage":
                availability = [
                    await action(
                        session,
                        actor,
                        task_id,
                        "rubric_coverage_decide",
                        "score:rubric:review",
                        role="bidder",
                        domain="commercial",
                        human=True,
                        checks=checks,
                        valid=state["state"] == "candidate",
                    )
                ]
            else:
                domain = value.review_domain
                role = {"commercial": "bidder", "technical": "technical"}.get(domain)
                decision = await action(
                    session,
                    actor,
                    task_id,
                    "rubric_section_decide" if query.part == "sections" else "rubric_item_decide",
                    "score:rubric:review",
                    role=role,
                    domain=domain,
                    human=True,
                    checks=checks,
                    valid=state["state"] == "candidate",
                )
                if role is None:
                    decision = decision.model_copy(
                        update={"allowed": False, "blocker_codes": ["unclassified"]}
                    )
                availability = [
                    await action(
                        session,
                        actor,
                        task_id,
                        "rubric_classify",
                        "score:rubric:review",
                        role="admin",
                        human=True,
                        checks=checks,
                        valid=state["state"] == "candidate",
                    ),
                    decision,
                ]
            actions.append(SubjectActions(subject_id=value.id, actions=availability))
    projected = ProjectionPage[
        ConsoleRubricSectionView | RubricItemView | RubricRequirementCoverageView | Notice
    ](
        data=PageData(
            task_id=task_id,
            parent_id=rubric_id,
            part=query.part,
            snapshot=snapshot_token(settings, actor, {"parent": rubric_id, "snapshot": snapshot}),
            total=total,
            filtered_total=filtered,
            returned=len(values),
            next_cursor=next_value,
            validity="stale" if state["state"] == "superseded" else "current",
            parent_revision=state["revision"],
            subject_actions=actions,
        ),
        items=values,
    )

    job = await session.get(Job, row.job_id)
    partial = job is not None and job.result.get("completion") == "partial"
    data, items = fit_items(
        "score rubric show",
        projected.data,
        projected.items,
        lambda index: cursor(settings, actor, binding, anchors[index]),
        ok=not partial,
    )
    return projected.model_copy(update={"data": data, "items": items})


async def replacement(
    session: AsyncSession, actor: Identity, task_id: UUID, rubric_id: UUID
) -> RubricReplacementData:
    _, row = await score.get_set(session, actor, task_id, rubric_id)
    event = await session.scalar(
        select(ScoreRubricRevisionEvent).where(
            ScoreRubricRevisionEvent.rubric_id == row.id,
            ScoreRubricRevisionEvent.task_id == task_id,
        )
    )
    if event is None:
        raise not_found()
    actual = await session.scalar(
        text(
            "SELECT encode(sha256(convert_to(replacement_snapshot::text, 'UTF8')), 'hex') FROM score_rubric_revision_events WHERE org_id=:org AND task_id=:task AND rubric_id=:rubric"
        ),
        {"org": actor.org_id, "task": task_id, "rubric": rubric_id},
    )
    if actual != event.snapshot_sha256:
        raise ServiceError(
            "assessment_input_integrity", "Saved replacement snapshot checksum changed", 409, 4
        )
    return RubricReplacementData(
        rubric_id=row.id,
        prior_rubric_id=event.prior_rubric_id,
        snapshot_sha256=event.snapshot_sha256,
        replacement=RubricReviseRequest.model_validate(event.replacement_snapshot),
    )


@task_authorized("score:read")
async def history(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    query: AssessmentHistoryQuery,
    settings: Settings,
) -> tuple[AssessmentListData, list[RubricSummaryData]]:
    actor = await score.access(session, actor)
    if await session.get(Task, task_id) is None:
        raise not_found()
    conditions: list[Any] = [
        ScoreRubricSet.task_id == task_id,
        ScoreRubricSet.org_id == actor.org_id,
    ]
    if query.extraction_job_id:
        extraction = await session.get(Job, query.extraction_job_id)
        if extraction is None or extraction.task_id != task_id or extraction.kind != "extract":
            raise not_found()
        conditions.append(ScoreRubricSet.extraction_job_id == query.extraction_job_id)
    signatures = []
    async for row in await session.stream_scalars(
        select(ScoreRubricSet)
        .where(*conditions)
        .order_by(ScoreRubricSet.id)
        .execution_options(yield_per=50)
    ):
        await score.require_dependencies(session, actor, row)
        signatures.append((str(row.id), await score.set_revision(session, row)))
    binding = {
        "task": task_id,
        "extraction": query.extraction_job_id,
        "snapshot": digest(signatures),
    }
    anchor = dated_anchor(read_cursor(settings, actor, binding, query.cursor))
    statement = select(ScoreRubricSet.id, ScoreRubricSet.created_at).where(*conditions)
    if anchor:
        statement = statement.where(
            tuple_(ScoreRubricSet.created_at, ScoreRubricSet.id)
            > tuple_(literal(anchor[0]), literal(anchor[1]))
        )
    rows = (
        await session.execute(
            statement.order_by(ScoreRubricSet.created_at, ScoreRubricSet.id).limit(query.limit + 1)
        )
    ).all()
    items = [await summary(session, actor, task_id, row.id) for row in rows[: query.limit]]
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
    outcomes = await session.scalars(
        select(Job.result)
        .join(ScoreRubricSet, ScoreRubricSet.job_id == Job.id)
        .where(ScoreRubricSet.id.in_([value.id for value in items]))
    )
    partial = any(outcome.get("completion") == "partial" for outcome in outcomes)
    data, items = fit_items(
        "score rubric list",
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
