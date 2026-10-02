"""G1 org-console read endpoints and recovery behavior.

Failure modes covered before the gate tests below:

* a task, document, or parse job from another org is disclosed instead of returning 404;
* a task-only token can discover jobs, or a job-only token can bypass task authorization;
* the viewer role cannot read the three recovery views granted by the approved contract;
* uploaded but unparsed documents disappear from task recovery;
* parse discovery accepts an unknown public job kind or returns extraction jobs;
* a document filter outside the requested task silently returns an empty list;
* worker completion is not reflected in the discovered parse-job status and result;
* response fields expose storage/queue internals or omit identifiers needed for recovery.
"""

from datetime import UTC, datetime, timedelta

from app.models.entities import Membership
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_api import create_document


async def create_task(api, header, name="Synthetic console task"):
    response = await api.post(
        "/tasks",
        headers=header,
        json={
            "name": name,
            "tender_number": "SYNTHETIC-001",
            "deadline": "2027-01-02T03:04:05+00:00",
            "budget_usd": 123.45,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


async def upload(api, header, task_id, name, content):
    response = await api.post(
        f"/tasks/{task_id}/documents", headers=header, files={"file": (name, content)}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


async def token(api, header, scopes):
    response = await api.post(
        "/tokens",
        headers=header,
        json={
            "name": "Synthetic console recovery",
            "scopes": scopes,
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert response.status_code == 200, response.text
    return {**header, "Authorization": "Bearer " + response.json()["data"]["token"]}


async def test_g1_views_return_stable_recovery_shapes_and_worker_state(
    api, application, headers, pdf_bytes, docx_bytes
):
    task_id = await create_task(api, headers[0])
    parsed_document = await upload(api, headers[0], task_id, "parsed.pdf", pdf_bytes)
    unparsed_document = await upload(api, headers[0], task_id, "unparsed.docx", docx_bytes)

    queued = await api.post(f"/documents/{parsed_document}/parse", headers=headers[0], json={})
    assert queued.status_code == 200, queued.text
    parse_job = queued.json()["data"]["job_id"]
    extraction_preview = await api.post(
        f"/documents/{parsed_document}/extract", headers=headers[0], json={"dry_run": True}
    )
    assert extraction_preview.status_code == 200

    detail = (await api.get(f"/tasks/{task_id}", headers=headers[0])).json()["data"]
    assert set(detail) == {
        "id",
        "org_id",
        "name",
        "tender_number",
        "deadline",
        "budget_usd",
        "created_by",
        "created_at",
        "model_redaction_enabled",
        "model_redaction_revision",
        "model_redaction_by",
    }
    assert detail["id"] == task_id
    assert detail["tender_number"] == "SYNTHETIC-001"
    # The same instant; the database session may render it in its own UTC offset.
    assert datetime.fromisoformat(detail["deadline"]) == datetime.fromisoformat(
        "2027-01-02T03:04:05+00:00"
    )
    assert detail["budget_usd"] == 123.45

    documents = (await api.get(f"/tasks/{task_id}/documents", headers=headers[0])).json()
    assert documents["data"] == {"task_id": task_id}
    assert [item["id"] for item in documents["items"]] == [
        parsed_document,
        unparsed_document,
    ]
    assert all(
        set(item)
        == {
            "id",
            "task_id",
            "name",
            "sha256",
            "media_type",
            "page_count",
            "status",
            "citation_mode",
            "created_at",
        }
        for item in documents["items"]
    )
    assert [item["status"] for item in documents["items"]] == ["uploaded", "uploaded"]
    assert all("storage_key" not in item for item in documents["items"])

    await application.state.processor(headers[0]["X-Org-Id"], parse_job)
    extraction = await api.post(
        f"/documents/{parsed_document}/extract", headers=headers[0], json={}
    )
    assert extraction.status_code == 200
    assert extraction.json()["data"]["job_id"] != parse_job
    jobs = (
        await api.get(
            f"/tasks/{task_id}/jobs",
            headers=headers[0],
            params={"kind": "parse"},
        )
    ).json()
    assert jobs["data"] == {"task_id": task_id, "kind": "parse", "document_id": None}
    assert len(jobs["items"]) == 1
    assert set(jobs["items"][0]) == {
        "id",
        "task_id",
        "document_id",
        "kind",
        "status",
        "result",
        "error",
        "attempts",
        "reasoning",
        "created_at",
        "finished_at",
    }
    assert jobs["items"][0]["id"] == parse_job
    assert jobs["items"][0]["kind"] == "parse"
    assert jobs["items"][0]["status"] == "succeeded"
    assert jobs["items"][0]["result"]["pages"] == 2
    assert "queue_id" not in jobs["items"][0] and "lease_until" not in jobs["items"][0]

    filtered = await api.get(
        f"/tasks/{task_id}/jobs",
        headers=headers[0],
        params={"kind": "parse", "document": parsed_document},
    )
    assert [item["id"] for item in filtered.json()["items"]] == [parse_job]
    wrong_document = await api.get(
        f"/tasks/{task_id}/jobs",
        headers=headers[0],
        params={"kind": "parse", "document": unparsed_document},
    )
    assert wrong_document.status_code == 200 and wrong_document.json()["items"] == []
    assert (await api.get(f"/tasks/{task_id}/jobs", headers=headers[0])).status_code == 422
    assert (
        await api.get(f"/tasks/{task_id}/jobs", headers=headers[0], params={"kind": "extract"})
    ).status_code == 422


async def test_g1_views_hide_every_foreign_task_and_document_filter(api, headers, pdf_bytes):
    own_task, _ = await create_document(api, headers[0], pdf_bytes)
    foreign_task, foreign_document = await create_document(api, headers[1], pdf_bytes)
    foreign_job = (
        await api.post(f"/documents/{foreign_document}/parse", headers=headers[1], json={})
    ).json()["data"]["job_id"]
    assert foreign_job

    for path in (
        f"/tasks/{foreign_task}",
        f"/tasks/{foreign_task}/documents",
        f"/tasks/{foreign_task}/jobs?kind=parse",
        f"/tasks/{own_task}/jobs?kind=parse&document={foreign_document}",
    ):
        response = await api.get(path, headers=headers[0])
        assert response.status_code == 404, path
        assert response.json()["data"]["error"]["code"] == "not_found"


async def test_g1_viewer_and_token_scope_intersection(
    api, headers, tenants, admin_engine, pdf_bytes
):
    task_id, document_id = await create_document(api, headers[0], pdf_bytes)
    queued = await api.post(f"/documents/{document_id}/parse", headers=headers[0], json={})
    assert queued.status_code == 200

    task_only = await token(api, headers[0], ["task:read"])
    job_only = await token(api, headers[0], ["job:read"])
    combined = await token(api, headers[0], ["task:read", "job:read"])
    routes = (
        f"/tasks/{task_id}",
        f"/tasks/{task_id}/documents",
        f"/tasks/{task_id}/jobs?kind=parse",
    )
    assert [(await api.get(path, headers=task_only)).status_code for path in routes] == [
        200,
        200,
        403,
    ]
    assert [(await api.get(path, headers=job_only)).status_code for path in routes] == [
        403,
        403,
        403,
    ]
    assert [(await api.get(path, headers=combined)).status_code for path in routes] == [
        200,
        200,
        200,
    ]

    with Session(admin_engine) as session, session.begin():
        member = session.scalar(
            select(Membership).where(
                Membership.org_id == tenants["orgs"][0],
                Membership.user_id == tenants["users"][0],
            )
        )
        assert member is not None
        member.role = "viewer"
    assert [(await api.get(path, headers=headers[0])).status_code for path in routes] == [
        200,
        200,
        200,
    ]
