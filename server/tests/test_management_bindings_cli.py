"""CLI transport acceptance: bounded binding reads preserve the human API gate.

Failure cases before implementation: missing/legacy input reaches transport;
wrong-parent or foreign-org rows are accepted; extra envelope keys leak; retryable
errors retry implicitly; old binding list silently changes; scope errors exit 2.
"""

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from app.schemas.contracts import Cost, Result
from app.services.export_template_sample import binding_sections
from bid_cli import main as cli
from bid_cli.client import Client

ID = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000002"
WHEN = "2026-10-06T00:00:00Z"
PREFIX = "/v4/management/export-bindings"


def binding():
    return {
        "id": ID,
        "org_id": ID,
        "template_revision_id": ID,
        "template_sha256": "a" * 64,
        "binding_hash": "b" * 64,
        "static_content_hash": "c" * 64,
        "adapter_version": "synthetic",
        "sections": binding_sections(),
        "current": True,
        "reviewed_by": ID,
        "reviewed_at": WHEN,
    }


def projection(command, empty=False):
    return Result(
        ok=True,
        command="export binding " + command,
        data=(
            binding()
            if command == "show"
            else {
                "org_id": ID,
                "as_of": WHEN,
                "returned": 0 if empty else 1,
                "next_cursor": None,
                "has_more": False,
            }
        ),
        items=[] if command == "show" or empty else [binding()],
    ).model_dump(mode="json")


def invoke(capsys, flags, exit_code=0):
    if exit_code:
        with pytest.raises(SystemExit) as exc:
            cli.main([*flags, "--json"])
        assert exc.value.code == exit_code
    else:
        cli.main([*flags, "--json"])
    result = json.loads(capsys.readouterr().out)
    assert set(result) == {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
    if "3.0" not in flags:
        assert result["cost"] == Cost().model_dump(mode="json")
    result["duration_ms"] = 0
    return result


def test_binding_management_transport_snapshots(monkeypatch, capsys):
    monkeypatch.setenv("BID_SESSION", "synthetic-only")
    monkeypatch.setenv("BID_ORG", ID)
    calls, snapshots = [], {}
    command, empty, failure = "browse", False, None

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
                    command="export binding " + command,
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
    cases = [
        (
            "browse",
            ["browse", "--template-revision", ID, "--cursor", "opaque", "--limit", "2"],
            (
                "POST",
                PREFIX + "/query",
                {},
                {"template_revision_id": ID, "cursor": "opaque", "limit": 2},
            ),
        ),
        (
            "show",
            ["show", "--id", ID, "--template-revision", ID],
            ("GET", PREFIX + "/" + ID, {"template_revision_id": ID}, None),
        ),
    ]
    for mode in ("remote", "local"):
        for command, flags, expected in cases:
            snapshots[mode + ":" + command] = invoke(
                capsys, ["--mode", mode, "export", "binding", *flags]
            )
            assert calls[-1] == expected
        command, empty = "browse", True
        snapshots[mode + ":empty"] = invoke(
            capsys, ["--mode", mode, "export", "binding", "browse", "--template-revision", ID]
        )
        empty = False
        for status, code, exit_code in (
            (403, "permission_denied", 4),
            (404, "not_found", 4),
            (400, "invalid_cursor", 2),
            (429, "rate_limit", 3),
        ):
            failure = status, code, exit_code
            before = len(calls)
            snapshots[f"{mode}:HTTP{status}"] = invoke(
                capsys,
                ["--mode", mode, "export", "binding", "browse", "--template-revision", ID],
                exit_code,
            )
            assert len(calls) == before + 1
        failure = None
    snapshot = Path(__file__).with_name("snapshots") / "management-bindings-cli.json"
    if os.environ.get("BID_UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(
            json.dumps(snapshots, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
    assert snapshots == json.loads(snapshot.read_text())


def test_binding_management_invalid_legacy_and_schema(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "call", lambda *a, **kw: calls.append(a))
    for flags in (
        ["browse"],
        ["show", "--id", ID],
        ["browse", "--template-revision", ID, "--limit", "101"],
        ["browse", "--template-revision", ID, "--cursor", ""],
    ):
        assert (
            invoke(capsys, ["export", "binding", *flags], 2)["data"]["error"]["code"]
            == "invalid_input"
        )
    for command in ("browse", "show"):
        result = invoke(capsys, ["--contract-version", "3.0", "export", "binding", command], 2)
        assert result["data"]["error"]["message"] == "This command requires contract 4.0"
    assert calls == []
    entries = invoke(capsys, ["schema"])["data"]["commands"]
    for command in ("browse", "show"):
        assert entries["export binding " + command]["input"]
        assert entries["export binding " + command]["output"]
    assert entries["export binding browse"]["items"]
    cli.main(["--contract-version", "3.0", "schema", "--json"])
    legacy = json.loads(capsys.readouterr().out)["data"]["commands"]
    assert "export binding list" in legacy and "export binding browse" not in legacy


@pytest.mark.parametrize(
    "command,defect",
    [
        ("browse", "foreign_org"),
        ("browse", "wrong_parent"),
        ("show", "wrong_parent"),
        ("browse", "cursor"),
        ("show", "extra_items"),
        ("show", "wrong_id"),
    ],
)
def test_binding_management_invalid_server_receipt(command, defect, monkeypatch, capsys):
    body = projection(command)
    if defect in {"foreign_org", "wrong_parent", "wrong_id"}:
        target = body["data"] if command == "show" else body["items"][0]
        target[
            {"foreign_org": "org_id", "wrong_parent": "template_revision_id", "wrong_id": "id"}[
                defect
            ]
        ] = OTHER
    elif defect == "cursor":
        body["data"]["has_more"] = True
    elif defect == "extra_items":
        body["items"] = [binding()]
    monkeypatch.setattr(cli, "call", lambda *a, **kw: body)
    flags = ["export", "binding", command, "--template-revision", ID]
    if command == "show":
        flags += ["--id", ID]
    result = invoke(capsys, flags, 4)
    assert result["data"]["error"]["code"] == "invalid_server_response"
