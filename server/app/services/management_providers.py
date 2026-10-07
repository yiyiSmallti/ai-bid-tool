"""Bounded metadata reads never resolve credentials or contact a provider."""

import json
import re
import time
from datetime import UTC, datetime
from typing import NoReturn
from uuid import UUID

from cryptography.fernet import InvalidToken
from pydantic import ValidationError
from sqlalchemy import String, cast, func, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.core.security import TokenSigner
from app.models.entities import AuditLog, PlatformModel
from app.models.provider_configs import ProviderConfig
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
    CatalogReasoningChoice,
    PlatformModelChoice,
    ProviderCatalogQuery,
    ProviderRevisionMetadata,
    ProviderSettingsData,
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
from app.services.task_workflow import live_actor

COMMANDS = {
    "settings": "provider show",
    "revision": "provider revision show",
    "history": "provider history-page",
    "catalog": "provider catalog",
}


def fail(code, message, status=400, exit_code=2) -> NoReturn:
    raise ServiceError(code, message, status, exit_code) from None


CONFIG_FIELDS = (
    "provider",
    "model",
    "base_url",
    "json_mode",
    "reasoning",
    "default_reasoning",
    "input_usd_per_mtok",
    "output_usd_per_mtok",
)
# Even internal SQL materialization excludes credential ciphertext/suffix and
# catalog endpoint, credential reference and wholesale prices.
CONFIG_COLUMNS = (
    ProviderConfig.id,
    ProviderConfig.org_id,
    ProviderConfig.revision,
    ProviderConfig.capability,
    ProviderConfig.source,
    ProviderConfig.platform_model_id,
    ProviderConfig.data,
    ProviderConfig.updated_by,
    ProviderConfig.created_at,
)
CATALOG_COLUMNS = (
    PlatformModel.id,
    PlatformModel.revision,
    PlatformModel.model,
    PlatformModel.provider,
    PlatformModel.sale_input_per_mtok,
    PlatformModel.sale_output_per_mtok,
    PlatformModel.is_default,
    PlatformModel.reasoning,
    PlatformModel.default_reasoning,
)


async def reader(session: AsyncSession, actor: Identity) -> Identity:
    authenticated = session.info.pop("management_authenticated_actor", None)
    live = actor if authenticated is actor else await live_actor(session, actor)
    if live.session_expires_at is not None and live.session_expires_at <= datetime.now(UTC):
        fail("invalid_session", "Invalid or expired credentials", 401, 4)
    live.require("provider:read")
    return live


def safe_reasoning(levels) -> list[CatalogReasoningChoice]:
    return [
        CatalogReasoningChoice(name=level["name"], label=level.get("label")) for level in levels
    ]


def catalog_view(row) -> PlatformModelChoice:
    return PlatformModelChoice(
        id=row["id"],
        revision=row["revision"],
        model=row["model"],
        provider=row["provider"],
        sale_input_per_mtok=row["sale_input_per_mtok"],
        sale_output_per_mtok=row["sale_output_per_mtok"],
        default=row["is_default"],
        reasoning=safe_reasoning(row["reasoning"]),
        default_reasoning=row["default_reasoning"],
    )


def config_statement(actor):
    return (
        select(*CONFIG_COLUMNS, PlatformModel.enabled.label("catalog_enabled"))
        .outerjoin(
            PlatformModel,
            (PlatformModel.id == ProviderConfig.platform_model_id)
            & (PlatformModel.capability == "llm_extract"),
        )
        .where(ProviderConfig.org_id == actor.org_id, ProviderConfig.capability == "llm_extract")
    )


