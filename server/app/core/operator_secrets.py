"""TOTP envelopes in the existing provider-secrets key domain, bound to email/generation."""

import json
import re

from cryptography.fernet import InvalidToken

from app.core.config import Settings
from app.core.errors import ServiceError
from app.core.provider_secrets import ProviderSecrets


def unavailable() -> ServiceError:
    return ServiceError("enrollment_unavailable", "Operator enrollment is unavailable", 503, 3)


class OperatorSecrets:
    VERSION = 1

    def __init__(self, settings: Settings):
        try:
            self.provider = ProviderSecrets(settings)
        except ServiceError:
            raise unavailable() from None

    def encrypt(self, secret: str, email: str, generation: int) -> str:
        payload = {
            "version": self.VERSION,
            "domain": "platform_operator_factor",
            "email": email,
            "generation": generation,
            "secret": secret,
        }
        return self.provider.cipher.encrypt(json.dumps(payload).encode()).decode()

    def decrypt(self, ciphertext: str, email: str, generation: int, key_version: int) -> str:
        try:
            payload = json.loads(self.provider.keyring.decrypt(ciphertext.encode()))
            if (
                key_version != self.VERSION
                or not isinstance(payload, dict)
                or set(payload) != {"version", "domain", "email", "generation", "secret"}
                or payload["version"] != key_version
                or payload["domain"] != "platform_operator_factor"
                or payload["email"] != email
                or payload["generation"] != generation
                or not isinstance(payload["secret"], str)
                or re.fullmatch(r"[A-Z2-7]{32}", payload["secret"]) is None
            ):
                raise ValueError
            return payload["secret"]
        except (InvalidToken, ValueError, UnicodeError, TypeError):
            raise unavailable() from None

    def rewrap(self, ciphertext: str, email: str, generation: int, key_version: int) -> str | None:
        self.decrypt(ciphertext, email, generation, key_version)
        return self.provider._rewrap(ciphertext)
