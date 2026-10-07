"""SQL/service failure inventory for confidential management.

Failures: RLS absent/foreign context, composite foreign parent insertion,
immutable field identity/value history rewrites, fabricated stale actor reuse,
audit failure leaving a value committed, task/field lock-order inversion,
unisolated or writable derived search tokens, caller-forged trigger depth,
stale label lexemes, projection
changes on CAS failure, prefix batches truncating or duplicating matches, token
order leaking into keyset pages, and pagination before org/task/archive filters.
Exercise only the explicitly supplied PostgreSQL test runtime.
"""

import json
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
from test_management_confidential import (
    FIELDS,
    SECRET,
    VALUES,
    assert_page,
    checked,
    create_field,
    write,
)
from test_team_workflow_membership import new_task

TOKEN_TABLE = "confidential_field_search_tokens"


async def projected_tokens(application, org, field_id):
    async with application.state.db.transaction(org) as session:
        return set(
            await session.scalars(
                text(
                    "SELECT token FROM public.confidential_field_search_tokens WHERE field_id=:id"
                ),
                {"id": UUID(field_id)},
            )
        )


async def test_search_projection_force_rls_schema_and_runtime_privileges(
    api, headers, application, tenants, admin_engine
):
    from app.models.confidential import ConfidentialFieldSearchToken

    fields = [
        await create_field(api, auth, "projected_bank", label="Unique lexeme repeated repeated")
        for auth in headers
    ]
    for org, index in ((None, None), (tenants["orgs"][0], 0), (tenants["orgs"][1], 1)):
        async with application.state.db.transaction(org) as session:
            rows = (await session.execute(select(ConfidentialFieldSearchToken))).scalars().all()
            assert {row.field_id for row in rows} == (
                set() if index is None else {UUID(fields[index]["id"])}
            )
            if index is not None:
                assert {row.org_id for row in rows} == {org}
                assert {row.token for row in rows} == {
                    "projected",
                    "bank",
                    "unique",
                    "lexeme",
                    "repeated",
                }
                assert len(rows) == 5
    with admin_engine.connect() as connection:
        flags = connection.execute(
            text(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class"
                " WHERE oid='public.confidential_field_search_tokens'::regclass"
            )
        ).one()
        assert flags.relrowsecurity and flags.relforcerowsecurity
        columns = connection.execute(
            text(
                "SELECT column_name,is_nullable,data_type,collation_name"
                " FROM information_schema.columns WHERE table_schema='public' AND table_name=:table"
            ),
            {"table": TOKEN_TABLE},
        ).all()
        assert {row.column_name for row in columns} == {"org_id", "field_id", "token"}
        assert all(row.is_nullable == "NO" for row in columns)
        assert {row.column_name: row.data_type for row in columns} == {
            "org_id": "uuid",
            "field_id": "uuid",
            "token": "text",
        }
        assert next(row.collation_name for row in columns if row.column_name == "token") == "C"
        constraints = connection.execute(
            text(
                "SELECT contype,pg_get_constraintdef(oid) AS definition FROM pg_constraint"
                " WHERE conrelid='public.confidential_field_search_tokens'::regclass"
            )
        ).all()
        assert any(
            row.contype == "p" and row.definition == "PRIMARY KEY (org_id, token, field_id)"
            for row in constraints
        )
        assert any(
            row.contype == "f"
            and "FOREIGN KEY (org_id, field_id) REFERENCES confidential_fields(org_id, id)"
            in row.definition
            and "ON DELETE CASCADE" in row.definition
            for row in constraints
        )
        owner_index = connection.scalar(
            text(
                "SELECT indexdef FROM pg_indexes WHERE schemaname='public' AND indexname='management_confidential_token_owner'"
            )
        )
        assert "(org_id, field_id, token)" in owner_index
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
        # Both synchronization and mutation guards must execute as the caller.
        trigger_functions = (
            connection.execute(
                text(
                    "SELECT p.prosecdef FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid"
                    " WHERE NOT t.tgisinternal AND t.tgrelid IN"
                    " ('public.confidential_fields'::regclass,'public.confidential_field_search_tokens'::regclass)"
                )
            )
            .scalars()
            .all()
        )
        assert trigger_functions and not any(trigger_functions)


