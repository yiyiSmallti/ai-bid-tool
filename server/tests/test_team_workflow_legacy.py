"""Legacy API acceptance for the task authorization cutover.

Failure scenarios fixed before implementation: old task/document/download/job routes
leak a same-org nonmember's task; token admin inherits recovery; observer can upload;
archived task accepts document/card/job/budget writes; org-only reads are unchanged.
The fixtures create both orgs and real parent objects so masking isn't merely a
nonexistent-object test. Database execution is owned by the integrating session.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.models.entities import Membership, User
from conftest import PASSWORD, PASSWORD_HASH
from sqlalchemy.orm import Session


async def other_member(api, admin_engine, org_id):
    user_id = uuid4()
    email = f"workflow-{user_id}@example.test"
    with Session(admin_engine) as session, session.begin():
        session.add(User(id=user_id, email=email, password_hash=PASSWORD_HASH))
        session.flush()
        session.add(Membership(org_id=org_id, user_id=user_id, role="bidder"))
    login = await api.post(
        "/auth/login", json={"email": email, "password": PASSWORD, "org_id": str(org_id)}
    )
    assert login.status_code == 200
    return user_id, {
        "Authorization": "Bearer " + login.json()["data"]["session"],
        "X-Org-Id": str(org_id),
    }


async def task_document(api, header, pdf_bytes):
    made = await api.post("/tasks", headers=header, json={"name": "Task access acceptance"})
    assert made.status_code == 200, made.text
    task_id = made.json()["data"]["id"]
    uploaded = await api.post(
        f"/tasks/{task_id}/documents", headers=header, files={"file": ("synthetic.pdf", pdf_bytes)}
    )
    assert uploaded.status_code == 200, uploaded.text
    return task_id, uploaded.json()["data"]["id"]


async def test_old_routes_mask_nonmember_and_cross_org(
    api, headers, tenants, admin_engine, pdf_bytes
):
    task, document = await task_document(api, headers[0], pdf_bytes)
    _, outsider = await other_member(api, admin_engine, tenants["orgs"][0])
    paths = [
        f"/tasks/{task}",
        f"/tasks/{task}/documents",
        f"/tasks/{task}/jobs?kind=parse",
        f"/documents/{document}",
        f"/documents/{document}/download-link",
        f"/documents/{document}/pages/1/preview",
        f"/tasks/{task}/requirements",
    ]
    for header in (outsider, headers[1]):
        for path in paths:
            response = await api.get(path, headers=header)
            assert response.status_code == 404, (path, response.text)
            assert response.json()["data"]["error"]["code"] == "not_found"
    assert (await api.get("/tasks", headers=outsider)).json()["items"] == []


async def test_admin_token_does_not_inherit_task_recovery(
    api, headers, tenants, admin_engine, pdf_bytes
):
    _, bidder = await other_member(api, admin_engine, tenants["orgs"][0])
    task, document = await task_document(api, bidder, pdf_bytes)
    token = await api.post(
        "/tokens",
        headers=headers[0],
        json={
            "name": "workflow token",
            "scopes": ["task:read", "job:read", "card:read"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert token.status_code == 200
    token_header = {**headers[0], "Authorization": "Bearer " + token.json()["data"]["token"]}
    assert (await api.get(f"/documents/{document}", headers=headers[0])).status_code == 200
    assert (await api.get(f"/tasks/{task}", headers=token_header)).status_code == 404


@pytest.mark.parametrize(
    "path,body",
    [
        ("/documents/{document}/parse", {}),
        ("/v4/documents/{document}/extract", {"dry_run": True}),
        (
            "/tasks/{task}/cards",
            {"extraction_job_id": str(uuid4()), "requirement_id": str(uuid4()), "content": {}},
        ),
    ],
)
async def test_legacy_mutation_masking(api, headers, tenants, admin_engine, pdf_bytes, path, body):
    task, document = await task_document(api, headers[0], pdf_bytes)
    _, outsider = await other_member(api, admin_engine, tenants["orgs"][0])
    response = await api.post(
        path.format(task=task, document=document), headers=outsider, json=body
    )
    assert response.status_code == 404, response.text


@pytest.mark.parametrize(
    "suffix",
    [
        "products",
        "features",
        "certificates",
        "certificate-files",
        "profiles",
        "templates",
        "evidence-sources",
        "budget",
        "budget/history",
        "checks",
        "scores",
        "score-rubrics",
        "sandbox-runs",
        "simulated-resources",
        "exports",
        "cards?job=00000000-0000-4000-8000-000000000001",
        "drafts?job=00000000-0000-4000-8000-000000000001",
        "screenshots?job=00000000-0000-4000-8000-000000000001",
        "prototype-decisions?job=00000000-0000-4000-8000-000000000001",
    ],
)
async def test_legacy_task_families_mask_existing_task(api, headers, tenants, admin_engine, suffix):
    task = (await api.post("/tasks", headers=headers[0], json={"name": "Private task"})).json()[
        "data"
    ]["id"]
    _, outsider = await other_member(api, admin_engine, tenants["orgs"][0])
    for header in (outsider, headers[1]):
        response = await api.get(f"/tasks/{task}/{suffix}", headers=header)
        assert response.status_code == 404, (suffix, response.text)
        assert response.json()["data"]["error"]["code"] == "not_found"


async def test_legacy_archive_blocks_new_business_keeps_read(api, headers, pdf_bytes):
    task, document = await task_document(api, headers[0], pdf_bytes)
    workflow = (await api.get(f"/tasks/{task}/workflow", headers=headers[0])).json()["data"][
        "workflow"
    ]
    archived = await api.post(
        f"/tasks/{task}/archive",
        headers=headers[0],
        json={"expected_revision": workflow["revision"], "reason": "Synthetic acceptance"},
    )
    assert archived.status_code == 200, archived.text
    assert (await api.get(f"/documents/{document}", headers=headers[0])).status_code == 200
    writes = [
        await api.post(f"/documents/{document}/parse", headers=headers[0], json={}),
        await api.post(
            f"/tasks/{task}/cards",
            headers=headers[0],
            json={"extraction_job_id": str(uuid4()), "requirement_id": str(uuid4()), "content": {}},
        ),
        await api.post(
            f"/tasks/{task}/documents",
            headers=headers[0],
            files={"file": ("synthetic.pdf", pdf_bytes)},
        ),
        await api.post(
            f"/tasks/{task}/products", headers=headers[0], json={"product_id": str(uuid4())}
        ),
        await api.put(
            f"/v4/tasks/{task}/budget",
            headers=headers[0],
            json={
                "limit": "20",
                "currency": "USD",
                "expected_revision": 1,
                "reason": "Synthetic acceptance",
            },
        ),
    ]
    for response in writes:
        assert response.status_code == 409, response.text
        assert response.json()["data"]["error"]["code"] == "task_archived"


async def test_shared_memory_hides_private_task_provenance(api, headers, tenants, admin_engine):
    task = (await api.post("/tasks", headers=headers[0], json={"name": "Private origin"})).json()[
        "data"
    ]["id"]
    created = await api.post(
        "/memories",
        headers=headers[0],
        json={
            "target": {"scope": "org"},
            "content": {
                "kind": "rule",
                "conflict_key": "synthetic.workflow",
                "text": "Check declared requirement units.",
            },
            "source": {"task_id": task},
        },
    )
    assert created.status_code == 200, created.text
    memory_id = created.json()["data"]["memory"]["id"]
    _, outsider = await other_member(api, admin_engine, tenants["orgs"][0])
    shown = await api.get(f"/memories/{memory_id}", headers=outsider)
    assert shown.status_code == 200, shown.text
    current = shown.json()["data"]["memory"]["current"]
    assert current["content"]["text"] == "Check declared requirement units."
    assert current["source"]["provenance_redacted"] is True
    assert current["source"]["task_id"] is None
    assert task not in shown.text
    assert (await api.get(f"/memories/{memory_id}", headers=headers[1])).status_code == 404
