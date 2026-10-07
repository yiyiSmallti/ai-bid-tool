"""Real PostgreSQL/storage attachment failure and isolation acceptance.

Failure inventory: table grants/RLS missing; same-org wrong root/file/profile/review
parents leaking business errors before FK/CHECK rejection; direct SQL bypassing
human decisions; immutable rows/deactivation revived; unbounded storage reads;
missing/corrupted ciphertext returning substituted content; storage/DB commit
failure publishing a receipt or success audit; recovery deleting retained objects.
"""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.core.errors import ServiceError
from app.services.auth import ROLE_SCOPES, Identity, set_actor_context
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_attachment_archive import (
    BASE,
    archive_fixture,
    artifact,
    result,
    source,
    upload,
    upload_body,
)
from test_attachment_archive import (
    api as api,  # noqa: F401 -- register the local Result 4.0 fixture
)

TABLES = (
    "attachment_archives",
    "attachment_revisions",
    "attachment_files",
    "attachment_file_parts",
    "attachment_reviews",
    "profile_attachment_links",
    "task_attachments",
    "attachment_privacy_holds",
)
IMMUTABLE = set(TABLES) - {"attachment_archives", "profile_attachment_links", "task_attachments"}


async def test_all_attachment_tables_rls_runtime_grants_and_append_only(
    api, headers, tenants, pdf_bytes, application, admin_engine
):
    chain = await archive_fixture(api, headers[0], tenants["users"][0], pdf_bytes)
    archived = result(await source(api, headers[0], chain))["source"]
    held = result(
        await api.post(
            f"/attachment-sources/{archived['id']}/privacy",
            headers=headers[0],
            json={
                "mode": "needs_redaction",
                "request_id": str(uuid4()),
                "reviewed_source_png_sha256": archived["source_png_sha256"],
                "expected_hold_id": None,
            },
        )
    )
    with admin_engine.connect() as connection:
        properties = (
            connection.execute(
                text(
                    "SELECT relname,relrowsecurity,relforcerowsecurity,"
                    "has_table_privilege('bid_app',oid,'DELETE') AS can_delete,"
                    "has_table_privilege('bid_app',oid,'UPDATE') AS can_update FROM pg_class WHERE relname=ANY(:tables)"
                ),
                {"tables": list(TABLES)},
            )
            .mappings()
            .all()
        )
        assert len(properties) == len(TABLES)
        for row in properties:
            assert row["relrowsecurity"] and row["relforcerowsecurity"] and not row["can_delete"]
            assert row["can_update"] is (row["relname"] not in IMMUTABLE)
    for org in (None, tenants["orgs"][1]):
        async with application.state.db.transaction(org) as session:
            for table in TABLES:
                assert await session.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    actor = Identity(tenants["users"][0], tenants["orgs"][0], set(ROLE_SCOPES["admin"]), "admin")
    for table in TABLES:
        with pytest.raises(DBAPIError) as rejected:
            async with application.state.db.transaction(tenants["orgs"][0]) as session:
                await set_actor_context(session, actor)
                await session.execute(text(f"DELETE FROM {table}"))
        assert rejected.value.orig.sqlstate in {"42501", "23514"}
    for table in IMMUTABLE:
        with pytest.raises(DBAPIError) as rejected:
            async with application.state.db.transaction(tenants["orgs"][0]) as session:
                await set_actor_context(session, actor)
                await session.execute(text(f"UPDATE {table} SET created_at=created_at"))
        assert rejected.value.orig.sqlstate in {"42501", "23514"}
    assert held["eligible_for_draft_export"] is False
    artifact(
        "rls-grants",
        {
            "scenario": "runtime-bid-app-rls-and-no-delete",
            "tables": [dict(row) for row in properties],
            "expected_gate": "42501-or-23514",
        },
    )


