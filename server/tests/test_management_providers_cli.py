"""DB-free CLI acceptance for metadata reads, written before their commands.

Failure cases: accidental secret calls; unbounded pages or lost cursors;
foreign-org/exact-ID mismatches; reflected secrets in metadata, warnings or errors;
legacy schema exposure; a retry of a failed read; changed legacy provider commands.
"""

import json
from contextlib import asynccontextmanager

import httpx
import pytest
from app.schemas.contracts import Cost, Result
from bid_cli import main as cli
from bid_cli.client import Client

ID = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000002"
WHEN = "2026-10-06T00:00:00Z"
PREFIX = "/v4/management/providers"
SECRET = "synthetic-provider-private"
ROW = {
    "id": ID,
    "slot": OTHER,
    "revision": 1,
    "configuration": {
        "capability": "llm_extract",
        "provider": "openai",
        "model": "Saved exact model",
    },
    "provider": "openai",
    "model": "Saved exact model",
    "credential_state": "configured",
    "updated_at": WHEN,
    "revised_at": WHEN,
    "reasoning": [],
    "default_reasoning": None,
}
CHOICE = {
    "id": "enabled-model",
    "revision": 2,
    "model": "Available model",
    "provider": "anthropic",
    "default": True,
    "reasoning": [{"name": "high", "label": "High"}],
    "default_reasoning": "high",
}


