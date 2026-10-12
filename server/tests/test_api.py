import os
from datetime import UTC, datetime, timedelta

import httpx
from app.api.main import create_app
from app.core.config import Settings
from app.models.entities import UsageRecord
from conftest import FakeQueue
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
    await application.state.processor(job)
    return job, (await api.get(f"/jobs/{job}", headers=header)).json()["data"]


async def test_access_key_and_session_act_as_human(api, headers):
    raw = {"Authorization": "Bearer " + os.environ["BID_ACCESS_KEY"]}
    raw_result = await api.get("/auth/session", headers=raw)
    session_result = await api.get("/auth/session", headers=headers)
    assert raw_result.status_code == session_result.status_code == 200
    raw_data, session_data = raw_result.json()["data"], session_result.json()["data"]
    assert raw_data["actor_kind"] == session_data["actor_kind"] == "session"
    assert raw_data["scopes"] == session_data["scopes"] == sorted(raw_data["scopes"])
    assert raw_data["expires_at"] is None and session_data["expires_at"] is not None
    for wrong in ("", "wrong", "x" * 32, "x" * 1000, "错误密钥"):
        invalid = await api.post("/auth/session", json={"access_key": wrong})
        assert invalid.status_code == 401, invalid.text
        assert invalid.json()["data"]["error"]["code"] == "invalid_access_key"
    invalid = await api.get("/auth/session", headers={"Authorization": "Bearer wrong"})
    assert invalid.status_code == 401
    assert invalid.json()["data"]["error"]["code"] == "invalid_access_key"


async def test_docs_declare_bearer_security_and_missing_credentials_fail_closed(api):
    schema = (await api.get("/openapi.json")).json()
    assert schema["components"]["securitySchemes"]["HTTPBearer"] == {
        "type": "http",
        "scheme": "bearer",
    }
    protected = schema["paths"]["/tasks"]["post"]
    assert protected["security"] == [{"HTTPBearer": []}]
    assert all(
        parameter["name"].lower() != "authorization"
        for parameter in protected.get("parameters", [])
    )
    for authorization in (None, "Basic synthetic", "Bearer "):
        headers = {}
        if authorization is not None:
            headers["Authorization"] = authorization
        response = await api.get("/tasks", headers=headers)
        assert (
            response.status_code == 401
            and response.json()["data"]["error"]["code"] == "invalid_session"
        )


async def test_expired_credentials_and_revoked_tokens(api, headers, admin_engine):
    from app.core.config import Settings
    from app.core.security import TokenSigner
    from sqlalchemy import text

    expired = TokenSigner.for_tokens(Settings()).issue({"kind": "session"}, -1)
    assert (
        await api.get("/tasks", headers={**headers, "Authorization": "Bearer " + expired})
    ).status_code == 401
    expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    token = (
        await api.post(
            "/tokens",
            headers=headers,
            json={"name": "synthetic-expiry-test", "scopes": ["task:read"], "expires_at": expiry},
        )
    ).json()["data"]["token"]
    with admin_engine.begin() as connection:
        connection.execute(text("UPDATE api_tokens SET expires_at=now()-interval '1 second'"))
    assert (
        await api.get("/tasks", headers={**headers, "Authorization": "Bearer " + token})
    ).status_code == 401
    with admin_engine.begin() as connection:
        connection.execute(
            text("UPDATE api_tokens SET expires_at=now()+interval '1 hour', revoked=true")
        )
    assert (
        await api.get("/tasks", headers={"Authorization": "Bearer " + token})
    ).status_code == 401
    assert (await api.get("/tasks", headers=headers)).status_code == 200


async def test_duplicate_upload_parse_dry_run_and_cancel(api, application, headers, pdf_bytes):
    task, document = await create_document(api, headers, pdf_bytes)
    duplicate = await api.post(
        f"/tasks/{task}/documents", headers=headers, files={"file": ("again.pdf", pdf_bytes)}
    )
    assert duplicate.json()["data"]["duplicate"] is True
    assert duplicate.json()["data"]["id"] == document
    dry_run = await api.post(
        f"/documents/{document}/parse", headers=headers, json={"dry_run": True}
    )
    assert dry_run.json()["data"]["dry_run"]
    assert application.state.queue.calls == []
    job, status = await run_job(api, application, headers, document, "parse")
    assert status["status"] == "succeeded" and status["result"]["pages"] == 2
    repeated = await api.post(
        f"/documents/{document}/parse", headers=headers, json={"dry_run": False}
    )
    assert repeated.json()["data"]["job_id"] == job and repeated.json()["data"]["cached"]
    assert len(application.state.queue.calls) == 1
    extraction = await api.post(
        f"/documents/{document}/extract", headers=headers, json={"dry_run": False}
    )
    cancelled = extraction.json()["data"]["job_id"]
    for _ in range(2):
        assert (await api.post(f"/jobs/{cancelled}/cancel", headers=headers)).json()["data"][
            "status"
        ] == "cancelled"
    await application.state.processor(cancelled)
    assert (await api.get(f"/jobs/{cancelled}", headers=headers)).json()["data"][
        "status"
    ] == "cancelled"
    assert (await api.post(f"/jobs/{job}/cancel", headers=headers)).status_code == 409


async def test_deferred_ai_is_explicit_failure(api, application, headers, pdf_bytes):
    task, document = await create_document(api, headers, pdf_bytes)
    await run_job(api, application, headers, document, "parse")
    job, status = await run_job(api, application, headers, document, "extract")
    assert status["status"] == "failed"
    assert status["error"]["code"] == "provider_unavailable"
    assert (await api.get(f"/tasks/{task}/requirements", headers=headers)).json()["items"] == []


