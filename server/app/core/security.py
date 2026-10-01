import base64
import hashlib
import hmac
import json
import secrets
import time

from cryptography.fernet import Fernet, InvalidToken

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
    def __init__(self, key: str):
        self.cipher = Fernet(key.encode())

    def encrypt(self, value: str) -> str:
        return self.cipher.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        return self.cipher.decrypt(value.encode()).decode()

    def issue(self, payload: dict, seconds: int) -> str:
        return self.encrypt(json.dumps({**payload, "exp": int(time.time()) + seconds}))

    def open(self, token: str) -> dict:
        try:
            payload = json.loads(self.decrypt(token))
            if payload["exp"] <= time.time():
                raise ValueError("expired")
            return payload
        except (InvalidToken, ValueError, KeyError, TypeError) as exc:
            raise ServiceError("invalid_session", "Invalid or expired credentials", 401, 4) from exc


def token_digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