async def test_search_projection_composite_fk_rejects_foreign_parent(
    api, headers, tenants, admin_engine
):
    foreign = await create_field(api, headers[1])
    with pytest.raises(DBAPIError) as failure, admin_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO public.confidential_field_search_tokens(org_id,field_id,token)"
                " VALUES(:org,:field,'foreign')"
            ),
            {"org": tenants["orgs"][0], "field": UUID(foreign["id"])},
        )
    assert failure.value.orig.sqlstate == "23503"


@pytest.mark.parametrize(
    "sql,sqlstate",
    [
        (
            "INSERT INTO public.confidential_field_search_tokens(org_id,field_id,token) VALUES(:org,:field,'forged')",
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
    sql, sqlstate, api, headers, application, tenants
):
    field = await create_field(api, headers[0], label="Original label")
    org = tenants["orgs"][0]
    before = await projected_tokens(application, org, field["id"])
    assert before
    with pytest.raises(DBAPIError) as failure:
        async with application.state.db.transaction(org) as session:
            await session.execute(text(sql), {"org": org, "field": UUID(field["id"])})
    assert failure.value.orig.sqlstate == sqlstate
    assert await projected_tokens(application, org, field["id"]) == before
    with pytest.raises(DBAPIError) as failure:
        async with application.state.db.transaction(None) as session:
            await session.execute(
                text(
                    "INSERT INTO public.confidential_field_search_tokens(org_id,field_id,token) VALUES(:org,:field,'forged')"
                ),
                {"org": org, "field": UUID(field["id"])},
            )
    assert failure.value.orig.sqlstate == "42501"


@pytest.mark.parametrize(
    "mutation,token",
    [
        (
            "INSERT INTO public.confidential_field_search_tokens(org_id,field_id,token)"
            " VALUES(NEW.org_id,NEW.field_id,NEW.token)",
            "forgedlexeme",
        ),
        (
            "DELETE FROM public.confidential_field_search_tokens"
            " WHERE org_id=NEW.org_id AND field_id=NEW.field_id AND token=NEW.token",
            "original",
        ),
    ],
    ids=["insert_forged_token", "delete_current_token"],
)
async def test_runtime_owned_temp_trigger_cannot_forge_search_projection_depth(
    mutation, token, api, headers, application, tenants
):
    field = await create_field(api, headers[0], label="Preserved original")
    org = tenants["orgs"][0]
    before = await projected_tokens(application, org, field["id"])
    assert "original" in before and "forgedlexeme" not in before
    with pytest.raises(DBAPIError) as failure:
        async with application.state.db.transaction(org) as session:
            assert await session.scalar(text("SELECT current_user")) == "bid_app"
            # A caller-owned temp trigger runs at depth 1, so its projection DML
            # reaches the real guard at depth 2 just like field synchronization.
            await session.execute(
                text(
                    "CREATE TEMP TABLE projection_depth_spoof"
                    " (org_id uuid,field_id uuid,token text) ON COMMIT DROP"
                )
            )
            await session.execute(
                text(
                    "CREATE FUNCTION pg_temp.projection_depth_spoof() RETURNS trigger"
                    " LANGUAGE plpgsql AS $$ BEGIN"
                    " IF pg_trigger_depth()<>1 THEN"
                    " RAISE EXCEPTION 'Unexpected fixture trigger depth' USING ERRCODE='XX000';"
                    " END IF; " + mutation + "; RETURN NEW; END $$"
                )
            )
            await session.execute(
                text(
                    "CREATE TRIGGER projection_depth_spoof AFTER INSERT"
                    " ON pg_temp.projection_depth_spoof FOR EACH ROW"
                    " EXECUTE FUNCTION pg_temp.projection_depth_spoof()"
                )
            )
            await session.execute(
                text(
                    "INSERT INTO pg_temp.projection_depth_spoof(org_id,field_id,token)"
                    " VALUES(:org,:field,:token)"
                ),
                {"org": org, "field": UUID(field["id"]), "token": token},
            )
    assert failure.value.orig.sqlstate == "23514"
    assert await projected_tokens(application, org, field["id"]) == before


async def test_search_projection_insert_label_refresh_cas_and_parent_delete(
    api, headers, application, tenants, admin_engine
):
    field = await create_field(
        api, headers[0], "projection_bank", label="Oldlexeme repeated repeated"
    )
    org = tenants["orgs"][0]
    before = await projected_tokens(application, org, field["id"])
    assert before == {"projection", "bank", "oldlexeme", "repeated"}
    changed = await api.post(
        f"/confidential-fields/{field['id']}/revisions",
        headers=headers[0],
        json={"expected_revision": 1, "label": "Newlexeme repeated repeated"},
    )
    assert changed.status_code == 200, changed.text
    after = await projected_tokens(application, org, field["id"])
    assert after == {"projection", "bank", "newlexeme", "repeated"}
    stale = await api.post(
        f"/confidential-fields/{field['id']}/revisions",
        headers=headers[0],
        json={"expected_revision": 1, "label": "Rejectedlexeme"},
    )
    assert stale.status_code == 409
    assert stale.json()["data"]["error"]["code"] == "revision_conflict"
    stale_value = await api.post(
        f"{FIELDS}/{field['id']}/values", headers=headers[0], json=checked(revision=1)
    )
    assert stale_value.status_code == 409
    assert await projected_tokens(application, org, field["id"]) == after
    for query, expected in (("oldlex", []), ("newlex", [field["id"]]), ("rejectedlex", [])):
        page = assert_page(
            await api.post(FIELDS + "/query", headers=headers[0], json={"q": query}), headers[0]
        )
        assert [row["id"] for row in page["items"]] == expected
    # Owner deletion is a fixture-only operation; runtime field DELETE stays forbidden.
    with admin_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM public.confidential_fields WHERE id=:id"), {"id": UUID(field["id"])}
        )
    assert not await projected_tokens(application, org, field["id"])


async def test_search_projection_prefix_semantics_unicode_and_filters_before_limit(api, headers):
    tasks = [await new_task(api, auth) for auth in headers]
    fields = [
        await create_field(
            api, headers[0], f"unicode_{index:02}", scope="task", label="Straße ΣΊΓΜΑ 中文秘密"
        )
        for index in range(3)
    ]
    target = await create_field(api, headers[0], "unicode_99", label="Straße ΣΊΓΜΑ 中文秘密")
    await create_field(api, headers[0], "unicode_other", label="Straße unrelated")
    await create_field(api, headers[1], "unicode_99", label="Straße ΣΊΓΜΑ 中文秘密")
    archived = await api.post(
        f"/confidential-fields/{fields[0]['id']}/revisions",
        headers=headers[0],
        json={"expected_revision": 1, "archived": True},
    )
    assert archived.status_code == 200, archived.text
    for query in ("STRASS ΣΊΓ 中文秘", "中文秘 strass σίγ", "unicode_99 strass"):
        page = assert_page(
            await api.post(VALUES + "/query", headers=headers[0], json={"q": query, "limit": 1}),
            headers[0],
            limit=1,
        )
        assert [row["field_id"] for row in page["items"]] == [target["id"]]
        assert page["items"][0]["task_id"] is None and not page["data"]["has_more"]
    for path, id_key in ((FIELDS, "id"), (VALUES, "field_id")):
        query = {"q": "strass σίγ 中文秘", "task_id": tasks[0], "limit": 1}
        ids = []
        while True:
            page = assert_page(
                await api.post(path + "/query", headers=headers[0], json=query), headers[0], limit=1
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
                headers=headers[0],
                json={"q": "strass σίγ", "field_id": target["id"], "limit": 1},
            ),
            headers[0],
            limit=1,
        )
        assert [row[id_key] for row in exact["items"]] == [target["id"]]
        miss = assert_page(
            await api.post(
                path + "/query",
                headers=headers[0],
                json={"q": "strass absent", "task_id": tasks[0]},
            ),
            headers[0],
        )
        assert miss["items"] == []
        denied = await api.post(
            path + "/query", headers=headers[0], json={"q": "strass", "task_id": tasks[1]}
        )
        assert denied.status_code == 404


