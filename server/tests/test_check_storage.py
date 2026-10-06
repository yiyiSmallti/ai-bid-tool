"""PostgreSQL integration gates for immutable checks using two synthetic tenants.

Failure inventory: cross-tenant/no-context reads and inserts; mutable report history;
late report children; old/expired worker attempts; mismatched report/task/draft/input;
incomplete coverage; forged tender/draft/evidence citations; nonhuman or wrong-domain
human decisions; stale decision CAS; forbidden token scopes; ciphertext rotation.
API acceptance covers current-input recomputation and public error envelopes.
"""

from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.core.db import Database
from app.core.security import Secrets
from app.models import Base
from app.models.check import (
    CheckCertificate,
    CheckCertificateItem,
    CheckDecision,
    CheckFinding,
    CheckFindingCitation,
    CheckItem,
    CheckRun,
)
from app.models.confidential import ConfidentialField, ConfidentialValue
from app.models.entities import Certificate, CertificateRevision, Job, Membership, TaskCertificate
from app.models.response_cards import (
    CardEvidenceLink,
    DraftRun,
    Evidence,
    ResponseCard,
    ResponseCardRevision,
    ResponseItem,
)
from sqlalchemy import insert, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_rls import seeded as seeded  # noqa: F401

TABLES = (
    "check_runs",
    "check_items",
    "check_certificates",
    "check_certificate_items",
    "check_findings",
    "check_finding_citations",
    "check_decisions",
)


def actor(session, org, user, kind="worker"):
    from task_fixtures import actor_context

    actor_context(session, org, user, kind=kind)


def confirmed_certificate_draft(session, org, user, ids, selection, certificate_revision):
    """Pass real edit, submit, confirm and draft gates for the storage fixture."""
    from task_fixtures import confirm_requirements

    confirm_requirements(session, org, ids["task"])
    actor(session, org, user, "session")
    old = session.get(ResponseItem, ids["item"])
    card = session.get(ResponseCard, old.card_id)
    evidence = Evidence(
        id=uuid4(),
        org_id=org,
        task_id=ids["task"],
        card_id=card.id,
        kind="certificate",
        task_certificate_id=selection.id,
        certificate_revision_id=certificate_revision.id,
        field_path="name",
        quote="Synthetic",
        material_kind="declaration",
        quote_check="exact_field_match",
    )
    session.add(evidence)
    session.flush()
    from app.services import drafts
    from task_fixtures import ServiceSession, actor_context, review_card, service_call

    previous = session.get(ResponseCardRevision, card.current_revision_id)
    values = {c.name: getattr(previous, c.name) for c in ResponseCardRevision.__table__.columns}
    values.update(
        id=uuid4(),
        revision=previous.revision + 1,
        state="draft",
        actor_user_id=user,
        actor_kind="session",
    )
    revision = ResponseCardRevision(**values)
    session.add(revision)
    session.flush()
    card.current_revision_id, card.revision = revision.id, revision.revision
    session.flush()
    session.add(
        CardEvidenceLink(
            org_id=org, card_id=card.id, revision_id=revision.id, evidence_id=evidence.id
        )
    )
    session.flush()
    review_card(session, org, user, card.id, action="submit")
    revision = review_card(session, org, user, card.id, action="confirm")
    _, _, draft_manifest, draft_hash, _ = service_call(
        drafts.assemble(
            ServiceSession(session),
            actor_context(session, org, user),
            card.task_id,
            card.extraction_job_id,
        )
    )
    original_draft = session.get(DraftRun, ids["draft"])
    draft_job = Job(
        id=uuid4(),
        org_id=org,
        task_id=ids["task"],
        document_id=ids["document"],
        kind="draft",
        cache_key=uuid4().hex * 2,
        status="running",
        run_id=uuid4(),
    )
    session.add(draft_job)
    session.flush()
    draft = DraftRun(
        id=uuid4(),
        org_id=org,
        task_id=ids["task"],
        extraction_job_id=original_draft.extraction_job_id,
        generation_job_id=draft_job.id,
        generation_run_id=draft_job.run_id,
        input_hash=draft_hash,
        actor_user_id=user,
        actor_kind="session",
        input_manifest=draft_manifest,
        completion="complete",
        summary={"rows": 1},
    )
    session.add(draft)
    session.flush()
    response = ResponseItem(
        id=uuid4(),
        org_id=org,
        draft_id=draft.id,
        requirement_id=old.requirement_id,
        category=old.category,
        starred=old.starred,
        card_id=card.id,
        card_revision_id=revision.id,
        kind="row",
        table="technical",
        source=old.source,
        location_label=old.location_label,
        response_kind=revision.response_kind,
        response_text=revision.response_text,
        deviation=revision.deviation,
        deviation_note=revision.deviation_note,
        gap_reasons=[],
    )
    session.add(response)
    session.flush()
    session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    return draft, response, evidence


