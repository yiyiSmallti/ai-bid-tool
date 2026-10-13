"""Owner-run credential root rotation through real PostgreSQL maintenance boundaries.

Failure modes fixed before implementation: noncanonical roots may alias another domain;
rolled-back writes must not be reported rewritten; a later enumeration failure must retain
partial progress; all BYOK historical revisions must survive previous-key removal; runtime
roles may neither modify historical BYOK nor invoke its maintenance bypass; bad ciphertext
must fail visibly without disclosing root keys, credential values or ciphertext.
"""

import json
import os
from uuid import UUID, uuid4

import pytest
from app.admin import rotate_provider_secrets
from app.core.config import Settings
from app.core.provider_secrets import ProviderSecrets
from app.models.provider_configs import ProviderConfig
from app.schemas.platform_credentials import CredentialSpec, PlatformCredentialEnvelope
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_platform_credentials_db import MANAGER, create_payload, manage, role_connection

PLATFORM_KEY = "synthetic-rotation-platform-key-1234"
PROVIDER_KEY = "synthetic-rotation-local-key-5678"


def seed_revision(engine, cipher, revision, *, broken=False):
    config_id = uuid4()
    ciphertext = (
        "synthetic-invalid-ciphertext" if broken else cipher.encrypt(PROVIDER_KEY, config_id)
    )
    with Session(engine) as session, session.begin():
        session.execute(
            text(
                "SELECT set_config('app.actor_kind','session',true), set_config('app.actor_token_id','',true)"
            )
        )
        session.add(
            ProviderConfig(
                id=config_id,
                capability="llm_extract",
                revision=revision,
                data={"provider": "openai", "model": "synthetic", "json_mode": "json_schema"},
                encrypted_key=ciphertext,
                key_last4=PROVIDER_KEY[-4:],
            )
        )
    return config_id, ciphertext


def read_rows(engine):
    with Session(engine) as session, session.begin():
        return [
            dict(
                id=row.id,
                revision=row.revision,
                data=row.data,
                encrypted_key=row.encrypted_key,
            )
            for row in session.scalars(select(ProviderConfig).order_by(ProviderConfig.revision))
        ]


def test_revision_history_and_service_rewrap_is_reentrant_and_preserves_values(
    admin_engine,
    database,
    monkeypatch,
    capsys,
    caplog,
):
    old_key, current_key = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    for name in tuple(os.environ):
        if name.startswith("BID_PLATFORM_CREDENTIAL_") or name in {
            "BID_LLM_API_KEY",
            "BID_PERPLEXITY_API_KEY",
        }:
            monkeypatch.delenv(name)
    monkeypatch.setenv("BID_SECRETS_KEY", current_key)
    monkeypatch.setenv("BID_SECRETS_KEY_PREVIOUS", old_key)
    monkeypatch.setenv("BID_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("BID_TOKEN_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("BID_ENCRYPTION_KEY_PREVIOUS", raising=False)
    monkeypatch.setenv("BID_LLM_PROVIDER", "disabled")
    monkeypatch.setenv("BID_SEARCH_PROVIDER", "disabled")
    monkeypatch.setenv(
        "BID_MIGRATION_DATABASE_URL", admin_engine.url.render_as_string(hide_password=False)
    )
    settings = Settings.load()
    old_cipher = ProviderSecrets(
        settings.model_copy(
            update={
                "secrets_key": SecretStr(old_key),
                "secrets_key_previous": [],
            }
        )
    )
    for revision in range(1, 5):
        seed_revision(admin_engine, old_cipher, revision)
    old_rows = read_rows(admin_engine)
    payload = create_payload()
    envelope = PlatformCredentialEnvelope(
        credential_id=UUID(payload["id"]),
        name=payload["name"],
        purpose=payload["purpose"],
        provider=payload["provider"],
        endpoint=payload["endpoint"],
        secret_version=1,
        api_key=PLATFORM_KEY,
    )
    payload["encrypted_key"] = old_cipher.encrypt_platform(envelope)
    with role_connection(admin_engine, MANAGER) as connection:
        initial_view = manage(connection, "create", payload)["credential"]

    first = rotate_provider_secrets()
    assert first == {
        "scope": "provider-secrets",
        "platform_checked": 1,
        "platform_rewritten": 1,
        "provider_revisions_checked": 4,
        "provider_revisions_rewritten": 4,
        "failed": 0,
        "exit_code": 0,
    }
    second = rotate_provider_secrets()
    assert (
        second["platform_rewritten"]
        == second["provider_revisions_rewritten"]
        == second["failed"]
        == 0
    )
    assert second["platform_checked"] == 1 and second["provider_revisions_checked"] == 4
    monkeypatch.delenv("BID_SECRETS_KEY_PREVIOUS")
    only_current = ProviderSecrets(Settings.load())
    rows = read_rows(admin_engine)
    for old, row in zip(old_rows, rows, strict=True):
        assert {name: row[name] for name in ("id", "revision", "data")} == {
            name: old[name] for name in ("id", "revision", "data")
        }
        assert row["encrypted_key"] != old["encrypted_key"]
        assert (
            only_current.decrypt(row["encrypted_key"], row["id"]).get_secret_value() == PROVIDER_KEY
        )
    with admin_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT encrypted_key, revision, secret_version FROM platform_credentials WHERE id=:id"
            ),
            {"id": payload["id"]},
        ).one()
    assert (
        row.revision == initial_view["revision"]
        and row.secret_version == initial_view["secret_version"]
    )
    assert (
        only_current.decrypt_platform(
            row.encrypted_key,
            expected=CredentialSpec(
                name=envelope.name,
                purpose=envelope.purpose,
                provider=envelope.provider,
                endpoint=envelope.endpoint,
            ),
            credential_id=envelope.credential_id,
            secret_version=1,
        ).get_secret_value()
        == PLATFORM_KEY
    )

    with pytest.raises(DBAPIError):
        with role_connection(admin_engine, "bid_app") as connection:
            connection.execute(
                text("UPDATE provider_configs SET encrypted_key=:value WHERE id=:id"),
                {"value": "synthetic-forbidden-replacement", "id": old_rows[0]["id"]},
            )
    with pytest.raises(DBAPIError):
        with role_connection(admin_engine, "bid_app") as connection:
            connection.execute(
                text("SELECT provider_credential_rewrap(:id,:old,:new)"),
                {
                    "id": old_rows[0]["id"],
                    "old": old_rows[0]["encrypted_key"],
                    "new": "synthetic-forbidden-replacement",
                },
            )
    monkeypatch.setenv("BID_SECRETS_KEY_PREVIOUS", old_key)
    seed_revision(admin_engine, old_cipher, 5, broken=True)
    partial = rotate_provider_secrets()
    assert partial["failed"] == 1 and partial["exit_code"] == 5
    assert partial["platform_rewritten"] == partial["provider_revisions_rewritten"] == 0
    outputs = capsys.readouterr()
    public_output = outputs.out + outputs.err + caplog.text + json.dumps(partial)
    assert all(
        secret not in public_output
        for secret in (old_key, current_key, PROVIDER_KEY, PLATFORM_KEY, payload["encrypted_key"])
    )
    assert "rewrap_failed" in outputs.err