@pytest.fixture
def prefix_batch_fields(admin_engine, tenants):
    """Token order opposes page order; eligible rows begin after two full batches."""
    fixtures = []
    with admin_engine.begin() as connection:
        for org, actor in zip(tenants["orgs"], tenants["users"], strict=True):
            connection.execute(
                text("SELECT set_config('app.current_org',:org,true)"), {"org": str(org)}
            )
            base = uuid4().int & ~0xFFFF
            rows = [
                {
                    "id": UUID(int=base + index + 1),
                    "org": org,
                    "actor": actor,
                    "key": f"batch_{319 - index:04d}",
                    "label": "Batchsame batchsameextra batchsameother"
                    + (" markeraccept" if index >= 310 else ""),
                    "scope": "task" if index < 310 else "org",
                    "archived": index < 300,
                }
                for index in range(320)
            ]
            connection.execute(
                text(
                    "INSERT INTO public.confidential_fields"
                    " (id,org_id,created_by,key,label,kind,scope,archived)"
                    " VALUES(:id,:org,:actor,:key,:label,'other',:scope,:archived)"
                ),
                rows,
            )
            # Let real field triggers populate the projection; never forge tokens.
            matching = connection.execute(
                text(
                    "SELECT token,field_id FROM public.confidential_field_search_tokens"
                    " WHERE org_id=:org AND token >= 'batchsame' AND token < 'batchsamf'"
                    " ORDER BY token,field_id"
                ),
                {"org": org},
            ).all()
            assert len(matching) == 320 * 3
            target_offset = next(
                index for index, match in enumerate(matching) if match.field_id == rows[-1]["id"]
            )
            assert target_offset == 319
            fixtures.append(
                {
                    "rows": rows,
                    "matching_tokens": len(matching),
                    "target_token_offset": target_offset,
                }
            )
    return fixtures