def seed_check(session, org, user, ids):
    from task_fixtures import set_role_in_session

    set_role_in_session(session, org, user, "technical", task_ids=[ids["task"]])
    actor(session, org, user)
    certificate = Certificate(id=uuid4(), org_id=org, created_by=user)
    session.add(certificate)
    session.flush()
    revision = CertificateRevision(
        id=uuid4(),
        org_id=org,
        certificate_id=certificate.id,
        revision=1,
        data={
            "kind": "qualification",
            "name": "Synthetic",
            "number": "TEST-ONLY",
            "valid_from": "2026-01-01",
            "valid_until": "2026-10-04",
        },
    )
    session.add(revision)
    session.flush()
    selection = TaskCertificate(
        id=uuid4(),
        org_id=org,
        task_id=ids["task"],
        certificate_id=certificate.id,
        certificate_revision_id=revision.id,
    )
    session.add(selection)
    session.flush()
    draft, response, evidence = confirmed_certificate_draft(
        session, org, user, ids, selection, revision
    )
    actor(session, org, user)
    field = ConfidentialField(
        id=uuid4(),
        org_id=org,
        created_by=user,
        key="synthetic_field",
        label="Synthetic field",
        kind="other",
        scope="task",
    )
    session.add(field)
    session.flush()
    value = ConfidentialValue(
        id=uuid4(),
        org_id=org,
        created_by=user,
        field_id=field.id,
        task_id=draft.task_id,
        version=1,
        encrypted_value=Secrets.for_data(Settings()).encrypt("synthetic-only-value"),
    )
    session.add(value)
    session.flush()
    input_hash = "d" * 64
    encrypted_input = Secrets.for_data(Settings()).encrypt("{}")
    manifest = {
        "org_id": str(org),
        "task_id": str(draft.task_id),
        "draft_id": str(draft.id),
        "extraction_job_id": str(draft.extraction_job_id),
        "document_id": str(ids["document"]),
        "document_sha256": "a" * 64,
        "draft_input_hash": draft.input_hash,
        "assessment_date": "2026-10-04",
        "mode": "rules",
        "rule_version": "test",
        "schema_version": "test",
        "model_redaction_enabled": True,
        "model_redaction_revision": 1,
        "confidential": [
            {"field_id": str(field.id), "field_revision": field.revision, "value_id": str(value.id)}
        ],
        "items": [
            {
                "response_item_id": str(response.id),
                "requirement_id": str(response.requirement_id),
                "card_id": str(response.card_id),
                "card_revision_id": str(response.card_revision_id),
                "document_id": str(ids["document"]),
                "chunk_id": str(ids["chunk"]),
                "partition": "response",
                "review_domain": "technical",
                "gap_reasons": [],
                "evidence": [{"id": str(evidence.id), "active": True, "confirmed_by": str(user)}],
                "generation_dependencies": None,
            }
        ],
        "certificates": [
            {
                "task_certificate_id": str(selection.id),
                "certificate_revision_id": str(revision.id),
                "valid_from": "2026-01-01",
                "valid_until": "2026-10-04",
                "date_status": "valid",
                "requirement_ids": [str(response.requirement_id)],
            }
        ],
    }
    job = Job(
        id=uuid4(),
        org_id=org,
        task_id=draft.task_id,
        document_id=ids["document"],
        kind="check",
        cache_key=uuid4().hex * 2,
        status="running",
        run_id=uuid4(),
        lease_until=datetime.now(UTC) + timedelta(minutes=10),
        result={
            "submission": {
                "input_hash": input_hash,
                "input_manifest": manifest,
                "encrypted_input": encrypted_input,
            }
        },
    )
    session.add(job)
    session.flush()
    run = CheckRun(
        id=uuid4(),
        org_id=org,
        task_id=draft.task_id,
        job_id=job.id,
        run_id=job.run_id,
        draft_id=draft.id,
        extraction_job_id=draft.extraction_job_id,
        document_id=job.document_id,
        input_hash=input_hash,
        draft_input_hash=draft.input_hash,
        assessment_date=date(2026, 10, 4),
        mode="rules",
        rule_version="test",
        schema_version="test",
        input_manifest=manifest,
        encrypted_input=encrypted_input,
        completion="complete",
        limitations=["Synthetic fixture"],
        summary={"item_count": 1, "finding_count": 1, "unassessed_count": 0},
        actor_user_id=user,
        actor_kind="worker",
    )
    session.add(run)
    session.flush()
    item = CheckItem(
        id=uuid4(),
        org_id=org,
        task_id=draft.task_id,
        report_id=run.id,
        draft_id=draft.id,
        extraction_job_id=draft.extraction_job_id,
        requirement_id=response.requirement_id,
        response_item_id=response.id,
        card_revision_id=response.card_revision_id,
        partition="response",
        source=response.source,
        rules=[],
        semantic_status="not_requested",
    )
    cert = CheckCertificate(
        id=uuid4(),
        org_id=org,
        task_id=draft.task_id,
        report_id=run.id,
        task_certificate_id=selection.id,
        certificate_revision_id=revision.id,
        assessment_date=date(2026, 10, 4),
        date_status="valid",
    )
    session.add_all([item, cert])
    session.flush()
    finding = CheckFinding(
        id=uuid4(),
        org_id=org,
        task_id=draft.task_id,
        report_id=run.id,
        check_item_id=item.id,
        requirement_id=response.requirement_id,
        method="deterministic",
        code="negative_deviation",
        severity="deduction_risk",
        review_domain="technical",
        reason="Synthetic negative deviation",
        source=response.source,
    )
    session.add(finding)
    session.add(
        CheckCertificateItem(
            id=uuid4(),
            org_id=org,
            task_id=draft.task_id,
            report_id=run.id,
            certificate_id=cert.id,
            check_item_id=item.id,
        )
    )
    session.flush()
    session.add(
        CheckFindingCitation(
            id=uuid4(),
            org_id=org,
            task_id=draft.task_id,
            report_id=run.id,
            finding_id=finding.id,
            kind="tender",
            quote=response.source["quote"],
            document_id=ids["document"],
            chunk_id=ids["chunk"],
            source=response.source,
        )
    )
    session.flush()
    session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    actor(session, org, user, "session")
    reason = "Synthetic human review"
    decision = CheckDecision(
        id=uuid4(),
        org_id=org,
        task_id=draft.task_id,
        report_id=run.id,
        finding_id=finding.id,
        revision=2,
        action="dismiss",
        reason=reason,
        reason_sha256=sha256(reason.encode()).hexdigest(),
        expected_input_hash=input_hash,
        decided_by=user,
        actor_kind="session",
    )
    session.add(decision)
    session.flush()
    return {
        "run": run.id,
        "item": item.id,
        "finding": finding.id,
        "decision": decision.id,
        "job": job.id,
        "certificate_selection": selection.id,
        "confidential_field": field.id,
        "response": response.id,
        "revision": response.card_revision_id,
    }


