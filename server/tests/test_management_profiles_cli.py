"""DB-free CLI acceptance scenarios written before command implementation.

Failures: missing/invalid input sends a request; legacy invocation reaches v4;
other resource kinds advertise unfinished commands; errors retry writes or change
exit codes; local transport differs; history loses its cursor; old list truncates.
Snapshots retain exactly seven Result keys and all current zero Cost fields.
"""

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from app.schemas.contracts import Cost, Result
from bid_cli import main as cli
from bid_cli.client import Client

ID = "00000000-0000-4000-8000-000000000001"
WHEN = "2026-10-06T00:00:00Z"
PREFIX = "/v4/management/resources/profiles"
REF = {"kind": "profiles", "resource_id": ID}
ROW = {
    "org_id": ID,
    "ref": REF,
    "name": "Synthetic profile",
    "revision_id": ID,
    "revision": 1,
    "lifecycle": {"state": "active", "revision": 0},
    "provenance": "declared",
    "created_at": WHEN,
    "revised_at": WHEN,
    "revised_by": ID,
    "actions": [{"action": "revise", "allowed": True, "reason": None}],
}
EVENT = {
    "id": ID,
    "org_id": ID,
    "ref": REF,
    "revision": 1,
    "resource_revision": 1,
    "before": "active",
    "after": "inactive",
    "reason_code": "obsolete",
    "actor_user_id": ID,
    "actor_kind": "session",
    "created_at": WHEN,
}
STATE = {
    "expected_revision": 1,
    "expected_lifecycle_revision": 0,
    "state": "inactive",
    "reason_code": "obsolete",
}


def projection(command, empty=False):
    data = {
        "org_id": ID,
        "as_of": WHEN,
        "returned": 0 if empty else 1,
        "next_cursor": None,
        "has_more": False,
    }
    items = [] if empty else [ROW]
    if command == "show":
        data = {
            "org_id": ID,
            "ref": REF,
            "current_revision": 1,
            "revised_at": WHEN,
            "revised_by": ID,
            "lifecycle": ROW["lifecycle"],
            "provenance": "declared",
            "actions": ROW["actions"],
            "detail": {
                "kind": "profiles",
                "revision": {
                    "id": ID,
                    "org_id": ID,
                    "profile_id": ID,
                    "revision": 1,
                    "data": {
                        "name": "Synthetic profile",
                        "registration_details": "Synthetic declaration",
                    },
                },
            },
        }
        items = []
    elif command == "history":
        items = (
            []
            if empty
            else [
                {
                    "org_id": ID,
                    "ref": REF,
                    "revision_id": ID,
                    "revision": 1,
                    "name": "Synthetic profile",
                    "created_at": WHEN,
                    "created_by": ID,
                    "current": True,
                    "has_file": False,
                }
            ]
        )
    elif command == "lifecycle set":
        data = {
            "event": EVENT,
            "lifecycle": {"state": "inactive", "revision": 1},
            "existing_selections": "preserved",
        }
        items = []
    elif command == "lifecycle history":
        items = [] if empty else [EVENT]
    return Result(
        ok=True, command="resource profile " + command, data=data, items=items
    ).model_dump(mode="json")


