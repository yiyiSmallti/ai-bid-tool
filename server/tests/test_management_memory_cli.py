"""DB-free memory browse acceptance, written before its CLI implementation.

Failures: missing/invalid input sends a request; legacy invocation reaches v4;
local transport differs; failures change exit codes or retry; malformed receipts
leak another org/scope; browse replaces legacy listing or relevance retrieval.
"""

import copy
import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from app.schemas.contracts import Cost, LegacyCost, Result
from bid_cli import main as cli
from bid_cli.client import Client

ID = "00000000-0000-4000-8000-000000000001"
OTHER_ID = "00000000-0000-4000-8000-000000000002"
WHEN = "2026-10-06T00:00:00Z"
ROW = {
    "id": ID,
    "org_id": ID,
    "current": {
        "id": ID,
        "org_id": ID,
        "memory_id": ID,
        "revision": 1,
        "target": {"scope": "org", "user_id": None, "task_id": None},
        "content": {
            "kind": "rule",
            "text": "Inspect the original source before use",
            "tags": ["review"],
            "conflict_key": "source-review",
        },
        "status": "candidate",
        "source": {"origin": "human"},
        "content_sha256": "a" * 64,
        "created_at": WHEN,
        "created_by": ID,
        "actor_kind": "session",
    },
    "effective_status": "candidate",
    "deleted_at": None,
}


def projection(empty=False):
    return Result(
        ok=True,
        command="memory browse",
        data={
            "org_id": ID,
            "as_of": WHEN,
            "returned": 0 if empty else 1,
            "next_cursor": None,
            "has_more": False,
        },
        items=[] if empty else [copy.deepcopy(ROW)],
    ).model_dump(mode="json")


def invoke(capsys, args, exit_code=0):
    if exit_code:
        with pytest.raises(SystemExit) as error:
            cli.main([*args, "--json"])
        assert error.value.code == exit_code
    else:
        cli.main([*args, "--json"])
    body = json.loads(capsys.readouterr().out)
    assert set(body) == {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
    cost_model = LegacyCost if "3.0" in args else Cost
    assert body["cost"] == cost_model().model_dump(mode="json")
    body["duration_ms"] = 0
    return body


def test_memory_browse_remote_and_local_receipts(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("BID_SESSION", "synthetic-only")
    monkeypatch.setenv("BID_ORG", ID)
    calls = []
    response = projection()

    def handler(request):
        assert request.headers["authorization"] == "Bearer synthetic-only"
        assert request.headers["x-org-id"] == ID
        calls.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(200, json=response)

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
                "q": "Inspect",
                "kind": "rule",
                "status": "candidate",
                "tags": [" Review "],
                "expiry": "unexpired",
                "cursor": "opaque-memory-page",
                "limit": 2,
            }
        )
    )
    expected = {
        "scope": "org",
        "q": "Inspect",
        "kind": "rule",
        "status": "candidate",
        "tags": ["review"],
        "expiry": "unexpired",
        "include_deleted": False,
        "cursor": "opaque-memory-page",
        "limit": 2,
    }
    for mode in ("remote", "local"):
        assert invoke(capsys, ["--mode", mode, "memory", "browse", "--input", str(query)]) == {
            **response,
            "duration_ms": 0,
        }
        assert calls[-1] == ("POST", "/v4/management/memories/query", expected)
        response = projection(empty=True)
        assert (
            invoke(capsys, ["--mode", mode, "memory", "browse", "--input", str(query)])["items"]
            == []
        )
        response = projection()