@pytest.fixture
def check_seeded(seeded, admin_engine):
    from task_fixtures import finish_scope

    ids = {}
    with Session(admin_engine) as session, session.begin():
        for org, user in zip(seeded["orgs"], seeded["users"], strict=True):
            ids[org] = seed_check(session, org, user, seeded["ids"][org])
            finish_scope(session)
    return {**seeded, "checks": ids}


@pytest.fixture
async def check_db(check_seeded):
    db = Database(Settings())
    yield db
    await db.engine.dispose()


async def runtime_actor(session, data, kind="session", user=None, token=None):
    from task_fixtures import actor_context_async

    await actor_context_async(
        session, data["orgs"][0], user or data["users"][0], kind=kind, token=token
    )


@pytest.mark.parametrize("table", TABLES)
async def test_check_tables_force_tenant_scope(table, check_seeded, check_db, admin_engine):
    org_a, org_b = check_seeded["orgs"]
    model = Base.metadata.tables[table]
    async with check_db.transaction(org_a) as session:
        assert (await session.execute(select(model.c.org_id))).scalars().all() == [org_a]
        assert not (await session.execute(select(model).where(model.c.org_id == org_b))).all()
    async with check_db.transaction() as session:
        assert not (await session.execute(select(model))).all()
    with admin_engine.connect() as connection:
        assert connection.execute(
            text("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname=:name"),
            {"name": table},
        ).one() == (True, True)
        foreign = dict(
            connection.execute(select(model).where(model.c.org_id == org_b)).mappings().one()
        )
    foreign["id"] = uuid4()
    with pytest.raises(DBAPIError):
        async with check_db.transaction(org_a) as session:
            await runtime_actor(session, check_seeded, "worker")
            await session.execute(insert(model).values(foreign))
    with pytest.raises(DBAPIError):
        async with check_db.transaction() as session:
            await session.execute(insert(model).values(foreign))


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
async def test_check_history_is_immutable(table, operation, check_seeded, check_db):
    with pytest.raises(DBAPIError) as error:
        async with check_db.transaction(check_seeded["orgs"][0]) as session:
            statement = (
                f'UPDATE "{table}" SET id=id' if operation == "UPDATE" else f'DELETE FROM "{table}"'
            )
            await session.execute(text(statement))
    assert error.value.orig.sqlstate == "42501"


