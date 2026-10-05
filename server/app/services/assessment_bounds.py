"""Shared projection limits and authenticated query/snapshot cursor bindings."""

import hashlib
import hmac
import json
from collections.abc import Callable
from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ServiceError
from app.core.security import TokenSigner
from app.schemas.console_assessments import PAGE_BYTE_LIMIT, CitationRequest, TextWindow
from app.schemas.contracts import Result
from app.services.auth import Identity


def encoded_size(result: Result) -> int:
    # The API middleware emits standard json.dumps spacing and replaces duration
    # after the route finishes. Reserve 19 decimal digits for that final value.
    payload = result.model_dump(mode="json")
    payload["duration_ms"] = 2**63 - 1
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def bounded_result(result: Result) -> Result:
    """Measure the serialized seven-key envelope, including escaping and UTF-8."""
    if encoded_size(result) > PAGE_BYTE_LIMIT:
        raise entry_too_large(result.data, result.items[0] if result.items else None)
    return result


def entry_too_large(data: dict[str, Any], item: dict[str, Any] | None = None) -> ServiceError:
    identifier = (
        (item or {}).get("id")
        or (item or {}).get("subject_id")
        or data.get("parent_id")
        or data.get("id")
        or data.get("report", {}).get("id")
        or data.get("task_id")
    )
    suffix = f" ({identifier})" if identifier is not None else ""
    return ServiceError(
        "assessment_entry_too_large",
        "Authorized assessment entry exceeds the 2 MiB projection limit" + suffix,
        422,
        2,
    )


def fit_items[DataT: BaseModel, ItemT: BaseModel](
    command: str,
    data: DataT,
    items: list[ItemT],
    next_cursor_for: Callable[[int], str],
    *,
    ok: bool = True,
    warnings: list[str] | None = None,
    byte_limit: int = PAGE_BYTE_LIMIT,
) -> tuple[DataT, list[ItemT]]:
    """Fit complete rows and issue a cursor immediately after the retained row.

    The limit includes all seven Result keys and serialized metadata. Callers
    supply their already-authorized keyset anchors; no row is truncated.
    """

    def candidate(count: int) -> tuple[DataT, Result]:
        selected = items[:count]
        updates: dict[str, Any] = {}
        if hasattr(data, "returned"):
            updates["returned"] = count
        if count < len(items):
            updates["next_cursor"] = next_cursor_for(count - 1) if count else None
        if hasattr(data, "subject_actions"):
            ids = {getattr(row, "id", None) for row in selected}
            updates["subject_actions"] = [
                value for value in cast(Any, data).subject_actions if value.subject_id in ids
            ]
        shaped = data.model_copy(update=updates)
        result = Result(
            ok=ok,
            command=command,
            data=shaped.model_dump(mode="json"),
            items=[row.model_dump(mode="json") for row in selected],
            warnings=warnings or [],
        )
        return shaped, result

    shaped, full = candidate(len(items))
    if encoded_size(full) <= byte_limit:
        return shaped, items
    if not items:
        raise entry_too_large(full.data)
    _, first = candidate(1)
    if encoded_size(first) > byte_limit:
        raise entry_too_large(first.data, first.items[0])
    low, high = 1, len(items)
    while low < high:
        middle = (low + high + 1) // 2
        _, result = candidate(middle)
        if encoded_size(result) <= byte_limit:
            low = middle
        else:
            high = middle - 1
    shaped, _ = candidate(low)
    return shaped, items[:low]


def query_model[T: BaseModel](model: type[T], **values: Any) -> T:
    try:
        return model.model_validate(values)
    except ValidationError:
        raise ServiceError(
            "invalid_input", "Projection parameters are incompatible or out of bounds", 422, 2
        ) from None


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def principal(actor: Identity) -> dict[str, Any]:
    return {
        "org": str(actor.org_id),
        "user": str(actor.user_id),
        "token": str(actor.token_id),
        "actor": actor.actor_kind,
        "role": actor.role,
        "scopes": digest(sorted(actor.scopes)),
    }