async def authors(session, actor, rows) -> dict[UUID, UUID | None]:
    ids = [row["id"] for row in rows]
    if not ids:
        return {}
    records = (
        await session.execute(
            select(
                ProviderConfig.id,
                func.count(AuditLog.id),
                func.min(cast(AuditLog.actor_user_id, String)),
            )
            .join(
                AuditLog,
                (AuditLog.org_id == ProviderConfig.org_id)
                & (AuditLog.object_id == ProviderConfig.id)
                & (AuditLog.details["revision"] == func.to_jsonb(ProviderConfig.revision)),
            )
            .where(
                ProviderConfig.org_id == actor.org_id,
                ProviderConfig.id.in_(ids),
                AuditLog.org_id == actor.org_id,
                AuditLog.object_id.in_(ids),
                text("audit_logs.action = 'provider.set'"),
            )
            .group_by(ProviderConfig.id)
        )
    ).all()
    result: dict[UUID, UUID | None] = {identifier: None for identifier in ids}
    for identifier, count, actor_id in records:
        if count == 1 and actor_id is not None:
            result[identifier] = UUID(actor_id)
    return result


def metadata(actor, row, author) -> ProviderRevisionMetadata:
    if row["org_id"] != actor.org_id or row["capability"] != "llm_extract" or row["revision"] < 1:
        fail("management_integrity_error", "Provider revision identity is invalid", 409, 4)
    saved = row["data"]
    org = row["source"] == "org"
    try:
        configuration = ProviderConfigInput(
            capability="llm_extract",
            source=row["source"],
            platform_model_id=row["platform_model_id"],
            **({key: saved.get(key) for key in CONFIG_FIELDS} if org else {}),
        )
        return ProviderRevisionMetadata(
            id=row["id"],
            org_id=row["org_id"],
            revision=row["revision"],
            configuration=configuration,
            provider=saved["provider"],
            model=saved["model"],
            catalog_state="not_applicable"
            if org
            else "enabled"
            if row["catalog_enabled"]
            else "unavailable",
            credential_state="configured" if org else "platform_managed",
            updated_by=row["updated_by"],
            updated_at=row["created_at"],
            revised_at=row["created_at"],
            revised_by=author,
            reasoning=safe_reasoning(saved.get("reasoning", [])),
            default_reasoning=saved.get("default_reasoning"),
            catalog_revision=None if org else saved.get("catalog_revision"),
            sale_input_per_mtok=None if org else saved.get("sale_input_per_mtok"),
            sale_output_per_mtok=None if org else saved.get("sale_output_per_mtok"),
        )
    except (ValidationError, KeyError, TypeError):
        fail("management_integrity_error", "Provider metadata is invalid", 409, 4)