@pytest.mark.parametrize("kind", ["", "worker", "token", "agent"])
async def test_check_decisions_require_human_context(kind, check_seeded, check_db):
    org = check_seeded["orgs"][0]
    with pytest.raises(DBAPIError) as error:
        async with check_db.transaction(org) as session:
            await runtime_actor(session, check_seeded, kind)
            original = await session.get(CheckDecision, check_seeded["checks"][org]["decision"])
            values = {c.name: getattr(original, c.name) for c in CheckDecision.__table__.columns}
            values.update(id=uuid4(), revision=3, action="reopen")
            session.add(CheckDecision(**values))
    assert error.value.orig.sqlstate == "42501"


@pytest.mark.parametrize(
    "changes",
    [
        {"revision": 2},
        {"action": "dismiss"},
        {"expected_input_hash": "f" * 64},
        {"reason_sha256": "f" * 64},
    ],
)
async def test_check_decision_cas_and_hash(changes, check_seeded, check_db):
    org = check_seeded["orgs"][0]
    with pytest.raises(DBAPIError) as error:
        async with check_db.transaction(org) as session:
            await runtime_actor(session, check_seeded)
            original = await session.get(CheckDecision, check_seeded["checks"][org]["decision"])
            values = {c.name: getattr(original, c.name) for c in CheckDecision.__table__.columns}
            values.update(id=uuid4(), revision=3, action="reopen")
            values.update(changes)
            session.add(CheckDecision(**values))
    assert error.value.orig.sqlstate == "23514"


async def test_check_decision_reopen_keeps_candidate_unchanged(check_seeded, check_db):
    org = check_seeded["orgs"][0]
    async with check_db.transaction(org) as session:
        await runtime_actor(session, check_seeded)
        original = await session.get(CheckDecision, check_seeded["checks"][org]["decision"])
        values = {c.name: getattr(original, c.name) for c in CheckDecision.__table__.columns}
        values.update(id=uuid4(), revision=3, action="reopen")
        session.add(CheckDecision(**values))
    async with check_db.transaction(org) as session:
        assert (
            await session.scalars(select(CheckDecision.revision).order_by(CheckDecision.revision))
        ).all() == [2, 3]
        assert len((await session.scalars(select(CheckFinding))).all()) == 1


