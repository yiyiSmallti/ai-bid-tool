"""Credential CLI workflows through a fake HTTP server; no database or vendor calls."""

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from app.schemas.contracts import Result
from bid_cli import main as cli
from bid_cli.client import Client

IDENTIFIER = "00000000-0000-0000-0000-000000000001"
SECRET = "synthetic-only-credential-key"
VIEW = {
    "id": IDENTIFIER,
    "name": "main",
    "purpose": "catalog_llm",
    "provider": "openai",
    "endpoint": "https://vendor.example.test/v1",
    "state": "disabled",
    "revision": 1,
    "secret_version": 1,
    "fingerprint": "sha256:" + "a" * 16,
    "last_four": "-key",
    "created_at": "2026-10-05T00:00:00Z",
    "updated_at": "2026-10-05T00:00:00Z",
    "updated_by": "ops@example.test",
    "consumers": [],
}
PROBE = {
    "probe_id": IDENTIFIER,
    "credential_id": IDENTIFIER,
    "tested_revision": 1,
    "secret_version": 1,
    "outcome": "unsupported",
    "duration_ms": 0,
    "checked_at": "2026-10-05T00:00:00Z",
    "proves": "authentication_only",
}


def private_file(path, value):
    path.write_text(value)
    path.chmod(0o600)
    return path


@pytest.fixture
def interface(monkeypatch):
    calls = []
    monkeypatch.setenv("BID_PLATFORM_SESSION", "synthetic-platform-session")
    monkeypatch.setenv("BID_SESSION", "synthetic-org-session")
    monkeypatch.setenv("BID_ORG", IDENTIFIER)

    def handler(request):
        assert request.headers["authorization"] == "Bearer synthetic-platform-session"
        assert "x-org-id" not in request.headers
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body, dict(request.url.params)))
        path = request.url.path.removeprefix("/v4")
        if path.endswith("/test"):
            data = {
                "error": {
                    "code": "credential_probe_unsupported",
                    "message": SECRET,
                    "exit_code": 4,
                },
                "probe": PROBE,
            }
            return httpx.Response(
                422,
                json=Result(ok=False, command="platform credential test", data=data).model_dump(
                    mode="json"
                ),
            )
        if path.endswith("/import-env"):
            assert body["entries"][0]["api_key"] == SECRET
            assert "BID_TOKEN_KEY" not in str(body)
            dry = body["dry_run"]
            data = {
                "dry_run": dry,
                "created": 0 if dry else 1,
                "skipped": 0,
                "would_create": 1 if dry else 0,
            }
            items = [
                {
                    "name": "main",
                    "action": "would_create" if dry else "created",
                    "credential_id": None if dry else IDENTIFIER,
                }
            ]
            command = "platform credential import-env"
        elif request.method == "GET" and path == "/platform/credentials":
            data, items, command = {"next_after_name": None}, [VIEW], "platform credential list"
        else:
            if path == "/platform/credentials":
                assert body["api_key"] == SECRET
                action = "create"
            elif path.endswith("/replace"):
                assert body["api_key"] == SECRET and body["expected_revision"] == 1
                action = "replace"
            else:
                action = (
                    "set-active"
                    if path.endswith("/active")
                    else "remove"
                    if path.endswith("/remove")
                    else "show"
                )
            data, items, command = {"credential": VIEW}, [], "platform credential " + action
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


