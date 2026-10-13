"""SQL/service failure inventory for confidential management.

Failures: missing parent insertion,
immutable field identity/value history rewrites, fabricated stale actor reuse,
audit failure leaving a value committed, task/field lock-order inversion,
unisolated or writable derived search tokens, caller-forged trigger depth,
stale label lexemes, projection
changes on CAS failure, prefix batches truncating or duplicating matches, token
order leaking into keyset pages, and pagination before task/library/archive filters.
Exercise only the explicitly supplied PostgreSQL test runtime.
"""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.core.errors import ServiceError
from app.models.confidential import ConfidentialValue
from app.models.entities import AuditLog
from app.schemas.management_pages import ConfidentialQuery
from app.services import confidential, management_confidential
from app.services.auth import authenticate
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from task_access_fixtures import new_task
from test_management_confidential import (
    FIELDS,
    SECRET,
    VALUES,
    assert_page,
    checked,
    create_field,
    write,
)

TOKEN_TABLE = "confidential_field_search_tokens"


async def projected_tokens(application, field_id):
    async with application.state.db.transaction() as session:
        return set(
            await session.scalars(
                text(
                    "SELECT token FROM public.confidential_field_search_tokens WHERE field_id=:id"
                ),
                {"id": UUID(field_id)},
            )
        )


async def test_search_projection_schema_and_runtime_privileges(
    api, headers, application, database, admin_engine
):
    from app.models.confidential import ConfidentialFieldSearchToken

    field = await create_field(
        api, headers, "projected_bank", label="Unique lexeme repeated repeated"
    )
    async with application.state.db.transaction() as session:
        rows = (await session.execute(select(ConfidentialFieldSearchToken))).scalars().all()
        assert {row.field_id for row in rows} == {UUID(field["id"])}
        assert {row.token for row in rows} == {"projected", "bank", "unique", "lexeme", "repeated"}
        assert len(rows) == 5
    with admin_engine.connect() as connection:
        flags = connection.execute(
            text(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='public.confidential_field_search_tokens'::regclass"
            )
        ).one()
        assert not flags.relrowsecurity and not flags.relforcerowsecurity
        columns = connection.execute(
            text(
                "SELECT column_name,is_nullable,data_type,collation_name FROM information_schema.columns WHERE table_schema='public' AND table_name=:table"
            ),
            {"table": TOKEN_TABLE},
        ).all()
        assert {row.column_name for row in columns} == {"field_id", "token"}
        assert all(row.is_nullable == "NO" for row in columns)
        assert {row.column_name: row.data_type for row in columns} == {
            "field_id": "uuid",
            "token": "text",
        }
        assert next(row.collation_name for row in columns if row.column_name == "token") == "C"
        constraints = connection.execute(
            text(
                "SELECT contype,pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE conrelid='public.confidential_field_search_tokens'::regclass"
            )
        ).all()
        assert any(
            row.contype == "p" and row.definition == "PRIMARY KEY (token, field_id)"
            for row in constraints
        )
        assert any(
            row.contype == "f"
            and "FOREIGN KEY (field_id) REFERENCES confidential_fields(id)" in row.definition
            and ("ON DELETE CASCADE" in row.definition)
            for row in constraints
        )
        owner_index = connection.scalar(
            text(
                "SELECT indexdef FROM pg_indexes WHERE schemaname='public' AND indexname='management_confidential_token_owner'"
            )
        )
        assert "(field_id, token)" in owner_index
        for privilege in ("SELECT", "INSERT", "DELETE"):
            assert connection.scalar(
                text("SELECT has_table_privilege('bid_app',:table,:privilege)"),
                {"table": "public." + TOKEN_TABLE, "privilege": privilege},
            )
        for privilege in ("UPDATE", "TRUNCATE", "TRIGGER"):
            assert not connection.scalar(
                text("SELECT has_table_privilege('bid_app',:table,:privilege)"),
                {"table": "public." + TOKEN_TABLE, "privilege": privilege},
            )
        trigger_functions = (
            connection.execute(
                text(
                    "SELECT p.prosecdef FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid WHERE NOT t.tgisinternal AND t.tgrelid IN ('public.confidential_fields'::regclass,'public.confidential_field_search_tokens'::regclass)"
                )
            )
            .scalars()
            .all()
        )
        assert trigger_functions and (not any(trigger_functions))


