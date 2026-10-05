"""Write-only operator workflows and uncached single-consumer credential resolution."""

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.core.credential_db import close_connections, get_connections
from app.core.errors import ServiceError
from app.core.provider_secrets import ProviderSecrets
from app.schemas.platform_credentials import (
    CatalogResolveTarget,
    CredentialCreate,
    CredentialData,
    CredentialError,
    CredentialErrorData,
    CredentialImportData,
    CredentialImportItem,
    CredentialImportRequest,
    CredentialListData,
    CredentialListQuery,
    CredentialProbeData,
    CredentialProbeView,
    CredentialReadiness,
    CredentialRemove,
    CredentialReplace,
    CredentialSetActive,
    CredentialSpec,
    CredentialTest,
    CredentialView,
    PlatformCredentialEnvelope,
    PlatformOperator,
    ResolvedCredential,
    ResolveTarget,
    ServiceResolveTarget,
)

ERROR_MESSAGES = {
    "invalid_input": "Invalid credential input",
    "invalid_session": "Invalid platform session",
    "not_found": "Credential not found",
    "revision_conflict": "Credential changed; refresh before retrying",
    "credential_name_conflict": "Credential name is already used",
    "credential_purpose_conflict": "A credential already occupies this service",
    "credential_import_conflict": "Import conflicts with an existing credential",
    "credential_reference_mismatch": "Credential does not match its consumer",
    "credential_missing": "Credential is missing",
    "credential_disabled": "Credential is disabled",
    "credential_removed": "Credential has been removed",
    "credential_unreadable": "Credential cannot be decrypted",
    "credential_backend_unavailable": "Credential database is unavailable",
    "credential_audit_unavailable": "Credential audit could not be saved",
    "credential_env_forbidden": "Legacy vendor credential configuration is forbidden",
    "credential_probe_auth_failed": "Credential authentication was refused",
    "credential_probe_unsupported": "Authentication metadata probe is unsupported",
    "credential_probe_timeout": "Credential authentication probe timed out",
    "credential_probe_rate_limited": "Credential authentication probe is rate limited",
    "credential_probe_unavailable": "Credential authentication probe is unavailable",
    "credential_probe_interrupted": "Credential authentication probe was interrupted",
    "credential_probe_endpoint_rejected": "Credential probe endpoint is not permitted",
}


def credential_error(code: str) -> ServiceError:
    if code not in ERROR_MESSAGES:
        code = "credential_backend_unavailable"
    if code == "invalid_input":
        status, exit_code = 422, 2
    elif code == "invalid_session":
        status, exit_code = 401, 4
    elif code == "not_found":
        status, exit_code = 404, 4
    elif code.endswith("conflict") or code == "credential_reference_mismatch":
        status, exit_code = 409, 2
    elif code in {"credential_missing", "credential_disabled", "credential_removed"}:
        status, exit_code = 409, 4
    elif code in {"credential_unreadable", "credential_env_forbidden"}:
        status, exit_code = 503, 4
    elif code in {
        "credential_probe_auth_failed",
        "credential_probe_unsupported",
        "credential_probe_endpoint_rejected",
    }:
        status, exit_code = 422, 4
    elif code == "credential_probe_rate_limited":
        status, exit_code = 429, 3
    else:
        status, exit_code = 503, 3
    return ServiceError(code, ERROR_MESSAGES[code], status, exit_code)


def database_error(exc: SQLAlchemyError) -> ServiceError:
    # Only exact server-defined codes are accepted. Never stringify the SQL exception:
    # even hidden bind parameters cannot sanitize arbitrary database diagnostic messages.
    diagnostic = getattr(getattr(exc, "orig", None), "diag", None)
    code = getattr(diagnostic, "message_primary", "")
    return credential_error(code if code in ERROR_MESSAGES else "credential_backend_unavailable")