def invoke(args, capsys, code=0):
    if code:
        with pytest.raises(SystemExit) as error:
            cli.main(["platform", "credential", *args, "--json"])
        assert error.value.code == code
    else:
        cli.main(["platform", "credential", *args, "--json"])
    output = capsys.readouterr().out
    assert SECRET not in output and "synthetic-platform-session" not in output
    result = json.loads(output)
    assert set(result) == {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
    result["duration_ms"] = 0
    return result


def test_credential_workflow_snapshot(interface, tmp_path, capsys):
    key = private_file(tmp_path / "key", SECRET + "\n")
    metadata = tmp_path / "metadata.json"
    metadata.write_text(
        json.dumps({key: VIEW[key] for key in ("name", "purpose", "provider", "endpoint")})
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        **json.loads(metadata.read_text()),
                        "source_env": "BID_PLATFORM_CREDENTIAL_MAIN",
                    }
                ]
            }
        )
    )
    env = private_file(
        tmp_path / "env",
        "BID_TOKEN_KEY=unrelated-bootstrap-value\nBID_PLATFORM_CREDENTIAL_MAIN='" + SECRET + "'\n",
    )
    args = {
        "list": ["list", "--state", "disabled", "--purpose", "catalog_llm", "--limit", "20"],
        "show": ["show", "--id", IDENTIFIER],
        "create": ["create", "--input", str(metadata), "--key-file", str(key)],
        "replace": [
            "replace",
            "--id",
            IDENTIFIER,
            "--expected-revision",
            "1",
            "--reason",
            "scheduled_rotation",
            "--key-file",
            str(key),
        ],
        "set-active": [
            "set-active",
            "--id",
            IDENTIFIER,
            "--expected-revision",
            "1",
            "--active",
            "--reason",
            "setup",
        ],
        "remove": ["remove", "--id", IDENTIFIER, "--expected-revision", "1", "--reason", "retired"],
        "test": ["test", "--id", IDENTIFIER, "--expected-revision", "1"],
        "dry-run": ["import-env", "--manifest", str(manifest), "--env-file", str(env), "--dry-run"],
        "import-env": ["import-env", "--manifest", str(manifest), "--env-file", str(env)],
    }
    actual = {
        name: invoke(value, capsys, 4 if name == "test" else 0) for name, value in args.items()
    }
    assert actual["test"]["data"]["probe"]["tested_revision"] == 1
    assert len(interface) == 9
    snapshot = Path(__file__).with_name("snapshots") / "platform-credentials-cli.json"
    if os.environ.get("BID_UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n")
    assert actual == json.loads(snapshot.read_text())


@pytest.mark.parametrize(
    "fault",
    [
        "permissions",
        "symlink",
        "parent_symlink",
        "too_long",
        "short",
        "space",
        "extra_newline",
        "control",
        "directory",
    ],
)
def test_key_input_rejected_before_network(interface, tmp_path, capsys, fault):
    key = private_file(tmp_path / "key", SECRET)
    if fault == "permissions":
        key.chmod(0o644)
    elif fault == "symlink":
        link = tmp_path / "link"
        link.symlink_to(key)
        key = link
    elif fault == "parent_symlink":
        link = tmp_path / "link"
        link.symlink_to(tmp_path, target_is_directory=True)
        key = link / "key"
    elif fault == "directory":
        key = tmp_path
    else:
        key.write_text(
            {
                "too_long": "a" * 4097,
                "short": "short",
                "space": SECRET + " ",
                "extra_newline": SECRET + "\n\n",
                "control": SECRET + "\x00",
            }[fault]
        )
    result = invoke(
        [
            "replace",
            "--id",
            IDENTIFIER,
            "--expected-revision",
            "1",
            "--reason",
            "incident",
            "--key-file",
            str(key),
        ],
        capsys,
        2,
    )
    assert result["data"]["error"]["code"] == "invalid_input"
    assert interface == []


@pytest.mark.parametrize(
    "assignment",
    [
        "BID_PLATFORM_CREDENTIAL_MAIN=$(touch bad)",
        "BID_PLATFORM_CREDENTIAL_MAIN=${KEY}",
        "BID_PLATFORM_CREDENTIAL_MAIN=`command`",
        "BID_PLATFORM_CREDENTIAL_MAIN=one two",
        "BID_PLATFORM_CREDENTIAL_MAIN='unterminated",
        "BID_PLATFORM_CREDENTIAL_MAIN=" + SECRET + "\nBID_PLATFORM_CREDENTIAL_MAIN=" + SECRET,
        "BID_TOKEN_KEY=unrelated",
    ],
)
def test_env_input_rejected_before_network(interface, tmp_path, capsys, assignment):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "entries": [
                    {key: VIEW[key] for key in ("name", "purpose", "provider", "endpoint")}
                    | {"source_env": "BID_PLATFORM_CREDENTIAL_MAIN"}
                ]
            }
        )
    )
    env = private_file(tmp_path / "env", assignment)
    invoke(["import-env", "--manifest", str(manifest), "--env-file", str(env)], capsys, 2)
    assert interface == []


def test_schema_lists_all_credential_inputs_outputs(capsys):
    cli.main(["schema", "--json"])
    schemas = json.loads(capsys.readouterr().out)["data"]["commands"]
    for action in (
        "list",
        "show",
        "create",
        "replace",
        "set-active",
        "remove",
        "test",
        "import-env",
    ):
        spec = schemas["platform credential " + action]
        assert spec["cli_parameters"] and spec["output"]
    assert "api_key" not in schemas["platform credential create"]["input"]["properties"]
    assert "api_key" not in json.dumps(schemas["platform credential import-env"]["input"])
