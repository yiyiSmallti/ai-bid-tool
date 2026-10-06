"""Database acceptance failure inventory, specified before slice 3 implementation.

Missing/foreign org contexts cannot see or write round/signature/invalidation rows.
Composite parents cannot cross tasks, cards or extractions. Policy edits require an
active human owner/admin, exact monotonic revisions and a reason. Review snapshots
cannot be forged or rebound; rounds and signatures remain append-only. Tokens,
workers, admins and wrong-domain/removed humans cannot sign. Every signer reviews
all exact evidence and warnings, and one person cannot fill both domains. Partial
signatures never confirm evidence or authorize response/disposition consumption.
Finalization must occur atomically with the last signature, under its human actor.
The other required domain may finalize but cannot reject, reopen or request material.
Reopen, policy/input change or loss of any actual signer's authority permanently
retires a round; unrelated membership/assignment changes preserve it. Historical
single-domain decisions remain usable without synthetic signatures. Events contain
only bound IDs. Platform org deactivation/reactivation permanently retires prior
signatures without granting the platform role access to business history. These
tests never start or stop database/runtime services.
"""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.core.db import Database
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_team_cosign_consumers import cosign_scope, signature, submit
from test_team_workflow_assignment_database import context, scope_with_card

TABLES = ("card_review_rounds", "card_review_signatures", "card_review_invalidations")
ARTIFACT = Path(__file__).resolve().parents[2] / "data/work/team-workflow-acceptance/cosign-db.json"


async def test_cosign_tables_force_rls_and_no_mutation_grants(admin_engine):
    with admin_engine.connect() as connection:
        for name in TABLES:
            row = (
                connection.execute(
                    text(
                        "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=to_regclass(:name)"
                    ),
                    {"name": name},
                )
            ).one()
            assert all(row)
            for privilege in ("UPDATE", "DELETE"):
                assert not connection.scalar(
                    text("SELECT has_table_privilege('bid_app',:name,:privilege)"),
                    {"name": name, "privilege": privilege},
                )
            columns = connection.execute(
                text(
                    "SELECT attname,has_column_privilege('bid_app',attrelid,attnum,'UPDATE') "
                    "FROM pg_attribute WHERE attrelid=to_regclass(:name) "
                    "AND attnum>0 AND NOT attisdropped"
                ),
                {"name": name},
            )
            assert [column for column, allowed in columns if allowed] == (
                ["id"] if name == "card_review_rounds" else []
            )
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert not connection.scalar(
                    text("SELECT has_table_privilege('bid_platform_fn',:name,:privilege)"),
                    {"name": name, "privilege": privilege},
                )
            assert connection.scalar(
                text(
                    "SELECT tgenabled='O' AND tgtype=31 FROM pg_trigger "
                    "WHERE tgrelid=to_regclass(:name) AND tgname='team_cosign_history_guard'"
                ),
                {"name": name},
            )
        functions = (
            connection.execute(
                text(
                    "SELECT proname,prosecdef,proconfig,prorettype='trigger'::regtype AS is_trigger, "
                    "has_function_privilege('bid_app',p.oid,'EXECUTE') AS app_execute, "
                    "has_function_privilege('bid_platform_fn',p.oid,'EXECUTE') AS platform_execute, "
                    "EXISTS(SELECT 1 FROM aclexplode(coalesce(proacl,acldefault('f',proowner))) acl "
                    "WHERE acl.grantee=0 AND acl.privilege_type='EXECUTE') AS public_execute "
                    "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE n.nspname='public' AND proname LIKE 'team_cosign_%'"
                )
            )
            .mappings()
            .all()
        )
        assert functions
        for function in functions:
            assert not function["prosecdef"], function["proname"]
            assert function["proconfig"] == ["search_path=pg_catalog"]
            assert function["app_execute"] is not function["is_trigger"]
            assert not function["platform_execute"] and not function["public_execute"]