@pytest.mark.parametrize(
    "change,state",
    [
        ("foreign_org", "42501"),
        ("no_context", "42501"),
        ("foreign_actor", "23503"),
        ("wrong_file", "23503"),
        ("wrong_root", "23503"),
        ("bad_hash", "23514"),
        ("bad_decision", "23514"),
        ("no_actor", "42501"),
        ("token_actor", "42501"),
    ],
)
async def test_review_fk_check_before_human_gate(
    change, state, api, headers, tenants, pdf_bytes, application
):
    from test_attachment_archive import revision

    written = result(await upload(api, headers[0], tenants["users"][0], pdf_bytes))
    fixed = await revision(api, headers[0], written["revision_id"])
    org = tenants["orgs"][0]
    values = {
        "id": uuid4(),
        "org": org,
        "root": UUID(written["attachment_id"]),
        "revision": UUID(written["revision_id"]),
        "file": UUID(written["file_id"]),
        "hash": fixed["original_sha256"],
        "metadata": fixed["metadata_sha256"],
        "reviewer": tenants["users"][0],
        "decision": "approve",
        "reason": "accepted_for_internal_use",
        "time": datetime.now(UTC),
        "request": uuid4(),
        "payload": "a" * 64,
    }
    if change == "foreign_org":
        values["org"] = tenants["orgs"][1]
    elif change == "foreign_actor":
        values["reviewer"] = tenants["users"][1]
    elif change == "wrong_file":
        values["file"] = uuid4()
    elif change == "wrong_root":
        values["root"] = uuid4()
    elif change == "bad_hash":
        values["hash"] = "invalid-hash"
    elif change == "bad_decision":
        values["decision"] = "revoke"
    actor = Identity(
        tenants["users"][0],
        org,
        set(ROLE_SCOPES["admin"]),
        "admin",
        actor_kind="token" if change == "token_actor" else "session",
    )
    with pytest.raises(DBAPIError) as rejected:
        async with application.state.db.transaction(
            None if change == "no_context" else org
        ) as session:
            if change not in {"no_actor", "no_context"}:
                await set_actor_context(session, actor)
            await session.execute(
                text(
                    "INSERT INTO attachment_reviews(id,org_id,attachment_id,attachment_revision_id,file_id,"
                    "original_sha256,metadata_sha256,decision,reason,reviewed_by,reviewed_at,request_id,payload_hash)"
                    " VALUES(:id,:org,:root,:revision,:file,:hash,:metadata,:decision,:reason,:reviewer,:time,:request,:payload)"
                ),
                values,
            )
    assert rejected.value.orig.sqlstate == state


@pytest.mark.parametrize(
    "scope",
    [
        "attachment:write",
        "attachment:review",
        "attachment:manage",
        "attachment:original:read",
        "attachment:page:read",
        "attachment:privacy",
        "task:attachment",
    ],
)
async def test_raw_sql_token_scope_constraint(scope, application, tenants):
    with pytest.raises(DBAPIError) as rejected:
        async with application.state.db.transaction(tenants["orgs"][0]) as session:
            await session.execute(
                text(
                    "INSERT INTO api_tokens(id,org_id,user_id,name,digest,scopes,expires_at,revoked)"
                    " VALUES(:id,:org,:user,'Synthetic forbidden token',:hash,CAST(:scopes AS jsonb),TIMESTAMPTZ '2030-01-01T00:00:00Z',false)"
                ),
                {
                    "id": uuid4(),
                    "org": tenants["orgs"][0],
                    "user": tenants["users"][0],
                    "hash": uuid4().hex * 2,
                    "scopes": json.dumps(["attachment:read", scope]),
                },
            )
    assert rejected.value.orig.sqlstate == "23514"


