"""Gateway project visibility, members, owner hand-over and agent cancellation.

Failure matrix before implementation: a non-member with every grant reaches a
project object through some route, or learns it exists from an answer that
differs from a random identifier's; the project list shows another member's
project; a project ends a transaction without an owner, or with two; a member
who is not the owner manages members; a disabled or unknown member joins; the
owner is removed; a non-administrator changes sharing; an agent keeps running
after its member is removed from the project or disabled; the CLI cannot reach
the routes with a `bid_` token. Artifacts are synthetic and reproducible with
--basetemp=data/work/project-visibility.
"""

import json
import re
import types
import typing
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4

import httpx
import pytest
from app.api.main import create_app
from app.jobs.agent import wake
from app.models.agent import AgentSession
from app.models.entities import AuditLog, Job
from app.providers.llm import OpenAICompatibleExtractor
from conftest import FakeQueue
from fastapi.params import File, Form
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from test_agent_workflow import start_body
from test_card_generation import DraftVendor
from test_llm_providers import settings_for
from test_members import GATEWAY, bearer, bootstrap_admin, code, run_cli, start_gateway
from test_response_cards import create_tender

PASSWORD = "synthetic-member-password"
GRANTS = ["library:edit", "memory:edit", "confidential:reveal"]


@pytest.fixture
async def gateway(database, tmp_path, monkeypatch):
    with database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO public.installation (mode, company_name)"
                " VALUES ('enterprise', 'Synthetic Company')"
            )
        )
    for name, value in GATEWAY.items():
        monkeypatch.setenv(name, value)
    settings = settings_for(tmp_path, "openai")
    llm = OpenAICompatibleExtractor(settings, httpx.MockTransport(DraftVendor("openai", None)))
    application = create_app(
        settings.model_copy(update={"llm_api_key": None}), llm=llm, queue=FakeQueue()
    )
    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as client,
    ):
        yield client, application


async def signed_in(api, login, password):
    """Sign in and replace the one-time password, as every new member must."""
    response = await api.post("/auth/session", json={"login": login, "password": password})
    assert response.status_code == 200, response.text
    header = bearer(response.json()["data"]["session"])
    changed = await api.post(
        "/account/password", headers=header, json={"current": password, "new": PASSWORD}
    )
    assert changed.status_code == 200, changed.text
    return header


async def new_member(api, admin, login, grants=()):
    created = await api.post(
        "/members",
        headers=admin,
        json={"login": login, "display_name": login, "grants": list(grants)},
    )
    assert created.status_code == 200, created.text
    data = created.json()["data"]
    return data["member"]["id"], await signed_in(api, login, data["initial_password"])


def routes(router):
    for route in router.routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from routes(included)
        elif isinstance(route, APIRoute):
            yield route


def constraint(metadata, name):
    return next(
        (getattr(item, name) for item in metadata if getattr(item, name, None) is not None), None
    )


STRINGS = ("a" * 64, "0" * 64, "synthetic", "a", "sha256:" + "a" * 64)


def sample(annotation, depth=0, metadata=()):
    """A minimal value of `annotation` that passes validation, so the handler runs."""
    origin, args = typing.get_origin(annotation), typing.get_args(annotation)
    if origin is typing.Annotated:
        return sample(args[0], depth, (*metadata, *args[1:]))
    if origin in {typing.Union, types.UnionType}:
        return sample(next(arg for arg in args if arg is not type(None)), depth, metadata)
    if origin is typing.Literal:
        return args[0]
    if origin in {list, set, tuple, frozenset}:
        if not args or depth >= 4:
            return []
        return [sample(args[0], depth + 1)] * max(1, constraint(metadata, "min_length") or 1)
    if origin is dict or annotation is dict:
        return {}
    if isinstance(annotation, type):
        if issubclass(annotation, BaseModel):
            return {
                name: sample(field.annotation, depth + 1, field.metadata)
                for name, field in annotation.model_fields.items()
                if field.is_required() and depth < 6
            }
        if issubclass(annotation, Enum):
            return next(iter(annotation)).value
        if issubclass(annotation, bool):
            return False
        if issubclass(annotation, (int, float, Decimal)):
            low = constraint(metadata, "ge")
            above = constraint(metadata, "gt")
            return max(1, low or 0, above + 1 if above is not None else 0)
        if issubclass(annotation, UUID):
            return str(uuid4())
        if issubclass(annotation, datetime):
            return (datetime.now(UTC) + timedelta(days=1)).isoformat()
        if issubclass(annotation, date):
            return date.today().isoformat()
    pattern = constraint(metadata, "pattern")
    shortest = constraint(metadata, "min_length") or 0
    for candidate in STRINGS:
        if len(candidate) >= shortest and (pattern is None or re.search(pattern, candidate)):
            return candidate
    return "a" * max(shortest, 1)