@pytest.mark.parametrize("table", TABLES)
async def test_cosign_missing_and_foreign_org_reads(api, headers, tenants, admin_engine, table):
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    db = Database(Settings())
    try:
        async with db.transaction(tenants["orgs"][1]) as session:
            assert (
                await session.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE org_id=:org"),
                    {"org": scope["org_id"]},
                )
                == 0
            )
        async with db.engine.connect() as connection:
            await connection.execute(text("SELECT set_config('app.current_org','',true)"))
            assert await connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize("actor_kind", ["token", "worker", "agent"])
async def test_cosign_policy_rejects_nonhuman(api, headers, tenants, admin_engine, actor_kind):
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError):
            async with db.transaction(scope["org_id"]) as session:
                await context(session, scope["user_id"], kind=actor_kind)
                await session.execute(
                    text(
                        "INSERT INTO requirement_workflows(org_id,task_id,extraction_job_id,"
                        "requirement_id,co_sign_required,policy_revision,policy_reason_ciphertext,"
                        "policy_changed_by_user_id) VALUES(:org,:task,:job,:req,true,1,'encrypted',:actor)"
                    ),
                    {
                        "org": scope["org_id"],
                        "task": scope["task_id"],
                        "job": scope["job_id"],
                        "req": scope["requirement_ids"][0],
                        "actor": scope["user_id"],
                    },
                )
    finally:
        await db.engine.dispose()


async def test_cosign_helpers_reject_missing_card_and_round(api, headers, tenants, admin_engine):
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    db = Database(Settings())
    try:
        async with db.transaction(scope["org_id"]) as session:
            for function in (
                "team_cosign_round_valid",
                "team_cosign_complete",
                "team_cosign_card_approved",
            ):
                assert (
                    await session.scalar(
                        text(f"SELECT {function}(:org,gen_random_uuid())"), {"org": scope["org_id"]}
                    )
                    is False
                )
        ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
        ARTIFACT.write_text(json.dumps({"database_helpers": "missing IDs deny", "tables": TABLES}))
    finally:
        await db.engine.dispose()


async def review_scope(api, headers, tenants, admin_engine):
    scope = await cosign_scope(api, headers, tenants, admin_engine)
    await submit(api, headers[0], scope)
    listing = await api.get(f"/cards/{scope['card']['id']}/signoffs", headers=headers[0])
    assert listing.status_code == 200, listing.text
    scope["round"] = listing.json()["data"]["round"]
    return scope


async def insert_signature(session, scope, domain="commercial", **changes):
    from app.core.security import Secrets

    values = {
        "org": scope["org_id"],
        "task": scope["task_id"],
        "card": scope["card"]["id"],
        "round": scope["round"]["id"],
        "domain": domain,
        "signer": scope["members"][domain]["user"],
        "role": "bidder" if domain == "commercial" else "technical",
        "evidence": "[]",
        "warnings": json.dumps(scope["card"]["warning_codes"]),
        "reason": Secrets.for_data(Settings()).encrypt("Synthetic database review"),
        "request": uuid4(),
        **changes,
    }
    return await session.scalar(
        text(
            "INSERT INTO card_review_signatures(org_id,task_id,card_id,round_id,purpose,domain,"
            "signer_user_id,signer_org_role,ordinal,reviewed_evidence_ids,reviewed_warning_codes,"
            "reason_ciphertext,reason_sha256,client_request_id,request_sha256) "
            "VALUES(:org,:task,:card,:round,'response',:domain,:signer,:role,1,CAST(:evidence AS jsonb),"
            "CAST(:warnings AS jsonb),:reason,repeat('a',64),:request,repeat('b',64)) RETURNING id"
        ),
        values,
    )


