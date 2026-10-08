"""CLI trust-store transport contracts; no database or certificate service calls."""

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from app.schemas.contracts import Result
from bid_cli import main as cli
from bid_cli.client import Client

ID = "00000000-0000-4000-8000-000000000001"
VIEW = {
    "id": ID,
    "label": "Synthetic local root",
    "fingerprint_sha256": "a" * 64,
    "subject": "CN=Synthetic CA",
    "issuer": "CN=Synthetic CA",
    "not_before": "2026-01-01T00:00:00Z",
    "not_after": "2027-01-01T00:00:00Z",
    "is_ca": True,
    "enabled": True,
    "revision": 1,
    "created_by": "operator@example.test",
    "created_at": "2026-10-08T00:00:00Z",
    "disabled_by": None,
    "disabled_at": None,
}


@pytest.fixture
def interface(monkeypatch):
    calls = []
    monkeypatch.setenv("BID_PLATFORM_SESSION", "synthetic-platform-session")
    monkeypatch.setenv("BID_SESSION", "synthetic-org-session")
    monkeypatch.setenv("BID_ORG", ID)

    def handler(request):
        assert request.headers["authorization"] == "Bearer synthetic-platform-session"
        assert "x-org-id" not in request.headers
        assert request.url.path.startswith("/v4/platform/trust-anchors")
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            command, data, items = "platform trust-anchor list", {}, [VIEW]
        elif request.url.path.endswith("/disable"):
            assert json.loads(request.content) == {}
            command, items = "platform trust-anchor disable", []
            data = VIEW | {
                "enabled": False,
                "revision": 2,
                "disabled_by": "operator@example.test",
                "disabled_at": "2026-10-08T00:00:00Z",
            }
        else:
            assert request.headers["content-type"].startswith("multipart/form-data")
            assert b"Synthetic local root" in request.content
            assert b'name="certificate"' in request.content
            assert b"SYNTHETIC CERTIFICATE TRANSPORT" in request.content
            command, data, items = "platform trust-anchor add", VIEW | {"created": True}, []
        return httpx.Response(
            200,
            json=Result(ok=True, command=command, data=data, items=items).model_dump(mode="json"),
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
    except SystemExit as exc:
        code = exc.code
    body = json.loads(capsys.readouterr().out)
    assert "synthetic-platform-session" not in json.dumps(body)
    body["duration_ms"] = 0
    return code, body


def test_trust_anchor_commands_snapshot_both_modes(interface, tmp_path, capsys):
    certificate = tmp_path / "root.der"
    certificate.write_bytes(b"SYNTHETIC CERTIFICATE TRANSPORT")
    flags = {
        "list": ["list"],
        "add": ["add", "--certificate", str(certificate), "--label", "Synthetic local root"],
        "disable": ["disable", "--id", ID],
    }
    actual = {}
    for mode in ("remote", "local"):
        for name, arguments in flags.items():
            code, body = invoke(["--mode", mode, "platform", "trust-anchor", *arguments], capsys)
            assert code == 0
            actual[f"{mode}: {name}"] = body
    snapshot = Path(__file__).with_name("snapshots") / "platform-trust-anchors-cli.json"
    if os.environ.get("BID_UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n")
    assert actual == json.loads(snapshot.read_text())
    assert len(interface) == 6


@pytest.mark.parametrize("fault", ["empty", "oversize", "missing", "empty-label", "long-label"])
def test_invalid_certificate_input_stops_before_transport(interface, tmp_path, capsys, fault):
    certificate = tmp_path / "root.der"
    certificate.write_bytes(b"x" * (65537 if fault == "oversize" else 1))
    if fault == "empty":
        certificate.write_bytes(b"")
    elif fault == "missing":
        certificate.unlink()
    label = (
        " "
        if fault == "empty-label"
        else "x" * 101
        if fault == "long-label"
        else "Synthetic local root"
    )
    code, body = invoke(
        ["platform", "trust-anchor", "add", "--certificate", str(certificate), "--label", label],
        capsys,
    )
    assert code == 2 and body["data"]["error"]["code"] == "invalid_input"
    assert interface == []


def test_trust_anchor_schema_matches_cli(capsys):
    cli.main(["schema", "--json"])
    schemas = json.loads(capsys.readouterr().out)["data"]["commands"]
    for action in ("list", "add", "disable"):
        spec = schemas["platform trust-anchor " + action]
        assert spec["cli_parameters"] and spec["output"]
    assert "fingerprint_sha256" in schemas["platform trust-anchor list"]["items"]["properties"]