def require_operator(actor: PlatformOperator) -> None:
    if not isinstance(actor, PlatformOperator) or actor.session_expires_at <= datetime.now(UTC):
        raise credential_error("invalid_session")


class PlatformCredentialResolver:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.connections = get_connections(settings)

    async def _read(self, statement: str, params: dict) -> dict:
        try:
            async with self.connections.reader.transaction() as connection:
                value = await connection.scalar(text(statement), params)
            if not isinstance(value, dict):
                raise credential_error("credential_missing")
            return value
        except SQLAlchemyError as exc:
            raise database_error(exc) from None

    async def readiness(self, target: ResolveTarget) -> CredentialReadiness:
        value = await self._read(
            "SELECT public.platform_credential_readiness(CAST(:target AS jsonb))",
            {"target": target.model_dump_json()},
        )
        return CredentialReadiness.model_validate(
            {key: value[key] for key in ("credential_id", "state", "configured")}
        )

    async def select_service(
        self, service: Literal["vendor_search", "standalone_llm"]
    ) -> ServiceResolveTarget:
        value = await self._read(
            "SELECT public.platform_credential_readiness(CAST(:target AS jsonb))",
            {"target": json.dumps({"kind": "service", "service": service})},
        )
        if value.get("credential_id") is None:
            raise credential_error("credential_missing")
        if not value.get("configured"):
            raise credential_error(
                "credential_removed" if value.get("state") == "removed" else "credential_disabled"
            )
        return ServiceResolveTarget(service=service, credential_id=value["credential_id"])

    def _decrypt(self, row: dict) -> ResolvedCredential:
        try:
            spec = CredentialSpec.model_validate(
                {key: row[key] for key in ("name", "purpose", "provider", "endpoint")}
            )
            key = ProviderSecrets(self.settings).decrypt_platform(
                row["encrypted_key"],
                expected=spec,
                credential_id=UUID(str(row["id"])),
                secret_version=row["secret_version"],
            )
            return ResolvedCredential(
                credential_id=UUID(str(row["id"])),
                revision=row["revision"],
                secret_version=row["secret_version"],
                provider=spec.provider,
                endpoint=spec.endpoint,
                api_key=key,
            )
        except (ServiceError, ValidationError, ValueError, KeyError, TypeError):
            raise credential_error("credential_unreadable") from None

    async def resolve_for_call(self, target: ResolveTarget) -> ResolvedCredential:
        if isinstance(target, CatalogResolveTarget):
            row = await self._read(
                "SELECT public.platform_credential_resolve_catalog(:model,:revision)",
                {"model": target.model_id, "revision": target.expected_model_revision},
            )
        else:
            row = await self._read(
                "SELECT public.platform_credential_resolve_service(:service,:id)",
                {"service": target.service, "id": target.credential_id},
            )
        return self._decrypt(row)

    async def resolve_for_probe(self, probe_id: UUID) -> ResolvedCredential:
        return self._decrypt(
            await self._read(
                "SELECT public.platform_credential_resolve_probe(:id)",
                {"id": probe_id},
            )
        )

    async def resolve_for_operator_check(
        self,
        actor: PlatformOperator,
        credential_id: UUID,
        expected_revision: int,
        purpose: Literal["import", "activate"],
    ) -> ResolvedCredential:
        require_operator(actor)
        return self._decrypt(
            await self._read(
                "SELECT public.platform_credential_resolve_operator_check(:id,:revision,:purpose)",
                {"id": credential_id, "revision": expected_revision, "purpose": purpose},
            )
        )

    async def close(self) -> None:
        await close_connections(self.settings)