@pytest.mark.parametrize(
    "failure",
    [
        "token",
        "worker",
        "agent",
        "admin",
        "foreign_card",
        "foreign_task",
        "wrong_domain",
        "extra_evidence",
        "extra_warning",
    ],
)
async def test_direct_signature_gate_rejects_bypass(api, headers, tenants, admin_engine, failure):
    scope = await review_scope(api, headers, tenants, admin_engine)
    user = scope["members"]["commercial"]["user"]
    role, kind, changes = "bidder", "session", {}
    if failure in {"token", "worker", "agent"}:
        kind = failure
    elif failure == "admin":
        user, role = scope["user_id"], "admin"
        changes["signer"] = user
    elif failure == "foreign_card":
        other = await scope_with_card(api, headers, tenants, admin_engine)
        changes["card"] = other["card_id"]
    elif failure == "foreign_task":
        other = await scope_with_card(api, headers, tenants, admin_engine)
        changes["task"] = other["task_id"]
    elif failure == "wrong_domain":
        changes["domain"] = "technical"
    elif failure == "extra_evidence":
        changes["evidence"] = json.dumps([str(uuid4())])
    else:
        changes["warnings"] = json.dumps([*scope["card"]["warning_codes"], "invented_warning"])
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError):
            async with db.transaction(scope["org_id"]) as session:
                await context(session, user, role=role, kind=kind)
                await insert_signature(session, scope, **changes)
    finally:
        await db.engine.dispose()


async def test_partial_signature_and_last_signature_atomicity(api, headers, tenants, admin_engine):
    scope = await review_scope(api, headers, tenants, admin_engine)
    db = Database(Settings())
    try:
        async with db.transaction(scope["org_id"]) as session:
            await context(session, scope["members"]["commercial"]["user"], role="bidder")
            await insert_signature(session, scope)
        async with db.transaction(scope["org_id"]) as session:
            assert not await session.scalar(
                text("SELECT team_cosign_card_approved(:org,:card)"),
                {"org": scope["org_id"], "card": scope["card"]["id"]},
            )
            assert (
                await session.scalar(
                    text("SELECT revision FROM response_cards WHERE id=:card"),
                    {"card": scope["card"]["id"]},
                )
                == scope["card"]["revision"]
            )
        with pytest.raises(DBAPIError):
            async with db.transaction(scope["org_id"]) as session:
                await context(session, scope["members"]["technical"]["user"], role="technical")
                await insert_signature(session, scope, "technical")
        # Deferred finalization failure rolls back the whole last-signature transaction.
        async with db.transaction(scope["org_id"]) as session:
            assert (
                await session.scalar(
                    text("SELECT count(*) FROM card_review_signatures WHERE round_id=:round"),
                    {"round": scope["round"]["id"]},
                )
                == 1
            )
    finally:
        await db.engine.dispose()


