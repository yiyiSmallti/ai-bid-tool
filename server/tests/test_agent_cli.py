"""DB-free public command contracts and complete invocation schemas.

Failure matrix: missing input, malformed JSON, forbidden server-owned fields,
invalid page bounds, legacy versions, transport failures and partial completion.
The transport boundary is injected; the real Typer command and Result emitter run.
"""

import json
from pathlib import Path
from uuid import UUID

import pytest
from app.core.errors import ServiceError
from app.schemas.contracts import Result
from bid_cli import main as cli
from bid_cli.schema import command_schema

KEY = "00000000-0000-0000-0000-000000000001"
SECOND = "00000000-0000-0000-0000-000000000002"
START = {
    "extraction_job_id": SECOND,
    "message": "Generate proposals, pause for human review, then assemble the draft.",
    "requested_scopes": ["task:read", "job:read", "card:read", "card:generate", "draft:run"],
    "limits": {"max_vendor_usd": "2", "max_platform_charge": "2", "billing_currency": "USD"},
    "idempotency_key": KEY,
}
INPUTS = {
    "start": START,
    "message": {
        "message": "Use the reviewed cards",
        "expected_revision": 1,
        "idempotency_key": KEY,
    },
    "resume": {"expected_revision": 1, "pause_id": SECOND, "idempotency_key": KEY},
    "cancel": {"expected_revision": 1, "idempotency_key": KEY},
}
ROUTES = {
    "start": ("POST", f"/tasks/{KEY}/agent-sessions"),
    "list": ("GET", f"/tasks/{KEY}/agent-sessions"),
    "show": ("GET", f"/agent-sessions/{KEY}"),
    "messages": ("GET", f"/agent-sessions/{KEY}/messages"),
    "steps": ("GET", f"/agent-sessions/{KEY}/steps"),
    "message": ("POST", f"/agent-sessions/{KEY}/messages"),
    "resume": ("POST", f"/agent-sessions/{KEY}/resume"),
    "cancel": ("POST", f"/agent-sessions/{KEY}/cancel"),
}


def invoke(args, capsys):
    try:
        cli.main([*args, "--json"])
    except SystemExit as exc:
        code = exc.code
    else:
        code = 0
    return code, json.loads(capsys.readouterr().out)


def arguments(action, tmp_path):
    args = ["agent", action, "--task" if action in {"start", "list"} else "--id", KEY]
    if action in INPUTS:
        source = tmp_path / f"{action}.json"
        source.write_text(json.dumps(INPUTS[action]))
        args += ["--input", str(source)]
    if action in {"list", "messages", "steps"}:
        args += ["--cursor", SECOND, "--limit", "3"]
    return args


