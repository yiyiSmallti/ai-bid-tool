"""Bounded metadata reads never resolve credentials or contact a provider."""

import json
import time
from datetime import UTC, datetime
from typing import NoReturn
from uuid import UUID

from cryptography.fernet import InvalidToken
from pydantic import ValidationError
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.core.security import TokenSigner
from app.models.provider_configs import ProviderConfig, ProviderSlot
from app.providers.configured import hidden_slots, latest_revisions, live_slots
from app.schemas.contracts import Cost, Result
from app.schemas.management_pages import (
    DETAIL_BYTE_LIMIT,
    PAGE_BYTE_LIMIT,
    ActionHint,
    Page,
    PageData,
    PageQuery,
)
from app.schemas.management_providers import (
    ProviderRevisionMetadata,
    ProviderSettingsData,
    ReasoningChoice,
)
from app.schemas.provider_contracts import ProviderConfigInput
from app.services.auth import Identity
from app.services.management_products import (
    authority,
    read_budget,
    result_bytes,
    settings_for,
    too_large,
)
from app.services.task_access import live_actor

COMMANDS = {
    "settings": "provider show",
    "revision": "provider revision show",
    "history": "provider history-page",
}


def fail(code, message, status=400, exit_code=2) -> NoReturn:
    raise ServiceError(code, message, status, exit_code) from None


CONFIG_FIELDS = (
    "name",
    "provider",
    "model",
    "base_url",
    "json_mode",
    "reasoning",
    "default_reasoning",
)
# Credential material is excluded before SQL results are materialized.
CONFIG_COLUMNS = (
    ProviderConfig.id,
    ProviderConfig.revision,
    ProviderConfig.capability,
    ProviderConfig.slot,
    ProviderConfig.data,
    ProviderConfig.created_at,
    select(ProviderSlot.owner_member_id)
    .where(ProviderSlot.slot == ProviderConfig.slot)
    .scalar_subquery()
    .label("owner_member_id"),
)


async def reader(session: AsyncSession, actor: Identity) -> Identity:
    authenticated = session.info.pop("management_authenticated_actor", None)
    live = actor if authenticated is actor else await live_actor(session, actor)
    if live.session_expires_at is not None and live.session_expires_at <= datetime.now(UTC):
        fail("invalid_session", "Invalid or expired credentials", 401, 4)
    live.require("provider:read")
    return live


def safe_reasoning(levels) -> list[ReasoningChoice]:
    return [ReasoningChoice(name=level["name"], label=level.get("label")) for level in levels]


def config_statement(actor):
    return select(*CONFIG_COLUMNS).where(
        ProviderConfig.capability == "llm_extract",
        ProviderConfig.slot.not_in(hidden_slots(actor.member_id)),
    )


def metadata(actor, row) -> ProviderRevisionMetadata:
    if row["capability"] != "llm_extract" or row["revision"] < 1:
        fail("management_integrity_error", "Provider revision identity is invalid", 409, 4)
    saved = row["data"]
    try:
        configuration = ProviderConfigInput(
            capability="llm_extract",
            slot=row["slot"],
            owner="personal" if row.get("owner_member_id") is not None else "company",
            **{key: saved.get(key) for key in CONFIG_FIELDS},
        )
        return ProviderRevisionMetadata(
            id=row["id"],
            slot=row["slot"],
            name=saved.get("name"),
            revision=row["revision"],
            configuration=configuration,
            provider=saved["provider"],
            model=saved["model"],
            updated_at=row["created_at"],
            revised_at=row["created_at"],
            reasoning=safe_reasoning(saved.get("reasoning", [])),
            default_reasoning=saved.get("default_reasoning"),
        )
    except (ValidationError, KeyError, TypeError):
        fail("management_integrity_error", "Provider metadata is invalid", 409, 4)


async def settings(session: AsyncSession, actor: Identity) -> ProviderSettingsData:
    async with read_budget(session, PageQuery()):
        actor = await reader(session, actor)
        slots, default = await live_slots(session)
        rows = (
            (await session.execute(latest_revisions(CONFIG_COLUMNS, slots))).mappings().all()
            if slots
            else []
        )
        rows = sorted(rows, key=lambda row: (row["slot"] != default, -row["revision"]))
        models = [metadata(actor, row) for row in rows]
        current = models[0] if models and models[0].slot == default else None
        write = (
            actor.actor_kind == "session"
            and actor.token_id is None
            and "provider:write" in actor.scopes
        )
        reason = "human_required"
        unavailable = current is None
        # A member configures personal models; testing the company default is an administrator's.
        tester = write and (actor.member_id is None or actor.member_role == "admin")
        actions = [
            ActionHint(action="configure", allowed=write, reason=None if write else reason),
            ActionHint(
                action="test",
                allowed=tester and not unavailable,
                reason=None
                if tester and not unavailable
                else "provider_unavailable"
                if tester
                else "scope_required"
                if write
                else reason,
            ),
        ]
        value = ProviderSettingsData(
            configured=current is not None,
            current=current,
            models=models,
            default_slot=current.slot if current else None,
            reasoning=current.reasoning if current else [],
            actions=actions,
        )
        detail_result(COMMANDS["settings"], value)
        return value


