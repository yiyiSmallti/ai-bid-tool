"""Authenticated, independently rotated encryption for org and platform credentials."""

import json
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from pydantic import SecretStr

from app.core.config import Settings, canonical_fernet_key
from app.core.errors import ServiceError
from app.schemas.platform_credentials import CredentialSpec, PlatformCredentialEnvelope


class ProviderSecrets:
    def __init__(self, settings: Settings):
        key = settings.secrets_key
        if key is None:
            raise ServiceError(
                "provider_secrets_unavailable",
                "BID_SECRETS_KEY is required for credentials",
                503,
                4,
            )
        roots = [
            key.get_secret_value(),
            *[item.get_secret_value() for item in settings.secrets_key_previous],
        ]
        other_roots = {
            settings.encryption_key.get_secret_value(),
            settings.token_key.get_secret_value(),
            *[item.get_secret_value() for item in settings.encryption_key_previous],
        }
        try:
            canonical_roots = [canonical_fernet_key(value) for value in roots]
            canonical_other_roots = {canonical_fernet_key(value) for value in other_roots}
            if (
                len(set(canonical_roots)) != len(canonical_roots)
                or set(canonical_roots) & canonical_other_roots
            ):
                raise ValueError
            ciphers = [Fernet(value.encode()) for value in roots]
        except (ValueError, TypeError):
            raise ServiceError(
                "provider_secrets_unavailable",
                "Provider secret roots are invalid or overlap another domain",
                503,
                4,
            ) from None
        self.cipher = ciphers[0]
        self.keyring = MultiFernet(ciphers)

    def encrypt(self, key: str, org_id: UUID, config_id: UUID) -> str:
        payload = json.dumps({"org": str(org_id), "config": str(config_id), "key": key})
        return self.cipher.encrypt(payload.encode()).decode()

    def decrypt(self, encrypted: str, org_id: UUID, config_id: UUID) -> SecretStr:
        try:
            payload = json.loads(self.keyring.decrypt(encrypted.encode()))
            if (
                not isinstance(payload, dict)
                or set(payload) != {"org", "config", "key"}
                or payload.get("org") != str(org_id)
                or payload.get("config") != str(config_id)
                or not isinstance(payload.get("key"), str)
            ):
                raise ValueError
            return SecretStr(payload["key"])
        except (InvalidToken, ValueError, UnicodeError):
            raise ServiceError(
                "provider_secrets_unavailable",
                "Stored provider credential cannot be decrypted",
                503,
                4,
            ) from None

    def encrypt_platform(self, envelope: PlatformCredentialEnvelope) -> str:
        # SecretInput is write-only even in internal dumps; add it only at this cipher boundary.
        payload = envelope.model_dump(mode="json")
        payload["api_key"] = envelope.api_key.get_secret_value()
        return self.cipher.encrypt(json.dumps(payload).encode()).decode()

    def decrypt_platform(
        self, ciphertext: str, *, expected: CredentialSpec, credential_id: UUID, secret_version: int
    ) -> SecretStr:
        try:
            envelope = PlatformCredentialEnvelope.model_validate_json(
                self.keyring.decrypt(ciphertext.encode())
            )
            if (
                envelope.credential_id != credential_id
                or envelope.secret_version != secret_version
                or envelope.name != expected.name
                or envelope.purpose != expected.purpose
                or envelope.provider != expected.provider
                or envelope.endpoint != expected.endpoint
            ):
                raise ValueError
            return envelope.api_key
        except (InvalidToken, ValueError, UnicodeError):
            raise ServiceError(
                "credential_unreadable", "Stored platform credential cannot be decrypted", 503, 4
            ) from None

    def _rewrap(self, ciphertext: str) -> str | None:
        try:
            self.cipher.decrypt(ciphertext.encode())
            return None
        except InvalidToken:
            return self.keyring.rotate(ciphertext.encode()).decode()

    def rewrap_platform(
        self, ciphertext: str, *, expected: CredentialSpec, credential_id: UUID, secret_version: int
    ) -> str | None:
        self.decrypt_platform(
            ciphertext,
            expected=expected,
            credential_id=credential_id,
            secret_version=secret_version,
        )
        return self._rewrap(ciphertext)

    def rewrap_org(self, ciphertext: str, *, org_id: UUID, config_id: UUID) -> str | None:
        self.decrypt(ciphertext, org_id, config_id)
        return self._rewrap(ciphertext)
