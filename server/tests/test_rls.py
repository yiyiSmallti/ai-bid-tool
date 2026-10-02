from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.core.db import Database
from app.models.entities import ApiToken, Chunk, Document, Job, Requirement, Task, UsageRecord
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

TENANT_TABLES = (
    "orgs",
    "memberships",
    "api_tokens",
    "tasks",
    "documents",
    "chunks",
    "requirements",
    "usage_records",
    "jobs",
    "response_cards",
    "response_card_revisions",
    "evidence",
    "card_evidence_links",
    "card_generation_runs",
    "draft_runs",
    "response_items",
)


@pytest.fixture
def seeded(tenants, admin_engine):
    identifiers = {}
    with Session(admin_engine) as session, session.begin():
        for index, org in enumerate(tenants["orgs"]):
            task = Task(
                id=uuid4(), org_id=org, created_by=tenants["users"][index], name="Synthetic task"
            )
            session.add(task)
            session.flush()
            document = Document(
                id=uuid4(),
                org_id=org,
                task_id=task.id,
                name="fixture.pdf",
                sha256="a" * 64,
                storage_key=f"org/{org}/fixture.pdf",
                media_type="application/pdf",
            )
            session.add(document)
            session.flush()
            chunk = Chunk(
                id=uuid4(),
                org_id=org,
                task_id=task.id,
                document_id=document.id,
                page=1,
                text="Synthetic source",
            )
            session.add(chunk)
            extraction = Job(
                org_id=org,
                task_id=task.id,
                document_id=document.id,
                kind="extract",
                cache_key="e" * 64,
                status="succeeded",
            )
            session.add(extraction)
            session.flush()
            session.add_all(
                [
                    Requirement(
                        org_id=org,
                        task_id=task.id,
                        document_id=document.id,
                        chunk_id=chunk.id,
                        page=1,
                        text="Synthetic",
                        quote="Synthetic source",
                        category="technical",
                        starred=False,
                        condition={},
                        fingerprint="b" * 64,
                        job_id=extraction.id,
                    ),
                    UsageRecord(
                        org_id=org,
                        task_id=task.id,
                        provider="test",
                        model="test",
                        version="1",
                        duration_ms=1,
                        usd=0,
                        test_only=True,
                    ),
                    ApiToken(
                        org_id=org,
                        user_id=tenants["users"][index],
                        name="test",
                        digest="c" * 64,
                        encrypted_secret="not-a-real-token",
                        scopes=["task:read"],
                        expires_at=datetime.now(UTC) + timedelta(days=1),
                    ),
                    Job(
                        org_id=org,
                        task_id=task.id,
                        document_id=document.id,
                        kind="parse",
                        cache_key="d" * 64,
                    ),
                ]
            )
            session.flush()
            from test_response_card_db import seed_response_rows

            requirement = session.scalar(select(Requirement).where(Requirement.org_id == org))
            response_ids = seed_response_rows(
                session, org, tenants["users"][index], task, extraction, requirement
            )
            identifiers[org] = {
                "task": task.id,
                "document": document.id,
                "chunk": chunk.id,
                **response_ids,
            }
    return {**tenants, "ids": identifiers}


@pytest.mark.parametrize("table", TENANT_TABLES)
async def test_every_business_table_enforces_read_write_scope(table, seeded, admin_engine):
    org_a, org_b = seeded["orgs"]
    db = Database(Settings())
    try:
        async with db.transaction(org_a) as session:
            visible = (await session.execute(text(f'SELECT org_id FROM "{table}"'))).scalars().all()
            assert visible and set(visible) == {org_a}
            # Append-only response tables deny UPDATE/DELETE privileges even for
            # a predicate matching no rows; verify that separately below.
            if table in TENANT_TABLES[9:]:
                updated = removed = None
            else:
                updated = await session.execute(
                    text(f'UPDATE "{table}" SET org_id=org_id WHERE org_id=:org'), {"org": org_b}
                )
                removed = await session.execute(
                    text(f'DELETE FROM "{table}" WHERE org_id=:org'), {"org": org_b}
                )
                assert updated.rowcount == removed.rowcount == 0
        async with db.transaction() as session:
            assert (await session.execute(text(f'SELECT id FROM "{table}"'))).all() == []
        with pytest.raises(DBAPIError) as error:
            async with db.transaction(org_a) as session:
                # Any attempt to relabel an owned row must fail WITH CHECK.
                await session.execute(text(f'UPDATE "{table}" SET org_id=:org'), {"org": org_b})
        assert error.value.orig.sqlstate == "42501"
        from app.models import Base

        table_model = Base.metadata.tables[table]
        with admin_engine.connect() as connection:
            foreign = dict(
                connection.execute(select(table_model).where(table_model.c.org_id == org_b))
                .mappings()
                .first()
            )
        foreign["id"] = uuid4()
        with pytest.raises(DBAPIError) as inserted:
            async with db.transaction(org_a) as session:
                await session.execute(table_model.insert().values(**foreign))
        assert inserted.value.orig.sqlstate == "42501"
        with admin_engine.connect() as connection:
            flags = connection.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname=:table"
                ),
                {"table": table},
            ).one()
            assert flags.relrowsecurity and flags.relforcerowsecurity
    finally:
        await db.engine.dispose()


async def test_cross_tenant_foreign_key_cannot_be_forged(seeded):
    org_a, org_b = seeded["orgs"]
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError) as error:
            async with db.transaction(org_a) as session:
                session.add(
                    Document(
                        org_id=org_a,
                        task_id=seeded["ids"][org_b]["task"],
                        name="forged.pdf",
                        sha256="f" * 64,
                        storage_key=f"org/{org_a}/forged.pdf",
                        media_type="application/pdf",
                    )
                )
        assert error.value.orig.sqlstate == "23503"
        async with db.transaction(org_a) as session:
            assert await session.get(Document, seeded["ids"][org_b]["document"]) is None
    finally:
        await db.engine.dispose()


async def test_runtime_refuses_privileged_role(admin_engine):
    settings = Settings(database_url=admin_engine.url.render_as_string(hide_password=False))
    db = Database(settings)
    try:
        with pytest.raises(RuntimeError, match="superuser"):
            await db.verify_role()
    finally:
        await db.engine.dispose()


async def test_database_rejects_forbidden_token_scope(seeded):
    org = seeded["orgs"][0]
    db = Database(Settings())
    try:
        for scope in ("export", "evidence:confirm"):
            with pytest.raises(DBAPIError) as error:
                async with db.transaction(org) as session:
                    session.add(
                        ApiToken(
                            org_id=org,
                            user_id=seeded["users"][0],
                            name="forbidden",
                            digest=uuid4().hex,
                            encrypted_secret="synthetic",
                            scopes=[scope],
                            expires_at=datetime.now(UTC) + timedelta(days=1),
                        )
                    )
            assert error.value.orig.sqlstate == "23514"
    finally:
        await db.engine.dispose()
