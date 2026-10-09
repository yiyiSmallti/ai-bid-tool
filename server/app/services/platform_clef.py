"""Platform-only Clef configuration and live, purpose-bound credential fences."""

import json
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.credential_db import get_connections
from app.core.errors import ServiceError
from app.schemas.clef import (
    ClefCheckRequest,
    ClefPlatformConfig,
    ClefResolution,
    ClefSettingsData,
    ClefSettingsSet,
)
from app.schemas.platform_credentials import PlatformOperator, ServiceResolveTarget
from app.services.platform_credentials import PlatformCredentialResolver, require_operator


async def _config(session: AsyncSession) -> ClefPlatformConfig | None:
    value = await session.scalar(text("SELECT public.platform_clef_read()"))
    return None if value is None else ClefPlatformConfig.model_validate(value)


async def _readiness(settings: Settings, config: ClefPlatformConfig) -> list[str]:
    resolver = PlatformCredentialResolver(settings)
    blockers = []
    targets = (
        (
            ServiceResolveTarget(
                service="clef_workers_ai", credential_id=config.workers_credential_id
            ),
            config.workers_credential_revision,
        ),
        (
            ServiceResolveTarget(
                service="clef_gateway", credential_id=config.gateway_credential_id
            ),
            config.gateway_credential_revision,
        ),
    )
    for target, revision in targets:
        raw = await resolver._read(
            "SELECT public.platform_credential_readiness(CAST(:target AS jsonb))",
            {"target": target.model_dump_json()},
        )
        if not raw.get("configured"):
            blockers.append("clef_credential_unavailable")
        elif raw.get("revision") != revision:
            blockers.append("clef_credential_changed")
    return sorted(set(blockers))


async def resolve(session: AsyncSession, settings: Settings) -> ClefResolution:
    """Write-free preflight snapshot; inability to resolve never authorizes dispatch."""
    try:
        config = await _config(session)
        if config is None:
            return ClefResolution(None, ["clef_unconfigured"])
        blockers = await _readiness(settings, config)
        if not config.enabled:
            blockers.append("clef_disabled")
        if config.gateway_check is None or config.gateway_check.gateway_id != config.gateway_id:
            blockers.append("clef_gateway_unchecked")
        elif config.gateway_check.checked_at > datetime.now(UTC):
            blockers.append("clef_gateway_unsafe")
        return ClefResolution(config, sorted(set(blockers)))
    except ServiceError as exc:
        return ClefResolution(None, [exc.code])
    except (SQLAlchemyError, ValidationError, TypeError, ValueError):
        return ClefResolution(None, ["clef_configuration_unavailable"])


async def resolve_credentials(settings: Settings, config: ClefPlatformConfig):
    """Decrypt both active platform secrets immediately before the one admitted call."""
    resolver = PlatformCredentialResolver(settings)
    workers = await resolver.resolve_for_call(
        ServiceResolveTarget(service="clef_workers_ai", credential_id=config.workers_credential_id)
    )
    gateway = await resolver.resolve_for_call(
        ServiceResolveTarget(service="clef_gateway", credential_id=config.gateway_credential_id)
    )
    if (
        workers.revision != config.workers_credential_revision
        or gateway.revision != config.gateway_credential_revision
        or workers.provider != "cloudflare"
        or gateway.provider != "cloudflare"
        or workers.endpoint != "https://gateway.ai.cloudflare.com"
        or gateway.endpoint != "https://api.cloudflare.com/client/v4"
    ):
        raise ServiceError(
            "clef_credential_changed", "Clef credentials changed; refresh configuration", 409, 2
        )
    return workers, gateway


class PlatformClefService:
    def __init__(self, settings: Settings, *, checker=None):
        self.settings = settings
        self.connections = get_connections(settings)
        if checker is None:
            from app.providers.clef_gateway import HTTPGatewayChecker

            checker = HTTPGatewayChecker()
        self.checker = checker

    async def show(self, actor: PlatformOperator) -> ClefSettingsData:
        require_operator(actor)
        async with self.connections.management.transaction() as connection:
            value = await connection.scalar(text("SELECT public.platform_clef_read()"))
        config = None if value is None else ClefPlatformConfig.model_validate(value)
        if config is None:
            return ClefSettingsData(blockers=["clef_unconfigured"])
        blockers = await _readiness(self.settings, config)
        if not config.enabled:
            blockers.append("clef_disabled")
        if config.gateway_check is None:
            blockers.append("clef_gateway_unchecked")
        return ClefSettingsData(config=config, blockers=sorted(set(blockers)))

    async def _manage(self, actor: PlatformOperator, action: str, body: dict) -> ClefPlatformConfig:
        require_operator(actor)
        try:
            async with self.connections.management.transaction() as connection:
                value = await connection.scalar(
                    text("SELECT public.platform_clef_manage(:action,:actor,CAST(:body AS jsonb))"),
                    {"action": action, "actor": actor.email, "body": json.dumps(body)},
                )
            return ClefPlatformConfig.model_validate(value)
        except SQLAlchemyError as exc:
            diagnostic = getattr(getattr(exc, "orig", None), "diag", None)
            code = getattr(diagnostic, "message_primary", "")
            if code == "revision_conflict":
                raise ServiceError(
                    code, "Clef settings changed; refresh before retrying", 409, 2
                ) from None
            if code in {"invalid_input", "credential_reference_mismatch"}:
                raise ServiceError(
                    code, "Clef settings or credentials are invalid", 422, 2
                ) from None
            raise ServiceError(
                "clef_configuration_unavailable", "Clef settings could not be saved", 503, 3
            ) from None

    async def set(self, actor: PlatformOperator, body: ClefSettingsSet) -> ClefSettingsData:
        if body.currency != self.settings.billing_currency:
            raise ServiceError(
                "invalid_input", "Clef price must use the deployment billing currency", 422, 2
            )
        config = await self._manage(actor, "set", body.model_dump(mode="json"))
        blockers = await _readiness(self.settings, config)
        if not config.enabled:
            blockers.append("clef_disabled")
        blockers.append("clef_gateway_unchecked")
        return ClefSettingsData(config=config, blockers=sorted(set(blockers)))

    async def check(self, actor: PlatformOperator, body: ClefCheckRequest) -> ClefSettingsData:
        current = await self.show(actor)
        config = current.config
        if config is None or config.revision != body.expected_revision:
            raise ServiceError(
                "revision_conflict", "Clef settings changed; refresh before retrying", 409, 2
            )
        receipt, check_error = None, None
        try:
            _, gateway = await resolve_credentials(self.settings, config)
            receipt = await self.checker.check(config, gateway)
        except ServiceError as exc:
            check_error = exc.code
        updated = await self._manage(
            actor,
            "check",
            {
                "expected_revision": body.expected_revision,
                "gateway_check": None if receipt is None else receipt.model_dump(mode="json"),
                "check_error": check_error,
            },
        )
        blockers = await _readiness(self.settings, updated)
        if not updated.enabled:
            blockers.append("clef_disabled")
        if check_error:
            blockers.append(check_error)
        return ClefSettingsData(config=updated, blockers=sorted(set(blockers)))