@pytest.mark.parametrize(
    "sql,sqlstate",
    [
        (
            "INSERT INTO public.confidential_field_search_tokens(field_id,token) VALUES(:field,'forged')",
            "23514",
        ),
        ("DELETE FROM public.confidential_field_search_tokens WHERE field_id=:field", "23514"),
        (
            "UPDATE public.confidential_field_search_tokens SET token='forged' WHERE field_id=:field",
            "42501",
        ),
    ],
)
async def test_runtime_cannot_mutate_search_projection_directly(
    sql, sqlstate, api, headers, application, database
):
    field = await create_field(api, headers, label="Original label")
    before = await projected_tokens(application, field["id"])
    assert before
    with pytest.raises(DBAPIError) as failure:
        async with application.state.db.transaction() as session:
            await session.execute(text(sql), {"field": UUID(field["id"])})
    assert failure.value.orig.sqlstate == sqlstate
    assert await projected_tokens(application, field["id"]) == before


@pytest.mark.parametrize(
    "mutation,token",
    [
        (
            "INSERT INTO public.confidential_field_search_tokens(field_id,token) VALUES(NEW.field_id,NEW.token)",
            "forgedlexeme",
        ),
        (
            "DELETE FROM public.confidential_field_search_tokens WHERE field_id=NEW.field_id AND token=NEW.token",
            "original",
        ),
    ],
    ids=["insert_forged_token", "delete_current_token"],
)
async def test_runtime_owned_temp_trigger_cannot_forge_search_projection_depth(
    mutation, token, api, headers, application, database
):
    field = await create_field(api, headers, label="Preserved original")
    before = await projected_tokens(application, field["id"])
    assert "original" in before and "forgedlexeme" not in before
    with pytest.raises(DBAPIError) as failure:
        async with application.state.db.transaction() as session:
            assert await session.scalar(text("SELECT current_user")) == "bid_app"
            await session.execute(
                text(
                    "CREATE TEMP TABLE projection_depth_spoof (field_id uuid,token text) ON COMMIT DROP"
                )
            )
            await session.execute(
                text(
                    "CREATE FUNCTION pg_temp.projection_depth_spoof() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF pg_trigger_depth()<>1 THEN RAISE EXCEPTION 'Unexpected fixture trigger depth' USING ERRCODE='XX000'; END IF; "
                    + mutation
                    + "; RETURN NEW; END $$"
                )
            )
            await session.execute(
                text(
                    "CREATE TRIGGER projection_depth_spoof AFTER INSERT ON pg_temp.projection_depth_spoof FOR EACH ROW EXECUTE FUNCTION pg_temp.projection_depth_spoof()"
                )
            )
            await session.execute(
                text(
                    "INSERT INTO pg_temp.projection_depth_spoof(field_id,token) VALUES(:field,:token)"
                ),
                {"field": UUID(field["id"]), "token": token},
            )
    assert failure.value.orig.sqlstate == "23514"
    assert await projected_tokens(application, field["id"]) == before


async def test_search_projection_insert_label_refresh_cas_and_parent_delete(
    api, headers, application, database, admin_engine
):
    field = await create_field(api, headers, "projection_bank", label="Oldlexeme repeated repeated")
    before = await projected_tokens(application, field["id"])
    assert before == {"projection", "bank", "oldlexeme", "repeated"}
    changed = await api.post(
        f"/confidential-fields/{field['id']}/revisions",
        headers=headers,
        json={"expected_revision": 1, "label": "Newlexeme repeated repeated"},
    )
    assert changed.status_code == 200, changed.text
    after = await projected_tokens(application, field["id"])
    assert after == {"projection", "bank", "newlexeme", "repeated"}
    stale = await api.post(
        f"/confidential-fields/{field['id']}/revisions",
        headers=headers,
        json={"expected_revision": 1, "label": "Rejectedlexeme"},
    )
    assert stale.status_code == 409
    assert stale.json()["data"]["error"]["code"] == "revision_conflict"
    stale_value = await api.post(
        f"{FIELDS}/{field['id']}/values", headers=headers, json=checked(revision=1)
    )
    assert stale_value.status_code == 409
    assert await projected_tokens(application, field["id"]) == after
    for query, expected in (("oldlex", []), ("newlex", [field["id"]]), ("rejectedlex", [])):
        page = assert_page(
            await api.post(FIELDS + "/query", headers=headers, json={"q": query}), headers
        )
        assert [row["id"] for row in page["items"]] == expected
    with admin_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM public.confidential_fields WHERE id=:id"), {"id": UUID(field["id"])}
        )
    assert not await projected_tokens(application, field["id"])


