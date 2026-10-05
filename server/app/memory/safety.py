"""Memory always rejects secrets, independently of a task's outbound switch."""

import re
from collections.abc import Iterable

from sqlalchemy import select

from app.core.errors import ServiceError
from app.core.security import Secrets
from app.models.confidential import ConfidentialField, ConfidentialValue
from app.services import redaction

SANITIZER_VERSION = "memory-sanitize-v1:" + redaction.RULE_VERSION
CREDENTIAL = re.compile(
    r"(?i)(?:\b(?:sk|ghp|gho|github_pat)-?[A-Za-z0-9_-]{16,}"
    r"|\b(?:password|passwd|api[_ -]?key|authorization|token|secret|密码|密钥)"
    r"\s*[:=：]\s*[^\s,，;；]+|\bBearer\s+[A-Za-z0-9._~-]+)"
)


async def secret_library(session, actor, settings):
    # Include historical registered values: superseded secrets remain confidential.
    rows = await session.execute(
        select(ConfidentialField, ConfidentialValue)
        .join(
            ConfidentialValue,
            (ConfidentialField.org_id == ConfidentialValue.org_id)
            & (ConfidentialField.id == ConfidentialValue.field_id),
        )
        .where(ConfidentialField.org_id == actor.org_id, ConfidentialValue.org_id == actor.org_id)
    )
    crypto = Secrets.for_data(settings)
    library = []
    for field, value in rows:
        item = redaction.library_value(field.key, field.kind, crypto.decrypt(value.encrypted_value))
        if item is not None:
            library.append(item)
    return library


async def sanitize(session, actor, text: str, settings) -> str:
    cleaned, _ = redaction.redact(text, True, await secret_library(session, actor, settings))
    cleaned = CREDENTIAL.sub("[REDACTED_CREDENTIAL]", cleaned)
    return redaction.SECRET_PLACEHOLDER.sub("[REDACTED_CONFIDENTIAL]", cleaned)


async def reject_sensitive(session, actor, texts: Iterable[str], settings) -> None:
    library = await secret_library(session, actor, settings)
    for text in texts:
        masked, _ = redaction.redact(text, True, library)
        # Sanitized feedback can contain an existing mask after a sensitive label.
        # A second identical mask is not a newly discovered confidential value.
        if masked != text or CREDENTIAL.search(text) or redaction.secret_keys(text):
            raise ServiceError(
                "memory_sensitive_value", "Memory cannot contain sensitive values", 422, 2
            )