def request_for(route, objects, real):
    """Path, query and body for one call; ids not in `objects` match in both calls."""
    path, params, body, files = route.path, {}, None, None
    for param in route.dependant.path_params:
        value = (
            objects[param.name]
            if param.name in objects
            else sample(param.field_info.annotation, metadata=param.field_info.metadata)
        )
        path = path.replace(
            "{" + param.name + "}",
            str(value if real else uuid4()) if param.name in objects else str(value),
        )
    for param in route.dependant.query_params:
        if param.field_info.is_required() or param.name in QUERY_OBJECTS:
            name = QUERY_OBJECTS.get(param.name)
            if name in objects:
                params[param.alias] = objects[name] if real else str(uuid4())
            else:
                params[param.alias] = sample(
                    param.field_info.annotation, metadata=param.field_info.metadata
                )
    forms = [param for param in route.dependant.body_params if isinstance(param.field_info, Form)]
    if forms:
        body, files = {}, {}
        for param in forms:
            if isinstance(param.field_info, File):
                files[param.alias] = ("synthetic.pdf", b"%PDF-1.4 synthetic", "application/pdf")
            else:
                value = sample(param.field_info.annotation, metadata=param.field_info.metadata)
                body[param.alias] = value if isinstance(value, str) else json.dumps(value)
        return path, {"params": params, "data": body, "files": files}
    if route.dependant.body_params:
        [param] = route.dependant.body_params
        body = sample(param.field_info.annotation, metadata=param.field_info.metadata)
    return path, {"params": params, "json": body} if body is not None else {"params": params}


QUERY_OBJECTS = {"job": "job_id", "job_id": "job_id", "document": "document_id"}


async def sweep(api, application, outsider, objects):
    """Every route that takes a project object answers the outsider like a random id."""
    coverage = {"compared": 0, "rejected_before_lookup": [], "routes": []}
    for route in sorted(routes(application.router), key=lambda item: item.path):
        names = {param.name for param in route.dependant.path_params}
        if not names & set(objects):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            real_path, kwargs = request_for(route, objects, True)
            random_path, control = request_for(route, objects, False)
            # Placeholders are drawn per call; the control reuses the real body.
            control_kwargs = {**kwargs, "params": control["params"]}
            response = await api.request(method, real_path, headers=outsider, **kwargs)
            seen = answer(response)
            control = answer(
                await api.request(method, random_path, headers=outsider, **control_kwargs)
            )
            assert seen == control, (method, route.path, seen, control)
            assert not 200 <= seen[0] < 300, (method, route.path, seen)
            coverage["compared"] += 1
            coverage["routes"].append(f"{method} {route.path} {seen[0]} {seen[1]}")
            if seen[0] == 422:
                # The input fails validation before the handler reads the object.
                coverage["rejected_before_lookup"].append(
                    {"route": f"{method} {route.path}", "response": response.json()}
                )
    return coverage


def answer(response):
    body = response.json() if response.content else {}
    data = body.get("data") if isinstance(body, dict) else None
    return [
        response.status_code,
        data.get("error", {}).get("code") if isinstance(data, dict) else None,
    ]


async def agent_state(application, session_id):
    async with application.state.db.transaction() as session:
        row = await session.get(AgentSession, UUID(session_id))
        assert row is not None
        jobs = (
            await session.scalars(select(Job.status).where(Job.agent_session_id == row.id))
        ).all()
        audited = await session.scalar(
            select(AuditLog.details).where(
                AuditLog.action == "agent.session.cancelled",
                AuditLog.object_id == row.id,
            )
        )
        return {"state": row.state, "jobs": sorted(set(jobs)), "audit": audited}


async def start_agent(api, application, header, task, extraction):
    started = await api.post(
        f"/v4/tasks/{task}/agent-sessions", headers=header, json=start_body(extraction)
    )
    assert started.status_code == 200, started.text
    return started.json()["data"]["session"]["id"]