@pytest.mark.parametrize(
    "status,code,exit_code",
    [
        (400, "management_cursor_invalid", 2),
        (403, "permission_denied", 4),
        (404, "not_found", 4),
        (409, "management_cursor_expired", 2),
        (413, "management_result_too_large", 2),
        (503, "management_query_timeout", 3),
    ],
)
def test_memory_browse_failure_receipt_is_not_retried(
    status, code, exit_code, monkeypatch, capsys, tmp_path
):
    monkeypatch.setenv("BID_SESSION", "synthetic-only")
    monkeypatch.setenv("BID_ORG", ID)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            json=Result(
                ok=False,
                command="memory browse",
                data={
                    "error": {"code": code, "message": "Synthetic failure", "exit_code": exit_code}
                },
            ).model_dump(mode="json"),
        )

    @asynccontextmanager
    async def transport(self):
        async with httpx.AsyncClient(
            base_url="http://local", transport=httpx.MockTransport(handler)
        ) as client:
            yield client

    monkeypatch.setattr(Client, "transport", transport)
    query = tmp_path / "query.json"
    query.write_text("{}")
    body = invoke(capsys, ["memory", "browse", "--input", str(query)], exit_code)
    assert body["data"]["error"] == {
        "code": code,
        "message": "Synthetic failure",
        "exit_code": exit_code,
    }
    assert len(calls) == 1


def test_memory_browse_invalid_input_and_legacy_send_no_request(monkeypatch, capsys, tmp_path):
    calls = []
    monkeypatch.setattr(cli, "call", lambda *a, **kw: calls.append(a))
    query = tmp_path / "query.json"
    for invalid in (
        {"limit": 101},
        {"limit": True},
        {"scope": "project"},
        {"q": " "},
        {"q": "x" * 201},
        {"tags": ["review", " Review "]},
        {"cursor": ""},
        {"status": "expired"},
        {"invented": "field"},
    ):
        query.write_text(json.dumps(invalid))
        body = invoke(capsys, ["memory", "browse", "--input", str(query)], 2)
        assert body["data"]["error"]["code"] == "invalid_input"
    invoke(capsys, ["memory", "browse"], 2)
    invoke(capsys, ["memory", "browse", "--input", str(tmp_path / "missing.json")], 2)
    body = invoke(capsys, ["--contract-version", "3.0", "memory", "browse"], 2)
    assert body["data"]["error"]["message"] == "This command requires contract 4.0"
    assert calls == []


@pytest.mark.parametrize(
    "defect", ["foreign_org", "scope", "cursor", "count", "command", "extra", "approval"]
)
def test_memory_browse_rejects_malformed_server_receipts(defect, monkeypatch, capsys, tmp_path):
    body = projection()
    if defect == "foreign_org":
        body["items"][0]["org_id"] = OTHER_ID
        body["items"][0]["current"]["org_id"] = OTHER_ID
    elif defect == "scope":
        body["items"][0]["current"]["target"] = {"scope": "project", "task_id": ID}
    elif defect == "cursor":
        body["data"]["has_more"] = True
    elif defect == "count":
        body["data"]["returned"] = 0
    elif defect == "command":
        body["command"] = "memory retrieve"
    elif defect == "extra":
        body["unexpected"] = True
    elif defect == "approval":
        body["items"][0]["current"]["status"] = "active"
        body["items"][0]["effective_status"] = "active"
    monkeypatch.setattr(cli, "call", lambda *a, **kw: body)
    query = tmp_path / "query.json"
    query.write_text("{}")
    result = invoke(capsys, ["memory", "browse", "--input", str(query)], 4)
    assert result["data"]["error"]["code"] == "invalid_server_response"


def test_memory_browse_schema_snapshot_and_legacy_preservation(monkeypatch, capsys):
    body = invoke(capsys, ["schema"])
    commands = body["data"]["commands"]
    entry = commands["memory browse"]
    assert "MemoryQuery" in json.dumps(entry["input"])
    assert "PageData" in json.dumps(entry["output"])
    assert "MemoryView" in json.dumps(entry["items"])
    snapshot = json.loads((Path(__file__).with_name("snapshots") / "cli-v1.json").read_text())
    assert entry == snapshot["schema"]["data"]["commands"]["memory browse"]
    legacy = invoke(capsys, ["--contract-version", "3.0", "schema"])
    assert "memory browse" not in legacy["data"]["commands"]
    assert "memory list" in legacy["data"]["commands"]
    calls = []

    def call(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return Result(ok=True, command="memory list", data={"returned": 0}, items=[]).model_dump(
            mode="json"
        )

    monkeypatch.setattr(cli, "call", call)
    invoke(capsys, ["memory", "list", "--scope", "org"])
    assert calls == [
        ("GET", "/memories", {"params": {"scope": "org", "include_deleted": False, "limit": 50}})
    ]