async def test_bounded_original_and_source_reads_missing_ciphertext_hash_fail_closed(
    api, headers, tenants, pdf_bytes, application, monkeypatch
):
    chain = await archive_fixture(api, headers[0], tenants["users"][0], pdf_bytes)
    archived = result(await source(api, headers[0], chain))["source"]
    original = result(
        await api.get(
            f"{BASE}/revisions/{chain['written']['revision_id']}/file/download-link",
            headers=headers[0],
        )
    )
    raw = result(
        await api.get(
            f"/attachment-sources/{archived['id']}/preview/download-link", headers=headers[0]
        )
    )
    storage = application.state.storage
    bounded = storage.read_bounded
    calls = []

    async def counted(org, key, limit):
        calls.append(
            {
                "org": str(org),
                "key_sha256": hashlib.sha256(key.encode()).hexdigest(),
                "limit": limit,
            }
        )
        return await bounded(org, key, limit)

    async def forbidden(*args, **kwargs):
        raise AssertionError("attachment reads must use authenticated bounded storage")

    monkeypatch.setattr(storage, "read", forbidden)
    monkeypatch.setattr(storage, "read_bounded", counted)
    assert (await api.get(original["url"], headers=headers[0])).content == pdf_bytes
    assert (await api.get(raw["url"], headers=headers[0])).status_code == 200
    assert len(calls) == 2 and calls[0]["limit"] == len(pdf_bytes)
    assert calls[1]["limit"] == archived["preview"]["size_bytes"]
    assert all(0 < item["limit"] <= 40 * 1024 * 1024 for item in calls)
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        key = await session.scalar(
            text("SELECT storage_key FROM attachment_files WHERE id=:file"),
            {"file": UUID(chain["written"]["file_id"])},
        )
    path = storage.path(tenants["orgs"][0], key)
    encrypted = path.read_bytes()
    for corruption in (
        b"not-ciphertext",
        storage.cipher.encrypt(key, b"same identity wrong bytes"),
    ):
        path.write_bytes(corruption)
        failed = await api.get(original["url"], headers=headers[0])
        assert failed.status_code in {409, 422, 500, 502}
        assert (
            "same identity wrong bytes" not in failed.text
            and "synthetic-private-contract" not in failed.text
        )
    path.unlink()
    missing = await api.get(original["url"], headers=headers[0])
    assert missing.status_code in {404, 409, 500, 502}
    path.write_bytes(encrypted)
    assert (await api.get(original["url"], headers=headers[0])).content == pdf_bytes
    artifact(
        "bounded-read-integrity",
        {
            "scenario": "bounded-original-source-and-exact-ciphertext-restore",
            "expected_gate": "integrity-fails-closed",
            "bounded_reads": calls,
            "restored_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        },
    )


async def test_put_failure_retains_ciphertext_without_public_success_then_retry(
    api, headers, tenants, pdf_bytes, application, monkeypatch
):
    storage = application.state.storage
    put = storage.put
    retained = []

    async def fail_after_put(org, key, content):
        await put(org, key, content)
        retained.append((org, key))
        raise ServiceError("storage_unavailable", "Storage publication failed", 503, 3)

    body = upload_body(tenants["users"][0])
    monkeypatch.setattr(storage, "put", fail_after_put)
    failed = await upload(api, headers[0], tenants["users"][0], pdf_bytes, body=body)
    assert failed.status_code == 503 and failed.json()["ok"] is False
    assert retained
    assert (await api.get(BASE, headers=headers[0])).json()["items"] == []
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        assert await session.scalar(text("SELECT count(*) FROM attachment_revisions")) == 0
        assert (
            await session.scalar(
                text("SELECT count(*) FROM audit_logs WHERE action LIKE '%attachment%'")
            )
            == 0
        )
    for org, key in retained:
        ciphertext = storage.path(org, key).read_bytes()
        assert ciphertext.startswith(storage.cipher.marker) and pdf_bytes not in ciphertext
    monkeypatch.setattr(storage, "put", put)
    successful = result(await upload(api, headers[0], tenants["users"][0], pdf_bytes, body=body))
    assert not successful["duplicate"]
    for org, key in retained:
        assert storage.path(org, key).exists()
    artifact(
        "storage-orphan-retry",
        {
            "scenario": "put-succeeded-request-failed-retain-inaccessible-ciphertext",
            "expected_gate": "no-public-revision-or-success-audit",
            "orphan_ids": [hashlib.sha256(key.encode()).hexdigest() for _, key in retained],
            "retry": successful,
        },
    )


async def test_db_rollback_after_put_retains_ciphertext_and_reconciles_ids(
    api, headers, tenants, pdf_bytes, application, monkeypatch
):
    from app.services import attachment_objects, attachments

    storage = application.state.storage
    put = storage.put
    retained = []

    async def counted(org, key, content):
        await put(org, key, content)
        retained.append((org, key))

    monkeypatch.setattr(storage, "put", counted)
    actor = Identity(tenants["users"][0], tenants["orgs"][0], set(ROLE_SCOPES["admin"]), "admin")
    from app.schemas.attachment_contracts import AttachmentCreate, AttachmentUploadBytes

    prepared = None
    staged = []
    with pytest.raises(RuntimeError, match="synthetic rollback"):
        async with application.state.db.transaction(tenants["orgs"][0]) as session:
            await set_actor_context(session, actor)
            session.info["attachment_settings"] = application.state.processor.settings
            session.info["attachment_queue"] = application.state.queue
            prepared = await attachments.upload(
                session,
                actor,
                AttachmentCreate(**upload_body(actor.user_id)),
                [AttachmentUploadBytes(name="synthetic.pdf", content=pdf_bytes)],
                storage,
            )
            staged = list(session.info["attachment_staged_objects"])
            raise RuntimeError("synthetic rollback after publication")
    assert prepared is not None and retained
    assert len(staged) == len(retained)
    assert staged[0]["object_id"] == str(prepared["revision_id"])
    assert staged[0]["key"] == retained[0][1]
    assert staged[0]["sha256"] == hashlib.sha256(pdf_bytes).hexdigest()
    assert (await api.get(BASE, headers=headers[0])).json()["items"] == []
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        assert await session.scalar(text("SELECT count(*) FROM attachment_files")) == 0
        assert (
            await session.scalar(
                text("SELECT count(*) FROM audit_logs WHERE action LIKE '%attachment%'")
            )
            == 0
        )
    for org, key in retained:
        assert storage.path(org, key).exists()
        assert await storage.read_bounded(org, key, len(pdf_bytes)) == pdf_bytes
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        reconciled_orphan = await attachment_objects.reconcile(
            session, tenants["orgs"][0], staged, storage
        )
        assert reconciled_orphan == [
            {
                "object_id": staged[0]["object_id"],
                "sha256": staged[0]["sha256"],
                "status": "retained_orphan",
            }
        ]
        assert await session.scalar(text("SELECT count(*) FROM attachment_files")) == 0
        assert (
            await session.scalar(
                text("SELECT count(*) FROM audit_logs WHERE action LIKE '%attachment%'")
            )
            == 0
        )
        for invalid_records in ([], staged * 101, [{**staged[0], "object_id": str(uuid4())}]):
            with pytest.raises(ServiceError) as invalid:
                await attachment_objects.reconcile(
                    session, tenants["orgs"][0], invalid_records, storage
                )
            assert invalid.value.status == 422
    async with application.state.db.transaction(tenants["orgs"][1]) as session:
        with pytest.raises(ServiceError) as foreign:
            await attachment_objects.reconcile(session, tenants["orgs"][1], staged, storage)
        assert foreign.value.code == "invalid_reconciliation_record"
    committed = result(await upload(api, headers[0], tenants["users"][0], pdf_bytes))
    committed_record = {
        "object_id": committed["revision_id"],
        "key": retained[-1][1],
        "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
    }
    missing_revision, missing_root = uuid4(), uuid4()
    missing_record = {
        "object_id": str(missing_revision),
        "key": f"org/{actor.org_id}/attachment/{missing_root}/{missing_revision}/{staged[0]['sha256']}.pdf",
        "sha256": staged[0]["sha256"],
    }
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        reconciled = await attachment_objects.reconcile(
            session, tenants["orgs"][0], [*staged, committed_record, missing_record], storage
        )
    assert [item["status"] for item in reconciled] == [
        "retained_orphan",
        "committed",
        "unavailable",
    ]
    orphan_path = storage.path(actor.org_id, staged[0]["key"])
    original_ciphertext = orphan_path.read_bytes()
    orphan_path.write_bytes(
        storage.cipher.encrypt(staged[0]["key"], b"synthetic corrupted orphan content")
    )
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        corrupted = await attachment_objects.reconcile(session, tenants["orgs"][0], staged, storage)
    assert corrupted[0]["status"] == "integrity_failure" and orphan_path.exists()
    orphan_path.write_bytes(original_ciphertext)
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        restored = await attachment_objects.reconcile(session, tenants["orgs"][0], staged, storage)
    assert restored == reconciled_orphan and orphan_path.read_bytes() == original_ciphertext
    artifact(
        "database-orphan-reconciliation",
        {
            "scenario": "database-rollback-after-encrypted-put",
            "expected_gate": "unreachable-ciphertext-retained",
            "orphan_ids": [hashlib.sha256(key.encode()).hexdigest() for _, key in retained],
            "rolled_back_revision_id": str(prepared["revision_id"]),
            "reconciled": reconciled,
            "corrupted_orphan": corrupted,
            "restored_orphan": restored,
            "plaintext_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        },
    )


async def test_cross_org_insert_update_and_no_org_context_for_all_tables(
    api, headers, tenants, application, admin_engine, pdf_bytes
):
    from app.models.entities import Base
    from sqlalchemy import insert, select, update

    chain = await archive_fixture(api, headers[0], tenants["users"][0], pdf_bytes)
    archived = result(await source(api, headers[0], chain))["source"]
    result(
        await api.post(
            f"/attachment-sources/{archived['id']}/privacy",
            headers=headers[0],
            json={
                "mode": "needs_redaction",
                "request_id": str(uuid4()),
                "reviewed_source_png_sha256": archived["source_png_sha256"],
                "expected_hold_id": None,
            },
        )
    )
    rows = {}
    with admin_engine.connect() as connection:
        for name in TABLES:
            table = Base.metadata.tables[name]
            stored = (
                connection.execute(
                    select(table).where(table.c.org_id == tenants["orgs"][0]).limit(1)
                )
                .mappings()
                .first()
            )
            if stored is not None:
                rows[name] = dict(stored)
    rows["attachment_file_parts"] = {
        "id": uuid4(),
        "org_id": tenants["orgs"][0],
        "attachment_id": UUID(chain["written"]["attachment_id"]),
        "attachment_revision_id": UUID(chain["written"]["revision_id"]),
        "attachment_file_id": UUID(chain["written"]["file_id"]),
        "ordinal": 1,
        "name": "part.pdf",
        "media_type": "application/pdf",
        "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        "size_bytes": len(pdf_bytes),
        "page_start": 1,
        "page_count": 2,
        "rotation": 0,
        "upload_name_encrypted": "synthetic-encrypted-part-name",
    }
    for context in (tenants["orgs"][0], None):
        for name, source_row in rows.items():
            row = {**source_row, "id": uuid4(), "org_id": tenants["orgs"][1]}
            if "request_id" in row:
                row["request_id"] = uuid4()
            if name == "attachment_file_parts":
                row["storage_key"] = (
                    f"org/{row['org_id']}/attachment/{row['attachment_id']}/{row['attachment_revision_id']}/parts/1/{row['sha256']}.pdf"
                )
            elif name == "attachment_files":
                row["storage_key"] = (
                    f"org/{row['org_id']}/attachment/{row['attachment_id']}/{row['attachment_revision_id']}/{row['file']['sha256']}.pdf"
                )
            with pytest.raises(DBAPIError) as rejected:
                async with application.state.db.transaction(context) as session:
                    await session.execute(insert(Base.metadata.tables[name]).values(**row))
            assert rejected.value.orig.sqlstate == "42501", (name, context)
    async with application.state.db.transaction(tenants["orgs"][1]) as session:
        for name in set(TABLES) - IMMUTABLE:
            updated = await session.execute(
                update(Base.metadata.tables[name])
                .where(Base.metadata.tables[name].c.org_id == tenants["orgs"][0])
                .values(active=False)
            )
            assert updated.rowcount == 0


@pytest.mark.parametrize("fault,state", [("missing_revision", "23503"), ("missing_file", "23514")])
async def test_root_current_pointer_and_committed_revision_completeness(
    fault, state, application, tenants
):
    from app.models.attachments import AttachmentArchive, AttachmentRevision

    org, actor_id, root_id = tenants["orgs"][0], tenants["users"][0], uuid4()
    actor = Identity(actor_id, org, set(ROLE_SCOPES["admin"]), "admin")
    with pytest.raises(DBAPIError) as rejected:
        async with application.state.db.transaction(org) as session:
            await set_actor_context(session, actor)
            session.add(
                AttachmentArchive(
                    id=root_id,
                    org_id=org,
                    current_revision=1,
                    state_version=1,
                    active=True,
                    custodian_user_id=actor_id,
                    reviewer_user_id=actor_id,
                    created_by=actor_id,
                )
            )
            await session.flush()
            if fault == "missing_file":
                session.add(
                    AttachmentRevision(
                        id=uuid4(),
                        org_id=org,
                        attachment_id=root_id,
                        revision=1,
                        kind="contract",
                        label_encrypted="synthetic-encrypted-label",
                        metadata_sha256="a" * 64,
                        created_by=actor_id,
                        request_id=uuid4(),
                        payload_hash="b" * 64,
                    )
                )
    assert rejected.value.orig.sqlstate == state
    async with application.state.db.transaction(org) as session:
        assert await session.scalar(text("SELECT count(*) FROM attachment_archives")) == 0


@pytest.mark.parametrize(
    "table,fault,state",
    [
        ("profile_attachment_links", "profile_revision_id", "23503"),
        ("profile_attachment_links", "file_id", "23503"),
        ("profile_attachment_links", "approval_id", "23503"),
        ("profile_attachment_links", "field", "23514"),
        ("task_attachments", "task_org_profile_id", "23503"),
        ("task_attachments", "task_id", "23503"),
        ("task_attachments", "profile_attachment_link_id", "23503"),
        ("task_attachments", "attachment_revision_id", "23503"),
    ],
)
async def test_exact_redundant_link_and_selection_parents_before_business_guards(
    table, fault, state, api, headers, tenants, application, admin_engine, pdf_bytes
):
    from app.models.entities import Base
    from sqlalchemy import insert, select

    chain = await archive_fixture(api, headers[0], tenants["users"][0], pdf_bytes)
    identifier = (
        chain["link"]["id"] if table == "profile_attachment_links" else chain["selection"]["id"]
    )
    relation = Base.metadata.tables[table]
    with admin_engine.connect() as connection:
        row = dict(
            connection.execute(select(relation).where(relation.c.id == UUID(identifier)))
            .mappings()
            .one()
        )
    row.update(id=uuid4(), request_id=uuid4(), active=False)
    row[fault] = "name" if fault == "field" else uuid4()
    actor = Identity(tenants["users"][0], tenants["orgs"][0], set(ROLE_SCOPES["admin"]), "admin")
    with pytest.raises(DBAPIError) as rejected:
        async with application.state.db.transaction(tenants["orgs"][0]) as session:
            await set_actor_context(session, actor)
            await session.execute(insert(relation).values(**row))
    assert rejected.value.orig.sqlstate == state


@pytest.mark.parametrize(
    "fault,state",
    [
        ("mixed_branch", "23514"),
        ("missing_parent", "23514"),
        ("wrong_selection", "23503"),
        ("wrong_page", "23514"),
    ],
)
async def test_extended_evidence_source_branch_cannot_accept_partial_or_mismatched_lineage(
    fault, state, api, headers, tenants, application, admin_engine, pdf_bytes
):
    from app.models.entities import Base
    from sqlalchemy import insert, select

    chain = await archive_fixture(api, headers[0], tenants["users"][0], pdf_bytes)
    archived = result(await source(api, headers[0], chain))["source"]
    relation = Base.metadata.tables["evidence_sources"]
    with admin_engine.connect() as connection:
        row = dict(
            connection.execute(select(relation).where(relation.c.id == UUID(archived["id"])))
            .mappings()
            .one()
        )
    row["id"] = uuid4()
    row["storage_key"] = (
        f"org/{row['org_id']}/evidence-source/{row['id']}/{row['preview']['sha256']}.png"
    )
    if fault == "mixed_branch":
        row["task_certificate_id"] = uuid4()
    elif fault == "missing_parent":
        row["attachment_file_id"] = None
    elif fault == "wrong_selection":
        row["task_attachment_id"] = uuid4()
    else:
        row["page"] = 201
    actor = Identity(tenants["users"][0], tenants["orgs"][0], set(ROLE_SCOPES["admin"]), "admin")
    with pytest.raises(DBAPIError) as rejected:
        async with application.state.db.transaction(tenants["orgs"][0]) as session:
            await set_actor_context(session, actor)
            await session.execute(insert(relation).values(**row))
    assert rejected.value.orig.sqlstate == state
