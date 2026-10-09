"""DB-free CLI transport acceptance for org member management.

Failure modes: invalid inputs reach HTTP; inactive false becomes true; CAS is
omitted; an error retries a write; member metadata or invitation links change
shape; local and remote modes diverge; legacy callers reach an unversioned route.
The reproducible artifacts are the JSON snapshots and pytest JUnit report.
"""

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from app.schemas.contracts import Cost, LegacyCost, Result
from bid_cli import main as cli
from bid_cli.client import Client

ID = "00000000-0000-4000-8000-000000000001"
WHEN = "2026-10-08T00:00:00Z"
MEMBER = {
    "user_id": ID,
    "email": "colleague@example.test",
    "role": "technical",
    "active": True,
    "password_set": False,
    "revision": 1,
    "created_by": ID,
    "created_at": WHEN,
    "updated_at": WHEN,
}
INVITED = {
    "member": MEMBER,
    "invitation_url": "/app/setup-password#token=synthetic-link",
    "expires_in": 86400,
}


def invoke(capsys, flags, exit_code=0):
    if exit_code:
        with pytest.raises(SystemExit) as error:
            cli.main([*flags, "--json"])
        assert error.value.code == exit_code
    else:
        cli.main([*flags, "--json"])
    body = json.loads(capsys.readouterr().out)
    assert set(body) == {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
    cost_model = LegacyCost if "3.0" in flags else Cost
    assert body["cost"] == cost_model().model_dump(mode="json")
    body["duration_ms"] = 0
    return body


def test_org_members_cli_transport_snapshots(monkeypatch, capsys):
    monkeypatch.setenv("BID_SESSION", "synthetic-session")
    monkeypatch.setenv("BID_ORG", ID)
    calls, snapshots = [], {}
    command, failure, variant = "list", None, "admin"

    def handler(request):
        assert request.headers["authorization"] == "Bearer synthetic-session"
        assert request.headers["x-org-id"] == ID
        calls.append(
            (
                request.method,
                request.url.path,
                json.loads(request.content) if request.content else None,
            )
        )
        if failure:
            status, code, exit_code = failure
            return httpx.Response(
                status,
                json=Result(
                    ok=False,
                    command="org member " + command,
                    data={
                        "error": {
                            "code": code,
                            "message": "Synthetic failure",
                            "exit_code": exit_code,
                        }
                    },
                ).model_dump(mode="json"),
            )
        data, items = {}, []
        if command == "list":
            items = (
                [MEMBER]
                if variant == "admin"
                else []
                if variant == "empty"
                else [{key: MEMBER[key] for key in ("user_id", "email", "role", "active")}]
            )
        elif command in {"add", "invite"}:
            data = (
                INVITED
                if variant != "existing"
                else {
                    "member": {**MEMBER, "password_set": True},
                    "invitation_url": "/app/org/login",
                    "expires_in": None,
                }
            )
        else:
            data = {**MEMBER, "revision": 2}
            if command == "role":
                data["role"] = "bidder"
            else:
                data["active"] = json.loads(request.content)["active"]
        return httpx.Response(
            200,
            json=Result(
                ok=True, command="org member " + command, data=data, items=items
            ).model_dump(mode="json"),
        )

    @asynccontextmanager
    async def transport(self):
        async with httpx.AsyncClient(
            base_url="http://local", transport=httpx.MockTransport(handler)
        ) as client:
            yield client

    monkeypatch.setattr(Client, "transport", transport)
    cases = [
        ("list", ["list"], "GET", "", None),
        (
            "add",
            ["add", "--email", " Colleague@Example.Test ", "--role", "technical"],
            "POST",
            "",
            {"email": MEMBER["email"], "role": "technical"},
        ),
        (
            "role",
            ["role", "--user", ID, "--role", "bidder", "--expected-revision", "1"],
            "POST",
            f"/{ID}/role",
            {"role": "bidder", "expected_revision": 1},
        ),
        (
            "set-active",
            ["set-active", "--user", ID, "--active", "false", "--expected-revision", "1"],
            "POST",
            f"/{ID}/active",
            {"active": False, "expected_revision": 1},
        ),
        (
            "set-active",
            ["set-active", "--user", ID, "--active", "true", "--expected-revision", "1"],
            "POST",
            f"/{ID}/active",
            {"active": True, "expected_revision": 1},
        ),
        ("invite", ["invite", "--user", ID], "POST", f"/{ID}/invitation", None),
    ]
    for mode in ("remote", "local"):
        for command, flags, method, path, content in cases:
            name = command + (
                ":" + str(content["active"]).lower() if command == "set-active" else ""
            )
            snapshots[mode + ":" + name] = invoke(capsys, ["--mode", mode, "org", "member", *flags])
            assert calls[-1] == (method, "/v4/org/members" + path, content)
        for variant in ("empty", "restricted"):
            command = "list"
            snapshots[mode + ":list:" + variant] = invoke(
                capsys, ["--mode", mode, "org", "member", "list"]
            )
        variant, command = "existing", "add"
        snapshots[mode + ":add:existing"] = invoke(
            capsys,
            [
                "--mode",
                mode,
                "org",
                "member",
                "add",
                "--email",
                MEMBER["email"],
                "--role",
                "technical",
            ],
        )
        variant = "admin"
        for status, code, exit_code in (
            (400, "invalid_input", 2),
            (403, "forbidden", 4),
            (401, "invalid_session", 4),
            (404, "not_found", 4),
            (409, "member_exists", 2),
            (409, "revision_conflict", 2),
            (409, "last_admin_required", 2),
            (409, "password_already_set", 2),
            (503, "unavailable", 3),
        ):
            failure = status, code, exit_code
            command = "invite"
            before = len(calls)
            snapshots[mode + ":error:" + code] = invoke(
                capsys, ["--mode", mode, "org", "member", "invite", "--user", ID], exit_code
            )
            assert len(calls) == before + 1
        failure = None
    snapshot = Path(__file__).with_name("snapshots") / "org-members-cli.json"
    if os.environ.get("BID_UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(
            json.dumps(snapshots, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
    assert snapshots == json.loads(snapshot.read_text())


def test_org_members_cli_invalid_and_legacy_do_not_request(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "call", lambda *args, **kwargs: calls.append((args, kwargs)))
    for flags in (
        ["add"],
        ["add", "--email", "invalid", "--role", "technical"],
        ["add", "--email", MEMBER["email"], "--role", "owner"],
        ["role", "--user", ID, "--role", "admin"],
        ["role", "--user", ID, "--role", "admin", "--expected-revision", "0"],
        ["set-active", "--user", ID, "--active", "false"],
        ["set-active", "--user", ID, "--active", "invalid", "--expected-revision", "1"],
        ["invite"],
        ["invite", "--user", "invalid"],
    ):
        body = invoke(capsys, ["org", "member", *flags], 2)
        assert body["data"]["error"]["code"] == "invalid_input"
    for command in ("list", "add", "role", "set-active", "invite"):
        body = invoke(capsys, ["--contract-version", "3.0", "org", "member", command], 2)
        assert body["data"]["error"]["message"] == "This command requires contract 4.0"
    assert calls == []


def test_org_members_cli_schema(capsys):
    commands = invoke(capsys, ["schema"])["data"]["commands"]
    for action in ("list", "add", "role", "set-active", "invite"):
        entry = commands["org member " + action]
        assert entry["output"]
        parameters = {param["name"]: param for param in entry["cli_parameters"]}
        assert "json_output" in parameters
        if action != "list":
            assert parameters["email" if action == "add" else "user"]["required"]
        if action in {"role", "set-active"}:
            assert parameters["expected_revision"]["required"]
    assert commands["org member list"]["items"]["title"] == "OrgMemberView"
    assert commands["org member role"]["input"]["title"] == "OrgMemberRoleChange"
    assert commands["org member set-active"]["input"]["title"] == "OrgMemberActiveChange"
    legacy = invoke(capsys, ["--contract-version", "3.0", "schema"])["data"]["commands"]
    assert not any(name.startswith("org member ") for name in legacy)


def test_org_members_cli_rejects_malformed_response(monkeypatch, capsys):
    malformed = Result(
        ok=True,
        command="org member invite",
        data={**INVITED, "invitation_url": "https://example.test/untrusted"},
    ).model_dump(mode="json")
    monkeypatch.setattr(cli, "call", lambda *args, **kwargs: malformed)
    body = invoke(capsys, ["org", "member", "invite", "--user", ID], 4)
    assert body["data"]["error"]["code"] == "invalid_server_response"