async def test_check_report_closed_to_late_children(check_seeded, check_db):
    org = check_seeded["orgs"][0]
    with pytest.raises(DBAPIError) as error:
        async with check_db.transaction(org) as session:
            await runtime_actor(session, check_seeded, "worker")
            original = await session.scalar(select(CheckFindingCitation))
            values = {
                c.name: getattr(original, c.name) for c in CheckFindingCitation.__table__.columns
            }
            values["id"] = uuid4()
            session.add(CheckFindingCitation(**values))
    assert error.value.orig.sqlstate == "42501"


async def test_check_scope_cannot_be_granted_to_token(check_seeded, check_db):
    from app.models.entities import ApiToken

    org = check_seeded["orgs"][0]
    with pytest.raises(DBAPIError) as error:
        async with check_db.transaction(org) as session:
            session.add(
                ApiToken(
                    org_id=org,
                    user_id=check_seeded["users"][0],
                    name="Synthetic forbidden token",
                    digest=uuid4().hex * 2,
                    scopes=["check:decide"],
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                )
            )
    assert error.value.orig.sqlstate == "23514"


async def pending_report(session, data):
    """Create a live check attempt; callers publish valid or deliberately invalid rows."""
    org = data["orgs"][0]
    await runtime_actor(session, data, "worker")
    original = await session.get(CheckRun, data["checks"][org]["run"])
    job = Job(
        id=uuid4(),
        org_id=org,
        task_id=original.task_id,
        document_id=original.document_id,
        kind="check",
        cache_key=uuid4().hex * 2,
        status="running",
        run_id=uuid4(),
        lease_until=datetime.now(UTC) + timedelta(minutes=10),
        result={
            "submission": {
                "input_hash": original.input_hash,
                "input_manifest": original.input_manifest,
                "encrypted_input": original.encrypted_input,
            }
        },
    )
    session.add(job)
    await session.flush()
    values = {c.name: getattr(original, c.name) for c in CheckRun.__table__.columns}
    values.update(id=uuid4(), job_id=job.id, run_id=job.run_id)
    report = CheckRun(**values)
    session.add(report)
    return report, job


@pytest.mark.parametrize(
    "failure",
    ["old_attempt", "expired_lease", "cancelled", "changed_hash", "changed_manifest", "wrong_task"],
)
async def test_check_publication_requires_owning_live_attempt(failure, check_seeded, check_db):
    from task_fixtures import related_task

    org = check_seeded["orgs"][0]
    with pytest.raises(DBAPIError) as error:
        async with check_db.transaction(org) as session:
            other_task = None
            if failure == "wrong_task":
                other_task = await related_task(
                    session,
                    org,
                    check_seeded["users"][0],
                    check_seeded["ids"][org]["task"],
                    name="Another synthetic task",
                )
            report, job = await pending_report(session, check_seeded)
            if failure == "old_attempt":
                report.run_id = uuid4()
            elif failure == "expired_lease":
                job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
            elif failure == "cancelled":
                job.status = "cancelled"
            elif failure == "changed_hash":
                report.input_hash = "f" * 64
            elif failure == "changed_manifest":
                report.input_manifest = {"unexpected": "manifest"}
            else:
                assert other_task is not None
                report.task_id = other_task.id
            await session.flush()
    assert error.value.orig.sqlstate in {"23514", "23503"}


async def test_check_rejects_incomplete_coverage_at_commit(check_seeded, check_db):
    with pytest.raises(DBAPIError) as error:
        async with check_db.transaction(check_seeded["orgs"][0]) as session:
            await pending_report(session, check_seeded)
            await session.flush()
    assert error.value.orig.sqlstate == "23514"


