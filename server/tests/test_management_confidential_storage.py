"""SQL/service failure inventory for confidential management.

Failures: RLS absent/foreign context, composite foreign parent insertion,
immutable field identity/value history rewrites, fabricated stale actor reuse,
audit failure leaving a value committed, and task/field lock-order inversion.
Exercise only the explicitly supplied PostgreSQL test runtime.
"""

from uuid import UUID, uuid4

import pytest
from app.core.errors import ServiceError
from app.models.confidential import ConfidentialField, ConfidentialValue
from app.models.entities import AuditLog, Membership
from app.schemas.management_pages import ConfidentialQuery
from app.services import confidential, management_confidential
from app.services.auth import authenticate
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_management_confidential import FIELDS, SECRET, checked, create_field, write
from test_team_workflow_membership import new_task


async def test_two_tables_force_rls_and_fail_closed(
    api, headers, application, tenants, admin_engine
):
    fields = [await create_field(api, auth) for auth in headers]
    values = [await write(api, auth, field) for auth, field in zip(headers, fields, strict=True)]
    for org, index in ((None, None), (tenants["orgs"][0], 0), (tenants["orgs"][1], 1)):
        async with application.state.db.transaction(org) as session:
            for model, receipts, key in (
                (ConfidentialField, fields, "id"),
                (ConfidentialValue, values, "value_id"),
            ):
                ids = set(await session.scalars(select(model.id)))
                assert ids == (set() if index is None else {UUID(receipts[index][key])})
    with admin_engine.connect() as connection:
        for table in ("confidential_fields", "confidential_values"):
            flags = connection.execute(
                text(
                    "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname=:table"
                ),
                {"table": table},
            ).one()
            assert flags.relrowsecurity and flags.relforcerowsecurity
    with pytest.raises(DBAPIError) as failure:
        async with application.state.db.transaction(tenants["orgs"][0]) as session:
            await session.execute(
                text(
                    "INSERT INTO confidential_fields(id,org_id,created_by,key,label,kind,scope) VALUES(:id,:org,:actor,'foreign_field','Synthetic','other','org')"
                ),
                {"id": uuid4(), "org": tenants["orgs"][1], "actor": tenants["users"][1]},
            )
    assert failure.value.orig.sqlstate == "42501"
    with pytest.raises(DBAPIError) as failure:
        async with application.state.db.transaction(tenants["orgs"][0]) as session:
            await session.execute(
                text(
                    "INSERT INTO confidential_values(id,org_id,created_by,field_id,version,encrypted_value) VALUES(:id,:org,:actor,:field,1,'synthetic-invalid-ciphertext')"
                ),
                {
                    "id": uuid4(),
                    "org": tenants["orgs"][0],
                    "actor": tenants["users"][0],
                    "field": UUID(fields[1]["id"]),
                },
            )
    assert failure.value.orig.sqlstate == "23503"


@pytest.mark.parametrize(
    "column,value",
    [
        ("key", "changed_key"),
        ("kind", "amount"),
        ("scope", "task"),
        ("created_by", None),
        ("created_at", None),
    ],
)
async def test_field_identity_is_fixed_even_for_table_owner(
    column, value, api, headers, tenants, admin_engine
):
    field = await create_field(api, headers[0])
    if column == "created_by":
        value = tenants["users"][1]
    elif column == "created_at":
        value = "2020-01-01T00:00:00+00:00"
    with pytest.raises(DBAPIError) as failure, admin_engine.begin() as connection:
        connection.execute(
            text(f"UPDATE confidential_fields SET {column}=:value WHERE id=:id"),
            {"value": value, "id": UUID(field["id"])},
        )
    # The composite membership FK deliberately wins over the AFTER guard.
    assert failure.value.orig.sqlstate in {"23503", "23514"}


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE confidential_fields SET key='changed_key' WHERE id=:id",
        "DELETE FROM confidential_fields WHERE id=:id",
        "UPDATE confidential_values SET encrypted_value='changed' WHERE field_id=:id",
        "DELETE FROM confidential_values WHERE field_id=:id",
    ],
)
async def test_runtime_cannot_rewrite_fixed_fields_or_value_history(
    sql, api, headers, application, tenants
):
    field = await create_field(api, headers[0])
    await write(api, headers[0], field)
    with pytest.raises(DBAPIError) as failure:
        async with application.state.db.transaction(tenants["orgs"][0]) as session:
            await session.execute(text(sql), {"id": UUID(field["id"])})
    assert failure.value.orig.sqlstate == "42501"


async def test_value_and_existing_audit_are_atomic(api, headers, application, tenants, monkeypatch):
    field = await create_field(api, headers[0])

    def fail_audit(*args, **kwargs):
        raise RuntimeError("Synthetic audit persistence failure")

    monkeypatch.setattr(confidential, "audit", fail_audit)
    with pytest.raises(RuntimeError, match="Synthetic audit persistence failure"):
        await api.post(f"{FIELDS}/{field['id']}/values", headers=headers[0], json=checked())
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        assert not (
            await session.scalars(
                select(ConfidentialValue).where(ConfidentialValue.field_id == UUID(field["id"]))
            )
        ).all()
        assert not (
            await session.scalars(
                select(AuditLog).where(
                    AuditLog.object_id == UUID(field["id"]),
                    AuditLog.action == "confidential.value.set",
                )
            )
        ).all()


async def test_direct_service_consumes_authenticated_marker_and_rechecks_membership(
    api, headers, application, tenants, admin_engine
):
    await create_field(api, headers[0])
    org = tenants["orgs"][0]
    async with application.state.db.transaction(org) as session:
        actor = await authenticate(
            session,
            headers[0]["Authorization"].removeprefix("Bearer "),
            org,
            application.state.crypto,
            joined_membership=True,
        )
        session.info["management_authenticated_actor"] = actor
        assert (
            await management_confidential.fields(session, actor, ConfidentialQuery())
        ).data.returned == 1
        assert "management_authenticated_actor" not in session.info
        with Session(admin_engine) as owner, owner.begin():
            owner.scalar(select(Membership).where(Membership.org_id == org)).active = False
        with pytest.raises(ServiceError) as failure:
            await management_confidential.fields(session, actor, ConfidentialQuery())
        assert failure.value.code == "not_found"


async def test_read_sql_excludes_ciphertext_and_checked_write_locks_task_first(
    api, headers, application
):
    task = await new_task(api, headers[0])
    field = await create_field(api, headers[0], scope="task")
    captured = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        captured.append(statement)

    engine = application.state.db.engine.sync_engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        await write(api, headers[0], field, task_id=task)
        locks = [statement.lower() for statement in captured if "for update" in statement.lower()]
        task_index = next(
            index for index, statement in enumerate(locks) if "task_workflows" in statement
        )
        field_index = next(
            index for index, statement in enumerate(locks) if "confidential_fields" in statement
        )
        assert task_index < field_index
        captured.clear()
        for path in (
            FIELDS + "/query",
            "/v4/management/confidential-values/query",
            f"{FIELDS}/{field['id']}/values/history/query",
        ):
            response = await api.post(path, headers=headers[0], json={"task_id": task})
            assert response.status_code == 200, response.text
            assert SECRET not in response.text
        value_reads = [
            statement
            for statement in captured
            if statement.lstrip().upper().startswith("SELECT")
            and "confidential_values" in statement
        ]
        assert value_reads and all("encrypted_value" not in statement for statement in value_reads)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