async def test_project_visibility_members_owner_and_agents(gateway, database, tmp_path):
    api, application = gateway
    report = {
        "rerun": "pytest -q server/tests/test_project_visibility.py"
        " --basetemp=data/work/project-visibility"
    }
    root = bootstrap_admin("root.admin")
    admin = await signed_in(api, "root.admin", root["initial_password"])
    owner_id, owner = await new_member(api, admin, "owner.one", GRANTS)
    helper_id, helper = await new_member(api, admin, "helper.two")
    outsider_id, outsider = await new_member(api, admin, "outsider", GRANTS)
    disabled_id, _ = await new_member(api, admin, "disabled.one")
    assert code(
        await api.patch(f"/members/{disabled_id}", headers=admin, json={"status": "disabled"})
    ) == [200, None]

    # The creator owns the project; it holds a parsed and extracted document.
    task, document, extraction, requirements = await create_tender(
        api, application, owner, tmp_path
    )
    listed = (await api.get(f"/tasks/{task}/members", headers=owner)).json()["items"]
    assert [(row["member_id"], row["role"]) for row in listed] == [(owner_id, "owner")]
    owner_agent = await start_agent(api, application, owner, task, extraction)

    # A grant opens the library, never another member's project.
    report["grants"] = {
        "outsider_library": code(await api.get("/resources/products", headers=outsider)),
        "granted_writes": [
            "resource:write"
            in (await api.get("/auth/session", headers=header)).json()["data"]["scopes"]
            for header in (outsider, helper)
        ],
        "outsider_task_list": [
            row["id"] for row in (await api.get("/tasks", headers=outsider)).json()["items"]
        ],
        "admin_task": code(await api.get(f"/tasks/{task}", headers=admin)),
        "owner_document": code(await api.get(f"/documents/{document}", headers=owner)),
    }
    assert report["grants"] == {
        "outsider_library": [200, None],
        "granted_writes": [True, False],
        "outsider_task_list": [],
        "admin_task": [200, None],
        "owner_document": [200, None],
    }

    objects = {
        "task_id": task,
        "document_id": document,
        "job_id": extraction,
        "requirement_id": requirements[0]["id"],
        "session_id": owner_agent,
    }
    coverage = await sweep(api, application, outsider, objects)
    report["sweep"] = {
        "objects": sorted(objects),
        "compared": coverage["compared"],
        "rejected_before_lookup": len(coverage["rejected_before_lookup"]),
    }
    assert coverage["compared"] > 100
    (tmp_path / "route-sweep.json").write_text(json.dumps(coverage, indent=2))
    # The sweep changed nothing the owner can see.
    assert code(await api.get(f"/tasks/{task}", headers=owner)) == [200, None]
    assert (await agent_state(application, owner_agent))["state"] == "queued"

    # The owner trigger holds at commit, whoever writes.
    with pytest.raises(DBAPIError, match="task_owner_required"):
        with database.begin() as connection:
            connection.execute(
                text("UPDATE task_members SET role='member' WHERE task_id=:task"), {"task": task}
            )

    # Members join, leave and take over through the owner or an administrator.
    members = f"/tasks/{task}/members"
    report["membership"] = {
        "outsider_adds": code(
            await api.post(members, headers=outsider, json={"member_ids": [outsider_id]})
        ),
        "unknown": code(
            await api.post(members, headers=owner, json={"member_ids": [str(uuid4())]})
        ),
        "disabled": code(
            await api.post(members, headers=owner, json={"member_ids": [disabled_id]})
        ),
        "helper_before": code(await api.get(f"/tasks/{task}", headers=helper)),
        "add": code(await api.post(members, headers=owner, json={"member_ids": [helper_id]})),
        "add_again": code(await api.post(members, headers=owner, json={"member_ids": [helper_id]})),
        "helper_after": code(await api.get(f"/tasks/{task}", headers=helper)),
        "helper_adds": code(
            await api.post(members, headers=helper, json={"member_ids": [outsider_id]})
        ),
        "remove_owner": code(await api.delete(f"{members}/{owner_id}", headers=owner)),
        "owner_to_outsider": code(
            await api.post(f"/tasks/{task}/owner", headers=owner, json={"member_id": outsider_id})
        ),
        "hand_over": code(
            await api.post(f"/tasks/{task}/owner", headers=owner, json={"member_id": helper_id})
        ),
        "old_owner_manages": code(
            await api.post(members, headers=owner, json={"member_ids": [outsider_id]})
        ),
        "member_sharing": code(
            await api.patch(
                f"/tasks/{task}/sharing", headers=helper, json={"export_to_personal_allowed": True}
            )
        ),
        "admin_sharing": code(
            await api.patch(
                f"/tasks/{task}/sharing", headers=admin, json={"export_to_personal_allowed": True}
            )
        ),
    }
    assert report["membership"] == {
        "outsider_adds": [404, "not_found"],
        "unknown": [404, "not_found"],
        "disabled": [409, "member_disabled"],
        "helper_before": [404, "not_found"],
        "add": [200, None],
        "add_again": [200, None],
        "helper_after": [200, None],
        "helper_adds": [403, "project_owner_required"],
        "remove_owner": [409, "owner_not_removable"],
        "owner_to_outsider": [409, "project_member_required"],
        "hand_over": [200, None],
        "old_owner_manages": [403, "project_owner_required"],
        "member_sharing": [403, "forbidden"],
        "admin_sharing": [200, None],
    }
    roles = {
        row["member_id"]: row["role"]
        for row in (await api.get(members, headers=helper)).json()["items"]
    }
    assert roles == {helper_id: "owner", owner_id: "member"}

    # Removal and disable each cancel the member's agent on its next wake.
    helper_agent = await start_agent(api, application, helper, task, extraction)
    assert code(await api.delete(f"{members}/{owner_id}", headers=helper)) == [200, None]
    assert code(await api.get(f"/tasks/{task}", headers=owner)) == [404, "not_found"]
    await wake(application.state.processor, UUID(owner_agent))
    assert code(
        await api.patch(f"/members/{helper_id}", headers=admin, json={"status": "disabled"})
    ) == [200, None]
    await wake(application.state.processor, UUID(helper_agent))
    report["agents"] = {
        "removed": await agent_state(application, owner_agent),
        "disabled": await agent_state(application, helper_agent),
    }
    cancelled = {"state": "cancelled", "jobs": ["cancelled"], "audit": {"reason": "member_removed"}}
    assert report["agents"] == {"removed": cancelled, "disabled": cancelled}

    # The CLI manages project members with an owner's `bid_` token.
    assert code(
        await api.patch(f"/members/{helper_id}", headers=admin, json={"status": "active"})
    ) == [200, None]
    helper = await signed_in_again(api, "helper.two")
    minted = await api.post(
        "/tokens",
        headers=helper,
        json={
            "name": "synthetic project cli",
            "scopes": ["task:read", "task:create"],
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    assert minted.status_code == 200, minted.text
    token = minted.json()["data"]["token"]
    server, address = start_gateway(tmp_path)
    try:
        add = run_cli(
            ["task", "member", "add", "--task", task, "--member", outsider_id], address, token
        )
        owner_change = run_cli(
            ["task", "owner", "--task", task, "--member", outsider_id], address, token
        )
        listing = run_cli(["task", "member", "list", "--task", task], address, token)
        remove = run_cli(
            ["task", "member", "remove", "--task", task, "--member", helper_id], address, token
        )
        sharing = run_cli(
            ["task", "sharing", "--task", task, "--no-export-to-personal"], address, token
        )
    finally:
        server.terminate()
        server.wait(timeout=30)
    report["cli"] = {
        "add": add[0],
        "owner": owner_change[0],
        "list": sorted((row["member_id"], row["role"]) for row in listing[1]["items"]),
        "remove_after_hand_over": [remove[0], remove[1]["data"]["error"]["code"]],
        "sharing": [sharing[0], sharing[1]["data"]["error"]["code"]],
    }
    assert report["cli"] == {
        "add": 0,
        "owner": 0,
        "list": sorted([(outsider_id, "owner"), (helper_id, "member")]),
        "remove_after_hand_over": [4, "project_owner_required"],
        "sharing": [4, "forbidden"],
    }
    (tmp_path / "project-visibility.json").write_text(json.dumps(report, indent=2, default=str))


async def signed_in_again(api, login):
    response = await api.post("/auth/session", json={"login": login, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return bearer(response.json()["data"]["session"])
