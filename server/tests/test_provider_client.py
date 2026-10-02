"""Non-database provider boundary and CLI contracts, specified before implementation.

Failures: JSON-key leaks, loose key-file permissions, URL credential injection,
unsupported or malformed balance replies, redirects, unsafe reasoning options,
wrong encryption context, and unregistered commands or Result shape drift.
"""

import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from app.core.config import Settings
from app.core.errors import ServiceError
from app.core.provider_secrets import ProviderSecrets
from app.providers.balance import provider_balance
from app.schemas.provider_contracts import ProviderConfigSet
from bid_cli.main import main
from cryptography.fernet import Fernet
from pydantic import SecretStr, ValidationError


def test_key_encryption_context_and_separation():
    settings = Settings(
        database_url=SecretStr("postgresql+psycopg://unused/bid_test"),
        encryption_key=SecretStr(Fernet.generate_key().decode()),
        secrets_key=SecretStr(Fernet.generate_key().decode()),
    )
    secrets = ProviderSecrets(settings)
    org, config = uuid4(), uuid4()
    encrypted = secrets.encrypt("synthetic-private-key", org, config)
    assert "synthetic-private-key" not in encrypted
    assert secrets.decrypt(encrypted, org, config).get_secret_value() == "synthetic-private-key"
    for wrong_org, wrong_id in ((uuid4(), config), (org, uuid4())):
        with pytest.raises(ServiceError, match="credential"):
            secrets.decrypt(encrypted, wrong_org, wrong_id)
    with pytest.raises(ServiceError):
        ProviderSecrets(settings.model_copy(update={"secrets_key": None}))


@pytest.mark.parametrize(
    "changes",
    [
        {"base_url": "https://user:password@example.test"},
        {"base_url": "https://example.test?api_key=private"},
        {"base_url": "http://example.test/v1"},
        {
            "reasoning": [{"name": "high", "request_options": {"max_tokens": 999}}],
            "default_reasoning": "high",
        },
        {"reasoning": [{"name": "low"}], "default_reasoning": "high"},
        {"input_usd_per_mtok": float("inf")},
        {"source": "platform", "platform_model_id": "paid", "api_key": "synthetic-private"},
    ],
)
def test_provider_input_failures(changes):
    with pytest.raises(ValidationError):
        ProviderConfigSet.model_validate(
            {
                "capability": "llm_extract",
                "source": "org",
                "provider": "openai",
                "model": "synthetic-model",
                **changes,
            }
        )


@pytest.mark.parametrize(
    "reply,status",
    [
        (
            httpx.Response(
                200,
                json={
                    "is_available": True,
                    "balance_infos": [
                        {
                            "currency": "USD",
                            "total_balance": "1.2",
                            "granted_balance": "0",
                            "topped_up_balance": "1.2",
                        }
                    ],
                },
            ),
            "supported",
        ),
        (
            httpx.Response(
                200,
                json={
                    "is_available": True,
                    "balance_infos": [{"currency": "USD", "total_balance": "NaN"}],
                },
            ),
            "unavailable",
        ),
        (httpx.Response(401, json={"error": "synthetic-private-key"}), "unavailable"),
        (httpx.Response(302, headers={"Location": "https://elsewhere.example"}), "unavailable"),
        (httpx.Response(200, json=[]), "unavailable"),
    ],
)
async def test_balance_http_boundary(reply, status):
    calls = []

    def vendor(request):
        calls.append(request)
        assert (
            request.method == "GET" and str(request.url) == "https://api.deepseek.com/user/balance"
        )
        return reply

    result = await provider_balance(
        "openai",
        "https://api.deepseek.com/v1",
        SecretStr("synthetic-private-key"),
        httpx.MockTransport(vendor),
    )
    assert result["status"] == status and len(calls) == 1
    assert "synthetic-private-key" not in json.dumps(result)
    for url in (None, "https://api.openai.com/v1", "https://api.deepseek.com.evil.test/v1"):
        unsupported = await provider_balance(
            "openai", url, SecretStr("synthetic-private-key"), httpx.MockTransport(vendor)
        )
        assert unsupported["status"] == "unsupported" and len(calls) == 1


def test_provider_cli_snapshots(tmp_path, monkeypatch, capsys):
    from bid_cli.client import Client

    requests = []

    async def request(_self, method, path, **kwargs):
        requests.append((method, path, kwargs))
        return {
            "ok": True,
            "command": "provider",
            "data": {"configured": True},
            "items": [],
            "warnings": [],
            "cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0},
            "duration_ms": 0,
        }

    monkeypatch.setattr(Client, "request", request)
    monkeypatch.setenv("BID_PROVIDER_KEY", "synthetic-private-key")
    path = tmp_path / "provider.json"
    path.write_text(
        json.dumps(
            {
                "capability": "llm_extract",
                "source": "org",
                "provider": "openai",
                "model": "synthetic-model",
            }
        )
    )
    actual = {}
    for command, options in (
        ("list", []),
        ("history", []),
        ("set", ["--input", str(path)]),
        ("test", ["--capability", "llm_extract"]),
    ):
        main(["--state", str(tmp_path / "state"), "provider", command, *options, "--json"])
        body = json.loads(capsys.readouterr().out)
        body["duration_ms"] = 0
        assert "synthetic-private-key" not in json.dumps(body)
        actual[command] = body
    assert requests[2][2]["json"]["api_key"] == "synthetic-private-key"
    snapshot = Path(__file__).with_name("snapshots") / "provider-cli-v1.json"
    if os.environ.get("BID_UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n")
    assert actual == json.loads(snapshot.read_text())
    monkeypatch.delenv("BID_PROVIDER_KEY")
    keyfile = tmp_path / "key"
    keyfile.write_text("synthetic-file-key")
    keyfile.chmod(0o644)
    with pytest.raises(SystemExit) as rejected:
        main(["provider", "set", "--input", str(path), "--key-file", str(keyfile), "--json"])
    assert rejected.value.code == 2
    assert "synthetic-file-key" not in capsys.readouterr().out
    keyfile.chmod(0o600)
    main(["provider", "set", "--input", str(path), "--key-file", str(keyfile), "--json"])
    assert requests[-1][2]["json"]["api_key"] == "synthetic-file-key"
    capsys.readouterr()
    path.write_text(
        json.dumps(
            {
                "capability": "llm_extract",
                "source": "org",
                "provider": "openai",
                "model": "synthetic-model",
                "api_key": "synthetic-private-key",
            }
        )
    )
    with pytest.raises(SystemExit) as leaked_input:
        main(["provider", "set", "--input", str(path), "--json"])
    assert leaked_input.value.code == 2 and "synthetic-private-key" not in capsys.readouterr().out
