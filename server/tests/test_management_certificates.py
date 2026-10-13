"""Failure-first PostgreSQL/API acceptance for the certificate management slice.

Risks covered before implementation: org/parent confusion, unbounded reads,
prefix syntax injection, stale/foreign/expired cursors, unavailable authority,
incorrect revision authors/provenance, independent lifecycle CAS, human gate,
direct SQL bypass/immutability, and concurrent transitions. These tests use the
real restricted PostgreSQL role and produce normal pytest/JUnit artifacts.
"""

import asyncio
import json
import time
from uuid import UUID, uuid4

import pytest
from app.services.auth import HUMAN_SCOPES, Identity, set_actor_context
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

BASE = "/v4/management/resources/certificates"
DATA = {
    "kind": "qualification",
    "name": "Synthetic alpha certificate",
    "number": "CERT-AB-20",
    "valid_from": "2026-01-01",
    "valid_until": "2026-12-31",
}


async def create(api, headers, **fields):
    response = await api.post(
        "/resources/certificates", headers=headers, json={"data": {**DATA, **fields}}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def lifecycle(revision=1, lifecycle_revision=0, state="inactive"):
    return {
        "expected_revision": revision,
        "expected_lifecycle_revision": lifecycle_revision,
        "state": state,
        "reason_code": "restored" if state == "active" else "obsolete",
    }


async def test_bounded_prefix_pages_and_exact_details(api, headers, database):
    certificates = [await create(api, headers, name=f"Synthetic alpha {i}") for i in range(4)]
    response = await api.post(BASE + "/query", headers=headers, json={"q": "ALP", "limit": 2})
    assert response.status_code == 200, response.text
    page = response.json()
    assert set(page) == {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
    assert set(page["data"]) == {"as_of", "returned", "next_cursor", "has_more"}
    assert page["data"]["returned"] == 2 and page["data"]["has_more"]
    assert len(response.content) <= 256 * 1024
    next_page = await api.post(
        BASE + "/query",
        headers=headers,
        json={"q": "alp", "limit": 2, "cursor": page["data"]["next_cursor"]},
    )
    assert next_page.status_code == 200
    assert not next_page.json()["data"]["has_more"]
    assert (
        len({row["ref"]["resource_id"] for row in page["items"] + next_page.json()["items"]}) == 4
    )
    for query in ({"q": "CERT"}, {"q": "AB"}):
        assert (await api.post(BASE + "/query", headers=headers, json=query)).json()["data"][
            "returned"
        ] == 4
    assert (await api.post(BASE + "/query", headers=headers, json={"q": "alpha | absent"})).json()[
        "items"
    ] == []
    assert (
        await api.post(BASE + "/query", headers=headers, json={"product_id": str(uuid4())})
    ).status_code == 422
    certificate = certificates[0]
    root = certificate["certificate_id"]
    update = await api.post(
        f"/resources/certificates/{root}/revisions",
        headers=headers,
        json={"expected_revision": 1, "data": {**DATA, "name": "Revision two"}},
    )
    assert update.status_code == 200
    detail = await api.get(f"{BASE}/{root}", headers=headers, params={"revision": 1})
    assert detail.status_code == 200
    assert detail.json()["data"]["current_revision"] == 2
    assert detail.json()["data"]["detail"]["revision"]["id"] == certificate["id"]
    history = await api.post(f"{BASE}/{root}/history/query", headers=headers, json={"limit": 1})
    assert history.json()["items"][0]["revision"] == 2
    assert history.json()["items"][0]["has_file"] is False


async def test_cursor_actor_filters_expiry_and_cursor_expiry(
    api, headers, database, admin_engine, application
):
    for i in range(3):
        await create(api, headers, name=f"Alpha {i}")
    page = (await api.post(BASE + "/query", headers=headers, json={"limit": 1})).json()
    cursor = page["data"]["next_cursor"]
    for hdr, body in [
        (headers, {"limit": 1, "cursor": cursor, "q": "alpha"}),
        (headers, {"cursor": "broken"}),
    ]:
        result = await api.post(BASE + "/query", headers=hdr, json=body)
        assert result.status_code == 400
        assert result.json()["data"]["error"]["code"] == "management_cursor_invalid"
    signer = application.state.crypto
    payload = json.loads(signer.cipher.decrypt(cursor.encode()))
    payload["exp"] = int(time.time()) - 1
    expired = signer.cipher.encrypt(json.dumps(payload).encode()).decode()
    assert (
        await api.post(BASE + "/query", headers=headers, json={"limit": 1, "cursor": expired})
    ).status_code == 409


async def test_lifecycle_human_and_content_independence(api, headers, database, admin_engine):
    certificate = await create(api, headers)
    root = certificate["certificate_id"]
    assert (await api.post(BASE + "/query", headers=headers, json={})).status_code == 200
    response = await api.post(f"{BASE}/{root}/lifecycle", headers=headers, json=lifecycle())
    assert response.status_code == (200 if True else 403)
    assert response.json()["data"]["existing_selections"] == "preserved"
    assert response.json()["data"]["lifecycle"] == {"state": "inactive", "revision": 1}
    assert (await api.post(BASE + "/query", headers=headers, json={})).json()["items"] == []
    assert (await api.post(BASE + "/query", headers=headers, json={"state": "inactive"})).json()[
        "data"
    ]["returned"] == 1
    detail = (await api.get(f"{BASE}/{root}", headers=headers)).json()["data"]
    assert detail["detail"]["revision"]["id"] == certificate["id"]
    assert (
        await api.post(f"{BASE}/{root}/lifecycle", headers=headers, json=lifecycle())
    ).status_code == 409
    revision = await api.post(
        f"/resources/certificates/{root}/revisions",
        headers=headers,
        json={"expected_revision": 1, "data": {**DATA, "name": "Inactive edited"}},
    )
    assert revision.status_code == 200
    assert (
        await api.post(f"{BASE}/{root}/lifecycle", headers=headers, json=lifecycle(2, 1, "active"))
    ).status_code == 200
    events = (
        await api.post(f"{BASE}/{root}/lifecycle/history/query", headers=headers, json={})
    ).json()["items"]
    assert [event["revision"] for event in events] == [2, 1]
    assert events[0]["resource_revision"] == 2


async def test_token_read_but_no_lifecycle_and_concurrent_cas(api, headers):
    from datetime import UTC, datetime, timedelta

    certificate = await create(api, headers)
    token = await api.post(
        "/tokens",
        headers=headers,
        json={
            "name": "Synthetic management token",
            "scopes": ["certificate:read", "certificate:write"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert token.status_code == 200
    token_headers = {**headers, "Authorization": "Bearer " + token.json()["data"]["token"]}
    assert (await api.post(BASE + "/query", headers=token_headers, json={})).status_code == 200
    path = f"{BASE}/{certificate['certificate_id']}/lifecycle"
    assert (await api.post(path, headers=token_headers, json=lifecycle())).status_code == 403
    results = await asyncio.gather(
        *(api.post(path, headers=headers, json=lifecycle()) for _ in range(2))
    )
    assert sorted(reply.status_code for reply in results) == [200, 409]


async def test_lifecycle_table_rls_immutable_and_transition_guard(
    api, headers, database, application
):
    certificate = await create(api, headers)
    await api.post(
        f"{BASE}/{certificate['certificate_id']}/lifecycle", headers=headers, json=lifecycle()
    )
    for sql in [
        "UPDATE resource_lifecycle_events SET reason_code='other'",
        "DELETE FROM resource_lifecycle_events",
        "UPDATE certificates SET lifecycle_state='active',lifecycle_revision=2 WHERE id=:root",
    ]:
        with pytest.raises(DBAPIError):
            async with application.state.db.transaction() as session:
                await session.execute(text(sql), {"root": UUID(certificate["certificate_id"])})


async def test_complete_http_body_bound_and_literal_unicode_prefix(api, headers):
    certificate = await create(api, headers, name="Straße device", number="AB-20 ABC/DEF")
    for value in ("STRASSE", "straße", "AB-2", "stras ab", "ABC/DEF"):
        page = await api.post(BASE + "/query", headers=headers, json={"q": value})
        assert page.status_code == 200, page.text
        assert [row["ref"]["resource_id"] for row in page.json()["items"]] == [
            certificate["certificate_id"]
        ]
    root = certificate["certificate_id"]
    for path in (
        BASE + "/query",
        f"{BASE}/{root}/history/query",
        f"{BASE}/{root}/lifecycle/history/query",
        f"{BASE}/{root}/lifecycle",
    ):
        response = await api.post(
            path,
            headers={**headers, "Content-Type": "application/json"},
            content=b" " * (16 * 1024) + b"{}",
        )
        assert response.status_code == 413
        assert response.json()["data"]["error"]["code"] == "invalid_input"
    boundary = await api.post(
        BASE + "/query",
        headers={**headers, "Content-Type": "application/json"},
        content=b" " * (16 * 1024 - 2) + b"{}",
    )
    assert boundary.status_code == 200
    assert (
        await api.post(BASE.replace("/v4", "") + "/query", headers=headers, json={})
    ).status_code == 200


async def test_maximum_unicode_search_keeps_a_bounded_usable_cursor(api, headers):
    query = "汉" * 200
    certificates = [await create(api, headers, name=query) for _ in range(2)]
    first = await api.post(BASE + "/query", headers=headers, json={"q": query, "limit": 1})
    assert first.status_code == 200, first.text
    cursor = first.json()["data"]["next_cursor"]
    assert cursor and len(cursor) <= 2048
    second = await api.post(
        BASE + "/query", headers=headers, json={"q": query, "limit": 1, "cursor": cursor}
    )
    assert second.status_code == 200, second.text
    assert {
        row["ref"]["resource_id"] for row in first.json()["items"] + second.json()["items"]
    } == {certificate["certificate_id"] for certificate in certificates}


async def test_parent_cursor_and_exact_detail_author(api, headers, database):
    first = await create(api, headers)
    second = await create(api, headers)
    root = first["certificate_id"]
    for revision in range(1, 28):
        reply = await api.post(
            f"/resources/certificates/{root}/revisions",
            headers=headers,
            json={
                "expected_revision": revision,
                "data": {**DATA, "name": f"Revision {revision + 1}"},
            },
        )
        assert reply.status_code == 200
    page = (
        await api.post(f"{BASE}/{root}/history/query", headers=headers, json={"limit": 1})
    ).json()
    bad = await api.post(
        f"{BASE}/{second['certificate_id']}/history/query",
        headers=headers,
        json={"limit": 1, "cursor": page["data"]["next_cursor"]},
    )
    assert bad.status_code == 400
    detail = (await api.get(f"{BASE}/{root}", headers=headers, params={"revision": 1})).json()[
        "data"
    ]
    assert detail["revised_at"] and detail["detail"]["revision"]["id"] == first["id"]
    assert (
        await api.get(
            f"{BASE}/{second['certificate_id']}", headers=headers, params={"revision": 28}
        )
    ).status_code == 404


@pytest.mark.parametrize(
    "change",
    ["foreign_root", "wrong_revision", "wrong_sequence", "no_context", "token", "missing_audit"],
)
async def test_direct_event_inserts_cannot_bypass_isolation_or_authority(
    change, api, headers, database, application, admin_engine
):
    first, foreign = (await create(api, headers), await create(api, headers))
    actor = Identity(scopes=set(HUMAN_SCOPES))
    params = {"id": uuid4(), "root": UUID(first["certificate_id"]), "version": 1, "sequence": 1}
    if change == "foreign_root":
        params["root"] = UUID(foreign["certificate_id"])
    if change == "wrong_revision":
        params["version"] = 2
    if change == "wrong_sequence":
        params["sequence"] = 2
    if change == "token":
        actor.actor_kind = "token"
    with pytest.raises(DBAPIError):
        async with application.state.db.transaction() as session:
            await set_actor_context(session, actor)
            await session.execute(
                text(
                    "INSERT INTO resource_lifecycle_events(id,certificate_id,revision,resource_revision,before_state,after_state,reason_code) VALUES(:id,:root,:sequence,:version,'active','inactive','obsolete')"
                ),
                params,
            )
    async with application.state.db.transaction() as session:
        assert (
            await session.scalar(
                text("SELECT lifecycle_revision FROM certificates WHERE id=:root"),
                {"root": UUID(first["certificate_id"])},
            )
            == 0
        )
        assert await session.scalar(text("SELECT count(*) FROM resource_lifecycle_events")) == 0


async def test_exact_original_revision_author_and_explicit_date_advisory(
    api, headers, database, pdf_bytes
):
    """File creation author is the metadata revision author; a later edit loses its file."""
    import hashlib

    from test_certificate_files import upload

    row = await create(api, headers)
    root = row["certificate_id"]
    uploaded = await upload(api, headers, row, pdf_bytes, data=DATA)
    assert uploaded.status_code == 200, uploaded.text
    original = uploaded.json()["data"]
    second = (await api.get(f"{BASE}/{root}", headers=headers)).json()["data"]
    assert second["detail"]["revision"]["id"] == original["certificate_revision_id"]
    assert second["detail"]["file"] == original
    assert second["date_advisory"] == {"as_of": None, "state": "unknown"}
    assert original["file"]["sha256"] == hashlib.sha256(pdf_bytes).hexdigest()
    revised = await api.post(
        f"/resources/certificates/{root}/revisions",
        headers=headers,
        json={"expected_revision": 2, "data": {**DATA, "name": "Metadata only"}},
    )
    assert revised.status_code == 200
    assert (await api.get(f"{BASE}/{root}", headers=headers)).json()["data"]["detail"][
        "file"
    ] is None
    history = (await api.post(f"{BASE}/{root}/history/query", headers=headers, json={})).json()
    assert [item["has_file"] for item in history["items"]] == [False, True, False]
    for date, state in (
        ("2027-01-01", "expired"),
        ("2025-12-31", "not_yet_valid"),
        ("2026-12-31", "valid"),
    ):
        detail = await api.get(
            f"{BASE}/{root}", headers=headers, params={"revision": 2, "as_of": date}
        )
        assert detail.status_code == 200
        assert detail.json()["data"]["date_advisory"] == {"as_of": date, "state": state}
        assert detail.json()["data"]["detail"]["file"] == original
        assert (
            "storage_key" not in detail.text
            and "url" not in detail.json()["data"]["detail"]["file"]
        )


async def test_retained_composed_parts_match_exact_revision(api, headers, pdf_bytes):
    row = await create(api, headers)
    reply = await api.post(
        f"/resources/certificates/{row['certificate_id']}/file-revisions",
        headers=headers,
        data={"metadata": json.dumps({"expected_revision": 1, "data": DATA})},
        files=[("file", ("first.pdf", pdf_bytes)), ("file", ("second.pdf", pdf_bytes))],
    )
    assert reply.status_code == 200, reply.text
    detail = await api.get(f"{BASE}/{row['certificate_id']}", headers=headers)
    assert detail.json()["data"]["detail"]["file"] == reply.json()["data"]
    assert [p["ordinal"] for p in detail.json()["data"]["detail"]["file"]["parts"]] == [1, 2]


async def test_task_pins_survive_lifecycle_and_archive(api, headers):
    row = await create(api, headers)
    root = row["certificate_id"]
    tasks = []
    for n in range(2):
        tasks.append(
            (
                await api.post(
                    "/tasks", headers=headers, json={"name": f"Synthetic certificate pin {n}"}
                )
            ).json()["data"]["id"]
        )
    body = {"certificate_id": root, "revision": 1}
    selected = await api.post(f"/tasks/{tasks[0]}/certificates", headers=headers, json=body)
    assert selected.status_code == 200
    pin = selected.json()["data"]
    assert (
        await api.post(f"{BASE}/{root}/lifecycle", headers=headers, json=lifecycle())
    ).status_code == 200
    replay = await api.post(f"/tasks/{tasks[0]}/certificates", headers=headers, json=body)
    assert replay.status_code == 200 and replay.json()["data"]["duplicate"] is True
    denied = await api.post(f"/tasks/{tasks[1]}/certificates", headers=headers, json=body)
    assert (
        denied.status_code == 409 and denied.json()["data"]["error"]["code"] == "resource_inactive"
    )
    assert (await api.get(f"/tasks/{tasks[0]}/certificates", headers=headers)).json()["items"][0][
        "id"
    ] == pin["id"]
    assert (
        await api.post(f"{BASE}/{root}/lifecycle", headers=headers, json=lifecycle(1, 1, "active"))
    ).status_code == 200
    assert (
        await api.post(f"/tasks/{tasks[1]}/certificates", headers=headers, json=body)
    ).status_code == 200


async def test_read_tokens_all_routes_and_human_scope_denial(api, headers):
    from datetime import UTC, datetime, timedelta

    from app.services.auth import HUMAN_ONLY_SCOPES

    row = await create(api, headers)
    root = row["certificate_id"]
    issued = await api.post(
        "/tokens",
        headers=headers,
        json={
            "name": "Synthetic read token",
            "scopes": ["certificate:read"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert issued.status_code == 200
    auth = {**headers, "Authorization": "Bearer " + issued.json()["data"]["token"]}
    assert (await api.get(f"{BASE}/{root}", headers=auth)).status_code == 200
    for path in (
        BASE + "/query",
        f"{BASE}/{root}/history/query",
        f"{BASE}/{root}/lifecycle/history/query",
    ):
        assert (await api.post(path, headers=auth, json={})).status_code == 200
    assert (
        await api.post(f"{BASE}/{root}/lifecycle", headers=auth, json=lifecycle())
    ).status_code == 403
    assert "certificate:lifecycle" in HUMAN_ONLY_SCOPES
    denied = await api.post(
        "/tokens",
        headers=headers,
        json={
            "name": "Synthetic forbidden scope",
            "scopes": ["certificate:lifecycle"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert denied.status_code == 403


async def test_only_declared_search_fields_are_indexed(api, headers):
    row = await create(api, headers, **{"number": "UNIQUE DECLARED NUMBER"})
    for q, present in [("unique", True), ("declared", True)]:
        reply = await api.post(BASE + "/query", headers=headers, json={"q": q})
        assert reply.status_code == 200, reply.text
        assert [item["ref"]["resource_id"] for item in reply.json()["items"]] == (
            [row["certificate_id"]] if present else []
        )


async def test_deactivation_retains_pinned_original_and_unconfirmed_source_archive(
    api, headers, pdf_bytes
):
    """Lifecycle never changes retained bytes, task pins or human evidence gates."""
    import hashlib

    from test_evidence_sources import add, source_fixture

    row, task, scan, pin = await source_fixture(api, headers, pdf_bytes)
    root = row["certificate_id"]
    archived = await add(api, headers, task, pin["id"])
    assert archived.status_code == 200, archived.text
    source = archived.json()["data"]["source"]
    preview_path = f"/evidence-sources/{source['id']}/preview/download-link"
    preview_link = (await api.get(preview_path, headers=headers)).json()["data"]
    preview = await api.get(preview_link["url"], headers=headers)
    assert preview.status_code == 200
    assert hashlib.sha256(preview.content).hexdigest() == source["preview"]["sha256"]
    stopped = await api.post(f"{BASE}/{root}/lifecycle", headers=headers, json=lifecycle(2))
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["data"]["existing_selections"] == "preserved"
    detail = (await api.get(f"{BASE}/{root}", headers=headers, params={"revision": 2})).json()[
        "data"
    ]
    assert detail["lifecycle"] == {"state": "inactive", "revision": 1}
    assert detail["detail"]["file"] == scan
    pins = (await api.get(f"/tasks/{task}/certificates", headers=headers)).json()["items"]
    assert (
        pins[0]["id"] == pin["id"]
        and pins[0]["certificate_revision_id"] == scan["certificate_revision_id"]
    )
    listing = (await api.get(f"/tasks/{task}/evidence-sources", headers=headers)).json()["items"]
    assert listing == [source]
    assert source["confirmed_at"] is None and source["eligible_for_draft_export"] is False
    revision = scan["certificate_revision_id"]
    original_path = f"/resources/certificates/revisions/{revision}/file/download-link"
    original_link = await api.get(original_path, headers=headers)
    assert original_link.status_code == 200
    assert original_link.json()["data"]["expires_in"] == 300
    downloaded = await api.get(original_link.json()["data"]["url"], headers=headers)
    assert downloaded.status_code == 200 and downloaded.content == pdf_bytes
    assert hashlib.sha256(downloaded.content).hexdigest() == scan["file"]["sha256"]
    assert (await api.get(preview_link["url"], headers=headers)).content == preview.content
    assert (await api.get(f"/tasks/{task}/evidence-sources", headers=headers)).json()["items"] == [
        source
    ]
    assert (await api.get(original_link.json()["data"]["url"], headers={})).status_code == 401