@pytest.mark.parametrize("change", ["source", "partition", "revision"])
async def test_check_item_must_match_fixed_response(change, check_seeded, check_db):
    org = check_seeded["orgs"][0]
    with pytest.raises(DBAPIError) as error:
        async with check_db.transaction(org) as session:
            report, _ = await pending_report(session, check_seeded)
            await session.flush()
            original = await session.get(CheckItem, check_seeded["checks"][org]["item"])
            values = {c.name: getattr(original, c.name) for c in CheckItem.__table__.columns}
            values.update(id=uuid4(), report_id=report.id)
            if change == "source":
                values["source"] = {**values["source"], "quote": "Invented text"}
            elif change == "partition":
                values["partition"] = "gap"
            else:
                values["card_revision_id"] = None
            session.add(CheckItem(**values))
    assert error.value.orig.sqlstate == "23514"


@pytest.mark.parametrize("role", ["admin", "bidder", "viewer"])
async def test_check_decision_professional_domain(role, check_seeded, check_db, admin_engine):
    org = check_seeded["orgs"][0]
    with admin_engine.begin() as connection:
        connection.execute(
            text("UPDATE memberships SET role=:role WHERE org_id=:org AND user_id=:user"),
            {"role": role, "org": org, "user": check_seeded["users"][0]},
        )
    with pytest.raises(DBAPIError) as error:
        async with check_db.transaction(org) as session:
            await runtime_actor(session, check_seeded)
            original = await session.get(CheckDecision, check_seeded["checks"][org]["decision"])
            values = {c.name: getattr(original, c.name) for c in CheckDecision.__table__.columns}
            values.update(id=uuid4(), revision=3, action="reopen")
            session.add(CheckDecision(**values))
    assert error.value.orig.sqlstate == "42501"


@pytest.mark.parametrize("table", TABLES)
async def test_check_tables_reject_same_org_cross_task(table, check_seeded, check_db):
    """All referenced parents exist in the same org; only task binding is changed."""
    from task_fixtures import related_task

    org = check_seeded["orgs"][0]
    original_report_id = check_seeded["checks"][org]["run"]
    with pytest.raises(DBAPIError) as error:
        async with check_db.transaction(org) as session:
            other_task = await related_task(
                session,
                org,
                check_seeded["users"][0],
                check_seeded["ids"][org]["task"],
                name="Synthetic other task",
            )
            if table == "check_decisions":
                await runtime_actor(session, check_seeded)
                original = await session.get(CheckDecision, check_seeded["checks"][org]["decision"])
                values = {
                    c.name: getattr(original, c.name) for c in CheckDecision.__table__.columns
                }
                values.update(id=uuid4(), task_id=other_task.id, revision=3, action="reopen")
                session.add(CheckDecision(**values))
                await session.flush()
            else:
                report, _ = await pending_report(session, check_seeded)
                if table == "check_runs":
                    report.task_id = other_task.id
                await session.flush()
                remapped = {}
                for child_table in TABLES[1:-1]:
                    metadata = Base.metadata.tables[child_table]
                    original = dict(
                        (
                            await session.execute(
                                select(metadata).where(metadata.c.report_id == original_report_id)
                            )
                        )
                        .mappings()
                        .one()
                    )
                    child_id = uuid4()
                    remapped[original["id"]] = child_id
                    values = {
                        key: remapped.get(value, value)
                        if key in {"check_item_id", "certificate_id", "finding_id"}
                        else value
                        for key, value in original.items()
                    }
                    values.update(id=child_id, report_id=report.id)
                    if child_table == table:
                        values["task_id"] = other_task.id
                    await session.execute(insert(metadata).values(values))
    assert error.value.orig.sqlstate in {"23503", "23514"}


async def copy_report_children(session, data, report, *, omit_table=None):
    original_report_id = data["checks"][data["orgs"][0]]["run"]
    remapped = {}
    for name in TABLES[1:-1]:
        if name == omit_table:
            continue
        metadata = Base.metadata.tables[name]
        original = dict(
            (
                await session.execute(
                    select(metadata).where(metadata.c.report_id == original_report_id)
                )
            )
            .mappings()
            .one()
        )
        new_id = uuid4()
        remapped[original["id"]] = new_id
        values = {
            key: remapped.get(value, value)
            if key in {"check_item_id", "certificate_id", "finding_id"}
            else value
            for key, value in original.items()
        }
        values.update(id=new_id, report_id=report.id)
        await session.execute(insert(metadata).values(values))