async def test_authority_revocation_is_permanent_but_unrelated_membership_is_not(
    api, headers, tenants, admin_engine
):
    scope = await review_scope(api, headers, tenants, admin_engine)
    result = await signature(api, scope, "commercial")
    assert result.status_code == 200, result.text
    user = scope["members"]["commercial"]["user"]
    other_user = scope["members"]["technical"]["user"]
    with Session(admin_engine) as session, session.begin():
        session.execute(
            text("SELECT set_config('app.current_org',:org,true)"), {"org": str(scope["org_id"])}
        )
        session.execute(
            text("UPDATE memberships SET active=false WHERE org_id=:org AND user_id=:user"),
            {"org": scope["org_id"], "user": other_user},
        )
        assert (
            session.scalar(
                text("SELECT team_cosign_round_valid(:org,:round)"),
                {"org": scope["org_id"], "round": scope["round"]["id"]},
            )
            is True
        )
        session.execute(
            text("UPDATE memberships SET active=true WHERE org_id=:org AND user_id=:user"),
            {"org": scope["org_id"], "user": other_user},
        )
    with Session(admin_engine) as session, session.begin():
        session.execute(
            text("SELECT set_config('app.current_org',:org,true)"), {"org": str(scope["org_id"])}
        )
        session.execute(
            text("UPDATE memberships SET active=false WHERE org_id=:org AND user_id=:user"),
            {"org": scope["org_id"], "user": user},
        )
    with Session(admin_engine) as session, session.begin():
        session.execute(
            text("SELECT set_config('app.current_org',:org,true)"), {"org": str(scope["org_id"])}
        )
        session.execute(
            text("UPDATE memberships SET active=true WHERE org_id=:org AND user_id=:user"),
            {"org": scope["org_id"], "user": user},
        )
    db = Database(Settings())
    try:
        async with db.transaction(scope["org_id"]) as session:
            assert not await session.scalar(
                text("SELECT team_cosign_round_valid(:org,:round)"),
                {"org": scope["org_id"], "round": scope["round"]["id"]},
            )
            assert (
                await session.scalar(
                    text(
                        "SELECT count(*) FROM card_review_invalidations WHERE round_id=:round AND cause='authority_lost'"
                    ),
                    {"round": scope["round"]["id"]},
                )
                >= 1
            )
        for table in TABLES:
            async with db.transaction(scope["org_id"]) as session:
                original = await session.scalar(
                    text(f"SELECT to_jsonb(row) FROM {table} row LIMIT 1")
                )
            assert original is not None
            forged = {**original, "id": str(uuid4()), "org_id": str(tenants["orgs"][1])}
            with pytest.raises(DBAPIError):
                async with db.transaction(scope["org_id"]) as session:
                    await context(session, scope["user_id"])
                    await session.execute(
                        text(
                            f"INSERT INTO {table} SELECT (jsonb_populate_record(NULL::{table},CAST(:record AS jsonb))).*"
                        ),
                        {"record": json.dumps(forged)},
                    )
            for command in (f"DELETE FROM {table}", f"UPDATE {table} SET id=gen_random_uuid()"):
                with pytest.raises(DBAPIError):
                    async with db.transaction(scope["org_id"]) as session:
                        await context(session, scope["user_id"])
                        await session.execute(text(command))
        for org in (tenants["orgs"][1], None):
            async with db.engine.begin() as connection:
                await connection.execute(
                    text("SELECT set_config('app.current_org',:org,true)"),
                    {"org": str(org) if org else ""},
                )
                for table in TABLES:
                    assert await connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize("mutation_context", ["foreign_org", "missing_org"])
async def test_global_user_reactivation_does_not_resurrect_other_org_signature(
    api, headers, tenants, admin_engine, mutation_context
):
    scope = await review_scope(api, headers, tenants, admin_engine)
    result = await signature(api, scope, "commercial")
    assert result.status_code == 200, result.text
    signer = scope["members"]["commercial"]["user"]
    for active in (False, True):
        with Session(admin_engine) as session, session.begin():
            session.execute(
                text("SELECT set_config('app.current_org',:org,true)"),
                {"org": str(tenants["orgs"][1]) if mutation_context == "foreign_org" else ""},
            )
            session.execute(
                text("UPDATE users SET active=:active WHERE id=:user"),
                {"active": active, "user": signer},
            )
    db = Database(Settings())
    try:
        async with db.transaction(scope["org_id"]) as session:
            assert not await session.scalar(
                text("SELECT team_cosign_round_valid(:org,:round)"),
                {"org": scope["org_id"], "round": scope["round"]["id"]},
            )
            assert (
                await session.scalar(
                    text(
                        "SELECT u.review_authority_epoch>sig.signer_user_epoch FROM users u JOIN card_review_signatures sig ON sig.signer_user_id=u.id WHERE sig.round_id=:round"
                    ),
                    {"round": scope["round"]["id"]},
                )
                is True
            )
    finally:
        await db.engine.dispose()


