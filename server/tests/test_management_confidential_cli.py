"""DB-free command acceptance, declared before implementation.

Failure scenarios: v3 reaches new routes; invalid query/CAS flags consume stdin
or send requests; null absence precondition disappears; remote/local differ;
writes retry on conflict/unavailability; plaintext enters files, schema/output,
or malformed server projections; legacy commands or history cursors change.
"""

import io
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
WHEN = "2026-10-06T00:00:00Z"
SECRET = "synthetic-stdin-only-value"
FIELD = {
    "id": ID,
    "key": "bid_total",
    "placeholder": "{{confidential.bid_total}}",
    "label": "投标总价",
    "kind": "amount",
    "scope": "task",
    "archived": False,
    "revision": 1,
    "created_at": WHEN,
}
VALUE = {
    "field_id": ID,
    "key": FIELD["key"],
    "placeholder": FIELD["placeholder"],
    "label": FIELD["label"],
    "kind": FIELD["kind"],
    "scope": "task",
    "task_id": ID,
    "status": "filled",
    "value_id": ID,
    "version": 1,
    "tail": None,
    "set_by": ID,
    "set_at": WHEN,
}
COMMANDS = (
    "confidential field browse",
    "confidential browse",
    "confidential history-page",
    "confidential set-checked",
)


def projection(command, empty=False):
    if command == "confidential set-checked":
        return Result(ok=True, command=command, data=VALUE).model_dump(mode="json")
    return Result(
        ok=True,
        command=command,
        data={
            "org_id": ID,
            "as_of": WHEN,
            "returned": 0 if empty else 1,
            "next_cursor": None,
            "has_more": False,
        },
        items=[] if empty else [FIELD if command == "confidential field browse" else VALUE],
    ).model_dump(mode="json")