@pytest.mark.parametrize("key", ["item_count", "finding_count", "unassessed_count"])
async def test_check_report_summary_matches_persisted_rows(key, check_seeded, check_db):
    with pytest.raises(DBAPIError) as error:
        async with check_db.transaction(check_seeded["orgs"][0]) as session:
            report, _ = await pending_report(session, check_seeded)
            report.summary = {**report.summary, key: 100}
            await session.flush()
            await copy_report_children(session, check_seeded, report)
    assert error.value.orig.sqlstate == "23514"


async def test_check_certificate_mapping_cannot_be_omitted(check_seeded, check_db):
    with pytest.raises(DBAPIError) as error:
        async with check_db.transaction(check_seeded["orgs"][0]) as session:
            report, _ = await pending_report(session, check_seeded)
            await session.flush()
            await copy_report_children(
                session, check_seeded, report, omit_table="check_certificate_items"
            )
    assert error.value.orig.sqlstate == "23514"


@pytest.mark.parametrize(
    "key", ["org_id", "task_id", "draft_id", "extraction_job_id", "document_id"]
)
async def test_check_manifest_ids_must_match_relational_columns(key, check_seeded, check_db):
    with pytest.raises(DBAPIError) as error:
        async with check_db.transaction(check_seeded["orgs"][0]) as session:
            report, job = await pending_report(session, check_seeded)
            report.input_manifest = {**report.input_manifest, key: str(uuid4())}
            job.result = {
                "submission": {**job.result["submission"], "input_manifest": report.input_manifest}
            }
            await session.flush()
    assert error.value.orig.sqlstate == "23514"


async def test_check_ciphertext_must_be_the_submitted_snapshot(check_seeded, check_db):
    with pytest.raises(DBAPIError) as error:
        async with check_db.transaction(check_seeded["orgs"][0]) as session:
            report, _ = await pending_report(session, check_seeded)
            report.encrypted_input = Secrets.for_data(Settings()).encrypt(
                "different synthetic snapshot"
            )
            await session.flush()
    assert error.value.orig.sqlstate == "23514"


@pytest.mark.parametrize(
    "change",
    [
        "redaction",
        "certificate_selection",
        "source",
        "field_revision",
        "value_revision",
        "new_field",
        "card_revision",
    ],
)
async def test_check_stale_report_rejects_direct_decision(
    change, check_seeded, check_db, admin_engine
):
    org, user = check_seeded["orgs"][0], check_seeded["users"][0]
    ids = check_seeded["checks"][org]
    with Session(admin_engine) as session, session.begin():
        actor(session, org, user, "session")
        response = session.get(ResponseItem, ids["response"])
        if change == "certificate_selection":
            session.get(TaskCertificate, ids["certificate_selection"]).active = False
        elif change == "source":
            session.execute(
                text("UPDATE requirements SET quote='Synthetic' WHERE org_id=:org AND id=:id"),
                {"org": org, "id": response.requirement_id},
            )
        elif change == "redaction":
            member = session.scalar(
                select(Membership).where(Membership.org_id == org, Membership.user_id == user)
            )
            member.role = "admin"
            session.flush()
            session.execute(
                text(
                    "UPDATE tasks SET model_redaction_enabled=false,model_redaction_revision=2,model_redaction_by=:user WHERE org_id=:org AND id=:id"
                ),
                {"user": user, "org": org, "id": check_seeded["ids"][org]["task"]},
            )
            member.role = "technical"
        elif change == "field_revision":
            session.get(ConfidentialField, ids["confidential_field"]).revision += 1
        elif change == "value_revision":
            session.add(
                ConfidentialValue(
                    org_id=org,
                    created_by=user,
                    field_id=ids["confidential_field"],
                    task_id=check_seeded["ids"][org]["task"],
                    version=2,
                    encrypted_value=Secrets.for_data(Settings()).encrypt("new-synthetic-value"),
                )
            )
        elif change == "new_field":
            session.add(
                ConfidentialField(
                    org_id=org,
                    created_by=user,
                    key="another_synthetic",
                    label="Another synthetic field",
                    kind="other",
                    scope="org",
                )
            )
        else:
            card = session.get(ResponseCard, response.card_id)
            previous = session.get(ResponseCardRevision, card.current_revision_id)
            values = {
                c.name: getattr(previous, c.name) for c in ResponseCardRevision.__table__.columns
            }
            values.update(
                id=uuid4(),
                revision=previous.revision + 1,
                state="draft",
                confirmed_by=None,
                confirmed_at=None,
                reason="Synthetic reopening",
            )
            revised = ResponseCardRevision(**values)
            session.add(revised)
            session.flush()
            card.current_revision_id, card.revision = revised.id, revised.revision
            session.flush()
            for link in session.scalars(
                select(CardEvidenceLink).where(CardEvidenceLink.revision_id == previous.id)
            ).all():
                session.add(
                    CardEvidenceLink(
                        org_id=org,
                        card_id=card.id,
                        revision_id=revised.id,
                        evidence_id=link.evidence_id,
                    )
                )
    with pytest.raises(DBAPIError) as error:
        async with check_db.transaction(org) as session:
            await runtime_actor(session, check_seeded)
            original = await session.get(CheckDecision, ids["decision"])
            values = {c.name: getattr(original, c.name) for c in CheckDecision.__table__.columns}
            values.update(id=uuid4(), revision=3, action="reopen")
            session.add(CheckDecision(**values))
    assert error.value.orig.sqlstate == "23514"