async def test_platform_org_reactivation_does_not_resurrect_signature(
    api, headers, tenants, admin_engine
):
    scope = await review_scope(api, headers, tenants, admin_engine)
    response = await signature(api, scope, "commercial")
    assert response.status_code == 200, response.text
    response = await signature(api, scope, "technical")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["summary"]["status"] == "complete"
    db = Database(Settings())
    try:
        for active in (False, True):
            async with db.transaction(scope["org_id"]) as session:
                assert await session.scalar(
                    text("SELECT platform_set_org_active(:org,:active)"),
                    {"org": scope["org_id"], "active": active},
                )
            async with db.transaction(scope["org_id"]) as session:
                assert not await session.scalar(
                    text("SELECT team_cosign_round_valid(:org,:round)"),
                    {"org": scope["org_id"], "round": scope["round"]["id"]},
                )
                assert not await session.scalar(
                    text("SELECT team_cosign_card_approved(:org,:card)"),
                    {"org": scope["org_id"], "card": scope["card"]["id"]},
                )
                assert await session.scalar(
                    text(
                        "SELECT bool_and(o.review_authority_epoch>sig.signer_org_epoch) "
                        "FROM orgs o JOIN card_review_signatures sig ON sig.org_id=o.id "
                        "WHERE sig.round_id=:round"
                    ),
                    {"round": scope["round"]["id"]},
                )
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize("state", ["rejected", "needs_material", "draft"])
async def test_secondary_signer_cannot_take_primary_domain_actions(
    api, headers, tenants, admin_engine, state
):
    scope = await review_scope(api, headers, tenants, admin_engine)
    signed = await signature(api, scope, "commercial")
    assert signed.status_code == 200, signed.text
    if state == "draft":
        signed = await signature(api, scope, "technical")
        assert signed.status_code == 200, signed.text
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError, match="task review domain not granted"):
            async with db.transaction(scope["org_id"]) as session:
                user = scope["members"]["commercial"]["user"]
                await context(session, user, role="bidder")
                await session.execute(
                    text("""
                    INSERT INTO response_card_revisions SELECT (jsonb_populate_record(NULL::response_card_revisions,
                      to_jsonb(v)||jsonb_build_object('id',gen_random_uuid(),'revision',v.revision+1,
                       'state',CAST(:state AS text),'confirmed_by',NULL,'confirmed_at',NULL,
                       'actor_user_id',CAST(:user AS uuid),'actor_token_id',NULL,
                       'actor_kind','session','origin','human',
                       'reason','Synthetic secondary-domain primary-action probe'))).* FROM response_card_revisions v
                      JOIN response_cards c ON (c.org_id,c.current_revision_id)=(v.org_id,v.id)
                      WHERE c.id=:card
                    """),
                    {"state": state, "user": user, "card": scope["card"]["id"]},
                )
    finally:
        await db.engine.dispose()


async def test_round_pointer_does_not_retire_new_round(api, headers, tenants, admin_engine):
    scope = await review_scope(api, headers, tenants, admin_engine)
    db = Database(Settings())
    try:
        async with db.transaction(scope["org_id"]) as session:
            # Runtime finalization locks immutable history without mutation authority.
            assert (
                await session.scalar(
                    text("SELECT id::text FROM card_review_rounds WHERE id=:round FOR UPDATE"),
                    {"round": scope["round"]["id"]},
                )
                == scope["round"]["id"]
            )
            assert (
                await session.scalar(
                    text("SELECT team_cosign_round_valid(:org,:round)"),
                    {"org": scope["org_id"], "round": scope["round"]["id"]},
                )
                is True
            )
            assert (
                await session.scalar(
                    text(
                        "SELECT current_round_id::text FROM requirement_workflows WHERE requirement_id=:requirement"
                    ),
                    {"requirement": scope["requirement_ids"][0]},
                )
                == scope["round"]["id"]
            )
            assert (
                await session.scalar(
                    text("SELECT count(*) FROM card_review_invalidations WHERE round_id=:round"),
                    {"round": scope["round"]["id"]},
                )
                == 0
            )
        with pytest.raises(DBAPIError, match="co-sign history is immutable"):
            async with db.transaction(scope["org_id"]) as session:
                await session.execute(
                    text("UPDATE card_review_rounds SET id=id WHERE id=:round"),
                    {"round": scope["round"]["id"]},
                )
    finally:
        await db.engine.dispose()


