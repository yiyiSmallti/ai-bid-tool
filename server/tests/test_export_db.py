"""Export SQL gates exercised against the actual PostgreSQL runtime role.

Failure inventory: absent/token/agent/worker actor; inactive or wrong-role member;
cross-org reads/inserts; same-org task/draft/job/template misbinding; immutable
history mutation; missing/extra/late items; candidate writes with expired lease,
wrong run/attempt/hash; worker publication; cancelled/failed candidate publication;
unconfirmed/stale response or evidence; gaps in final mode; descriptor mismatch.
These tests require the isolated PostgreSQL instances; no SQLite substitutes.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.core.config import Settings
from app.core.db import Database
from app.models import Base
from app.models.entities import Job, TaskTemplate, Template, TemplateRevision
from app.models.exports import (
    Export,
    ExportRenderCandidate,
    ExportRun,
    ExportRunEvidence,
    ExportRunItem,
    ExportTemplateBinding,
)
from app.models.response_cards import (
    CardEvidenceLink,
    DraftRun,
    Evidence,
    ResponseCardRevision,
    ResponseItem,
)
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from task_fixtures import reviewer_header
from test_response_cards import (
    PRODUCT_DATA,
    create_card,
    create_tender,
    phase_one_client,
    require_action,
    select_real_materials,
    set_role,
)

EXPORT_TABLES = (
    "export_template_bindings",
    "export_runs",
    "export_run_items",
    "export_run_evidence",
    "export_render_candidates",
    "exports",
)
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def actor(session, org, user, kind="session", *, run=None, attempt=None):
    from task_fixtures import actor_context

    actor_context(session, org, user, kind=kind)
    session.execute(
        text("""SELECT set_config('app.export_run_id',:run,true),
          set_config('app.export_attempt_id',:attempt,true)"""),
        {
            "run": str(run) if run else "",
            "attempt": str(attempt) if attempt else "",
        },
    )


def binding_rows(session, org, user, task):
    actor(session, org, user)
    template = Template(id=uuid4(), org_id=org, created_by=user, current_revision=1)
    session.add(template)
    session.flush()
    revision = TemplateRevision(
        id=uuid4(),
        org_id=org,
        template_id=template.id,
        revision=1,
        data={"name": "Synthetic database descriptor"},
        file={
            "name": "synthetic.docx",
            "sha256": "a" * 64,
            "size_bytes": 20,
            "media_type": DOCX_TYPE,
        },
        storage_key="",
    )
    revision.storage_key = f"org/{org}/template/{template.id}/{revision.id}/{'a' * 64}.docx"
    session.add(revision)
    session.flush()
    selection = TaskTemplate(
        id=uuid4(),
        org_id=org,
        task_id=task,
        template_id=template.id,
        template_revision_id=revision.id,
    )
    binding = ExportTemplateBinding(
        id=uuid4(),
        org_id=org,
        template_revision_id=revision.id,
        template_sha256="a" * 64,
        binding_hash="b" * 64,
        sections=[{"synthetic": n} for n in range(6)],
        static_content_hash="c" * 64,
        adapter_version="sql-fixture-v1",
        reviewed_by=user,
        reviewed_at=datetime.now(UTC),
    )
    session.add_all([selection, binding])
    session.flush()
    return selection.id, revision.id, binding.id


def run_rows(
    session, org, user, draft_id, selection, revision, binding, *, mode="review_copy", omit=False
):
    actor(session, org, user)
    draft = session.get(DraftRun, draft_id)
    assert draft is not None
    extraction = session.get(Job, draft.extraction_job_id)
    assert extraction is not None
    job = Job(
        id=uuid4(),
        org_id=org,
        task_id=draft.task_id,
        document_id=extraction.document_id,
        kind="export_render",
        cache_key=uuid4().hex * 2,
    )
    session.add(job)
    session.flush()
    items = list(
        session.scalars(
            select(ResponseItem).where(ResponseItem.draft_id == draft.id).order_by(ResponseItem.id)
        )
    )
    from app.services import task_cosign
    from task_fixtures import ServiceSession, service_call

    approvals = service_call(
        task_cosign.projections(
            ServiceSession(session), org, [item.card_id for item in items if item.card_id]
        )
    )
    manifest_items = []
    for ordinal, item in enumerate(items):
        entry = {
            "response_item_id": str(item.id),
            "requirement_id": str(item.requirement_id),
            "card_revision_id": str(item.card_revision_id) if item.card_revision_id else None,
            "card_id": str(item.card_id) if item.card_id else None,
            "kind": item.kind,
            "ordinal": ordinal,
            "source": item.source,
            "location_label": item.location_label,
            "category": item.category,
            "starred": item.starred,
            "evidence": [],
        }
        if item.card_id is not None:
            entry.update(task_cosign.manifest_fields(approvals[item.card_id]))
        if item.kind == "row":
            reviewed = session.get(ResponseCardRevision, item.card_revision_id)
            assert reviewed is not None
            entry |= {
                name: getattr(item, name)
                for name in (
                    "response_kind",
                    "response_text",
                    "deviation",
                    "deviation_note",
                    "table",
                )
            }
            entry |= {
                "confirmed_by": str(reviewed.confirmed_by),
                "confirmed_at": reviewed.confirmed_at.isoformat(),
                "disposition_by": str(reviewed.disposition_by),
                "disposition_at": reviewed.disposition_at.isoformat(),
            }
            for link in session.scalars(
                select(CardEvidenceLink).where(
                    CardEvidenceLink.revision_id == item.card_revision_id
                )
            ):
                evidence = session.get(Evidence, link.evidence_id)
                assert evidence is not None
                entry["evidence"].append(
                    {
                        "id": str(evidence.id),
                        "confirmed_by": str(evidence.confirmed_by),
                        "confirmed_at": evidence.confirmed_at.isoformat(),
                        "input": {
                            "kind": evidence.kind,
                            "quote": evidence.quote,
                            "field_path": evidence.field_path,
                            "selection_id": str(evidence.task_resource_id),
                        },
                        "selection_id": str(evidence.task_resource_id),
                        "resource_revision_id": str(evidence.product_revision_id),
                        "material_kind": evidence.material_kind,
                        "quote_check": evidence.quote_check,
                    }
                )
        elif item.kind == "gap":
            entry["gap_reasons"] = item.gap_reasons
        elif item.kind == "comply_only":
            entry |= {
                "disposition_by": str(item.disposition_by),
                "disposition_at": item.disposition_at.isoformat(),
            }
        manifest_items.append(entry)
    run = ExportRun(
        id=uuid4(),
        org_id=org,
        task_id=draft.task_id,
        extraction_job_id=draft.extraction_job_id,
        document_id=extraction.document_id,
        draft_run_id=draft.id,
        task_template_id=selection,
        template_revision_id=revision,
        binding_id=binding,
        render_job_id=job.id,
        mode=mode,
        input_hash=uuid4().hex * 2,
        manifest_hash="d" * 64,
        renderer_profile="sql-fixture-v1",
        manifest={"items": manifest_items},
        issue_snapshot=[],
        acknowledged_issue_ids=[],
        initiated_by=user,
        initiated_at=datetime.now(UTC),
    )
    session.add(run)
    session.flush()
    if not omit:
        for ordinal, item in enumerate(items):
            linked = ExportRunItem(
                id=uuid4(),
                org_id=org,
                run_id=run.id,
                draft_run_id=draft.id,
                response_item_id=item.id,
                requirement_id=item.requirement_id,
                card_revision_id=item.card_revision_id,
                kind=item.kind,
                ordinal=ordinal,
            )
            session.add(linked)
            session.flush()
            if item.kind == "row":
                for evidence in session.scalars(
                    select(CardEvidenceLink).where(
                        CardEvidenceLink.revision_id == item.card_revision_id
                    )
                ):
                    session.add(
                        ExportRunEvidence(
                            org_id=org,
                            run_id=run.id,
                            run_item_id=linked.id,
                            card_revision_id=item.card_revision_id,
                            evidence_id=evidence.evidence_id,
                        )
                    )
    session.flush()
    return run.id, job.id


@pytest.fixture
async def exports_seeded(tenants, tmp_path, admin_engine):
    ids = {}
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        for index, org in enumerate(tenants["orgs"]):
            user, header = tenants["users"][index], headers[index]
            task, _, extraction, requirements = await create_tender(
                api, app, header, tmp_path, suffix=str(index)
            )
            _, product, _, _, _ = await select_real_materials(api, header, task, tmp_path)
            card = await create_card(
                api,
                header,
                task,
                extraction,
                requirements[2],
                {
                    "response_kind": "evidence",
                    "response_text": "Synthetic reviewed response.",
                    "deviation": "negative",
                    "deviation_note": "The reviewed declaration does not promise delivery.",
                    "evidence": [
                        {
                            "kind": "product",
                            "selection_id": product["id"],
                            "field_path": "model",
                            "quote": PRODUCT_DATA["model"],
                        }
                    ],
                },
            )
            _, technical_header = await reviewer_header(
                api, admin_engine, org, UUID(task), "technical"
            )
            card = await require_action(api, header, card, "submit")
            await require_action(
                api,
                technical_header,
                card,
                "confirm",
                reviewed_evidence_ids=[e["id"] for e in card["evidence"]],
                reviewed_warning_codes=card["warning_codes"],
            )
            submitted = await api.post(
                f"/tasks/{task}/drafts", headers=header, json={"extraction_job_id": extraction}
            )
            assert submitted.status_code == 200, submitted.text
            job_id = submitted.json()["data"]["job_id"]
            await app.state.processor(str(org), job_id)
            shown = (await api.get(f"/jobs/{job_id}", headers=header)).json()["data"]
            assert shown["status"] == "succeeded", shown
            draft_id = UUID(shown["result"]["draft_id"])
            with Session(admin_engine) as session, session.begin():
                selection, revision, binding = binding_rows(session, org, user, UUID(task))
            set_role(admin_engine, org, user, "bidder")
            with Session(admin_engine) as session, session.begin():
                run_id, render_job = run_rows(
                    session, org, user, draft_id, selection, revision, binding
                )
            attempt = uuid4()
            with Session(admin_engine) as session, session.begin():
                job = session.get(Job, render_job)
                assert job is not None
                job.status, job.run_id = "running", attempt
                job.lease_until = datetime.now(UTC) + timedelta(minutes=5)
            candidate_id, export_id = uuid4(), uuid4()
            with Session(admin_engine) as session, session.begin():
                actor(session, org, user, "worker", run=run_id, attempt=attempt)
                run = session.get(ExportRun, run_id)
                assert run is not None
                session.add(
                    ExportRenderCandidate(
                        id=candidate_id,
                        org_id=org,
                        run_id=run_id,
                        render_job_id=render_job,
                        attempt_id=attempt,
                        input_hash=run.input_hash,
                        plaintext_sha256="e" * 64,
                        size_bytes=100,
                        renderer_profile=run.renderer_profile,
                        manifest_hash=run.manifest_hash,
                        object_key=f"org/{org}/export-candidates/{run_id}/{attempt}/{'e' * 64}.docx",
                    )
                )
                session.flush()
                job = session.get(Job, render_job)
                assert job is not None
                job.status = "succeeded"
            with Session(admin_engine) as session, session.begin():
                actor(session, org, user)
                run = session.get(ExportRun, run_id)
                assert run is not None
                session.add(
                    Export(
                        id=export_id,
                        org_id=org,
                        run_id=run_id,
                        candidate_id=candidate_id,
                        task_id=UUID(task),
                        mode=run.mode,
                        input_hash=run.input_hash,
                        manifest_hash=run.manifest_hash,
                        file_sha256="e" * 64,
                        size_bytes=100,
                        media_type=DOCX_TYPE,
                        object_key=f"org/{org}/exports/{export_id}/{'e' * 64}.docx",
                        released_by=user,
                        released_at=datetime.now(UTC),
                    )
                )
            ids[org] = {
                "run": run_id,
                "job": render_job,
                "attempt": attempt,
                "draft": draft_id,
                "selection": selection,
                "revision": revision,
                "binding": binding,
                "export": export_id,
                "candidate": candidate_id,
            }
    return {**tenants, "ids": ids}


async def async_actor(session, seed, index=0, kind="session", *, run=None, attempt=None):
    await session.run_sync(
        lambda sync: actor(
            sync, seed["orgs"][index], seed["users"][index], kind, run=run, attempt=attempt
        )
    )


@pytest.mark.parametrize("table", EXPORT_TABLES)
async def test_export_tables_two_org_actor_isolation_and_immutability(
    table, exports_seeded, admin_engine
):
    seed = exports_seeded
    org_a, org_b = seed["orgs"]
    db = Database(Settings())
    try:
        async with db.transaction(org_a) as session:
            await async_actor(session, seed)
            rows = (await session.execute(text(f'SELECT org_id FROM "{table}"'))).scalars().all()
            assert rows and set(rows) == {org_a}
            assert not (
                await session.execute(
                    text(f'SELECT id FROM "{table}" WHERE org_id=:org'), {"org": org_b}
                )
            ).all()
        for kind in ("", "token", "agent"):
            async with db.transaction(org_a) as session:
                await async_actor(session, seed, kind=kind)
                assert not (await session.execute(text(f'SELECT id FROM "{table}"'))).all()
        async with db.transaction() as session:
            assert not (await session.execute(text(f'SELECT id FROM "{table}"'))).all()
        for statement in (
            f'UPDATE "{table}" SET id=id',
            f'DELETE FROM "{table}"',
            f'TRUNCATE "{table}"',
        ):
            with pytest.raises(DBAPIError) as failure:
                async with db.transaction(org_a) as session:
                    await async_actor(session, seed)
                    await session.execute(text(statement))
            assert failure.value.orig.sqlstate == "42501"
        with admin_engine.connect() as connection:
            source = dict(
                connection.execute(
                    select(Base.metadata.tables[table]).where(
                        Base.metadata.tables[table].c.org_id == org_b
                    )
                )
                .mappings()
                .first()
            )
            flags = connection.execute(
                text(
                    "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname=:table"
                ),
                {"table": table},
            ).one()
            assert flags.relrowsecurity and flags.relforcerowsecurity
        source["id"] = uuid4()
        with pytest.raises(DBAPIError) as failure:
            async with db.transaction(org_a) as session:
                await async_actor(session, seed)
                await session.execute(Base.metadata.tables[table].insert().values(**source))
        assert failure.value.orig.sqlstate == "42501"
    finally:
        await db.engine.dispose()


async def test_worker_run_attempt_scope_never_reads_released_files(exports_seeded, admin_engine):
    seed = exports_seeded
    org = seed["orgs"][0]
    row = seed["ids"][org]
    with Session(admin_engine) as session, session.begin():
        job = session.get(Job, row["job"])
        assert job is not None
        job.status = "running"
        job.lease_until = datetime.now(UTC) + timedelta(minutes=5)
    db = Database(Settings())
    try:
        async with db.transaction(org) as session:
            await async_actor(session, seed, kind="worker", run=row["run"], attempt=row["attempt"])
            assert await session.get(ExportRun, row["run"]) is not None
            assert await session.get(ExportRenderCandidate, row["candidate"]) is not None
            assert await session.get(Export, row["export"]) is None
        async with db.transaction(org) as session:
            await async_actor(session, seed, kind="worker", run=row["run"], attempt=uuid4())
            for table in EXPORT_TABLES:
                assert not (await session.execute(text(f'SELECT id FROM "{table}"'))).all()
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize("omit,mode", [(True, "review_copy"), (False, "final_section")])
async def test_deferred_gate_rejects_missing_items_and_final_gaps(exports_seeded, omit, mode):
    seed = exports_seeded
    org = seed["orgs"][0]
    row = seed["ids"][org]
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as failure:
            async with db.transaction(org) as session:
                await session.run_sync(
                    lambda sync: run_rows(
                        sync,
                        org,
                        seed["users"][0],
                        row["draft"],
                        row["selection"],
                        row["revision"],
                        row["binding"],
                        mode=mode,
                        omit=omit,
                    )
                )
        assert failure.value.orig.sqlstate == "23514"
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize("kind", ["token", "agent", "worker", ""])
async def test_nonhuman_cannot_publish_via_direct_sql(kind, exports_seeded, admin_engine):
    seed = exports_seeded
    org = seed["orgs"][0]
    row = seed["ids"][org]
    table = Base.metadata.tables["exports"]
    with admin_engine.connect() as connection:
        data = dict(
            connection.execute(select(table).where(table.c.id == row["export"])).mappings().one()
        )
    data["id"] = uuid4()
    data["object_key"] = f"org/{org}/exports/{data['id']}/{data['file_sha256']}.docx"
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as failure:
            async with db.transaction(org) as session:
                await async_actor(session, seed, kind=kind, run=row["run"], attempt=row["attempt"])
                await session.execute(table.insert().values(**data))
        assert failure.value.orig.sqlstate == "42501"
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize("failure_mode", ["wrong_attempt", "expired_lease", "cancelled"])
async def test_candidate_insert_requires_current_live_worker(
    failure_mode, exports_seeded, admin_engine
):
    seed = exports_seeded
    org = seed["orgs"][0]
    row = seed["ids"][org]
    table = Base.metadata.tables["export_render_candidates"]
    with Session(admin_engine) as session, session.begin():
        data = dict(
            session.execute(select(table).where(table.c.id == row["candidate"])).mappings().one()
        )
        job = session.get(Job, row["job"])
        assert job is not None
        job.status = "cancelled" if failure_mode == "cancelled" else "running"
        job.lease_until = datetime.now(UTC) + timedelta(
            minutes=-1 if failure_mode == "expired_lease" else 5
        )
    data["id"] = uuid4()
    if failure_mode == "wrong_attempt":
        data["attempt_id"] = uuid4()
    data["object_key"] = (
        f"org/{org}/export-candidates/{row['run']}/{data['attempt_id']}/{data['plaintext_sha256']}.docx"
    )
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as failure:
            async with db.transaction(org) as session:
                await async_actor(
                    session, seed, kind="worker", run=row["run"], attempt=data["attempt_id"]
                )
                await session.execute(table.insert().values(**data))
        assert failure.value.orig.sqlstate == "42501"
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize("failure_mode", ["hash", "size", "failed_job", "wrong_task"])
async def test_release_rechecks_candidate_descriptor_and_job(
    failure_mode, exports_seeded, admin_engine
):
    seed = exports_seeded
    org = seed["orgs"][0]
    row = seed["ids"][org]
    table = Base.metadata.tables["exports"]
    with Session(admin_engine) as session, session.begin():
        data = dict(
            session.execute(select(table).where(table.c.id == row["export"])).mappings().one()
        )
        if failure_mode == "failed_job":
            job = session.get(Job, row["job"])
            assert job is not None
            job.status = "failed"
    data["id"] = uuid4()
    if failure_mode == "hash":
        data["file_sha256"] = "f" * 64
    elif failure_mode == "size":
        data["size_bytes"] += 1
    elif failure_mode == "wrong_task":
        data["task_id"] = uuid4()
    data["object_key"] = f"org/{org}/exports/{data['id']}/{data['file_sha256']}.docx"
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as failure:
            async with db.transaction(org) as session:
                await async_actor(session, seed)
                await session.execute(table.insert().values(**data))
        assert failure.value.orig.sqlstate == "23514"
    finally:
        await db.engine.dispose()


async def test_closed_run_cannot_receive_late_item(exports_seeded, admin_engine):
    seed = exports_seeded
    org = seed["orgs"][0]
    table = Base.metadata.tables["export_run_items"]
    with admin_engine.connect() as connection:
        data = dict(
            connection.execute(select(table).where(table.c.org_id == org)).mappings().first()
        )
    data["id"] = uuid4()
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as failure:
            async with db.transaction(org) as session:
                await async_actor(session, seed)
                await session.execute(table.insert().values(**data))
        assert failure.value.orig.sqlstate == "42501"
    finally:
        await db.engine.dispose()


async def test_worker_cannot_forge_human_export_audit(exports_seeded):
    from app.models.entities import AuditLog

    seed = exports_seeded
    org = seed["orgs"][0]
    row = seed["ids"][org]
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as failure:
            async with db.transaction(org) as session:
                await async_actor(
                    session, seed, kind="worker", run=row["run"], attempt=row["attempt"]
                )
                session.add(
                    AuditLog(
                        org_id=org,
                        actor_user_id=seed["users"][0],
                        action="export.released",
                        object_id=row["export"],
                        details={"actor_kind": "session"},
                    )
                )
        assert failure.value.orig.sqlstate == "42501"
    finally:
        await db.engine.dispose()