class PlatformCredentialService:
    def __init__(self, settings: Settings, *, probe=None):
        self.settings = settings
        self.connections = get_connections(settings)
        self.resolver = PlatformCredentialResolver(settings)
        if probe is None:
            from app.providers.credential_probe import MetadataCredentialProbe

            probe = MetadataCredentialProbe()
        self.probe = probe

    async def _manage(self, actor: PlatformOperator, action: str, body: dict) -> dict:
        require_operator(actor)
        try:
            async with self.connections.management.transaction() as connection:
                value = await connection.scalar(
                    text(
                        "SELECT public.platform_credential_manage(:action,:actor,CAST(:body AS jsonb))"
                    ),
                    {"action": action, "actor": actor.email, "body": json.dumps(body)},
                )
            if not isinstance(value, dict):
                raise credential_error("credential_backend_unavailable")
            return value
        except SQLAlchemyError as exc:
            raise database_error(exc) from None

    async def record_failure(
        self,
        actor: PlatformOperator,
        action: str,
        error: ServiceError,
        credential_id: UUID | None = None,
    ):
        action = {"list": "read", "show": "read", "test": "probe_start"}.get(action, action)
        try:
            await self._manage(
                actor,
                "audit",
                {
                    "action": action,
                    "outcome": "failed",
                    "id": str(credential_id) if credential_id else None,
                    "error_code": error.code,
                },
            )
        except ServiceError:
            raise credential_error("credential_audit_unavailable") from None

    def _view(self, raw: dict) -> CredentialView:
        view = CredentialView.model_validate(raw)
        for consumer in view.consumers:
            if consumer.kind == "service":
                selected = (
                    self.settings.search_provider == "perplexity"
                    if consumer.service == "vendor_search"
                    else self.settings.llm_provider == view.provider
                )
                consumer.selected = selected and view.state != "removed"
        return view

    def _data(self, raw: dict) -> CredentialData:
        return CredentialData(credential=self._view(raw["credential"]))

    async def list_credentials(self, actor: PlatformOperator, query: CredentialListQuery):
        result = await self._manage(actor, "list", query.model_dump(mode="json", exclude_none=True))
        return CredentialListData(next_after_name=result.get("next_after_name")), [
            self._view(item) for item in result["items"]
        ]

    async def show(self, actor: PlatformOperator, credential_id: UUID) -> CredentialData:
        return self._data(await self._manage(actor, "show", {"id": str(credential_id)}))

    def _encrypted(
        self, body: CredentialCreate, credential_id: UUID, secret_version: int = 1
    ) -> dict:
        envelope = PlatformCredentialEnvelope(
            **body.model_dump(include={"name", "purpose", "provider", "endpoint"}),
            credential_id=credential_id,
            secret_version=secret_version,
            api_key=body.api_key,
        )
        try:
            ciphertext = ProviderSecrets(self.settings).encrypt_platform(envelope)
        except ServiceError:
            raise credential_error("credential_unreadable") from None
        raw = body.api_key.get_secret_value()
        return {
            "encrypted_key": ciphertext,
            "fingerprint": "sha256:" + hashlib.sha256(raw.encode()).hexdigest()[:16],
            "last_four": raw[-4:],
        }

    async def create(self, actor: PlatformOperator, body: CredentialCreate) -> CredentialData:
        require_operator(actor)
        credential_id = uuid4()
        value = {
            **body.model_dump(mode="json"),
            "id": str(credential_id),
            **self._encrypted(body, credential_id),
        }
        return self._data(await self._manage(actor, "create", value))

    async def replace(
        self, actor: PlatformOperator, credential_id: UUID, body: CredentialReplace
    ) -> CredentialData:
        view = (await self.show(actor, credential_id)).credential
        if view.revision != body.expected_revision:
            raise credential_error("revision_conflict")
        if view.state == "removed":
            raise credential_error("credential_removed")
        create = CredentialCreate(
            **view.model_dump(include={"name", "purpose", "provider", "endpoint"}),
            api_key=body.api_key,
        )
        value = {
            **body.model_dump(mode="json"),
            "id": str(credential_id),
            **self._encrypted(create, credential_id, view.secret_version + 1),
        }
        return self._data(await self._manage(actor, "replace", value))

    async def set_active(
        self, actor: PlatformOperator, credential_id: UUID, body: CredentialSetActive
    ) -> CredentialData:
        value = {**body.model_dump(mode="json"), "id": str(credential_id)}
        if body.active:
            resolved = await self.resolver.resolve_for_operator_check(
                actor, credential_id, body.expected_revision, "activate"
            )
            value["checked_revision"] = resolved.revision
        return self._data(await self._manage(actor, "set_active", value))

    async def remove(
        self, actor: PlatformOperator, credential_id: UUID, body: CredentialRemove
    ) -> CredentialData:
        return self._data(
            await self._manage(
                actor, "remove", {**body.model_dump(mode="json"), "id": str(credential_id)}
            )
        )

    async def import_env(self, actor: PlatformOperator, body: CredentialImportRequest):
        require_operator(actor)
        metadata = [entry.model_dump(mode="json") for entry in body.entries]
        preflight = await self._manage(actor, "import", {"preflight": True, "entries": metadata})
        existing = {item["name"]: item.get("credential") for item in preflight["items"]}
        entries = []
        for entry, values in zip(body.entries, metadata, strict=True):
            old = existing.get(entry.name)
            if old is None:
                credential_id = uuid4()
                values.update({"id": str(credential_id), **self._encrypted(entry, credential_id)})
            else:
                view = CredentialView.model_validate(old)
                if view.state == "removed":
                    raise credential_error("credential_import_conflict")
                resolved = await self.resolver.resolve_for_operator_check(
                    actor, view.id, view.revision, "import"
                )
                values.update(
                    {
                        "id": str(view.id),
                        "expected_revision": view.revision,
                        "same_secret": hmac.compare_digest(
                            entry.api_key.get_secret_value(), resolved.api_key.get_secret_value()
                        ),
                    }
                )
            entries.append(values)
        result = await self._manage(actor, "import", {"entries": entries, "dry_run": body.dry_run})
        return CredentialImportData.model_validate(
            {key: result[key] for key in ("dry_run", "created", "skipped", "would_create")}
        ), [CredentialImportItem.model_validate(item) for item in result["items"]]

    async def test(
        self, actor: PlatformOperator, credential_id: UUID, body: CredentialTest
    ) -> CredentialProbeData | CredentialErrorData:
        authorization = await self._manage(
            actor,
            "probe_begin",
            {"id": str(credential_id), "expected_revision": body.expected_revision},
        )
        probe_id = UUID(authorization["probe_id"])
        started = time.monotonic()
        failure = None
        try:
            resolved = await self.resolver.resolve_for_probe(probe_id)
            probe = await self.probe.authenticate(resolved, probe_id=probe_id)
        except ServiceError as exc:
            failure = credential_error(exc.code)
            probe = CredentialProbeView(
                probe_id=probe_id,
                credential_id=credential_id,
                tested_revision=authorization["tested_revision"],
                secret_version=authorization["secret_version"],
                outcome="unavailable",
                duration_ms=int((time.monotonic() - started) * 1000),
                checked_at=datetime.now(UTC),
            )
        except Exception:
            failure = credential_error("credential_probe_unavailable")
            probe = CredentialProbeView(
                probe_id=probe_id,
                credential_id=credential_id,
                tested_revision=authorization["tested_revision"],
                secret_version=authorization["secret_version"],
                outcome="unavailable",
                duration_ms=int((time.monotonic() - started) * 1000),
                checked_at=datetime.now(UTC),
            )
        try:
            result = await self._manage(actor, "probe_finish", probe.model_dump(mode="json"))
        except ServiceError:
            raise credential_error("credential_audit_unavailable") from None
        safe = CredentialProbeData.model_validate(result)
        if safe.probe.outcome == "passed":
            return safe
        error = failure or credential_error("credential_probe_" + safe.probe.outcome)
        return CredentialErrorData(
            error=CredentialError.model_validate(
                {"code": error.code, "message": error.message, "exit_code": error.exit_code}
            ),
            probe=safe.probe,
        )