async def test_invalidation_audit_and_event_roll_back_with_authority_change(
    api, headers, tenants, admin_engine
):
    scope = await review_scope(api, headers, tenants, admin_engine)
    response = await signature(api, scope, "commercial")
    assert response.status_code == 200, response.text
    db = Database(Settings())
    task, round_id = scope["task_id"], scope["round"]["id"]
    try:
        async with db.transaction(scope["org_id"]) as session:
            baseline = await session.scalar(
                text("SELECT last_seq FROM task_event_heads WHERE task_id=:task"), {"task": task}
            )
        with pytest.raises(RuntimeError, match="rollback probe"):
            async with db.transaction(scope["org_id"]) as session:
                await context(session, scope["user_id"])
                await session.execute(
                    text("UPDATE memberships SET active=false WHERE user_id=:user"),
                    {"user": scope["members"]["commercial"]["user"]},
                )
                audit_row = (
                    await session.execute(
                        text(
                            "SELECT actor_user_id,details FROM audit_logs WHERE action='task.review_round_invalidated' AND details->>'round_id'=:round"
                        ),
                        {"round": round_id},
                    )
                ).one()
                assert audit_row.actor_user_id == scope["user_id"]
                assert audit_row.details["cause"] == "authority_lost"
                assert set(audit_row.details) == {
                    "task_id",
                    "card_id",
                    "round_id",
                    "invalidation_id",
                    "cause",
                    "source_id",
                    "actor_user_id",
                    "actor_kind",
                }
                assert (
                    await session.scalar(
                        text("SELECT last_seq FROM task_event_heads WHERE task_id=:task"),
                        {"task": task},
                    )
                    > baseline
                )
                raise RuntimeError("rollback probe")
        async with db.transaction(scope["org_id"]) as session:
            assert (
                await session.scalar(
                    text("SELECT last_seq FROM task_event_heads WHERE task_id=:task"),
                    {"task": task},
                )
                == baseline
            )
            assert (
                await session.scalar(
                    text("SELECT count(*) FROM card_review_invalidations WHERE round_id=:round"),
                    {"round": round_id},
                )
                == 0
            )
            assert (
                await session.scalar(
                    text(
                        "SELECT count(*) FROM audit_logs WHERE action='task.review_round_invalidated' AND details->>'round_id'=:round"
                    ),
                    {"round": round_id},
                )
                == 0
            )
            assert (
                await session.scalar(
                    text("SELECT team_cosign_round_valid(:org,:round)"),
                    {"org": scope["org_id"], "round": round_id},
                )
                is True
            )
    finally:
        await db.engine.dispose()


