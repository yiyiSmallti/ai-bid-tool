"""Platform-only CLI transport and secret-free snapshots for both execution modes."""

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from app.schemas.contracts import Result
from bid_cli import main as cli
from bid_cli.client import Client

CONFIG = {
    "expected_revision": None,
    "account_id": "a" * 32,
    "gateway_id": "presence",
    "enabled": True,
    "price_revision": 1,
    "fixed_sale_price": "0.01",
    "currency": "USD",
    "workers_credential_id": "00000000-0000-4000-8000-000000000001",
    "gateway_credential_id": "00000000-0000-4000-8000-000000000002",
    "platform_model_id": "bid-review-clef",
    "revision": 1,
    "workers_credential_revision": 1,
    "gateway_credential_revision": 1,
    "gateway_check": None,
    "updated_at": "2026-10-08T00:00:00Z",
}
INPUT = {
    key: CONFIG[key]
    for key in (
        "expected_revision",
        "account_id",
        "gateway_id",
        "enabled",
        "price_revision",
        "fixed_sale_price",
        "currency",
        "workers_credential_id",
        "gateway_credential_id",
    )
}
CHECK = {
    "gateway_id": "presence",
    "authentication": True,
    "collect_logs": False,
    "logpush": False,
    "cache_ttl": 0,
    "gateway_retries": False,
    "rate_limit_requests": 200,
    "rate_limit_seconds": 60,
    "workers_ai_billing_mode": "unified",
    "checked_at": "2026-10-08T00:00:00Z",
}


@pytest.fixture
def interface(monkeypatch):
    calls = []
    monkeypatch.setenv("BID_PLATFORM_SESSION", "synthetic-platform-session")
    monkeypatch.setenv("BID_SESSION", "synthetic-org-session")
    monkeypatch.setenv("BID_ORG", CONFIG["workers_credential_id"])

    def handler(request):
        assert request.headers["authorization"] == "Bearer synthetic-platform-session"
        assert "x-org-id" not in request.headers
        assert request.url.path.startswith("/v4/platform/clef")
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            command, data = (
                "platform clef show",
                {"config": CONFIG, "blockers": ["clef_gateway_unchecked"]},
            )
        elif request.method == "PUT":
            assert json.loads(request.content) == INPUT
            command, data = (
                "platform clef set",
                {"config": CONFIG, "blockers": ["clef_gateway_unchecked"]},
            )
        else:
            assert request.url.path.endswith("/check")
            assert json.loads(request.content) == {"expected_revision": 1}
            command, data = (
                "platform clef check",
                {"config": CONFIG | {"revision": 2, "gateway_check": CHECK}, "blockers": []},
            )
        return httpx.Response(
            200, json=Result(ok=True, command=command, data=data).model_dump(mode="json")
        )

    @asynccontextmanager
    async def transport(self):
        async with httpx.AsyncClient(
            base_url="https://console.example.test", transport=httpx.MockTransport(handler)
        ) as client:
            yield client

    monkeypatch.setattr(Client, "transport", transport)
    return calls


def invoke(args, capsys):
    code = 0
    try:
        cli.main([*args, "--json"])
    except SystemExit as error:
        code = error.code
    body = json.loads(capsys.readouterr().out)
    assert "synthetic-platform-session" not in json.dumps(body)
    body["duration_ms"] = 0
    return code, body


def test_commands_snapshot_both_modes(interface, tmp_path, capsys):
    input_file = tmp_path / "clef.json"
    input_file.write_text(json.dumps(INPUT))
    actual = {}
    for mode in ("remote", "local"):
        for action, arguments in {
            "show": ["show"],
            "set": ["set", "--input", str(input_file)],
            "check": ["check", "--expected-revision", "1"],
        }.items():
            code, body = invoke(["--mode", mode, "platform", "clef", *arguments], capsys)
            assert code == 0, body
            actual[f"{mode}: {action}"] = body
    snapshot = Path(__file__).with_name("snapshots") / "platform-clef-cli.json"
    if os.environ.get("BID_UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n")
    assert actual == json.loads(snapshot.read_text())
    assert len(interface) == 6


def test_schema_exposes_platform_clef_contract(capsys):
    cli.main(["schema", "--json"])
    commands = json.loads(capsys.readouterr().out)["data"]["commands"]
    for action in ("show", "set", "check"):
        assert commands["platform clef " + action]["cli_parameters"]
        assert commands["platform clef " + action]["output"]
    assert "fixed_sale_price" in commands["platform clef set"]["input"]["properties"]


def test_missing_revision_and_secret_fields_rejected_before_transport(interface, tmp_path, capsys):
    code, _ = invoke(["platform", "clef", "check"], capsys)
    assert code == 2 and not interface
    input_file = tmp_path / "clef.json"
    input_file.write_text(json.dumps(INPUT | {"workers_token": "synthetic-secret"}))
    code, body = invoke(["platform", "clef", "set", "--input", str(input_file)], capsys)
    assert code == 2 and not interface
    assert "synthetic-secret" not in json.dumps(body)
