"""DB-free CLI acceptance for metadata reads, written before their commands.

Failure cases: accidental balance/secret calls; unbounded pages or lost cursors;
foreign-org/exact-ID mismatches; reflected secrets in metadata, warnings or errors;
legacy schema exposure; a retry of a failed read; changed legacy provider commands.
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
OTHER = "00000000-0000-4000-8000-000000000002"
WHEN = "2026-10-06T00:00:00Z"
PREFIX = "/v4/management/providers"
SECRET = "synthetic-provider-private"
ROW = {
    "id": ID,
    "org_id": ID,
    "revision": 1,
    "configuration": {
        "capability": "llm_extract",
        "source": "platform",
        "platform_model_id": "retired-model",
    },
    "provider": "openai",
    "model": "Saved exact model",
    "catalog_state": "unavailable",
    "credential_state": "platform_managed",
    "updated_by": ID,
    "updated_at": WHEN,
    "revised_at": WHEN,
    "revised_by": None,
    "reasoning": [],
    "default_reasoning": None,
    "catalog_revision": 1,
    "sale_input_per_mtok": 1.0,
    "sale_output_per_mtok": 2.0,
}
CHOICE = {
    "id": "enabled-model",
    "revision": 2,
    "model": "Available model",
    "provider": "anthropic",
    "sale_input_per_mtok": 2.0,
    "sale_output_per_mtok": 4.0,
    "default": True,
    "reasoning": [{"name": "high", "label": "High"}],
    "default_reasoning": "high",
}


def projection(command, empty=False, billing_currency="USD"):
    if command == "provider show":
        data = {
            "org_id": ID,
            "capability": "llm_extract",
            "effective_source": "platform",
            "current": ROW,
            "default_model": None,
            "billing_currency": billing_currency,
            "reasoning": [],
            "actions": [{"action": "configure", "allowed": True, "reason": None}],
            "connection_status": "not_checked",
        }
        items = []
    elif command == "provider revision show":
        data, items = ROW, []
    else:
        data = {
            "org_id": ID,
            "as_of": WHEN,
            "returned": 0 if empty else 1,
            "next_cursor": None,
            "has_more": False,
        }
        items = [] if empty else [CHOICE if command == "provider catalog" else ROW]
    return Result(
        ok=True,
        command=command,
        data=data,
        items=items,
        cost=Cost(billing_currency=billing_currency),
    ).model_dump(mode="json")


def invoke(capsys, flags, exit_code=0, billing_currency="USD"):
    if exit_code:
        with pytest.raises(SystemExit) as error:
            cli.main([*flags, "--json"])
        assert error.value.code == exit_code
    else:
        cli.main([*flags, "--json"])
    body = json.loads(capsys.readouterr().out)
    assert set(body) == {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
    assert body["cost"] == Cost(billing_currency=billing_currency).model_dump(mode="json")
    assert SECRET not in json.dumps(body)
    body["duration_ms"] = 0
    return body


def test_management_provider_transport_snapshots(monkeypatch, capsys):
    monkeypatch.setenv("BID_SESSION", "synthetic-session")
    monkeypatch.setenv("BID_ORG", ID)
    calls, snapshots = [], {}
    command, empty, failure = "provider show", False, None

    def handler(request):
        assert request.headers["authorization"] == "Bearer synthetic-session"
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
        ("provider catalog", ["catalog"], "POST", "/catalog/query", {"limit": 25}),
        (
            "provider catalog",
            ["catalog", "--q", "ENAB", "--cursor", "opaque-catalog", "--limit", "2"],
            "POST",
            "/catalog/query",
            {"q": "ENAB", "cursor": "opaque-catalog", "limit": 2},
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
    snapshot = Path(__file__).with_name("snapshots") / "management-providers-cli.json"
    if os.environ.get("BID_UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(
            json.dumps(snapshots, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
    assert snapshots == json.loads(snapshot.read_text())


def test_management_provider_invalid_and_legacy_no_request(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "call", lambda *a, **kw: calls.append(a))
    for flags in (
        ["revision", "show"],
        ["revision", "show", "--id", "invalid"],
        ["history-page", "--limit", "101"],
        ["catalog", "--limit", "0"],
        ["catalog", "--cursor", ""],
        ["catalog", "--q", " "],
    ):
        assert invoke(capsys, ["provider", *flags], 2)["data"]["error"]["code"] == "invalid_input"
    for command in ("show", "revision show", "history-page", "catalog"):
        with pytest.raises(SystemExit) as error:
            cli.main(["--contract-version", "3.0", "provider", *command.split(), "--json"])
        assert error.value.code == 2
        assert (
            json.loads(capsys.readouterr().out)["data"]["error"]["message"]
            == "This command requires contract 4.0"
        )
    assert calls == []


@pytest.mark.parametrize(
    "command,defect",
    [
        ("provider show", "foreign_org"),
        ("provider revision show", "wrong_id"),
        ("provider history-page", "foreign_org"),
        ("provider catalog", "broken_cursor"),
        ("provider show", "secret"),
        ("provider revision show", "secret"),
        ("provider history-page", "secret"),
        ("provider catalog", "secret"),
        ("provider show", "warning"),
        ("provider catalog", "warning"),
        ("provider show", "currency_mismatch"),
        ("provider show", "paid_cost"),
        ("provider catalog", "paid_cost"),
    ],
)
def test_management_provider_rejects_unsafe_metadata(command, defect, monkeypatch, capsys):
    monkeypatch.setenv("BID_SESSION", "synthetic-session")
    monkeypatch.setenv("BID_ORG", ID)
    response = projection(command)
    if defect == "foreign_org":
        target = response["data"]["current"] if command == "provider show" else response["items"][0]
        target["org_id"] = OTHER
    elif defect == "wrong_id":
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
    elif defect == "currency_mismatch":
        response["data"]["billing_currency"] = "CNY"
    elif defect == "paid_cost":
        response["cost"]["usd"] = 1
    else:
        response["warnings"] = [SECRET]
    monkeypatch.setattr(cli, "call", lambda *a, **kw: response)
    flags = command.split()
    if command == "provider revision show":
        flags += ["--id", ID]
    result = invoke(capsys, flags, 4)
    assert result["data"]["error"]["code"] == "invalid_server_response"


@pytest.mark.parametrize("billing_currency", ["CNY", "EUR"])
@pytest.mark.parametrize(
    "flags", [["show"], ["revision", "show", "--id", ID], ["history-page"], ["catalog"]]
)
def test_management_provider_metadata_preserves_zero_cost_currency(
    flags, billing_currency, monkeypatch, capsys
):
    monkeypatch.setenv("BID_SESSION", "synthetic-session")
    monkeypatch.setenv("BID_ORG", ID)
    calls = []
    command = "provider " + " ".join(flags[:2] if flags[0] == "revision" else flags[:1])

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=projection(command, billing_currency=billing_currency))

    @asynccontextmanager
    async def transport(self):
        async with httpx.AsyncClient(
            base_url="http://local", transport=httpx.MockTransport(handler)
        ) as client:
            yield client

    monkeypatch.setattr(Client, "transport", transport)
    body = invoke(capsys, ["provider", *flags], billing_currency=billing_currency)
    assert body["ok"] and len(calls) == 1
    if command == "provider show":
        assert body["data"]["billing_currency"] == billing_currency


def test_management_provider_schema(capsys):
    commands = invoke(capsys, ["schema"])["data"]["commands"]
    for command in (
        "provider show",
        "provider revision show",
        "provider history-page",
        "provider catalog",
    ):
        assert commands[command]["output"] is not None
        assert "api_key" not in json.dumps(commands[command])
        assert "key_last4" not in json.dumps(commands[command])
    assert "ProviderSettingsData" in json.dumps(commands["provider show"])
    assert "ProviderRevisionMetadata" in json.dumps(commands["provider revision show"])
    assert "PlatformModelChoice" in json.dumps(commands["provider catalog"])
    cli.main(["--contract-version", "3.0", "schema", "--json"])
    legacy = json.loads(capsys.readouterr().out)["data"]["commands"]
    for command in (
        "provider show",
        "provider revision show",
        "provider history-page",
        "provider catalog",
    ):
        assert command not in legacy
    assert all("provider " + action in legacy for action in ("set", "list", "history", "test"))


@pytest.mark.parametrize(
    "field",
    [
        "api_key",
        "key_ciphertext",
        "key_last4",
        "fingerprint",
        "credential_id",
        "base_url",
        "input_usd_per_mtok",
        "output_usd_per_mtok",
    ],
)
def test_catalog_allowlist_rejects_secret_and_platform_internal_fields(field, monkeypatch, capsys):
    monkeypatch.setenv("BID_SESSION", "synthetic-session")
    monkeypatch.setenv("BID_ORG", ID)
    response = projection("provider catalog")
    response["items"][0][field] = SECRET
    monkeypatch.setattr(cli, "call", lambda *a, **kw: response)
    assert (
        invoke(capsys, ["provider", "catalog"], 4)["data"]["error"]["code"]
        == "invalid_server_response"
    )


def test_provider_metadata_error_never_echoes_arbitrary_code_or_message(monkeypatch, capsys):
    from app.core.errors import ServiceError

    monkeypatch.setenv("BID_SESSION", "synthetic-session")
    monkeypatch.setenv("BID_ORG", ID)

    def call(*args, **kwargs):
        raise ServiceError(SECRET, SECRET, 503, 3)

    monkeypatch.setattr(cli, "call", call)
    assert (
        invoke(capsys, ["provider", "show"], 4)["data"]["error"]["code"]
        == "invalid_server_response"
    )
