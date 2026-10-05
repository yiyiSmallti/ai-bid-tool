"""PostgreSQL semantic-publication gates on existing two-tenant fixtures.

Failure inventory before implementation: forged complete coverage, inconsistent public
status/outcome/reason, risk without findings, unknown with findings, unsupported
no-risk/contradiction, dual or absent citation target, cross-report/tenant/task target,
changed source locations, ambiguous/nonexact tender spans, and late/immutable rows.
Existing test_check_storage retains attempt, parent, RLS and deterministic gates.
"""

from typing import Any
from uuid import UUID, uuid4

import pytest
from app.models.check import CheckFinding, CheckFindingCitation, CheckItem
from app.models.response_cards import ResponseItem
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from test_check_storage import (
    TABLES,
    pending_report,
)
from test_check_storage import (
    check_db as check_db,  # noqa: F401
)
from test_check_storage import (
    check_seeded as check_seeded,  # noqa: F401
)
from test_rls import seeded as seeded  # noqa: F401


async def semantic_report(
    session,
    data,
    *,
    outcome="no_risk_found",
    status="assessed",
    reason=None,
    mode="combined",
    support=("tender", "draft"),
    finding=False,
    citation_change=None,
    hide_unassessed=False,
    manifest_change=None,
):
    """Clone the real confirmed snapshot, retaining all deterministic dependencies."""
    from app.models import Base

    report, job = await pending_report(session, data)
    report.mode = mode
    report.prompt_version = "check-semantic-test" if mode == "combined" else None
    report.input_manifest = {
        **report.input_manifest,
        "mode": mode,
        "prompt_version": report.prompt_version,
    }
    if manifest_change:
        report.input_manifest = {**report.input_manifest, **manifest_change}
    job.result = {
        "submission": {**job.result["submission"], "input_manifest": report.input_manifest}
    }
    # pending_report already inserted the rules submission. With no ORM relationship,
    # one flush can INSERT CheckRun before UPDATE Job; its trigger must see the fixed
    # combined submission first, just as a real worker sees a previously submitted job.
    await session.flush([job])
    report.summary = {
        "item_count": 1,
        "finding_count": 1 + int(finding),
        "unassessed_count": int(status == "unassessed"),
    }
    report.completion = "partial" if status == "unassessed" else "complete"
    if hide_unassessed:
        report.summary = {**report.summary, "unassessed_count": 0}
        report.completion = "complete"
    await session.flush()
    remapped = {}
    item = None
    original_id = data["checks"][data["orgs"][0]]["run"]
    for name in TABLES[1:-1]:
        table = Base.metadata.tables[name]
        old = dict(
            (await session.execute(select(table).where(table.c.report_id == original_id)))
            .mappings()
            .one()
        )
        new_id = uuid4()
        remapped[old["id"]] = new_id
        values = {
            key: remapped.get(value, value)
            if key in {"check_item_id", "certificate_id", "finding_id"}
            else value
            for key, value in old.items()
        }
        values.update(id=new_id, report_id=report.id)
        if name == "check_items":
            values.update(
                semantic_status=status, semantic_outcome=outcome, semantic_reason_code=reason
            )
        await session.execute(table.insert().values(values))
        if name == "check_items":
            item = await session.get(CheckItem, new_id)
    assert item is not None
    target = {"check_item_id": item.id}
    if finding:
        semantic = CheckFinding(
            id=uuid4(),
            org_id=report.org_id,
            task_id=report.task_id,
            report_id=report.id,
            check_item_id=item.id,
            requirement_id=item.requirement_id,
            method="semantic",
            code="semantic_contradiction",
            severity="deduction_risk",
            review_domain="technical",
            reason="Synthetic supported contradiction",
            source=item.source,
        )
        session.add(semantic)
        await session.flush()
        target = {"finding_id": semantic.id}
    response = await session.get(ResponseItem, item.response_item_id)
    for kind in support:
        values: dict[str, Any] = dict(
            org_id=report.org_id, task_id=report.task_id, report_id=report.id, kind=kind, **target
        )
        if kind == "tender":
            # Semantic spans need not repeat the complete requirement quote.
            quote = item.source["quote"][:-1]
            values.update(
                quote=quote,
                source={**item.source, "quote": quote},
                document_id=UUID(item.source["document_id"]),
                chunk_id=UUID(item.source["chunk_id"]),
            )
        else:
            values.update(
                quote=response.response_text,
                draft_id=report.draft_id,
                response_item_id=response.id,
                card_revision_id=response.card_revision_id,
                field="response_text",
            )
        if citation_change:
            citation_change(values, item, remapped)
        session.add(CheckFindingCitation(**values))
    await session.flush()
    return report, item


@pytest.mark.parametrize("outcome,finding", [("no_risk_found", False), ("risk", True)])
async def test_semantic_supported_result_persists(check_seeded, check_db, outcome, finding):
    org = check_seeded["orgs"][0]
    async with check_db.transaction(org) as session:
        report, item = await semantic_report(
            session, check_seeded, outcome=outcome, finding=finding
        )
        report_id, item_id = report.id, item.id
    async with check_db.transaction(org) as session:
        stored = await session.get(CheckItem, item_id)
        assert stored.semantic_outcome == outcome
        citations = (
            await session.scalars(
                select(CheckFindingCitation).where(
                    CheckFindingCitation.report_id == report_id,
                    CheckFindingCitation.check_item_id == item_id,
                )
            )
        ).all()
        assert len(citations) == (0 if finding else 2)
    async with check_db.transaction() as session:
        assert not (
            await session.scalars(
                select(CheckFindingCitation).where(
                    CheckFindingCitation.report_id == report_id,
                )
            )
        ).all()
    async with check_db.transaction(check_seeded["orgs"][1]) as session:
        assert await session.get(CheckItem, item_id) is None
        assert not (
            await session.scalars(
                select(CheckFindingCitation).where(
                    CheckFindingCitation.report_id == report_id,
                )
            )
        ).all()