async def test_check_certificate_relation_requires_actual_confirmed_link(check_seeded, check_db):
    org = check_seeded["orgs"][0]
    with pytest.raises(DBAPIError) as error:
        async with check_db.transaction(org) as session:
            report, _ = await pending_report(session, check_seeded)
            await session.flush()
            await copy_report_children(
                session, check_seeded, report, omit_table="check_certificate_items"
            )
            original = await session.get(
                TaskCertificate, check_seeded["checks"][org]["certificate_selection"]
            )
            unrelated = TaskCertificate(
                org_id=org,
                task_id=report.task_id,
                certificate_id=original.certificate_id,
                certificate_revision_id=original.certificate_revision_id,
                lot="other-synthetic-slot",
            )
            session.add(unrelated)
            await session.flush()
            certificate = CheckCertificate(
                org_id=org,
                task_id=report.task_id,
                report_id=report.id,
                task_certificate_id=unrelated.id,
                certificate_revision_id=unrelated.certificate_revision_id,
                assessment_date=report.assessment_date,
                date_status="valid",
            )
            session.add(certificate)
            await session.flush()
            item = await session.scalar(select(CheckItem).where(CheckItem.report_id == report.id))
            session.add(
                CheckCertificateItem(
                    org_id=org,
                    task_id=report.task_id,
                    report_id=report.id,
                    certificate_id=certificate.id,
                    check_item_id=item.id,
                )
            )
            await session.flush()
    assert error.value.orig.sqlstate == "23514"
    assert "lacks confirmed requirement binding" in str(error.value.orig)


@pytest.mark.parametrize("reason", ["", "   ", "\t\n"])
async def test_check_decision_rejects_whitespace_only_reason(reason, check_seeded, check_db):
    org = check_seeded["orgs"][0]
    with pytest.raises(DBAPIError) as error:
        async with check_db.transaction(org) as session:
            await runtime_actor(session, check_seeded)
            original = await session.get(CheckDecision, check_seeded["checks"][org]["decision"])
            values = {c.name: getattr(original, c.name) for c in CheckDecision.__table__.columns}
            values.update(
                id=uuid4(),
                revision=3,
                action="reopen",
                reason=reason,
                reason_sha256=sha256(reason.encode()).hexdigest(),
            )
            session.add(CheckDecision(**values))
    assert error.value.orig.sqlstate == "23514"
