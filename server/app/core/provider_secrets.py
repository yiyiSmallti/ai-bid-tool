"""Separate encryption domain for org-owned vendor credentials."""

import json
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr

from app.core.config import Settings
from app.core.errors import ServiceError


class ProviderSecrets:
    def __init__(self, settings: Settings):
        key = settings.secrets_key
        if key is None:
            raise ServiceError(
                "provider_secrets_unavailable",
                "BID_SECRETS_KEY is required for org credentials",
                503,
                4,
            )
        try:
            self.cipher = Fernet(key.get_secret_value().encode())
        except (ValueError, TypeError):
            raise ServiceError(
                "provider_secrets_unavailable", "BID_SECRETS_KEY is invalid", 503, 4
            ) from None
        if key.get_secret_value() == settings.encryption_key.get_secret_value():
            raise ServiceError(
                "provider_secrets_unavailable",
                "BID_SECRETS_KEY must differ from BID_ENCRYPTION_KEY",
                503,
                4,
            )

    def encrypt(self, key: str, org_id: UUID, config_id: UUID) -> str:
        payload = json.dumps({"org": str(org_id), "config": str(config_id), "key": key})
        return self.cipher.encrypt(payload.encode()).decode()

    def decrypt(self, encrypted: str, org_id: UUID, config_id: UUID) -> SecretStr:
        try:
            payload = json.loads(self.cipher.decrypt(encrypted.encode()))
            if (
                not isinstance(payload, dict)
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