def invoke(capsys, flags, exit_code=0):
    if exit_code:
        with pytest.raises(SystemExit) as error:
            cli.main([*flags, "--json"])
        assert error.value.code == exit_code
    else:
        cli.main([*flags, "--json"])
    body = json.loads(capsys.readouterr().out)
    assert set(body) == {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
    assert body["cost"] == Cost().model_dump(mode="json")
    body["duration_ms"] = 0
    return body


def test_management_profiles_transport_snapshots(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("BID_SESSION", "synthetic-only")
    monkeypatch.setenv("BID_ORG", ID)
    calls, snapshots = [], {}
    failure = None
    empty = False
    command = "browse"

    def handler(request):
        assert request.headers["authorization"] == "Bearer synthetic-only"
        assert request.headers["x-org-id"] == ID
        calls.append(
            (
                request.method,
                request.url.path,
                dict(request.url.params),
                json.loads(request.content) if request.content else None,
            )
        )
        if failure:
            status, code, exit_code = failure
            return httpx.Response(
                status,
                json=Result(
                    ok=False,
                    command="resource profile " + command,
                    data={
                        "error": {
                            "code": code,
                            "message": "Synthetic failure",
                            "exit_code": exit_code,
                        }
                    },
                ).model_dump(mode="json"),
            )
        return httpx.Response(200, json=projection(command, empty))

    @asynccontextmanager
    async def transport(self):
        async with httpx.AsyncClient(
            base_url="http://local", transport=httpx.MockTransport(handler)
        ) as client:
            yield client

    monkeypatch.setattr(Client, "transport", transport)
    query_file, state_file = tmp_path / "query.json", tmp_path / "state.json"
    query_file.write_text(
        json.dumps(
            {
                "q": "Synthetic",
                "state": "all",
                "cursor": "opaque-query",
                "limit": 2,
            }
        )
    )
    state_file.write_text(json.dumps(STATE))
    cases = [
        (
            "browse",
            ["browse", "--input", str(query_file)],
            "POST",
            "/query",
            {},
            {
                "q": "Synthetic",
                "state": "all",
                "cursor": "opaque-query",
                "limit": 2,
                "product_id": None,
                "implementation_status": None,
            },
        ),
        ("show", ["show", "--id", ID, "--revision", "1"], "GET", "/" + ID, {"revision": "1"}, None),
        (
            "history",
            ["history", "--id", ID, "--cursor", "opaque-history", "--limit", "2"],
            "POST",
            "/" + ID + "/history/query",
            {},
            {"cursor": "opaque-history", "limit": 2},
        ),
        (
            "lifecycle set",
            ["lifecycle", "set", "--id", ID, "--input", str(state_file)],
            "POST",
            "/" + ID + "/lifecycle",
            {},
            STATE,
        ),
        (
            "lifecycle history",
            ["lifecycle", "history", "--id", ID],
            "POST",
            "/" + ID + "/lifecycle/history/query",
            {},
            {"limit": 25},
        ),
    ]
    for mode in ("remote", "local"):
        for command, flags, method, path, params, content in cases:
            snapshots[mode + ":" + command] = invoke(
                capsys, ["--mode", mode, "resource", "profile", *flags]
            )
            assert calls[-1] == (method, PREFIX + path, params, content)
        empty = True
        for command, flags, _method, _path, _params, _content in cases:
            if command not in {"browse", "history", "lifecycle history"}:
                continue
            snapshots[mode + ":empty:" + command] = invoke(
                capsys, ["--mode", mode, "resource", "profile", *flags]
            )
            assert snapshots[mode + ":empty:" + command]["items"] == []
        empty = False
        command = "lifecycle set"
        for status, code, exit_code in (
            (404, "not_found", 4),
            (409, "revision_conflict", 2),
            (429, "rate_limit", 3),
            (503, "unavailable", 3),
        ):
            failure = status, code, exit_code
            before = len(calls)
            snapshots[f"{mode}:HTTP{status}"] = invoke(
                capsys,
                [
                    "--mode",
                    mode,
                    "resource",
                    "profile",
                    "lifecycle",
                    "set",
                    "--id",
                    ID,
                    "--input",
                    str(state_file),
                ],
                exit_code,
            )
            assert len(calls) == before + 1
        failure = None
    snapshot = Path(__file__).with_name("snapshots") / "management-profiles-cli.json"
    if os.environ.get("BID_UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(
            json.dumps(snapshots, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
    assert snapshots == json.loads(snapshot.read_text())


def test_management_profiles_invalid_and_legacy_no_request(monkeypatch, capsys, tmp_path):
    calls = []
    monkeypatch.setattr(cli, "call", lambda *a, **kw: calls.append(a))
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"limit": 101}')
    for flags in (
        ["browse"],
        ["browse", "--input", str(invalid)],
        ["show"],
        ["show", "--id", ID, "--revision", "0"],
        ["history", "--id", ID, "--limit", "101"],
        ["history", "--id", ID, "--cursor", ""],
        ["lifecycle", "set", "--id", ID],
        ["lifecycle", "history"],
        ["browse", "--input", str(tmp_path / "missing.json")],
    ):
        body = invoke(capsys, ["resource", "profile", *flags], 2)
        assert body["data"]["error"]["code"] == "invalid_input"
    for command in ("browse", "show", "history", "lifecycle set", "lifecycle history"):
        with pytest.raises(SystemExit) as error:
            cli.main(
                ["--contract-version", "3.0", "resource", "profile", *command.split(), "--json"]
            )
        assert error.value.code == 2
        assert (
            json.loads(capsys.readouterr().out)["data"]["error"]["message"]
            == "This command requires contract 4.0"
        )
    assert calls == []


def test_management_profiles_schema_and_legacy_list(monkeypatch, capsys):
    body = invoke(capsys, ["schema"])
    commands = body["data"]["commands"]
    for command in ("browse", "show", "history", "lifecycle set", "lifecycle history"):
        entry = commands["resource profile " + command]
        assert entry["input"] is not None and entry["output"] is not None
        for kind in ("template",):
            assert "resource " + kind + " " + command not in commands
    assert "ResourceQuery" in json.dumps(commands["resource profile browse"])
    cli.main(["--contract-version", "3.0", "schema", "--json"])
    legacy = json.loads(capsys.readouterr().out)["data"]["commands"]
    assert "resource profile list" in legacy
    assert "resource profile browse" not in legacy
    calls = []

    def call(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return Result(
            ok=True, command="resource profile list", data={"current_revisions": {}}, items=[]
        ).model_dump(mode="json")

    monkeypatch.setattr(cli, "call", call)
    result = invoke(capsys, ["resource", "profile", "list", "--history"])
    assert calls == [("GET", "/resources/profiles", {"params": {"history": "true"}})]
    assert result["data"] == {"current_revisions": {}}


@pytest.mark.parametrize(
    "command,defect",
    [
        ("browse", "foreign_org"),
        ("browse", "wrong_kind"),
        ("browse", "broken_cursor"),
        ("show", "foreign_org"),
        ("show", "wrong_kind"),
        ("show", "future_revision"),
        ("lifecycle set", "wrong_kind"),
        ("lifecycle set", "mismatched_event"),
    ],
)
def test_profile_cli_rejects_malformed_server_receipt(
    command, defect, monkeypatch, capsys, tmp_path
):
    response = projection(command)
    if defect == "foreign_org":
        if command == "browse":
            response["items"] = [{**ROW, "org_id": "00000000-0000-4000-8000-000000000002"}]
        else:
            response["data"]["detail"]["revision"]["org_id"] = (
                "00000000-0000-4000-8000-000000000002"
            )
    elif defect == "wrong_kind":
        if command == "browse":
            response["items"] = [{**ROW, "ref": {"kind": "products", "resource_id": ID}}]
        elif command == "show":
            response["data"]["ref"] = {"kind": "products", "resource_id": ID}
        else:
            response["data"]["event"] = {**EVENT, "ref": {"kind": "products", "resource_id": ID}}
    elif defect == "broken_cursor":
        response["data"]["has_more"] = True
    elif defect == "future_revision":
        response["data"]["detail"]["revision"]["revision"] = 2
    elif defect == "mismatched_event":
        response["data"]["lifecycle"]["revision"] = 2
    calls = []

    def call(*args, **kwargs):
        calls.append((args, kwargs))
        return response

    monkeypatch.setattr(cli, "call", call)
    query = tmp_path / "input.json"
    query.write_text(json.dumps(STATE if command == "lifecycle set" else {}))
    flags = command.split()
    if command != "browse":
        flags += ["--id", ID]
    if command != "show":
        flags += ["--input", str(query)]
    result = invoke(capsys, ["resource", "profile", *flags], 4)
    assert result["data"]["error"]["code"] == "invalid_server_response"
    assert len(calls) == 1
