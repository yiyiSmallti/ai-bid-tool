"""Memory public CLI and role contract checks."""

import json

import pytest
from app.services.auth import ROLE_SCOPES, SCOPES
from bid_cli import main as cli
from bid_cli.schema import command_schema
from typer.testing import CliRunner

COMMANDS = {
    "memory add",
    "memory list",
    "memory show",
    "memory update",
    "memory history",
    "memory approve",
    "memory reject",
    "memory disable",
    "memory delete",
    "memory retrieve",
    "memory retrieval show",
    "memory used",
    "memory feedback list",
    "memory candidates run",
    "memory samples list",
    "memory samples show",
    "memory samples review",
}


def test_schema_registers_all_memory_contracts():
    schema = command_schema(cli.app)
    assert COMMANDS <= schema["commands"].keys()
    for name in COMMANDS:
        assert "output" in schema["commands"][name]
        assert any(
            "--json" in item["options"] for item in schema["commands"][name]["cli_parameters"]
        )


@pytest.mark.parametrize("role", ["admin", "bidder", "technical", "viewer"])
def test_memory_role_gate(role):
    scopes = ROLE_SCOPES[role]
    assert {"memory:read", "memory:retrieve"} <= scopes
    assert ("memory:write" in scopes) == (role != "viewer")
    assert ("memory:approve" in scopes) == (role == "admin")
    assert ("memory:manage" in scopes) == (role == "admin")
    assert ("memory:eval:review" in scopes) == (role == "admin")
    assert (
        not {"memory:approve", "memory:manage", "memory:eval:read", "memory:eval:review"} & SCOPES
    )


@pytest.mark.parametrize(
    "action,method,path",
    [
        ("show", "GET", "/memories/{id}"),
        ("used", "GET", "/jobs/{id}/memory"),
        ("history", "GET", "/memories/{id}/history"),
        ("retrieval show", "GET", "/memory-retrievals/{id}"),
        ("feedback list", "GET", "/tasks/{id}/memory-feedback"),
        ("samples list", "GET", "/tasks/{id}/memory-evaluations"),
        ("samples show", "GET", "/memory-evaluations/{id}"),
    ],
)
def test_memory_json_snapshot(monkeypatch, action, method, path):
    key = "00000000-0000-0000-0000-000000000001"
    calls = []

    def call(verb, url, **kwargs):
        calls.append((verb, url))
        return {
            "ok": True,
            "command": f"memory {action}",
            "data": {},
            "items": [],
            "warnings": [],
            "cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0.0},
            "duration_ms": 0,
        }

    monkeypatch.setattr(cli, "call", call)
    arg = (
        "--job"
        if action == "used"
        else "--task"
        if action in {"feedback list", "samples list"}
        else "--id"
    )
    result = CliRunner().invoke(cli.app, ["memory", *action.split(), arg, key, "--json"])
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert list(body) == ["ok", "command", "data", "items", "warnings", "cost", "duration_ms"]
    assert calls == [(method, path.format(id=key))]


@pytest.mark.parametrize(
    "command,flags,method,path,payload",
    [
        (
            "add",
            [],
            "POST",
            "/memories",
            {
                "target": {"scope": "org"},
                "content": {
                    "kind": "rule",
                    "conflict_key": "delivery.source",
                    "text": "交货期以答疑为准",
                },
            },
        ),
        (
            "update",
            ["--id"],
            "PUT",
            "/memories/{id}",
            {
                "expected_revision": 1,
                "content": {
                    "kind": "rule",
                    "conflict_key": "delivery.source",
                    "text": "交货期以答疑为准",
                },
            },
        ),
        (
            "approve",
            ["--id"],
            "POST",
            "/memories/{id}/decisions",
            {"expected_revision": 1, "action": "approve", "reason": "Reviewed"},
        ),
        (
            "reject",
            ["--id"],
            "POST",
            "/memories/{id}/decisions",
            {"expected_revision": 1, "action": "reject", "reason": "Reviewed"},
        ),
        (
            "disable",
            ["--id"],
            "POST",
            "/memories/{id}/disable",
            {"expected_revision": 1, "reason": "Reviewed"},
        ),
        (
            "delete",
            ["--id"],
            "DELETE",
            "/memories/{id}",
            {"expected_revision": 1, "reason": "Reviewed"},
        ),
        (
            "retrieve",
            ["--dry-run"],
            "POST",
            "/memories/retrieve",
            {
                "org_id": "00000000-0000-0000-0000-000000000001",
                "scopes": ["org"],
                "query": "交货期",
            },
        ),
        (
            "samples review",
            ["--id"],
            "POST",
            "/memory-evaluations/{id}/review",
            {"expected_revision": 1, "action": "accept", "reason": "Reviewed"},
        ),
        (
            "candidates run",
            ["--task", "--dry-run"],
            "POST",
            "/tasks/{id}/memory-candidates",
            {"event_ids": ["00000000-0000-0000-0000-000000000001"]},
        ),
    ],
)
def test_memory_input_commands_snapshot(
    monkeypatch, tmp_path, command, flags, method, path, payload
):
    key = "00000000-0000-0000-0000-000000000001"
    source = tmp_path / "input.json"
    source.write_text(json.dumps(payload))
    calls = []

    def call(verb, url, **kwargs):
        calls.append((verb, url, kwargs))
        return {
            "ok": True,
            "command": f"memory {command}",
            "data": {},
            "items": [],
            "warnings": [],
            "cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0.0},
            "duration_ms": 0,
        }

    monkeypatch.setattr(cli, "call", call)
    args = ["memory", *command.split(), "--input", str(source), "--json"]
    for flag in flags:
        args.append(flag)
        if flag in {"--id", "--task"}:
            args.append(key)
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert list(json.loads(result.output)) == [
        "ok",
        "command",
        "data",
        "items",
        "warnings",
        "cost",
        "duration_ms",
    ]
    assert calls[0][:2] == (method, path.format(id=key))
    if command == "retrieve":
        assert calls[0][2]["params"] == {"preview": True}
    if command == "candidates run":
        assert calls[0][2]["json"]["action"]["dry_run"] is True


@pytest.mark.parametrize(
    "status,exit_code",
    [("failed", 2), ("failed", 3), ("failed", 4), ("cancelled", 4), ("succeeded", 5)],
)
def test_memory_job_status_exit_snapshot(monkeypatch, capsys, status, exit_code):
    key = "00000000-0000-0000-0000-000000000001"

    def call(*args, **kwargs):
        return {
            "ok": True,
            "command": "job status",
            "data": {
                "id": key,
                "kind": "memory_candidate",
                "status": status,
                "error": {
                    "code": "synthetic_failure",
                    "message": "Synthetic failure",
                    "exit_code": exit_code,
                }
                if status == "failed"
                else None,
                "result": {"completion": "partial"} if status == "succeeded" else None,
            },
            "items": [],
            "warnings": [],
            "cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0.0},
            "duration_ms": 0,
        }

    monkeypatch.setattr(cli, "call", call)
    with pytest.raises(SystemExit) as result:
        cli.main(["job", "status", key, "--json"])
    assert result.value.code == exit_code
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is False
    assert set(body) == {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