def projection(command, empty=False):
    if command == "provider show":
        data = {
            "capability": "llm_extract",
            "configured": True,
            "current": ROW,
            "models": [ROW],
            "default_slot": OTHER,
            "reasoning": [],
            "actions": [{"action": "configure", "allowed": True, "reason": None}],
            "connection_status": "not_checked",
        }
        items = []
    elif command == "provider revision show":
        data, items = ROW, []
    else:
        data = {
            "as_of": WHEN,
            "returned": 0 if empty else 1,
            "next_cursor": None,
            "has_more": False,
        }
        items = [] if empty else [ROW]
    return Result(
        ok=True,
        command=command,
        data=data,
        items=items,
        cost=Cost(),
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
    assert SECRET not in json.dumps(body)
    body["duration_ms"] = 0
    return body


def test_management_provider_transport_snapshots(monkeypatch, capsys):
    monkeypatch.setenv("BID_ACCESS_KEY", "synthetic-session")
    calls, snapshots = [], {}
    command, empty, failure = "provider show", False, None

    def handler(request):
        assert request.headers["authorization"] == "Bearer synthetic-session"
        assert "x-org-id" not in request.headers
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
                    command=command,
                    data={"error": {"code": code, "message": SECRET, "exit_code": exit_code}},
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
    cases = [
        ("provider show", ["show"], "GET", "", None),
        (
            "provider revision show",
            ["revision", "show", "--id", ID],
            "GET",
            "/revisions/" + ID,
            None,
        ),
        (
            "provider history-page",
            ["history-page", "--cursor", "opaque-history", "--limit", "2"],
            "POST",
            "/history/query",
            {"cursor": "opaque-history", "limit": 2},
        ),
    ]
    for mode in ("remote", "local"):
        for command, flags, method, path, content in cases:
            snapshots[mode + ":" + command] = invoke(capsys, ["--mode", mode, "provider", *flags])
            assert calls[-1] == (method, PREFIX + path, {}, content)
        empty = True
        for command, flags, _, _, _ in cases[2:]:
            snapshots[mode + ":empty:" + command] = invoke(
                capsys, ["--mode", mode, "provider", *flags]
            )
            assert snapshots[mode + ":empty:" + command]["items"] == []
        empty = False
        command = "provider show"
        for status, code, exit_code in (
            (404, "not_found", 4),
            (403, "forbidden", 4),
            (422, "invalid_input", 2),
            (429, "rate_limit", 3),
            (503, "unavailable", 3),
            (400, "management_cursor_invalid", 2),
            (409, "management_cursor_expired", 2),
            (413, "management_result_too_large", 2),
            (503, "management_query_timeout", 3),
            (409, "management_integrity_error", 4),
        ):
            failure = status, code, exit_code
            before = len(calls)
            key = f"{mode}:HTTP{status}" + (":" + code if code.startswith("management_") else "")
            snapshots[key] = invoke(capsys, ["--mode", mode, "provider", "show"], exit_code)
            assert snapshots[key]["data"]["error"]["code"] == code
            assert len(calls) == before + 1
        failure = None


def test_management_provider_invalid_and_legacy_no_request(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "call", lambda *a, **kw: calls.append(a))
    for flags in (
        ["revision", "show"],
        ["revision", "show", "--id", "invalid"],
        ["history-page", "--limit", "101"],
    ):
        assert invoke(capsys, ["provider", *flags], 2)["data"]["error"]["code"] == "invalid_input"
    for command in ("show", "revision show", "history-page"):
        with pytest.raises(SystemExit) as error:
            cli.main(["--contract-version", "3.0", "provider", *command.split(), "--json"])
        assert error.value.code == 2
        assert (
            json.loads(capsys.readouterr().out)["data"]["error"]["message"]
            == "Only contract 5.0 is supported"
        )
    assert calls == []


@pytest.mark.parametrize(
    "command,defect",
    [
        ("provider revision show", "wrong_id"),
        ("provider show", "secret"),
        ("provider revision show", "secret"),
        ("provider history-page", "secret"),
        ("provider show", "warning"),
        ("provider show", "removed_metadata"),
        ("provider show", "removed_cost"),
    ],
)
def test_management_provider_rejects_unsafe_metadata(command, defect, monkeypatch, capsys):
    monkeypatch.setenv("BID_ACCESS_KEY", "synthetic-session")
    response = projection(command)
    if defect == "wrong_id":
        response["data"]["id"] = OTHER
    elif defect == "broken_cursor":
        response["data"]["has_more"] = True
    elif defect == "secret":
        target = (
            response["data"]
            if command in {"provider show", "provider revision show"}
            else response["items"][0]
        )
        target["api_key"] = SECRET
    elif defect == "removed_metadata":
        response["data"]["billing_currency"] = "CNY"
    elif defect == "removed_cost":
        response["cost"]["usd"] = 1
    else:
        response["warnings"] = [SECRET]
    monkeypatch.setattr(cli, "call", lambda *a, **kw: response)
    flags = command.split()
    if command == "provider revision show":
        flags += ["--id", ID]
    result = invoke(capsys, flags, 4)
    assert result["data"]["error"]["code"] == "invalid_server_response"


def test_management_provider_schema(capsys):
    commands = invoke(capsys, ["schema"])["data"]["commands"]
    for command in (
        "provider show",
        "provider revision show",
        "provider history-page",
    ):
        assert commands[command]["output"] is not None
        assert "api_key" not in json.dumps(commands[command])
        assert "key_last4" not in json.dumps(commands[command])
    assert "ProviderSettingsData" in json.dumps(commands["provider show"])
    assert "ProviderRevisionMetadata" in json.dumps(commands["provider revision show"])
    with pytest.raises(SystemExit) as error:
        cli.main(["--contract-version", "3.0", "schema", "--json"])
    assert error.value.code == 2
    assert json.loads(capsys.readouterr().out)["data"]["error"]["code"] == "invalid_input"


def test_provider_metadata_error_never_echoes_arbitrary_code_or_message(monkeypatch, capsys):
    from app.core.errors import ServiceError

    monkeypatch.setenv("BID_ACCESS_KEY", "synthetic-session")

    def call(*args, **kwargs):
        raise ServiceError(SECRET, SECRET, 503, 3)

    monkeypatch.setattr(cli, "call", call)
    assert (
        invoke(capsys, ["provider", "show"], 4)["data"]["error"]["code"]
        == "invalid_server_response"
    )
