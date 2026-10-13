"""Runtime-role PostgreSQL gate tests (synthetic inputs, no vendor calls).

Failure inventory before implementation: absent/forged actor context; token/agent/
worker human decisions; direct confirmed INSERT; skipped
review transitions; stale revision/pointer; incomplete confirmed evidence and linked
commitments; forged dispositions/classification/redaction; cross-task/job selection,
revision or PDF binding; mutable history; late links/items; missing/duplicate draft
coverage; material replaced before consumption; worker provenance spoofing and workers submitting a human review state.
Service/API tests cover the same decisions through authenticated routes.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.core.db import Database
from app.models.entities import Job, Product, ProductRevision, TaskResource
from app.models.response_cards import (
    CardEvidenceLink,
    CardGenerationRun,
    DraftRun,
    Evidence,
    ResponseCard,
    ResponseCardRevision,
    ResponseItem,
)
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

RESPONSE_TABLES = (
    "response_cards",
    "response_card_revisions",
    "evidence",
    "card_evidence_links",
    "card_generation_runs",
    "draft_runs",
    "response_items",
)


def seed_response_rows(session, task, extraction, requirement):
    """Seed genuine relational materials under actor context, never bypass triggers."""
    from task_fixtures import actor_context, confirm_requirements, requirement_review_manifest

    confirm_requirements(session, task.id, [requirement.id])
    actor_context(session)
    card_id, revision_id = uuid4(), uuid4()
    card = ResponseCard(
        id=card_id,
        task_id=task.id,
        extraction_job_id=extraction.id,
        requirement_id=requirement.id,
        current_revision_id=revision_id,
        revision=1,
    )
    session.add(card)
    session.flush()
    revision = ResponseCardRevision(
        id=revision_id,
        card_id=card_id,
        revision=1,
        state="draft",
        review_domain="technical",
        response_kind="evidence",
        response_text="Synthetic response",
        deviation="negative",
        deviation_note="Synthetic difference",
        origin="human",
        actor_kind="session",
        reviewed_warning_codes=[],
    )
    session.add(revision)
    product = Product(id=uuid4(), current_revision=1)
    session.add(product)
    session.flush()
    product_revision = ProductRevision(
        id=uuid4(),
        product_id=product.id,
        revision=1,
        data={"name": "Synthetic evidence declaration"},
    )
    session.add(product_revision)
    session.flush()
    selection = TaskResource(
        id=uuid4(), task_id=task.id, product_id=product.id, product_revision_id=product_revision.id
    )
    session.add(selection)
    session.flush()
    evidence = Evidence(
        id=uuid4(),
        task_id=task.id,
        card_id=card.id,
        kind="product",
        task_resource_id=selection.id,
        product_revision_id=product_revision.id,
        field_path="name",
        quote="Synthetic evidence declaration",
        material_kind="declaration",
        quote_check="exact_field_match",
    )
    session.add(evidence)
    session.flush()
    session.add(
        CardEvidenceLink(
            id=uuid4(), card_id=card.id, revision_id=revision.id, evidence_id=evidence.id
        )
    )
    draft_job = Job(
        id=uuid4(),
        task_id=task.id,
        document_id=extraction.document_id,
        kind="draft",
        cache_key=uuid4().hex * 2,
        status="running",
        run_id=uuid4(),
    )
    model_job = Job(
        id=uuid4(),
        task_id=task.id,
        document_id=extraction.document_id,
        kind="card_generate",
        cache_key=uuid4().hex * 2,
        status="running",
        run_id=uuid4(),
    )
    session.add_all([draft_job, model_job])
    session.flush()
    manifest = requirement_review_manifest(session, task.id, extraction.id)
    run = DraftRun(
        id=uuid4(),
        task_id=task.id,
        extraction_job_id=extraction.id,
        generation_job_id=draft_job.id,
        generation_run_id=draft_job.run_id,
        input_hash=uuid4().hex * 2,
        actor_kind="session",
        input_manifest=manifest,
        completion="partial",
        summary={"gap_requirements": 1},
    )
    session.add(run)
    session.add(
        CardGenerationRun(
            id=uuid4(),
            task_id=task.id,
            extraction_job_id=extraction.id,
            generation_job_id=model_job.id,
            generation_run_id=model_job.run_id,
            input_hash=uuid4().hex * 2,
            actor_kind="session",
            input_manifest={},
            target_revisions={str(card.id): 1},
            model_redaction_enabled=True,
            model_redaction_revision=1,
            redaction_rule_version="test-v1",
            prompt_version="test-v1",
            schema_version="test-v1",
            adapter_version="test-v1",
            result={},
        )
    )
    session.flush()
    item = ResponseItem(
        id=uuid4(),
        draft_id=run.id,
        requirement_id=requirement.id,
        category=requirement.category,
        starred=requirement.starred,
        card_id=card.id,
        card_revision_id=revision.id,
        kind="gap",
        source={
            "document_id": str(requirement.document_id),
            "chunk_id": str(requirement.chunk_id),
            "location": requirement.location,
            "quote": requirement.quote,
            "page": requirement.page,
        },
        location_label="Synthetic fixture · page 1",
        gap_reasons=["unconfirmed"],
    )
    session.add(item)
    session.flush()
    # Check complete relational setup before returning its identifiers.
    session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    return {
        "card": card.id,
        "revision": revision.id,
        "evidence": evidence.id,
        "selection": selection.id,
        "draft": run.id,
        "item": item.id,
    }


@pytest.fixture
async def gate_db(seeded):
    db = Database(Settings())
    yield db
    await db.engine.dispose()


@pytest.fixture
def seeded(database, admin_engine):
    from app.models.entities import Chunk, Document, Requirement, Task
    from task_fixtures import actor_context, seed_task

    with Session(admin_engine) as session, session.begin():
        actor_context(session)
        task = Task(id=uuid4(), name="Synthetic SQL response task")
        session.add(task)
        session.flush()
        seed_task(session, task)
        document = Document(
            id=uuid4(),
            task_id=task.id,
            name="synthetic.pdf",
            sha256=uuid4().hex * 2,
            storage_key="synthetic/sql.pdf",
            media_type="application/pdf",
            page_count=1,
            status="parsed",
            citation_mode="page",
        )
        session.add(document)
        session.flush()
        chunk = Chunk(
            id=uuid4(),
            task_id=task.id,
            document_id=document.id,
            page=1,
            text="Synthetic requirement clause.",
            citation_verified=True,
        )
        session.add(chunk)
        parse = Job(
            id=uuid4(),
            task_id=task.id,
            document_id=document.id,
            kind="parse",
            cache_key=uuid4().hex * 2,
            status="succeeded",
        )
        extraction = Job(
            id=uuid4(),
            task_id=task.id,
            document_id=document.id,
            kind="extract",
            cache_key=uuid4().hex * 2,
            status="succeeded",
        )
        session.add_all([parse, extraction])
        session.flush()
        requirement = Requirement(
            id=uuid4(),
            task_id=task.id,
            document_id=document.id,
            chunk_id=chunk.id,
            page=1,
            quote=chunk.text,
            text=chunk.text,
            category="technical",
            starred=False,
            condition={},
            fingerprint=uuid4().hex * 2,
            job_id=extraction.id,
        )
        session.add(requirement)
        session.flush()
        ids = seed_response_rows(session, task, extraction, requirement)
        ids.update(
            task=task.id,
            document=document.id,
            chunk=chunk.id,
            requirement=requirement.id,
            extraction=extraction.id,
            parse=parse.id,
        )
    return {"ids": ids}


async def context(session, seeded, kind="session", token=None):
    from task_fixtures import actor_context_async

    await actor_context_async(session, kind=kind, token=token)


async def append_revision(session, seeded, link_evidence=True, **changes):

    card_id = seeded["ids"]["card"]
    card = await session.get(ResponseCard, card_id)
    previous = await session.get(ResponseCardRevision, card.current_revision_id)
    if (
        changes.get("state") == "pending_review"
        and changes.get("actor_kind", "session") == "session"
    ):
        from task_fixtures import review_card_async

        edits = {key: value for key, value in changes.items() if key != "state"}
        if edits or not link_evidence:
            await append_revision(session, seeded, link_evidence=link_evidence, **edits)
        revision = await review_card_async(session, card_id, action="submit")
        return {
            column.name: getattr(revision, column.name)
            for column in ResponseCardRevision.__table__.columns
        }
    payload = {
        column.name: getattr(previous, column.name)
        for column in ResponseCardRevision.__table__.columns
    }
    payload.update(id=uuid4(), revision=previous.revision + 1, created_at=datetime.now(UTC))
    payload.update(changes)
    session.add(ResponseCardRevision(**payload))
    await session.flush()
    card.current_revision_id = payload["id"]
    card.revision = payload["revision"]
    for link in (
        await session.scalars(
            select(CardEvidenceLink).where(CardEvidenceLink.revision_id == previous.id)
        )
    ).all():
        if not link_evidence:
            continue
        session.add(
            CardEvidenceLink(
                card_id=card_id, revision_id=payload["id"], evidence_id=link.evidence_id
            )
        )
    return payload


@pytest.mark.parametrize("kind", [None, "token", "agent", "worker"])
@pytest.mark.parametrize("decision", ["disposition", "confirmation", "redaction"])
async def test_nonhuman_or_missing_actor_cannot_make_decisions(gate_db, seeded, kind, decision):

    if decision == "confirmation":
        async with gate_db.transaction() as session:
            await context(session, seeded)
            await append_revision(session, seeded, state="pending_review")
    with pytest.raises(DBAPIError) as error:
        async with gate_db.transaction() as session:
            if kind is not None:
                await context(session, seeded, kind)
            if decision == "redaction":
                await session.execute(
                    text(
                        "UPDATE tasks SET model_redaction_enabled=false, model_redaction_revision=2 WHERE id=:task"
                    ),
                    {"task": seeded["ids"]["task"]},
                )
            elif decision == "disposition":
                await append_revision(
                    session,
                    seeded,
                    disposition="comply_only",
                    disposition_at=datetime.now(UTC),
                    actor_kind=kind or "session",
                    reason="Synthetic decision",
                )
            else:
                await append_revision(
                    session,
                    seeded,
                    state="confirmed",
                    disposition="respond",
                    disposition_at=datetime.now(UTC),
                    confirmed_at=datetime.now(UTC),
                    actor_kind=kind or "session",
                )
    assert error.value.orig.sqlstate == "42501"


@pytest.mark.parametrize("table", RESPONSE_TABLES)
async def test_response_history_cannot_be_deleted(gate_db, seeded, table):
    with pytest.raises(DBAPIError) as error:
        async with gate_db.transaction() as session:
            await session.execute(text(f'DELETE FROM "{table}"'))
    assert error.value.orig.sqlstate == "42501"


@pytest.mark.parametrize("target", ["confirmed", "rejected", "needs_material"])
async def test_draft_cannot_skip_pending_review(gate_db, confirmed_inputs, target):
    # A real human context lets this case exercise the later transition gate.
    seeded = confirmed_inputs
    with pytest.raises(DBAPIError) as error:
        async with gate_db.transaction() as session:
            await context(session, seeded)
            await append_revision(session, seeded, state=target)
    assert error.value.orig.sqlstate == "23514"


async def test_old_revision_cannot_gain_an_evidence_link(gate_db, seeded):

    with pytest.raises(DBAPIError) as error:
        async with gate_db.transaction() as session:
            row = seeded["ids"]
            session.add(
                CardEvidenceLink(
                    card_id=row["card"], revision_id=row["revision"], evidence_id=row["evidence"]
                )
            )
    assert error.value.orig.sqlstate == "42501"


async def test_card_pointer_cannot_skip_a_revision(gate_db, seeded):

    with pytest.raises(DBAPIError) as error:
        async with gate_db.transaction() as session:
            await session.execute(
                text("UPDATE response_cards SET revision=revision+2 WHERE id=:card"),
                {"card": seeded["ids"]["card"]},
            )
    assert error.value.orig.sqlstate == "23514"


async def test_evidence_content_cannot_be_overwritten(gate_db, seeded):
    with pytest.raises(DBAPIError) as error:
        async with gate_db.transaction() as session:
            await session.execute(text("UPDATE evidence SET quote='forged response'"))
    assert error.value.orig.sqlstate == "42501"


async def test_commitment_cannot_keep_evidence_links(gate_db, seeded):
    with pytest.raises(DBAPIError) as error:
        async with gate_db.transaction() as session:
            await context(session, seeded)
            await append_revision(session, seeded, response_kind="commitment")
    assert error.value.orig.sqlstate == "23514"


async def test_orphan_revision_cannot_leave_current_pointer_behind(gate_db, seeded):

    with pytest.raises(DBAPIError) as error:
        async with gate_db.transaction() as session:
            await context(session, seeded)
            previous = await session.get(ResponseCardRevision, seeded["ids"]["revision"])
            payload = {
                c.name: getattr(previous, c.name) for c in ResponseCardRevision.__table__.columns
            }
            payload.update(id=uuid4(), revision=2)
            session.add(ResponseCardRevision(**payload))
    assert error.value.orig.sqlstate == "23514"


@pytest.fixture
def confirmed_inputs(seeded, admin_engine):
    from task_fixtures import confirm_requirements

    with Session(admin_engine) as session, session.begin():
        confirm_requirements(session, seeded["ids"]["task"])

    return seeded


@pytest.mark.parametrize("state", ["confirmed", "rejected", "needs_material"])
async def test_human_cannot_decide_without_pending_review(gate_db, confirmed_inputs, state):
    with pytest.raises(DBAPIError) as error:
        async with gate_db.transaction() as session:
            await context(session, confirmed_inputs)
            await append_revision(session, confirmed_inputs, state=state, reason="Synthetic review")
    assert error.value.orig.sqlstate == "23514"


@pytest.mark.parametrize("with_links", [True, False])
async def test_confirmation_requires_nonempty_confirmed_evidence(
    gate_db, confirmed_inputs, with_links
):
    seeded = confirmed_inputs

    async with gate_db.transaction() as session:
        await context(session, seeded)
        await append_revision(session, seeded, state="pending_review", link_evidence=with_links)
    with pytest.raises(DBAPIError) as error:
        async with gate_db.transaction() as session:
            await context(session, seeded)
            await append_revision(
                session,
                seeded,
                state="confirmed",
                disposition="respond",
                disposition_at=datetime.now(UTC),
                confirmed_at=datetime.now(UTC),
                link_evidence=with_links,
            )
    assert error.value.orig.sqlstate == "23514"


async def test_commitment_confirmation_needs_no_evidence(gate_db, confirmed_inputs):
    seeded = confirmed_inputs

    async with gate_db.transaction() as session:
        await context(session, seeded)
        await append_revision(session, seeded, response_kind="commitment", link_evidence=False)
    async with gate_db.transaction() as session:
        await context(session, seeded)
        await append_revision(session, seeded, state="pending_review", link_evidence=False)
    async with gate_db.transaction() as session:
        await context(session, seeded)
        from task_fixtures import review_card_async

        await review_card_async(session, seeded["ids"]["card"], action="confirm")
    async with gate_db.transaction() as session:
        card = await session.get(ResponseCard, seeded["ids"]["card"])
        revision = await session.get(ResponseCardRevision, card.current_revision_id)
        assert revision.state == "confirmed" and revision.deviation == "negative"
        assert (
            await session.scalars(
                select(CardEvidenceLink).where(CardEvidenceLink.revision_id == revision.id)
            )
        ).all() == []


async def test_evidence_confirmation_requires_pending_review(gate_db, confirmed_inputs):
    with pytest.raises(DBAPIError) as error:
        async with gate_db.transaction() as session:
            await context(session, confirmed_inputs)
            await session.execute(
                text("UPDATE evidence SET confirmed_at=now()"),
                {},
            )
    assert error.value.orig.sqlstate == "23514"


async def test_evidence_confirmation_cannot_commit_without_card_confirmation(
    gate_db, confirmed_inputs
):
    seeded = confirmed_inputs

    async with gate_db.transaction() as session:
        await context(session, seeded)
        await append_revision(session, seeded, state="pending_review")
    with pytest.raises(DBAPIError) as error:
        async with gate_db.transaction() as session:
            await context(session, seeded)
            await session.execute(
                text("UPDATE evidence SET confirmed_at=now()"),
                {},
            )
    assert error.value.orig.sqlstate == "23514"


async def test_selection_replaced_before_confirmation_is_rejected(
    gate_db, confirmed_inputs, admin_engine
):
    seeded = confirmed_inputs

    async with gate_db.transaction() as session:
        await context(session, seeded)
        await append_revision(session, seeded, state="pending_review")
    with admin_engine.begin() as connection:
        connection.execute(
            text("UPDATE task_resources SET active=false WHERE id=:id"),
            {"id": seeded["ids"]["selection"]},
        )
    with pytest.raises(DBAPIError) as error:
        async with gate_db.transaction() as session:
            await context(session, seeded)
            await session.execute(
                text("UPDATE evidence SET confirmed_at=now()"),
                {},
            )
    assert error.value.orig.sqlstate == "23514"


async def test_redaction_admin_change_is_versioned(gate_db, seeded):

    async with gate_db.transaction() as session:
        await context(session, seeded)
        await session.execute(
            text(
                "UPDATE tasks SET model_redaction_enabled=false, model_redaction_revision=2 WHERE id=:task"
            ),
            {"task": seeded["ids"]["task"]},
        )
    with pytest.raises(DBAPIError) as error:
        async with gate_db.transaction() as session:
            await context(session, seeded)
            await session.execute(
                text("UPDATE tasks SET model_redaction_enabled=true WHERE id=:task"),
                {"task": seeded["ids"]["task"]},
            )
    assert error.value.orig.sqlstate == "23514"


async def test_draft_cannot_publish_without_full_requirement_coverage(gate_db, seeded):

    with pytest.raises(DBAPIError) as error:
        async with gate_db.transaction() as session:
            await context(session, seeded)
            previous = await session.get(DraftRun, seeded["ids"]["draft"])
            job = await session.get(Job, previous.generation_job_id)
            new_job = Job(
                task_id=job.task_id,
                document_id=job.document_id,
                kind="draft",
                cache_key=uuid4().hex * 2,
                status="running",
                run_id=uuid4(),
            )
            session.add(new_job)
            await session.flush()
            session.add(
                DraftRun(
                    task_id=previous.task_id,
                    extraction_job_id=previous.extraction_job_id,
                    generation_job_id=new_job.id,
                    generation_run_id=new_job.run_id,
                    input_hash=uuid4().hex * 2,
                    actor_kind="session",
                    input_manifest=previous.input_manifest,
                    completion="complete",
                    summary={},
                )
            )
    assert error.value.orig.sqlstate == "23514"


async def test_old_draft_cannot_gain_items(gate_db, seeded):

    with pytest.raises(DBAPIError) as error:
        async with gate_db.transaction() as session:
            item = await session.get(ResponseItem, seeded["ids"]["item"])
            payload = {c.name: getattr(item, c.name) for c in ResponseItem.__table__.columns}
            payload["id"] = uuid4()
            session.add(ResponseItem(**payload))
    assert error.value.orig.sqlstate == "42501"


async def test_evidence_and_card_confirm_in_one_human_transaction(gate_db, confirmed_inputs):
    seeded = confirmed_inputs

    async with gate_db.transaction() as session:
        await context(session, seeded)
        await append_revision(session, seeded, state="pending_review")
    async with gate_db.transaction() as session:
        await context(session, seeded)
        from task_fixtures import review_card_async

        await review_card_async(session, seeded["ids"]["card"], action="confirm")
    async with gate_db.transaction() as session:
        card = await session.get(ResponseCard, seeded["ids"]["card"])
        revision = await session.get(ResponseCardRevision, card.current_revision_id)
        evidence = await session.get(Evidence, seeded["ids"]["evidence"])
        assert revision.state == "confirmed"
        assert evidence.confirmed_at == revision.confirmed_at and evidence.confirmed_at is not None


async def test_pending_review_cannot_receive_separate_disposition(gate_db, confirmed_inputs):
    seeded = confirmed_inputs

    async with gate_db.transaction() as session:
        await context(session, seeded)
        await append_revision(session, seeded, state="pending_review")
    with pytest.raises(DBAPIError) as error:
        async with gate_db.transaction() as session:
            await context(session, seeded)
            await append_revision(
                session,
                seeded,
                state="draft",
                disposition="comply_only",
                disposition_at=datetime.now(UTC),
                reason="Synthetic decision",
            )
    assert error.value.orig.sqlstate == "23514"


@pytest.mark.parametrize("terminal", ["rejected", "needs_material"])
async def test_disposition_preserves_review_state(gate_db, confirmed_inputs, terminal):
    seeded = confirmed_inputs

    async with gate_db.transaction() as session:
        await context(session, seeded)
        await append_revision(session, seeded, state="pending_review")
    async with gate_db.transaction() as session:
        await context(session, seeded)
        await append_revision(session, seeded, state=terminal, reason="Synthetic review")
    async with gate_db.transaction() as session:
        await context(session, seeded)
        await append_revision(
            session,
            seeded,
            state=terminal,
            disposition="comply_only",
            disposition_at=datetime.now(UTC),
            reason="Synthetic disposition",
        )
    async with gate_db.transaction() as session:
        card = await session.get(ResponseCard, seeded["ids"]["card"])
        revision = await session.get(ResponseCardRevision, card.current_revision_id)
        assert revision.state == terminal and revision.disposition == "comply_only"
        assert revision.confirmed_at is None


async def test_unreviewed_proof_warning_blocks_commitment_confirmation(
    gate_db, confirmed_inputs, admin_engine
):
    seeded = confirmed_inputs

    # Change synthetic extraction and its actual chunk together; quote remains exact.
    with admin_engine.begin() as connection:
        quote = "A valid certificate must be provided."
        connection.execute(
            text("UPDATE requirements SET quote=:quote"),
            {
                "quote": quote,
            },
        )
        connection.execute(
            text("UPDATE chunks SET text=:quote"),
            {
                "quote": quote,
            },
        )
    async with gate_db.transaction() as session:
        await context(session, seeded)
        await append_revision(session, seeded, response_kind="commitment", link_evidence=False)
    async with gate_db.transaction() as session:
        await context(session, seeded)
        await append_revision(session, seeded, state="pending_review", link_evidence=False)
    with pytest.raises(DBAPIError) as error:
        async with gate_db.transaction() as session:
            await context(session, seeded)
            await append_revision(
                session,
                seeded,
                state="confirmed",
                disposition="respond",
                disposition_at=datetime.now(UTC),
                confirmed_at=datetime.now(UTC),
                link_evidence=False,
            )
    assert error.value.orig.sqlstate == "23514"


async def copy_draft_item(session, seeded, **changes):

    original_run = await session.get(DraftRun, seeded["ids"]["draft"])
    original_job = await session.get(Job, original_run.generation_job_id)
    job = Job(
        task_id=original_run.task_id,
        document_id=original_job.document_id,
        kind="draft",
        cache_key=uuid4().hex * 2,
        status="running",
        run_id=uuid4(),
    )
    session.add(job)
    await session.flush()
    run = DraftRun(
        task_id=original_run.task_id,
        extraction_job_id=original_run.extraction_job_id,
        generation_job_id=job.id,
        generation_run_id=job.run_id,
        input_hash=uuid4().hex * 2,
        actor_kind="session",
        input_manifest=original_run.input_manifest,
        completion="partial",
        summary={},
    )
    session.add(run)
    await session.flush()
    original_item = await session.get(ResponseItem, seeded["ids"]["item"])
    values = {c.name: getattr(original_item, c.name) for c in ResponseItem.__table__.columns}
    values.update(id=uuid4(), draft_id=run.id, **changes)
    session.add(ResponseItem(**values))
    return run


async def test_draft_source_cannot_forge_chunk_location(gate_db, seeded):

    with pytest.raises(DBAPIError) as error:
        async with gate_db.transaction() as session:
            await context(session, seeded)
            original = await session.get(ResponseItem, seeded["ids"]["item"])
            await copy_draft_item(
                session, seeded, source={**original.source, "chunk_id": str(uuid4())}
            )
    assert error.value.orig.sqlstate == "23514"


async def test_draft_gap_reason_must_match_current_state(gate_db, seeded):
    with pytest.raises(DBAPIError) as error:
        async with gate_db.transaction() as session:
            await context(session, seeded)
            await copy_draft_item(session, seeded, gap_reasons=["missing_card"])
    assert error.value.orig.sqlstate == "23514"


async def test_eligible_negative_response_cannot_be_hidden_as_gap(gate_db, confirmed_inputs):
    seeded = confirmed_inputs

    async with gate_db.transaction() as session:
        await context(session, seeded)
        await append_revision(session, seeded, response_kind="commitment", link_evidence=False)
    async with gate_db.transaction() as session:
        await context(session, seeded)
        await append_revision(session, seeded, state="pending_review", link_evidence=False)
    async with gate_db.transaction() as session:
        await context(session, seeded)
        from task_fixtures import review_card_async

        await review_card_async(session, seeded["ids"]["card"], action="confirm")
    with pytest.raises(DBAPIError) as error:
        async with gate_db.transaction() as session:
            await context(session, seeded)
            card = await session.get(ResponseCard, seeded["ids"]["card"])
            await copy_draft_item(session, seeded, card_revision_id=card.current_revision_id)
    assert error.value.orig.sqlstate == "23514"


@pytest.mark.parametrize("change", ["cancel", "attempt"])
async def test_draft_publication_rechecks_job_attempt(gate_db, seeded, change):
    with pytest.raises(DBAPIError) as error:
        async with gate_db.transaction() as session:
            await context(session, seeded)
            run = await copy_draft_item(session, seeded)
            await session.flush()
            job = await session.get(Job, run.generation_job_id)
            if change == "cancel":
                job.status = "cancelled"
            else:
                job.run_id = uuid4()
    assert error.value.orig.sqlstate == "23514"


async def test_material_selection_cannot_claim_another_revision(gate_db, seeded):

    with pytest.raises(DBAPIError) as error:
        async with gate_db.transaction() as session:
            await context(session, seeded)
            original = await session.get(Evidence, seeded["ids"]["evidence"])
            selected = await session.get(TaskResource, original.task_resource_id)
            revision = ProductRevision(
                product_id=selected.product_id, revision=2, data={"name": original.quote}
            )
            session.add(revision)
            await session.flush()
            values = {c.name: getattr(original, c.name) for c in Evidence.__table__.columns}
            values.update(id=uuid4(), product_revision_id=revision.id)
            session.add(Evidence(**values))
    # A foreign key or the rewritten evidence checks (0023) may reject it first.
    assert error.value.orig.sqlstate in {"23503", "23514"}


async def test_requirement_cannot_rebind_after_card_creation(gate_db, seeded):

    with pytest.raises(DBAPIError) as error:
        async with gate_db.transaction() as session:
            await context(session, seeded)
            card = await session.get(ResponseCard, seeded["ids"]["card"])
            previous = await session.get(Job, card.extraction_job_id)
            extraction = Job(
                task_id=previous.task_id,
                document_id=previous.document_id,
                kind="extract",
                cache_key=uuid4().hex * 2,
                status="succeeded",
            )
            session.add(extraction)
            await session.flush()
            await session.execute(
                text("UPDATE requirements SET job_id=:job WHERE id=:requirement"),
                {"job": extraction.id, "requirement": card.requirement_id},
            )
    assert error.value.orig.sqlstate == "23503"


async def test_worker_cannot_submit_a_card_for_review(gate_db, seeded):

    with pytest.raises(DBAPIError) as error:
        async with gate_db.transaction() as session:
            await context(session, seeded, "worker")
            model_job = await session.scalar(select(Job).where(Job.kind == "card_generate"))
            await append_revision(
                session,
                seeded,
                state="pending_review",
                actor_kind="worker",
                origin="model",
                model_job_id=model_job.id,
            )
    assert error.value.orig.sqlstate == "42501"


async def test_model_revision_cannot_claim_an_unrelated_job(gate_db, seeded):

    with pytest.raises(DBAPIError) as error:
        async with gate_db.transaction() as session:
            await context(session, seeded, "worker")
            parse_job = await session.scalar(select(Job).where(Job.kind == "parse"))
            await append_revision(
                session,
                seeded,
                state="draft",
                actor_kind="worker",
                origin="model",
                model_job_id=parse_job.id,
            )
    assert error.value.orig.sqlstate == "23514"
