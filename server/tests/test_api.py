from datetime import UTC, datetime, timedelta

import httpx
from app.api.main import create_app
from app.core.config import Settings
from app.models.entities import UsageRecord
from conftest import PASSWORD, FakeQueue
from fakes import FakeLLM
from sqlalchemy import func, select


async def create_document(api, header, pdf):
    task = (await api.post("/tasks", headers=header, json={"name": "Synthetic test task"})).json()[
        "data"
    ]["id"]
    response = await api.post(
        f"/tasks/{task}/documents", headers=header, files={"file": ("fixture.pdf", pdf)}
    )
    assert response.status_code == 200
    return task, response.json()["data"]["id"]


async def run_job(api, application, header, document, kind):
    response = await api.post(
        f"/documents/{document}/{kind}", headers=header, json={"dry_run": False}
    )
    assert response.status_code == 200
    job = response.json()["data"]["job_id"]
    await application.state.processor(header["X-Org-Id"], job)
    return job, (await api.get(f"/jobs/{job}", headers=header)).json()["data"]


async def test_login_errors_and_switching(api, headers, tenants):
    invalid = await api.post(
        "/auth/login",
        json={"email": "a@example.test", "password": "wrong", "org_id": str(tenants["orgs"][0])},
    )
    assert invalid.status_code == 401 and not invalid.json()["ok"]
    wrong_org = await api.post(
        "/auth/login",
        json={"email": "a@example.test", "password": PASSWORD, "org_id": str(tenants["orgs"][1])},
    )
    assert wrong_org.status_code == 404
    response = await api.get(
        "/org/current", headers={**headers[0], "X-Org-Id": str(tenants["orgs"][1])}
    )
    assert response.status_code == 404
    missing = await api.get("/tasks")
    assert missing.status_code == 422 and not missing.json()["ok"]


async def test_docs_declare_bearer_security_and_missing_credentials_fail_closed(api, tenants):
    schema = (await api.get("/openapi.json")).json()
    assert schema["components"]["securitySchemes"]["HTTPBearer"] == {
        "type": "http",
        "scheme": "bearer",
    }
    protected = schema["paths"]["/resources/products"]["post"]
    assert protected["security"] == [{"HTTPBearer": []}]
    assert all(
        parameter["name"].lower() != "authorization" for parameter in protected["parameters"]
    )
    for authorization in (None, "Basic synthetic", "Bearer "):
        headers = {"X-Org-Id": str(tenants["orgs"][0])}
        if authorization is not None:
            headers["Authorization"] = authorization
        response = await api.get("/tasks", headers=headers)
        assert (
            response.status_code == 401
            and response.json()["data"]["error"]["code"] == "invalid_session"
        )


async def test_expired_credentials_and_revoked_membership(api, headers, tenants, admin_engine):
    from app.core.config import Settings
    from app.core.security import Secrets
    from sqlalchemy import text

    expired = Secrets(Settings().encryption_key.get_secret_value()).issue(
        {"kind": "session", "user_id": str(tenants["users"][0])}, -1
    )
    assert (
        await api.get("/tasks", headers={**headers[0], "Authorization": "Bearer " + expired})
    ).status_code == 401
    expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    token = (
        await api.post(
            "/tokens",
            headers=headers[0],
            json={"name": "synthetic-expiry-test", "scopes": ["task:read"], "expires_at": expiry},
        )
    ).json()["data"]["token"]
    with admin_engine.begin() as connection:
        connection.execute(text("UPDATE api_tokens SET expires_at=now()-interval '1 second'"))
    assert (
        await api.get("/tasks", headers={**headers[0], "Authorization": "Bearer " + token})
    ).status_code == 401
    with admin_engine.begin() as connection:
        connection.execute(
            text("UPDATE memberships SET active=false WHERE user_id=:id"),
            {"id": tenants["users"][0]},
        )
    assert (await api.get("/tasks", headers=headers[0])).status_code == 404


async def test_every_resource_endpoint_hides_other_tenant(api, application, headers, pdf_bytes):
    task_a, doc_a = await create_document(api, headers[0], pdf_bytes)
    task_b, doc_b = await create_document(api, headers[1], pdf_bytes)
    job_b, status = await run_job(api, application, headers[1], doc_b, "parse")
    for method, path, body in [
        ("GET", f"/documents/{doc_b}", None),
        ("GET", f"/documents/{doc_b}/chunks", None),
        ("GET", f"/documents/{doc_b}/download-link", None),
        ("GET", f"/documents/{doc_b}/download?signature=invalid", None),
        ("GET", f"/tasks/{task_b}/requirements", None),
        ("GET", f"/jobs/{job_b}", None),
        ("POST", f"/documents/{doc_b}/parse", {"dry_run": False}),
        ("POST", f"/documents/{doc_b}/extract", {"dry_run": True}),
        ("POST", f"/jobs/{job_b}/cancel", {}),
    ]:
        response = await api.request(method, path, headers=headers[0], json=body)
        assert response.status_code == 404, path
        assert response.json()["data"]["error"]["code"] == "not_found"
    upload = await api.post(
        f"/tasks/{task_b}/documents", headers=headers[0], files={"file": ("fixture.pdf", pdf_bytes)}
    )
    assert upload.status_code == 404
    listing = (await api.get("/tasks", headers=headers[0])).json()["items"]
    assert [row["id"] for row in listing] == [task_a]