async def test_system_invalidation_audit_has_no_fabricated_actor_and_cannot_be_forged(
    api, headers, tenants, admin_engine
):
    scope = await review_scope(api, headers, tenants, admin_engine)
    response = await signature(api, scope, "commercial")
    assert response.status_code == 200, response.text
    with Session(admin_engine) as session, session.begin():
        session.execute(
            text(
                "SELECT set_config('app.current_org','',true),set_config('app.actor_user_id','',true),set_config('app.actor_kind','',true)"
            )
        )
        session.execute(
            text("UPDATE users SET active=false WHERE id=:user"),
            {"user": scope["members"]["commercial"]["user"]},
        )
    db = Database(Settings())
    try:
        async with db.transaction(scope["org_id"]) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT actor_user_id,actor_kind,details FROM audit_logs WHERE action='task.review_round_invalidated' AND details->>'round_id'=:round"
                    ),
                    {"round": scope["round"]["id"]},
                )
            ).one()
            assert row.actor_user_id is None and row.actor_kind == "system"
            assert row.details["actor_user_id"] is None and row.details["source_id"] == str(
                scope["members"]["commercial"]["user"]
            )
            details = row.details
        with pytest.raises(DBAPIError, match="invalidation audit requires"):
            async with db.transaction(scope["org_id"]) as session:
                await context(session, scope["user_id"])
                await session.execute(
                    text(
                        "INSERT INTO audit_logs(id,org_id,actor_user_id,actor_kind,action,object_id,details) VALUES(gen_random_uuid(),:org,NULL,'system','task.review_round_invalidated',:task,CAST(:details AS jsonb))"
                    ),
                    {
                        "org": scope["org_id"],
                        "task": scope["task_id"],
                        "details": json.dumps(details),
                    },
                )
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize("target", ["evidence", "confirmed_revision"])
async def test_partial_round_cannot_directly_confirm_evidence_or_response(
    api, headers, tenants, admin_engine, target
):
    from test_response_cards import PRODUCT_DATA

    scope = await cosign_scope(api, headers, tenants, admin_engine)
    product = await api.post("/resources/products", headers=headers[0], json={"data": PRODUCT_DATA})
    assert product.status_code == 200, product.text
    selected = await api.post(
        f"/tasks/{scope['task_id']}/products",
        headers=headers[0],
        json={"product_id": product.json()["data"]["product_id"]},
    )
    assert selected.status_code == 200, selected.text
    updated = await api.put(
        f"/cards/{scope['card']['id']}",
        headers=headers[0],
        json={
            "expected_revision": scope["card"]["revision"],
            "content": {
                "response_kind": "evidence",
                "response_text": "Synthetic model declaration",
                "deviation": "none",
                "deviation_note": "Synthetic declared model comparison",
                "evidence": [
                    {
                        "kind": "product",
                        "selection_id": selected.json()["data"]["id"],
                        "field_path": "model",
                        "quote": PRODUCT_DATA["model"],
                    }
                ],
            },
        },
    )
    assert updated.status_code == 200, updated.text
    scope["card"] = updated.json()["data"]
    await submit(api, headers[0], scope)
    first = await signature(api, scope, "commercial")
    assert first.status_code == 200, first.text
    reviewer = scope["members"]["technical"]["user"]
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError, match="complete current co-sign required"):
            async with db.transaction(scope["org_id"]) as session:
                await context(session, reviewer, role="technical")
                if target == "evidence":
                    await session.execute(
                        text(
                            "UPDATE evidence SET confirmed_by=:user,confirmed_at=clock_timestamp() WHERE card_id=:card"
                        ),
                        {"user": reviewer, "card": scope["card"]["id"]},
                    )
                else:
                    await session.execute(
                        text("""
                        INSERT INTO response_card_revisions SELECT (jsonb_populate_record(NULL::response_card_revisions,
                          to_jsonb(v)||jsonb_build_object('id',gen_random_uuid(),'revision',v.revision+1,
                           'state','confirmed','disposition','respond','disposition_by',CAST(:user AS uuid),
                           'disposition_at',clock_timestamp(),'confirmed_by',CAST(:user AS uuid),
                           'confirmed_at',clock_timestamp(),'actor_user_id',CAST(:user AS uuid),
                           'actor_token_id',NULL,'actor_kind','session','origin','human',
                           'reason','Synthetic exact partial review bypass probe'))).* FROM response_card_revisions v
                          WHERE v.id=:revision
                    """),
                        {"user": reviewer, "revision": scope["card"]["revision_id"]},
                    )
        async with db.transaction(scope["org_id"]) as session:
            assert (
                await session.scalar(
                    text(
                        "SELECT count(*) FROM evidence WHERE card_id=:card AND confirmed_by IS NOT NULL"
                    ),
                    {"card": scope["card"]["id"]},
                )
                == 0
            )
            assert (
                await session.scalar(
                    text("SELECT revision FROM response_cards WHERE id=:card"),
                    {"card": scope["card"]["id"]},
                )
                == scope["card"]["revision"]
            )
    finally:
        await db.engine.dispose()