@pytest.mark.parametrize("path,id_key", [(FIELDS, "id"), (VALUES, "field_id")])
async def test_prefix_batches_preserve_all_matches_filters_and_keyset_pages(
    path, id_key, api, headers, tenants, prefix_batch_fields, tmp_path
):
    task = await new_task(api, headers[0])
    records = prefix_batch_fields[0]["rows"]
    ordered = sorted(records, key=lambda row: (row["key"], row["id"]))
    active = [row for row in ordered if not row["archived"]]
    org_active = [row for row in active if row["scope"] == "org"]
    receipt = {
        "path": path,
        "org_ids": [str(org) for org in tenants["orgs"]],
        "fields_per_org": len(records),
        "matching_tokens_per_org": prefix_batch_fields[0]["matching_tokens"],
        "late_target_token_offset": prefix_batch_fields[0]["target_token_offset"],
        "pages": [],
        "exact_queries": [],
    }
    cases = [
        ({"q": "batchsame", "archived": True, "task_id": task}, ordered),
        ({"q": "batchsame", "task_id": task}, active),
        ({"q": "batchsame"}, active if path == FIELDS else org_active),
        (
            {"q": "batchsame markeraccept", "archived": True, "task_id": task},
            org_active,
        ),
    ]
    for filters, expected in cases:
        body = {**filters, "limit": 37}
        observed = []
        cursors = set()
        while True:
            page = assert_page(
                await api.post(path + "/query", headers=headers[0], json=body),
                headers[0],
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
        (prefix_batch_fields[1]["rows"][-1], {}, []),
        ({"id": uuid4()}, {}, []),
    ):
        body = {"q": "batchsame", "field_id": str(record["id"]), "limit": 1, **extra}
        page = assert_page(
            await api.post(path + "/query", headers=headers[0], json=body), headers[0], limit=1
        )
        identifiers = [row[id_key] for row in page["items"]]
        assert identifiers == [str(row["id"]) for row in expected]
        assert not page["data"]["has_more"]
        receipt["exact_queries"].append({"filters": body, "ids": identifiers})
    receipt["status"] = "passed"
    (tmp_path / "confidential-prefix-batches.json").write_text(json.dumps(receipt, indent=2) + "\n")


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