async def test_duplicate_upload_parse_dry_run_and_cancel(api, application, headers, pdf_bytes):
    task, document = await create_document(api, headers[0], pdf_bytes)
    duplicate = await api.post(
        f"/tasks/{task}/documents", headers=headers[0], files={"file": ("again.pdf", pdf_bytes)}
    )
    assert duplicate.json()["data"]["duplicate"] is True
    assert duplicate.json()["data"]["id"] == document
    dry_run = await api.post(
        f"/documents/{document}/parse", headers=headers[0], json={"dry_run": True}
    )
    assert dry_run.json()["data"]["dry_run"]
    assert application.state.queue.calls == []
    job, status = await run_job(api, application, headers[0], document, "parse")
    assert status["status"] == "succeeded" and status["result"]["pages"] == 2
    repeated = await api.post(
        f"/documents/{document}/parse", headers=headers[0], json={"dry_run": False}
    )
    assert repeated.json()["data"]["job_id"] == job and repeated.json()["data"]["cached"]
    assert len(application.state.queue.calls) == 1
    extraction = await api.post(
        f"/documents/{document}/extract", headers=headers[0], json={"dry_run": False}
    )
    cancelled = extraction.json()["data"]["job_id"]
    for _ in range(2):
        assert (await api.post(f"/jobs/{cancelled}/cancel", headers=headers[0])).json()["data"][
            "status"
        ] == "cancelled"
    await application.state.processor(headers[0]["X-Org-Id"], cancelled)
    assert (await api.get(f"/jobs/{cancelled}", headers=headers[0])).json()["data"][
        "status"
    ] == "cancelled"
    assert (await api.post(f"/jobs/{job}/cancel", headers=headers[0])).status_code == 409


async def test_deferred_ai_is_explicit_failure(api, application, headers, pdf_bytes):
    task, document = await create_document(api, headers[0], pdf_bytes)
    await run_job(api, application, headers[0], document, "parse")
    job, status = await run_job(api, application, headers[0], document, "extract")
    assert status["status"] == "failed"
    assert status["error"]["code"] == "provider_unavailable"
    assert (await api.get(f"/tasks/{task}/requirements", headers=headers[0])).json()["items"] == []


async def test_scoped_tokens_cannot_confirm_export_or_expand(api, headers):
    expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    for scope in ("evidence:confirm", "export", "*"):
        response = await api.post(
            "/tokens",
            headers=headers[0],
            json={"name": "test", "scopes": [scope], "expires_at": expiry},
        )
        assert response.status_code == 403
    token = (
        await api.post(
            "/tokens",
            headers=headers[0],
            json={"name": "read only", "scopes": ["task:read"], "expires_at": expiry},
        )
    ).json()["data"]["token"]
    scoped = {**headers[0], "Authorization": "Bearer " + token}
    assert (await api.get("/tasks", headers=scoped)).status_code == 200
    assert (
        await api.post("/tasks", headers=scoped, json={"name": "disallowed"})
    ).status_code == 403
    assert (
        await api.post(
            "/tokens",
            headers=scoped,
            json={"name": "disallowed", "scopes": ["task:read"], "expires_at": expiry},
        )
    ).status_code == 403
    assert (
        await api.get("/tasks", headers={**scoped, "X-Org-Id": headers[1]["X-Org-Id"]})
    ).status_code == 401


async def test_signed_download_and_invalid_file(api, headers, pdf_bytes):
    task, document = await create_document(api, headers[0], pdf_bytes)
    link = (await api.get(f"/documents/{document}/download-link", headers=headers[0])).json()[
        "data"
    ]["url"]
    downloaded = await api.get(link, headers=headers[0])
    assert downloaded.content == pdf_bytes
    assert (await api.get(link, headers=headers[1])).status_code == 404
    invalid = await api.post(
        f"/tasks/{task}/documents", headers=headers[0], files={"file": ("invalid.pdf", b"bad")}
    )
    assert invalid.status_code == 400


async def test_fake_provider_integration_is_labelled_and_cached(tenants, tmp_path, pdf_bytes):
    provider = FakeLLM()
    application = create_app(Settings(data_dir=tmp_path), llm=provider, queue=FakeQueue())
    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as api,
    ):
        logged = await api.post(
            "/auth/login",
            json={
                "email": "a@example.test",
                "password": PASSWORD,
                "org_id": str(tenants["orgs"][0]),
            },
        )
        header = {
            "Authorization": "Bearer " + logged.json()["data"]["session"],
            "X-Org-Id": str(tenants["orgs"][0]),
        }
        task, document = await create_document(api, header, pdf_bytes)
        await run_job(api, application, header, document, "parse")
        job, status = await run_job(api, application, header, document, "extract")
        assert status["status"] == "succeeded" and status["result"]["warnings"]
        rows = (await api.get(f"/tasks/{task}/requirements", headers=header)).json()["items"]
        assert len(rows) == 2 and all(row["source"]["page"] >= 1 for row in rows)
        await run_job(api, application, header, document, "extract")
        assert provider.calls == 1
        async with application.state.db.transaction(tenants["orgs"][0]) as session:
            assert await session.scalar(select(func.count()).select_from(UsageRecord)) == 1
