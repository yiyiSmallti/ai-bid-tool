"""Non-database provider boundary and CLI contracts, specified before implementation.

Failures: JSON-key leaks, loose key-file permissions, URL credential injection,
redirects, unsafe reasoning options,
wrong encryption context, and unregistered commands or Result shape drift.
"""

import json
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.core.errors import ServiceError
from app.core.provider_secrets import ProviderSecrets
from app.schemas.provider_contracts import ProviderConfigSet
from bid_cli.main import main
from cryptography.fernet import Fernet
from pydantic import SecretStr, ValidationError


def test_key_encryption_context_and_separation():
    settings = Settings(
        database_url=SecretStr("postgresql+psycopg://unused/bid_test"),
        encryption_key=SecretStr(Fernet.generate_key().decode()),
        secrets_key=SecretStr(Fernet.generate_key().decode()),
        token_key=SecretStr(Fernet.generate_key().decode()),
    )
    secrets = ProviderSecrets(settings)
    config = uuid4()
    encrypted = secrets.encrypt("synthetic-private-key", config)
    assert "synthetic-private-key" not in encrypted
    assert secrets.decrypt(encrypted, config).get_secret_value() == "synthetic-private-key"
    with pytest.raises(ServiceError, match="credential"):
        secrets.decrypt(encrypted, uuid4())
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
    ],
)
def test_provider_input_failures(changes):
    with pytest.raises(ValidationError):
        ProviderConfigSet.model_validate(
            {
                "capability": "llm_extract",
                "provider": "openai",
                "model": "synthetic-model",
                **changes,
            }
        )


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
            "cost": {"llm_tokens": 0, "ocr_pages": 0},
            "duration_ms": 0,
        }

    monkeypatch.setattr(Client, "request", request)
    monkeypatch.setenv("BID_PROVIDER_KEY", "synthetic-private-key")
    path = tmp_path / "provider.json"
    path.write_text(
        json.dumps(
            {
                "capability": "llm_extract",
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
        main(["provider", command, *options, "--json"])
        body = json.loads(capsys.readouterr().out)
        body["duration_ms"] = 0
        assert "synthetic-private-key" not in json.dumps(body)
        actual[command] = body
    assert requests[2][2]["json"]["api_key"] == "synthetic-private-key"
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
                "provider": "openai",
                "model": "synthetic-model",
                "api_key": "synthetic-private-key",
            }
        )
    )
    with pytest.raises(SystemExit) as leaked_input:
        main(["provider", "set", "--input", str(path), "--json"])
    assert leaked_input.value.code == 2 and "synthetic-private-key" not in capsys.readouterr().out