def cursor(
    settings: Settings, actor: Identity, binding: dict[str, Any], anchor: dict[str, Any]
) -> str:
    return TokenSigner.for_tokens(settings).issue(
        {
            "kind": "assessment_cursor",
            "principal": principal(actor),
            "binding": digest({key: value for key, value in binding.items() if key != "snapshot"}),
            "snapshot": binding.get("snapshot"),
            "anchor": anchor,
        },
        86400,
    )


def snapshot_token(settings: Settings, actor: Identity, binding: dict[str, Any]) -> str:
    """Stable signed identity for assembling pages from one authorized snapshot."""
    value = digest({"principal": principal(actor), "binding": binding})
    signature = hmac.new(
        settings.token_key.get_secret_value().encode(), value.encode(), hashlib.sha256
    ).hexdigest()
    return f"{value}.{signature}"


def read_cursor(
    settings: Settings, actor: Identity, binding: dict[str, Any], value: str | None
) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        payload = TokenSigner.for_tokens(settings).open(value)
        if (
            payload.get("kind") != "assessment_cursor"
            or payload.get("principal") != principal(actor)
            or payload.get("binding")
            != digest({key: value for key, value in binding.items() if key != "snapshot"})
            or not isinstance(payload.get("anchor"), dict)
        ):
            raise ValueError("binding")
        if payload.get("snapshot") != binding.get("snapshot"):
            raise ServiceError(
                "assessment_view_changed", "Assessment snapshot changed; reload the view", 409, 2
            )
        return payload["anchor"]
    except ServiceError as error:
        if error.code == "assessment_view_changed":
            raise
        raise ServiceError(
            "invalid_cursor", "Cursor does not match this authorized snapshot/query", 400, 2
        ) from None
    except (ValueError, KeyError, TypeError):
        raise ServiceError(
            "invalid_cursor", "Cursor does not match this authorized snapshot/query", 400, 2
        ) from None


async def notice_positions(session: AsyncSession, total: int, start: int, limit: int) -> list[int]:
    """SQL-keyset the derived metadata positions before projecting public rows."""
    if start < 0 or start > total:
        raise ServiceError("invalid_cursor", "Cursor has an invalid notice position", 400, 2)
    if total == 0:
        return []
    positions = select(func.generate_series(0, total - 1).label("position")).subquery()
    return list(
        (
            await session.scalars(
                select(positions.c.position)
                .where(positions.c.position >= start)
                .order_by(positions.c.position)
                .limit(limit + 1)
            )
        ).all()
    )


def order_values(anchor: dict[str, Any], length: int) -> tuple[list[int], UUID]:
    try:
        values = anchor["order"]
        if (
            not isinstance(values, list)
            or len(values) != length
            or any(type(value) is not int for value in values)
        ):
            raise ValueError("order")
        return values, UUID(anchor["id"])
    except (KeyError, TypeError, ValueError):
        raise ServiceError(
            "invalid_cursor", "Cursor has an invalid ordered row position", 400, 2
        ) from None


def text_window(original: str, quote: str, query: CitationRequest) -> tuple[TextWindow, int, int]:
    start = original.find(quote)
    if not quote or start < 0:
        raise ServiceError(
            "invalid_input_citation", "Saved quote does not occur at its source", 409, 4
        )
    if query.offset > len(original if query.text == "context" else quote):
        raise ServiceError("invalid_input", "Text offset exceeds source length", 400, 2)
    text = original if query.text == "context" else quote
    selected = text[query.offset : query.offset + query.limit]
    end = query.offset + len(selected)
    return (
        TextWindow(
            text=selected,
            offset=query.offset,
            total_characters=len(text),
            next_offset=end if end < len(text) else None,
        ),
        start,
        start + len(quote),
    )