async def test_search_projection_prefix_semantics_unicode_and_filters_before_limit(api, headers):
    tasks = [await new_task(api, headers)]
    fields = [
        await create_field(
            api, headers, f"unicode_{index:02}", scope="task", label="Straße ΣΊΓΜΑ 中文秘密"
        )
        for index in range(3)
    ]
    target = await create_field(api, headers, "unicode_99", label="Straße ΣΊΓΜΑ 中文秘密")
    await create_field(api, headers, "unicode_other", label="Straße unrelated")
    archived = await api.post(
        f"/confidential-fields/{fields[0]['id']}/revisions",
        headers=headers,
        json={"expected_revision": 1, "archived": True},
    )
    assert archived.status_code == 200, archived.text
    for query in ("STRASS ΣΊΓ 中文秘", "中文秘 strass σίγ", "unicode_99 strass"):
        page = assert_page(
            await api.post(VALUES + "/query", headers=headers, json={"q": query, "limit": 1}),
            headers,
            limit=1,
        )
        assert [row["field_id"] for row in page["items"]] == [target["id"]]
        assert page["items"][0]["task_id"] is None and (not page["data"]["has_more"])
    for path, id_key in ((FIELDS, "id"), (VALUES, "field_id")):
        query = {"q": "strass σίγ 中文秘", "task_id": tasks[0], "limit": 1}
        ids = []
        while True:
            page = assert_page(
                await api.post(path + "/query", headers=headers, json=query), headers, limit=1
            )
            ids.extend(row[id_key] for row in page["items"])
            if not page["data"]["has_more"]:
                break
            assert len(ids) <= 3
            query["cursor"] = page["data"]["next_cursor"]
        assert ids == [fields[1]["id"], fields[2]["id"], target["id"]]
        exact = assert_page(
            await api.post(
                path + "/query",
                headers=headers,
                json={"q": "strass σίγ", "field_id": target["id"], "limit": 1},
            ),
            headers,
            limit=1,
        )
        assert [row[id_key] for row in exact["items"]] == [target["id"]]
        miss = assert_page(
            await api.post(
                path + "/query", headers=headers, json={"q": "strass absent", "task_id": tasks[0]}
            ),
            headers,
        )
        assert miss["items"] == []
        denied = await api.post(
            path + "/query", headers=headers, json={"q": "strass", "task_id": str(uuid4())}
        )
        assert denied.status_code == 404


@pytest.fixture
def prefix_batch_fields(admin_engine, database):
    """Token order opposes page order; eligible rows begin after two full batches."""
    fixtures = []
    with admin_engine.begin() as connection:
        base = uuid4().int & ~65535
        rows = [
            {
                "id": UUID(int=base + index + 1),
                "key": f"batch_{319 - index:04d}",
                "label": "Batchsame batchsameextra batchsameother"
                + (" markeraccept" if index >= 310 else ""),
                "scope": "task" if index < 310 else "library",
                "archived": index < 300,
            }
            for index in range(320)
        ]
        connection.execute(
            text(
                "INSERT INTO public.confidential_fields (id,key,label,kind,scope,archived) VALUES(:id,:key,:label,'other',:scope,:archived)"
            ),
            rows,
        )
        matching = connection.execute(
            text(
                "SELECT token,field_id FROM public.confidential_field_search_tokens WHERE token >= 'batchsame' AND token < 'batchsamf' ORDER BY token,field_id"
            ),
            {},
        ).all()
        assert len(matching) == 320 * 3
        target_offset = next(
            (index for index, match in enumerate(matching) if match.field_id == rows[-1]["id"])
        )
        assert target_offset == 319
        fixtures.append(
            {"rows": rows, "matching_tokens": len(matching), "target_token_offset": target_offset}
        )
    return fixtures