async def settings(session: AsyncSession, actor: Identity) -> ProviderSettingsData:
    async with read_budget(session, PageQuery()):
        actor = await reader(session, actor)
        row = (
            (
                await session.execute(
                    config_statement(actor)
                    .order_by(ProviderConfig.revision.desc(), ProviderConfig.id.desc())
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        current = (
            metadata(actor, row, (await authors(session, actor, [row]))[row["id"]]) if row else None
        )
        # A saved selection remains authoritative even when its catalog is disabled.
        # Never insert an enabled default into that saved selection's display.
        default = None
        if current is None:
            default_row = (
                (
                    await session.execute(
                        select(*CATALOG_COLUMNS)
                        .where(
                            text("platform_models.capability = 'llm_extract'"),
                            PlatformModel.enabled.is_(True),
                            PlatformModel.is_default.is_(True),
                        )
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )
            default = catalog_view(default_row) if default_row else None
        write = (
            actor.actor_kind == "session"
            and actor.token_id is None
            and actor.role == "admin"
            and "provider:write" in actor.scopes
        )
        reason = (
            "human_required"
            if actor.actor_kind != "session" or actor.token_id is not None
            else "role_required"
        )
        unavailable = (
            current is not None
            and current.catalog_state == "unavailable"
            or current is None
            and default is None
        )
        actions = [
            ActionHint(action="configure", allowed=write, reason=None if write else reason),
            ActionHint(
                action="test",
                allowed=write and not unavailable,
                reason=None
                if write and not unavailable
                else "provider_unavailable"
                if write
                else reason,
            ),
        ]
        value = ProviderSettingsData(
            org_id=actor.org_id,
            effective_source=current.configuration.source
            if current
            else "platform"
            if default
            else "unconfigured",
            current=current,
            default_model=default,
            billing_currency=settings_for(session).billing_currency,
            reasoning=current.reasoning if current else default.reasoning if default else [],
            actions=actions,
        )
        detail_result(COMMANDS["settings"], value, settings_for(session).billing_currency)
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
        value = metadata(actor, row, (await authors(session, actor, [row]))[row["id"]])
        detail_result(COMMANDS["revision"], value, settings_for(session).billing_currency)
        return value


def binding(actor, purpose, normalized):
    return {
        "kind": "management_provider_cursor",
        "org": str(actor.org_id),
        "authority": authority(actor),
        "purpose": purpose,
        "filters": normalized,
        "order": "id_asc" if purpose == "catalog" else "revision_desc",
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
        if purpose == "catalog":
            if not isinstance(anchor, str) or not 1 <= len(anchor) <= 40:
                raise ValueError
            return anchor
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


def encoded_result(command, data, items, currency, duration_ms=0):
    return Result(
        ok=True,
        command=command,
        data=data.model_dump(mode="json"),
        items=[item.model_dump(mode="json") for item in items],
        cost=Cost(billing_currency=currency),
        duration_ms=duration_ms,
    )


def detail_result(command, data, currency, duration_ms=0):
    value = encoded_result(command, data, [], currency, duration_ms)
    if result_bytes(value) > DETAIL_BYTE_LIMIT:
        too_large()
    return value


def page_result(command, page, currency, duration_ms=0):
    value = encoded_result(command, page.data, page.items, currency, duration_ms)
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
            org_id=actor.org_id,
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
                    settings_for(session).billing_currency,
                    9223372036854775807,
                )
            )
            <= PAGE_BYTE_LIMIT
        ):
            return Page(data=data, items=items[:retained])
        retained -= 1
    if items:
        too_large()
    return Page(
        data=PageData(org_id=actor.org_id, as_of=as_of, returned=0, has_more=False), items=[]
    )


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
        revision_authors = await authors(session, actor, rows_page)
        items = [metadata(actor, row, revision_authors[row["id"]]) for row in rows_page]
        return build_page(
            session,
            actor,
            "history",
            {},
            items,
            [[row["revision"], str(row["id"])] for row in rows_page],
            len(rows) > body.limit,
        )


async def catalog(
    session: AsyncSession, actor: Identity, body: ProviderCatalogQuery
) -> Page[PlatformModelChoice]:
    async with read_budget(session, body):
        actor = await reader(session, actor)
        normalized = {"id_prefix": body.q.casefold() if body.q else None}
        anchor = open_cursor(session, actor, body, "catalog", normalized)
        # Keep this fixed predicate identical to migration 0056's partial index,
        # including IS TRUE (not bare enabled). Literal capability also lets
        # generic prepared plans prove the predicate before applying LIMIT.
        statement = select(*CATALOG_COLUMNS).where(
            text("platform_models.enabled IS TRUE AND platform_models.capability = 'llm_extract'")
        )
        ordered_id = PlatformModel.id.collate("C")
        if body.q:
            prefix = body.q.casefold()
            # Catalog IDs contain only this ASCII alphabet. Literal range bounds
            # stay indexable for generic prepared plans, unlike parameterized LIKE.
            # Invalid ID characters have no possible catalog match (including NUL).
            statement = (
                statement.where(ordered_id >= prefix, ordered_id < prefix + "\uffff")
                if re.fullmatch(r"[a-z0-9_-]+", prefix)
                else statement.where(text("false"))
            )
        if anchor:
            statement = statement.where(ordered_id > anchor)
        rows = (
            (await session.execute(statement.order_by(ordered_id.asc()).limit(body.limit + 1)))
            .mappings()
            .all()
        )
        try:
            items = [catalog_view(row) for row in rows[: body.limit]]
        except (ValidationError, KeyError, TypeError):
            fail("management_integrity_error", "Platform model metadata is invalid", 409, 4)
        return build_page(
            session,
            actor,
            "catalog",
            normalized,
            items,
            [row["id"] for row in rows[: body.limit]],
            len(rows) > body.limit,
        )