@pytest.mark.parametrize(
    "outcome,reason", [("unknown", "semantic_unknown"), (None, "quote_not_at_position")]
)
async def test_semantic_unknown_and_rejection_are_partial(check_seeded, check_db, outcome, reason):
    async with check_db.transaction(check_seeded["orgs"][0]) as session:
        report, _ = await semantic_report(
            session, check_seeded, outcome=outcome, status="unassessed", reason=reason, support=()
        )
        assert report.completion == "partial"


@pytest.mark.parametrize(
    "options",
    [
        {"support": ()},
        {"support": ("tender",)},
        {"support": ("draft",)},
        {"outcome": "risk", "support": ()},
        {"outcome": "risk", "finding": True, "support": ("tender",)},
        {
            "outcome": "unknown",
            "status": "unassessed",
            "reason": "semantic_unknown",
            "finding": True,
        },
        {"outcome": "unknown", "status": "assessed", "support": ()},
        {
            "outcome": "no_risk_found",
            "status": "unassessed",
            "reason": "semantic_unknown",
            "support": (),
        },
        {
            "outcome": None,
            "status": "unassessed",
            "reason": "arbitrary provider text",
            "support": (),
        },
        {"outcome": "unknown", "status": "unassessed", "reason": None, "support": ()},
        {"mode": "rules"},
        {"outcome": None, "status": "not_requested", "support": ()},
    ],
)
async def test_semantic_inconsistent_publication_rejected(check_seeded, check_db, options):
    with pytest.raises(DBAPIError) as error:
        async with check_db.transaction(check_seeded["orgs"][0]) as session:
            await semantic_report(session, check_seeded, **options)
    assert error.value.orig.sqlstate == "23514"
    assert "Invalid check publication attempt or input" not in str(error.value.orig)


@pytest.mark.parametrize(
    "mutation",
    ["both", "neither", "foreign_item", "other_report", "other_task", "wrong_location", "invented"],
)
async def test_semantic_citation_target_and_source_gates(check_seeded, check_db, mutation):
    def change(values, item, remapped):
        if mutation == "both":
            values["finding_id"] = remapped[check_seeded["checks"][item.org_id]["finding"]]
        elif mutation == "neither":
            values["check_item_id"] = None
        elif mutation == "foreign_item":
            values["check_item_id"] = check_seeded["checks"][check_seeded["orgs"][1]]["item"]
        elif mutation == "other_report":
            values["check_item_id"] = check_seeded["checks"][item.org_id]["item"]
        elif mutation == "other_task":
            values["task_id"] = check_seeded["ids"][check_seeded["orgs"][1]]["task"]
        elif mutation == "wrong_location" and values["kind"] == "tender":
            values["source"] = {**values["source"], "page": 900}
        elif mutation == "invented":
            values["quote"] = "invented semantic quote"

    with pytest.raises(DBAPIError) as error:
        async with check_db.transaction(check_seeded["orgs"][0]) as session:
            await semantic_report(session, check_seeded, citation_change=change)
    assert error.value.orig.sqlstate in {"23514", "23503"}
    assert "Invalid check publication attempt or input" not in str(error.value.orig)


async def test_semantic_publication_stays_immutable(check_seeded, check_db):
    with pytest.raises(DBAPIError) as error:
        async with check_db.transaction(check_seeded["orgs"][0]) as session:
            report, _ = await semantic_report(
                session,
                check_seeded,
                outcome="unknown",
                status="unassessed",
                reason="semantic_unknown",
                support=(),
            )
            # The immutable report cannot be repaired after insertion either.
            await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            report.completion = "complete"
    assert error.value.orig.sqlstate == "42501"


async def test_semantic_summary_cannot_hide_unknown(check_seeded, check_db):
    with pytest.raises(DBAPIError) as error:
        async with check_db.transaction(check_seeded["orgs"][0]) as session:
            await semantic_report(
                session,
                check_seeded,
                outcome="unknown",
                status="unassessed",
                reason="semantic_unknown",
                support=(),
                hide_unassessed=True,
            )
    assert error.value.orig.sqlstate == "23514"
    assert "Check summary must match published coverage" in str(error.value.orig)


@pytest.mark.parametrize(
    "manifest_change",
    [
        {"prompt_version": "different-prompt"},
        {"model_redaction_enabled": False},
        {"model_redaction_enabled": None},
    ],
)
async def test_semantic_prompt_and_redaction_are_fixed(check_seeded, check_db, manifest_change):
    with pytest.raises(DBAPIError) as error:
        async with check_db.transaction(check_seeded["orgs"][0]) as session:
            await semantic_report(session, check_seeded, manifest_change=manifest_change)
    assert error.value.orig.sqlstate == "23514"