@pytest.mark.parametrize("path,id_key", [(FIELDS, "id"), (VALUES, "field_id")])
async def test_prefix_batches_preserve_all_matches_filters_and_keyset_pages(
    path, id_key, api, headers, database, prefix_batch_fields, tmp_path
):
    task = await new_task(api, headers)
    records = prefix_batch_fields[0]["rows"]
    ordered = sorted(records, key=lambda row: (row["key"], row["id"]))
    active = [row for row in ordered if not row["archived"]]
    library_active = [row for row in active if row["scope"] == "library"]
    receipt = {
        "path": path,
        "fields": len(records),
        "matching_tokens": prefix_batch_fields[0]["matching_tokens"],
        "late_target_token_offset": prefix_batch_fields[0]["target_token_offset"],
        "pages": [],
        "exact_queries": [],
    }
    cases = [
        ({"q": "batchsame", "archived": True, "task_id": task}, ordered),
        ({"q": "batchsame", "task_id": task}, active),
        ({"q": "batchsame"}, active if path == FIELDS else library_active),
        ({"q": "batchsame markeraccept", "archived": True, "task_id": task}, library_active),
    ]
    for filters, expected in cases:
        body = {**filters, "limit": 37}
        observed = []
        cursors = set()
        while True:
            page = assert_page(
                await api.post(path + "/query", headers=headers, json=body),
                headers,
                limit=body["limit"],
            )
            identifiers = [(row["key"], row[id_key]) for row in page["items"]]
            observed.extend(identifiers)
            receipt["pages"].append(
                {"filters": filters, "items": identifiers, "has_more": page["data"]["has_more"]}
            )
            if path == VALUES:
                assert all(row["status"] == "missing" for row in page["items"])
                assert all(
                    row["task_id"] == (task if row["scope"] == "task" else None)
                    for row in page["items"]
                )
            if not page["data"]["has_more"]:
                break
            assert identifiers, "Continuation must advance through matching fields"
            cursor = page["data"]["next_cursor"]
            assert cursor not in cursors
            cursors.add(cursor)
            assert len(observed) < len(expected), "Unexpected duplicate or extra continuation"
            body["cursor"] = cursor
        assert observed == [(row["key"], str(row["id"])) for row in expected]
        assert len({identifier for _, identifier in observed}) == len(observed)
    target = records[-1]
    archived = records[0]
    task_field = records[309]
    for record, extra, expected in (
        (target, {}, [target]),
        (target, {"q": "batchsame markeraccept"}, [target]),
        (archived, {}, []),
        (archived, {"archived": True, "task_id": task}, [archived]),
        (task_field, {}, [task_field] if path == FIELDS else []),
        (task_field, {"task_id": task}, [task_field]),
        ({"id": uuid4()}, {}, []),
    ):
        body = {"q": "batchsame", "field_id": str(record["id"]), "limit": 1, **extra}
        page = assert_page(
            await api.post(path + "/query", headers=headers, json=body), headers, limit=1
        )
        identifiers = [row[id_key] for row in page["items"]]
        assert identifiers == [str(row["id"]) for row in expected]
        assert not page["data"]["has_more"]
        receipt["exact_queries"].append({"filters": body, "ids": identifiers})
    receipt["status"] = "passed"
    (tmp_path / "confidential-prefix-batches.json").write_text(json.dumps(receipt, indent=2) + "\n")


@pytest.mark.parametrize(
    "column,value",
    [("key", "changed_key"), ("kind", "amount"), ("scope", "task"), ("created_at", None)],
)
async def test_field_identity_is_fixed_even_for_table_owner(
    column, value, api, headers, database, admin_engine
):
    field = await create_field(api, headers)
    if column == "created_at":
        value = "2020-01-01T00:00:00+00:00"
    with pytest.raises(DBAPIError) as failure, admin_engine.begin() as connection:
        connection.execute(
            text(f"UPDATE confidential_fields SET {column}=:value WHERE id=:id"),
            {"value": value, "id": UUID(field["id"])},
        )
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
    sql, api, headers, application, database
):
    field = await create_field(api, headers)
    await write(api, headers, field)
    with pytest.raises(DBAPIError) as failure:
        async with application.state.db.transaction() as session:
            await session.execute(text(sql), {"id": UUID(field["id"])})
    assert failure.value.orig.sqlstate == "42501"


async def test_value_and_existing_audit_are_atomic(
    api, headers, application, database, monkeypatch
):
    field = await create_field(api, headers)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("Synthetic audit persistence failure")

    monkeypatch.setattr(confidential, "audit", fail_audit)
    with pytest.raises(RuntimeError, match="Synthetic audit persistence failure"):
        await api.post(f"{FIELDS}/{field['id']}/values", headers=headers, json=checked())
    async with application.state.db.transaction() as session:
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


async def test_direct_service_consumes_authenticated_marker_and_rechecks_expiry(
    api, headers, application, database
):
    await create_field(api, headers)
    async with application.state.db.transaction() as session:
        actor = await authenticate(
            session,
            headers["Authorization"].removeprefix("Bearer "),
            application.state.crypto,
            application.state.processor.settings,
        )
        session.info["management_authenticated_actor"] = actor
        assert (
            await management_confidential.fields(session, actor, ConfidentialQuery())
        ).data.returned == 1
        assert "management_authenticated_actor" not in session.info
        expired = replace(actor, session_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        with pytest.raises(ServiceError) as failure:
            await management_confidential.fields(session, expired, ConfidentialQuery())
        assert failure.value.code == "invalid_session"


async def test_read_sql_excludes_ciphertext_and_checked_write_locks_task_first(
    api, headers, application
):
    task = await new_task(api, headers)
    field = await create_field(api, headers, scope="task")
    captured = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        captured.append(statement)

    engine = application.state.db.engine.sync_engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        await write(api, headers, field, task_id=task)
        locks = [statement.lower() for statement in captured if "for update" in statement.lower()]
        task_index = next((index for index, statement in enumerate(locks) if "tasks" in statement))
        field_index = next(
            (index for index, statement in enumerate(locks) if "confidential_fields" in statement)
        )
        assert task_index < field_index
        captured.clear()
        for path in (
            FIELDS + "/query",
            "/v4/management/confidential-values/query",
            f"{FIELDS}/{field['id']}/values/history/query",
        ):
            response = await api.post(path, headers=headers, json={"task_id": task})
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