async def revision(
    session: AsyncSession, actor: Identity, identifier: UUID
) -> ProviderRevisionMetadata:
    async with read_budget(session, PageQuery()):
        actor = await reader(session, actor)
        row = (
            (
                await session.execute(
                    config_statement(actor).where(ProviderConfig.id == identifier).limit(1)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise not_found()
        value = metadata(actor, row)
        detail_result(COMMANDS["revision"], value)
        return value


def binding(actor, purpose, normalized):
    return {
        "kind": "management_provider_cursor",
        "authority": authority(actor),
        "purpose": purpose,
        "filters": normalized,
        "order": "revision_desc",
    }


def open_cursor(session, actor, body, purpose, normalized):
    if body.cursor is None:
        return None
    try:
        value = json.loads(
            TokenSigner.for_tokens(settings_for(session)).cipher.decrypt(body.cursor.encode())
        )
        if not isinstance(value, dict) or any(
            value.get(key) != expected
            for key, expected in binding(actor, purpose, normalized).items()
        ):
            raise ValueError
        if type(value.get("exp")) is not int:
            raise ValueError
        if value["exp"] <= time.time():
            fail("management_cursor_expired", "Management cursor expired; restart the page", 409)
        anchor = value["anchor"]
        if (
            not isinstance(anchor, list)
            or len(anchor) != 2
            or type(anchor[0]) is not int
            or anchor[0] < 1
        ):
            raise ValueError
        return anchor[0], UUID(anchor[1])
    except (InvalidToken, ValueError, TypeError, KeyError):
        fail("management_cursor_invalid", "Invalid management cursor")


def encoded_result(command, data, items, duration_ms=0):
    return Result(
        ok=True,
        command=command,
        data=data.model_dump(mode="json"),
        items=[item.model_dump(mode="json") for item in items],
        cost=Cost(),
        duration_ms=duration_ms,
    )


def detail_result(command, data, duration_ms=0):
    value = encoded_result(command, data, [], duration_ms)
    if result_bytes(value) > DETAIL_BYTE_LIMIT:
        too_large()
    return value


def page_result(command, page, duration_ms=0):
    value = encoded_result(command, page.data, page.items, duration_ms)
    if result_bytes(value) > PAGE_BYTE_LIMIT:
        too_large()
    return value


def build_page(session, actor, purpose, normalized, items, anchors, more):
    retained = len(items)
    as_of = datetime.now(UTC)
    while retained:
        has_more = more or retained < len(items)
        continuation = (
            TokenSigner.for_tokens(settings_for(session)).issue(
                {**binding(actor, purpose, normalized), "anchor": anchors[retained - 1]}, 15 * 60
            )
            if has_more
            else None
        )
        if continuation is not None and len(continuation) > 2048:
            fail("management_cursor_invalid", "Invalid management cursor")
        data = PageData(
            as_of=as_of,
            returned=retained,
            next_cursor=continuation,
            has_more=has_more,
        )
        if (
            result_bytes(
                encoded_result(
                    COMMANDS[purpose],
                    data,
                    items[:retained],
                    9223372036854775807,
                )
            )
            <= PAGE_BYTE_LIMIT
        ):
            return Page(data=data, items=items[:retained])
        retained -= 1
    if items:
        too_large()
    return Page(data=PageData(as_of=as_of, returned=0, has_more=False), items=[])


async def history(
    session: AsyncSession, actor: Identity, body: PageQuery
) -> Page[ProviderRevisionMetadata]:
    async with read_budget(session, body):
        actor = await reader(session, actor)
        anchor = open_cursor(session, actor, body, "history", {})
        statement = config_statement(actor)
        if anchor:
            statement = statement.where(
                tuple_(ProviderConfig.revision, ProviderConfig.id) < tuple_(*anchor)
            )
        rows = (
            (
                await session.execute(
                    statement.order_by(
                        ProviderConfig.revision.desc(), ProviderConfig.id.desc()
                    ).limit(body.limit + 1)
                )
            )
            .mappings()
            .all()
        )
        rows_page = rows[: body.limit]
        items = [metadata(actor, row) for row in rows_page]
        return build_page(
            session,
            actor,
            "history",
            {},
            items,
            [[row["revision"], str(row["id"])] for row in rows_page],
            len(rows) > body.limit,
        )