@pytest.mark.parametrize("mode", ["remote", "local"])
@pytest.mark.parametrize("action", ROUTES)
def test_agent_command_snapshots_without_database(monkeypatch, tmp_path, capsys, mode, action):
    calls = []

    def call(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return Result(ok=True, command=f"agent {action}").model_dump(mode="json")

    monkeypatch.setattr(cli, "call", call)
    code, body = invoke(
        ["--mode", mode, "--state", str(tmp_path / "state.enc"), *arguments(action, tmp_path)],
        capsys,
    )
    assert code == 0
    Result.model_validate(body)
    assert len(calls) == 1 and calls[0][:2] == ROUTES[action]
    if action in {"list", "messages", "steps"}:
        assert calls[0][2] == {"params": {"cursor": SECOND, "limit": 3}}
    if action in INPUTS:
        for name, value in INPUTS[action].items():
            if name != "limits":
                assert calls[0][2]["json"][name] == value
    body["duration_ms"] = 0
    snapshot = json.loads((Path(__file__).parent / "snapshots" / "agent-cli.json").read_text())
    assert body == snapshot[action]


def test_agent_start_dry_run_overrides_input(monkeypatch, tmp_path, capsys):
    calls = []

    def call(*args, **kwargs):
        calls.append(kwargs)
        return Result(ok=True, command="agent start").model_dump(mode="json")

    monkeypatch.setattr(cli, "call", call)
    code, _ = invoke([*arguments("start", tmp_path), "--dry-run"], capsys)
    assert code == 0 and calls[0]["json"]["dry_run"] is True


@pytest.mark.parametrize("action", INPUTS)
@pytest.mark.parametrize("failure", ["malformed", "server_owned", "missing"])
def test_agent_invalid_input_does_not_call_server(monkeypatch, tmp_path, capsys, action, failure):
    def call(*args, **kwargs):
        pytest.fail("Invalid input reached the server")

    monkeypatch.setattr(cli, "call", call)
    args = arguments(action, tmp_path)
    if failure == "missing":
        args = args[: args.index("--input")]
    else:
        source = Path(args[args.index("--input") + 1])
        source.write_text(
            "{" if failure == "malformed" else json.dumps({**INPUTS[action], "org_id": KEY})
        )
    code, body = invoke(args, capsys)
    assert code == 2 and body["data"]["error"]["code"] == "invalid_input"


@pytest.mark.parametrize("action", ["list", "messages", "steps"])
def test_agent_invalid_pagination(monkeypatch, tmp_path, capsys, action):
    monkeypatch.setattr(cli, "call", lambda *a, **k: pytest.fail("Invalid page reached server"))
    args = arguments(action, tmp_path)
    args[args.index("--limit") + 1] = "101"
    assert invoke(args, capsys)[0] == 2


@pytest.mark.parametrize("exit_code", [2, 3, 4])
def test_agent_server_error_exit_contract(monkeypatch, tmp_path, capsys, exit_code):
    def call(*args, **kwargs):
        raise ServiceError("synthetic_failure", "Synthetic failure", 503, exit_code)

    monkeypatch.setattr(cli, "call", call)
    code, body = invoke(arguments("show", tmp_path), capsys)
    assert code == exit_code
    from app.schemas.agent_contracts import AgentFailureData

    failure = AgentFailureData.model_validate(body["data"])
    assert body["ok"] is False and failure.error.retryable == (exit_code == 3)


def test_agent_partial_session_exit_contract(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        cli,
        "call",
        lambda *a, **k: Result(
            ok=True, command="agent show", data={"session": {"state": "partial"}}
        ).model_dump(mode="json"),
    )
    code, body = invoke(arguments("show", tmp_path), capsys)
    assert code == 5 and body["ok"] is False and body["data"]["session"]["state"] == "partial"


def test_agent_schema_and_complete_tool_invocations():
    from app.schemas.agent_contracts import (
        CardGenerateArguments,
        CardShowArguments,
        DraftArguments,
        DraftShowArguments,
        ExtractionArguments,
        JobStatusArguments,
    )

    schema = command_schema(cli.app)["commands"]
    for action in ROUTES:
        entry = schema[f"agent {action}"]
        assert "output" in entry
        assert any("--json" in param["options"] for param in entry["cli_parameters"])
    for name, model in {
        "req list": ExtractionArguments,
        "card list": ExtractionArguments,
        "card show": CardShowArguments,
        "card generate": CardGenerateArguments,
        "draft": DraftArguments,
        "draft show": DraftShowArguments,
        "job status": JobStatusArguments,
    }.items():
        assert schema[name]["invocation_input"] == model.model_json_schema()
        assert schema[name]["invocation_input"]["additionalProperties"] is False
    assert schema["req list"]["input"] is None
    assert schema["card generate"]["input"]["title"] == "CardGenerateRequest"
    assert schema["agent list"]["items"]["title"] == "AgentSessionView"
    assert schema["agent messages"]["items"]["title"] == "AgentMessageView"
    assert schema["agent steps"]["items"]["title"] == "AgentStepView"
    assert UUID(KEY)


@pytest.mark.parametrize("action", ROUTES)
def test_agent_commands_require_current_contract(monkeypatch, tmp_path, capsys, action):
    monkeypatch.setattr(
        cli, "call", lambda *a, **k: pytest.fail("Legacy agent request reached server")
    )
    assert invoke(["--contract-version", "3.0", *arguments(action, tmp_path)], capsys)[0] == 2
    assert not any(name.startswith("agent ") for name in command_schema(cli.app, "3.0")["commands"])
