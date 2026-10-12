"""Legacy database policy gates remain blocking after collaboration API removal."""

import pytest
from app.core.config import Settings
from app.core.db import Database
from response_review_fixtures import response_scope
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from task_fixtures import actor_context_async


async def test_multi_domain_legacy_disposition_batch_rejected(api, headers, tenants, admin_engine):
    scope = await response_scope(api, headers, tenants, admin_engine, required=True)
    auth = scope["members"]["technical"]["headers"]
    disposed = await api.post(
        f"/tasks/{scope['task_id']}/cards/dispositions",
        headers=auth,
        json={
            "extraction_job_id": str(scope["job_id"]),
            "items": [
                {
                    "requirement_id": str(scope["requirement_ids"][0]),
                    "expected_revision": scope["card"]["revision"],
                    "disposition": "comply_only",
                    "reason": "Cannot bypass",
                }
            ],
        },
    )
    assert disposed.status_code == 409, disposed.text
    assert disposed.json()["data"]["error"]["code"] == "cosign_required"


@pytest.mark.parametrize("target", ["evidence", "confirmed_revision"])
async def test_legacy_multidomain_policy_cannot_directly_confirm_evidence_or_response(
    api, headers, tenants, admin_engine, target
):
    from test_response_cards import PRODUCT_DATA

    scope = await response_scope(api, headers, tenants, admin_engine, required=True)
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
    reviewer = scope["members"]["technical"]["user"]
    db = Database(Settings())
    try:
        with pytest.raises(
            DBAPIError,
            match=(
                "complete current co-sign required"
                if target == "evidence"
                else "response confirmation requires review round"
            ),
        ):
            async with db.transaction(scope["org_id"]) as session:
                await actor_context_async(session, scope["org_id"], reviewer)
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


def revision_count(admin_engine, task_id):
    with admin_engine.connect() as connection:
        return connection.scalar(
            text(
                "SELECT count(*) FROM response_card_revisions v "
                "JOIN response_cards c ON c.org_id=v.org_id AND c.id=v.card_id "
                "WHERE c.task_id=:task"
            ),
            {"task": task_id},
        )


async def test_mixed_disposition_batch_rolls_back_single_domain_item(
    api, headers, tenants, admin_engine
):
    # Index 0 is co-sign technical; index 2 is independent single-domain technical.
    # One professional actor can legitimately approve either primary domain, so
    # the batch cannot pass merely because a different-domain permission fails.
    scope = await response_scope(api, headers, tenants, admin_engine, count=3, required=True)
    single = await api.post(
        f"/tasks/{scope['task_id']}/cards",
        headers=headers[0],
        json={
            "extraction_job_id": str(scope["job_id"]),
            "requirement_id": str(scope["requirement_ids"][2]),
            "content": {
                "response_kind": "commitment",
                "response_text": "Synthetic independent response",
                "deviation": "none",
                "deviation_note": "Synthetic requirement is met",
            },
        },
    )
    assert single.status_code == 200, single.text
    single = single.json()["data"]
    technical = scope["members"]["technical"]["headers"]
    with admin_engine.connect() as connection:
        assert connection.scalar(
            text("SELECT team_cosign_domains(:org,:card)"),
            {"org": scope["org_id"], "card": single["id"]},
        ) == ["technical"]
    before = revision_count(admin_engine, scope["task_id"])
    response = await api.post(
        f"/tasks/{scope['task_id']}/cards/dispositions",
        headers=technical,
        json={
            "extraction_job_id": str(scope["job_id"]),
            "items": [
                {
                    "requirement_id": str(scope["requirement_ids"][2]),
                    "expected_revision": single["revision"],
                    "disposition": "comply_only",
                    "reason": "Synthetic permitted single-domain item",
                },
                {
                    "requirement_id": str(scope["requirement_ids"][0]),
                    "expected_revision": scope["card"]["revision"],
                    "disposition": "comply_only",
                    "reason": "Synthetic unsigned multi-domain item",
                },
            ],
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["data"]["error"]["code"] == "cosign_required"
    assert revision_count(admin_engine, scope["task_id"]) == before
    for initial in (single, scope["card"]):
        shown = await api.get(f"/cards/{initial['id']}", headers=headers[0])
        assert shown.status_code == 200, shown.text
        current = shown.json()["data"]
        assert current["revision"] == initial["revision"]
        assert current["revision_id"] == initial["revision_id"]
        assert current["disposition"] == initial["disposition"]
        assert current["disposition"] != "comply_only"
        assert current["disposition_by"] is None and current["disposition_at"] is None
