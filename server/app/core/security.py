import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Sequence

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.errors import ServiceError


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
    return (
        "pbkdf2$600000$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, digest = encoded.split("$")
        if algorithm != "pbkdf2":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.b64decode(salt), int(rounds)
        )
        return hmac.compare_digest(actual, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


class Secrets:
    """Stored-data encryption. New values use the current key; values written under a
    retired key stay readable until `app.admin rotate-encryption` rewrites them."""

    def __init__(self, key: str, previous: Sequence[str] = ()):
        self.current = Fernet(key.encode())
        self.cipher = MultiFernet([self.current, *(Fernet(item.encode()) for item in previous)])

    @classmethod
    def for_data(cls, settings) -> "Secrets":
        return cls(
            settings.encryption_key.get_secret_value(),
            [item.get_secret_value() for item in settings.encryption_key_previous],
        )

    def encrypt(self, value: str) -> str:
        return self.cipher.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        return self.cipher.decrypt(value.encode()).decode()

    def rotate(self, value: str) -> str | None:
        """The value re-encrypted under the current key, or None if it already is."""
        try:
            self.current.decrypt(value.encode())
            return None
        except InvalidToken:
            return self.cipher.rotate(value.encode()).decode()


class TokenSigner:
    """Sessions and signed links. Its own key, so they never share one with stored data;
    replacing it ends every outstanding session and link at once."""

    def __init__(self, key: str):
        self.cipher = Fernet(key.encode())

    @classmethod
    def for_tokens(cls, settings) -> "TokenSigner":
        return cls(settings.token_key.get_secret_value())

    def issue(self, payload: dict, seconds: int) -> str:
        value = json.dumps({**payload, "exp": int(time.time()) + seconds})
        return self.cipher.encrypt(value.encode()).decode()

    def open(self, token: str) -> dict:
        try:
            payload = json.loads(self.cipher.decrypt(token.encode()))
            if payload["exp"] <= time.time():
                raise ValueError("expired")
            return payload
        except (InvalidToken, ValueError, KeyError, TypeError) as exc:
            raise ServiceError("invalid_session", "Invalid or expired credentials", 401, 4) from exc


def token_digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