def invoke(capsys, flags, exit_code=0):
    if exit_code:
        with pytest.raises(SystemExit) as error:
            cli.main([*flags, "--json"])
        assert error.value.code == exit_code
    else:
        cli.main([*flags, "--json"])
    output = capsys.readouterr()
    assert SECRET not in output.out + output.err
    body = json.loads(output.out)
    assert set(body) == {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
    cost_model = LegacyCost if "3.0" in flags else Cost
    assert body["cost"] == cost_model().model_dump(mode="json")
    body["duration_ms"] = 0
    return body


def test_management_confidential_transport_snapshots(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("BID_SESSION", "synthetic-only")
    monkeypatch.setenv("BID_ORG", ID)
    calls, snapshots = [], {}
    command, empty, failure = COMMANDS[0], False, None

    def handler(request):
        assert request.headers["authorization"] == "Bearer synthetic-only"
        assert request.headers["x-org-id"] == ID
        calls.append((request.method, request.url.path, json.loads(request.content)))
        if failure:
            status, code, exit_code = failure
            return httpx.Response(
                status,
                json=Result(
                    ok=False,
                    command=command,
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
    query = tmp_path / "query.json"
    query.write_text(
        json.dumps(
            {
                "q": "投标",
                "field_id": ID,
                "task_id": ID,
                "archived": True,
                "cursor": "opaque-query",
                "limit": 2,
            }
        )
    )
    checked = [
        "confidential",
        "set-checked",
        "--field",
        ID,
        "--expected-field-revision",
        "1",
        "--task",
        ID,
        "--value-stdin",
    ]
    cases = [
        (
            COMMANDS[0],
            ["confidential", "field", "browse", "--input", str(query)],
            "/management/confidential-fields/query",
            {
                "cursor": "opaque-query",
                "limit": 2,
                "q": "投标",
                "field_id": ID,
                "task_id": ID,
                "archived": True,
            },
        ),
        (
            COMMANDS[1],
            ["confidential", "browse", "--input", str(query)],
            "/management/confidential-values/query",
            {
                "cursor": "opaque-query",
                "limit": 2,
                "q": "投标",
                "field_id": ID,
                "task_id": ID,
                "archived": True,
            },
        ),
        (
            COMMANDS[2],
            [
                "confidential",
                "history-page",
                "--field",
                ID,
                "--task",
                ID,
                "--cursor",
                "opaque-history",
                "--limit",
                "2",
            ],
            f"/management/confidential-fields/{ID}/values/history/query",
            {"cursor": "opaque-history", "limit": 2, "task_id": ID},
        ),
        (
            COMMANDS[3],
            [*checked, "--expected-value", ID],
            f"/management/confidential-fields/{ID}/values",
            {"value": SECRET, "task_id": ID, "expected_field_revision": 1, "expected_value_id": ID},
        ),
        (
            COMMANDS[3],
            [*checked, "--expect-empty"],
            f"/management/confidential-fields/{ID}/values",
            {
                "value": SECRET,
                "task_id": ID,
                "expected_field_revision": 1,
                "expected_value_id": None,
            },
        ),
    ]
    for mode in ("remote", "local"):
        for index, (command, flags, path, content) in enumerate(cases):
            monkeypatch.setattr("sys.stdin", io.StringIO(SECRET + "\n"))
            before = len(calls)
            snapshots[f"{mode}:{index}:{command}"] = invoke(capsys, ["--mode", mode, *flags])
            assert len(calls) == before + 1
            assert calls[-1] == ("POST", "/v4" + path, content)
        empty = True
        for command, flags, _path, _content in cases[:3]:
            snapshots[f"{mode}:empty:{command}"] = invoke(capsys, ["--mode", mode, *flags])
        empty = False
        command = COMMANDS[3]
        for failure in (
            (404, "not_found", 4),
            (409, "confidential_value_conflict", 2),
            (409, "revision_conflict", 2),
            (429, "rate_limit", 3),
            (503, "unavailable", 3),
        ):
            status, code, exit_code = failure
            monkeypatch.setattr("sys.stdin", io.StringIO(SECRET))
            before = len(calls)
            snapshots[f"{mode}:HTTP{status}:{code}"] = invoke(
                capsys, ["--mode", mode, *checked, "--expect-empty"], exit_code
            )
            assert len(calls) == before + 1
        failure = None
    assert all(SECRET not in path.read_text() for path in tmp_path.iterdir() if path.is_file())
    snapshot = Path(__file__).with_name("snapshots") / "management-confidential-cli.json"
    if os.environ.get("BID_UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(json.dumps(snapshots, ensure_ascii=False, indent=2) + "\n")
    assert snapshots == json.loads(snapshot.read_text())


def test_management_confidential_invalid_and_legacy_no_request(monkeypatch, capsys, tmp_path):
    calls = []
    monkeypatch.setattr(cli, "call", lambda *a, **kw: calls.append((a, kw)))

    class Unreadable:
        def isatty(self):
            return False

        def read(self, *_args):
            pytest.fail("invalid arguments must not consume secret stdin")

    monkeypatch.setattr("sys.stdin", Unreadable())
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"limit": 101}')
    checked = ["confidential", "set-checked", "--field", ID, "--expected-field-revision", "1"]
    for flags in (
        ["confidential", "browse"],
        ["confidential", "field", "browse", "--input", str(invalid)],
        ["confidential", "browse", "--input", str(tmp_path / "missing.json")],
        ["confidential", "history-page"],
        ["confidential", "history-page", "--field", ID, "--cursor", ""],
        ["confidential", "history-page", "--field", ID, "--limit", "101"],
        [*checked, "--value-stdin"],
        [*checked, "--expect-empty"],
        [*checked, "--value-stdin", "--expected-value", ID, "--expect-empty"],
        [*checked, "--value-stdin", "--expected-value", "broken"],
        [*checked, "--value-stdin", "--expect-empty", "--expected-field-revision", "0"],
        [*checked, "--value-stdin", "--expect-empty", "--input", str(invalid)],
        [*checked, "--value-stdin", "--expect-empty", "--value", SECRET],
    ):
        invoke(capsys, flags, 2)
    for command in COMMANDS:
        body = invoke(capsys, ["--contract-version", "3.0", *command.split()], 2)
        assert body["data"]["error"]["message"] == "This command requires contract 4.0"
    assert calls == []


@pytest.mark.parametrize("value", ["", "  \n", "x" * 2001, "x" * 8193])
def test_management_confidential_rejects_invalid_stdin(value, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "call", lambda *a, **kw: calls.append(a))
    monkeypatch.setattr("sys.stdin", io.StringIO(value))
    body = invoke(
        capsys,
        [
            "confidential",
            "set-checked",
            "--field",
            ID,
            "--expected-field-revision",
            "1",
            "--expect-empty",
            "--value-stdin",
        ],
        2,
    )
    assert value not in json.dumps(body) if value.strip() else True
    assert calls == []


def test_management_confidential_schema_and_legacy_list(monkeypatch, capsys):
    commands = invoke(capsys, ["schema"])["data"]["commands"]
    for command in COMMANDS:
        assert commands[command]["input"] and commands[command]["output"]
    for command in COMMANDS[:2]:
        assert commands[command]["input"]["properties"]["field_id"]["default"] is None
    assert commands[COMMANDS[0]]["items"]["title"] == "ConfidentialFieldView"
    assert commands[COMMANDS[1]]["items"]["title"] == "ConfidentialValueView"
    assert commands[COMMANDS[3]]["output"]["title"] == "ConfidentialValueView"
    write = commands[COMMANDS[3]]["input"]
    assert "value" in write["required"] and "expected_value_id" in write["required"]
    assert "confidential reveal" not in commands
    legacy = invoke(capsys, ["--contract-version", "3.0", "schema"])["data"]["commands"]
    assert all(command not in legacy for command in COMMANDS)
    calls = []

    def call(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return Result(ok=True, command="confidential list", items=[VALUE]).model_dump(mode="json")

    monkeypatch.setattr(cli, "call", call)
    invoke(capsys, ["confidential", "list", "--task", ID])
    assert calls == [("GET", "/confidential-values", {"params": {"task_id": ID}})]


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize("defect", ["plaintext", "broken_page", "wrong_command"])
def test_management_confidential_rejects_malformed_projection(
    command, defect, monkeypatch, capsys, tmp_path
):
    response = projection(command)
    if defect == "plaintext":
        target = response["data"] if command == COMMANDS[3] else response["items"][0]
        target["value"] = SECRET
    elif defect == "broken_page":
        if command == COMMANDS[3]:
            response["items"] = [VALUE]
        else:
            response["data"]["has_more"] = True
    else:
        response["command"] = "confidential list"
    monkeypatch.setattr(cli, "call", lambda *_a, **_kw: response)
    monkeypatch.setattr("sys.stdin", io.StringIO(SECRET))
    query = tmp_path / "query.json"
    query.write_text("{}")
    flags = command.split()
    if command in COMMANDS[:2]:
        flags += ["--input", str(query)]
    else:
        flags += ["--field", ID]
    if command == COMMANDS[3]:
        flags += ["--expected-field-revision", "1", "--expect-empty", "--value-stdin"]
    body = invoke(capsys, flags, 4)
    assert body["data"]["error"]["code"] == "invalid_server_response"