async def test_scoped_tokens_cannot_confirm_export_or_expand(api, headers):
    expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    for scope in ("evidence:confirm", "export", "*"):
        response = await api.post(
            "/tokens",
            headers=headers,
            json={"name": "test", "scopes": [scope], "expires_at": expiry},
        )
        assert response.status_code == 403
    token = (
        await api.post(
            "/tokens",
            headers=headers,
            json={"name": "read only", "scopes": ["task:read"], "expires_at": expiry},
        )
    ).json()["data"]["token"]
    scoped = {**headers, "Authorization": "Bearer " + token}
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


async def test_signed_download_and_invalid_file(api, headers, pdf_bytes):
    task, document = await create_document(api, headers, pdf_bytes)
    link = (await api.get(f"/documents/{document}/download-link", headers=headers)).json()["data"][
        "url"
    ]
    downloaded = await api.get(link, headers=headers)
    assert downloaded.content == pdf_bytes
    assert (await api.get(link)).status_code == 401
    invalid = await api.post(
        f"/tasks/{task}/documents", headers=headers, files={"file": ("invalid.pdf", b"bad")}
    )
    assert invalid.status_code == 400


async def test_fake_provider_integration_is_labelled_and_cached(database, tmp_path, pdf_bytes):
    provider = FakeLLM()
    application = create_app(Settings(data_dir=tmp_path), llm=provider, queue=FakeQueue())
    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as api,
    ):
        logged = await api.post("/auth/session", json={"access_key": os.environ["BID_ACCESS_KEY"]})
        header = {"Authorization": "Bearer " + logged.json()["data"]["session"]}
        task, document = await create_document(api, header, pdf_bytes)
        await run_job(api, application, header, document, "parse")
        job, status = await run_job(api, application, header, document, "extract")
        assert status["status"] == "succeeded" and status["result"]["warnings"]
        rows = (await api.get(f"/tasks/{task}/requirements", headers=header)).json()["items"]
        assert len(rows) == 2 and all(row["source"]["page"] >= 1 for row in rows)
        await run_job(api, application, header, document, "extract")
        assert provider.calls == 1
        async with application.state.db.transaction() as session:
            assert await session.scalar(select(func.count()).select_from(UsageRecord)) == 1


async def test_access_key_token_and_persisted_agent_human_gates(api, application, headers):
    from uuid import UUID

    from app.models.agent import AgentPrincipal
    from app.services.auth import AGENT_SCOPES, HUMAN_ONLY_SCOPES, agent_identity, set_actor_context
    from fastapi import Depends
    from sqlalchemy import text

    def routes(router):
        for route in router.routes:
            included = getattr(route, "original_router", None)
            if included is not None:
                yield from routes(included)
            else:
                yield route

    session_route = next(
        route
        for route in routes(application.router)
        if getattr(route, "name", None) == "auth_session_show"
    )
    context = session_route.dependant.dependencies[0].call
    observed = []

    @application.post("/_test/agent-delegations")
    async def delegate(ctx=Depends(context, scope="function")):
        session, actor = ctx
        actor.require("agent:run")
        principal = AgentPrincipal(
            initial_grants=sorted(actor.scopes),
            scopes=sorted(actor.scopes & AGENT_SCOPES),
            authority_expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        session.add(principal)
        await session.flush()
        return {"principal_id": str(principal.id)}

    @application.get("/_test/human-gate/{scope}")
    async def gate(scope: str, ctx=Depends(context, scope="function")):
        session, actor = ctx
        database_human = await session.scalar(text("SELECT public.actor_is_human()"))
        observed.append((actor.actor_kind, database_human))
        actor.require(scope)
        return {"human": database_human, "actor_kind": actor.actor_kind}

    raw = {"Authorization": "Bearer " + os.environ["BID_ACCESS_KEY"]}
    for authorization in (raw, headers):
        for scope in ("evidence:confirm", "export"):
            allowed = await api.get(f"/_test/human-gate/{scope}", headers=authorization)
            assert allowed.status_code == 200, allowed.text
            assert allowed.json() == {"human": True, "actor_kind": "session"}
    delegated = await api.post("/_test/agent-delegations", headers=raw)
    assert delegated.status_code == 200, delegated.text
    principal_id = UUID(delegated.json()["principal_id"])
    minted = await api.post(
        "/tokens",
        headers=headers,
        json={
            "name": "synthetic-human-gate",
            "scopes": ["task:read"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert minted.status_code == 200, minted.text
    token_header = {"Authorization": "Bearer " + minted.json()["data"]["token"]}
    for scope in ("evidence:confirm", "export"):
        denied = await api.get(f"/_test/human-gate/{scope}", headers=token_header)
        assert denied.status_code == 403, denied.text
        assert denied.json()["data"]["error"]["code"] == "forbidden"
        assert observed[-1] == ("token", False)

    async def delegated_context():
        async with application.state.db.transaction() as session:
            principal = await session.get(AgentPrincipal, principal_id)
            actor = await agent_identity(session, principal)
            assert actor.scopes <= AGENT_SCOPES
            assert not actor.scopes & HUMAN_ONLY_SCOPES
            await set_actor_context(session, actor)
            yield session, actor

    application.dependency_overrides[context] = delegated_context
    try:
        for scope in ("evidence:confirm", "export"):
            denied = await api.get(f"/_test/human-gate/{scope}")
            assert denied.status_code == 403, denied.text
            assert denied.json()["data"]["error"]["code"] == "forbidden"
            assert observed[-1] == ("agent", False)
    finally:
        del application.dependency_overrides[context]
