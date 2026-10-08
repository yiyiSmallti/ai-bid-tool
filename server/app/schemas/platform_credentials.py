"""Platform credential wire contracts and private resolver interfaces."""

from dataclasses import dataclass, field
from typing import Annotated, Literal, Protocol, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, SecretStr, field_validator, model_validator

from app.schemas.contracts import CONTRACT_VERSION, Contract, Cost, Result

# Additive commands keep the existing seven-key Result and its published version.
RESULT_CONTRACT_VERSION = CONTRACT_VERSION
CredentialName = Annotated[str, Field(pattern=r"^[a-z0-9_]{1,40}$")]
Revision = Annotated[int, Field(strict=True, ge=1)]
Fingerprint = Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{16}$")]
Provider = Literal["anthropic", "openai", "perplexity"]
Purpose = Literal["catalog_llm", "standalone_llm", "vendor_search"]
CredentialState = Literal["active", "disabled", "removed"]
Reason = Literal[
    "setup", "scheduled_rotation", "vendor_revoked", "incident", "retired", "migration"
]
Command = Literal[
    "platform credential list",
    "platform credential show",
    "platform credential create",
    "platform credential replace",
    "platform credential set-active",
    "platform credential remove",
    "platform credential test",
    "platform credential import-env",
]
ErrorCode = Literal[
    "invalid_input",
    "invalid_session",
    "not_found",
    "revision_conflict",
    "credential_name_conflict",
    "credential_purpose_conflict",
    "credential_import_conflict",
    "credential_reference_mismatch",
    "credential_missing",
    "credential_disabled",
    "credential_removed",
    "credential_unreadable",
    "credential_backend_unavailable",
    "credential_audit_unavailable",
    "credential_env_forbidden",
    "credential_probe_auth_failed",
    "credential_probe_unsupported",
    "credential_probe_timeout",
    "credential_probe_rate_limited",
    "credential_probe_unavailable",
    "credential_probe_interrupted",
    "credential_probe_endpoint_rejected",
]


class CredentialContract(Contract):
    model_config = ConfigDict(extra="forbid", from_attributes=True, hide_input_in_errors=True)


class CredentialSpec(CredentialContract):
    """Immutable routing identity; endpoint is a normalized base URL, never an auth URL."""

    name: CredentialName
    purpose: Purpose
    provider: Provider
    endpoint: str = Field(min_length=8, max_length=300)

    @field_validator("endpoint")
    @classmethod
    def safe_endpoint(cls, value: str) -> str:
        # Egress/DNS policy is a service concern; syntax validation alone is not SSRF protection.
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError:
            raise ValueError("invalid credential endpoint") from None
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or "?" in value
            or "#" in value
            or port not in (None, 443)
            or any(char.isspace() or ord(char) < 32 for char in value)
            or "%" in value
            or "\\" in value
        ):
            raise ValueError("credential endpoint must be an HTTPS base URL without auth")
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        return f"https://{host}{parsed.path.rstrip('/')}"

    @model_validator(mode="after")
    def capability_matches_provider(self) -> Self:
        if self.purpose == "vendor_search":
            if self.provider != "perplexity" or self.endpoint != "https://api.perplexity.ai":
                raise ValueError("vendor search requires the fixed Perplexity endpoint")
        elif self.provider == "perplexity":
            raise ValueError("LLM credentials require an LLM provider")
        return self


class SecretInput(CredentialContract):
    # Exclusion is defense in depth, not a response model. HTTP writers extract this field only
    # at the request boundary; generic dumps, validation errors and repr must never carry it.
    api_key: SecretStr = Field(repr=False, exclude=True, json_schema_extra={"writeOnly": True})

    @field_validator("api_key")
    @classmethod
    def bounded_api_key(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not 16 <= len(raw) <= 4096 or any(ord(char) < 33 or ord(char) > 126 for char in raw):
            raise ValueError("API key must contain 16 to 4096 printable non-space ASCII characters")
        return value


class CredentialCreateInput(CredentialSpec):
    """Non-secret CLI input file; the API adds a separately supplied key."""

    active: bool = False
    reason: Reason = "setup"


class CredentialCreate(CredentialCreateInput, SecretInput):
    pass


class CredentialReplace(SecretInput):
    expected_revision: Revision
    reason: Reason


class CredentialSetActive(CredentialContract):
    expected_revision: Revision
    active: bool
    reason: Reason


class CredentialRemove(CredentialContract):
    expected_revision: Revision
    reason: Literal["vendor_revoked", "incident", "retired"]


class CredentialTest(CredentialContract):
    expected_revision: Revision
    # No caller-supplied endpoint, prompt, model, headers or raw key is accepted.
    mode: Literal["authentication"] = "authentication"


class CatalogConsumer(CredentialContract):
    kind: Literal["catalog_model"] = "catalog_model"
    model_id: str
    model_revision: Revision
    enabled: bool
    default: bool


class ServiceConsumer(CredentialContract):
    kind: Literal["service"] = "service"
    service: Literal["vendor_search", "standalone_llm"]
    selected: bool


Consumer = Annotated[CatalogConsumer | ServiceConsumer, Field(discriminator="kind")]


class CredentialView(CredentialSpec):
    id: UUID
    revision: Revision
    secret_version: Revision
    state: CredentialState
    fingerprint: Fingerprint
    last_four: str = Field(pattern=r"^[!-~]{4}$")
    created_at: AwareDatetime
    updated_at: AwareDatetime
    updated_by: str
    # Catalog references and fixed service selection only; never tenant jobs, orgs or BYOK.
    consumers: list[Consumer] = Field(default_factory=list)


class CredentialListQuery(CredentialContract):
    state: CredentialState | None = None
    purpose: Purpose | None = None
    after_name: CredentialName | None = None
    limit: int = Field(default=100, strict=True, ge=1, le=100)


class CredentialListData(CredentialContract):
    next_after_name: CredentialName | None = None


class CredentialData(CredentialContract):
    credential: CredentialView


ProbeOutcome = Literal[
    "passed", "auth_failed", "unsupported", "timeout", "rate_limited", "unavailable", "interrupted"
]


class CredentialProbeView(CredentialContract):
    probe_id: UUID
    credential_id: UUID
    tested_revision: Revision
    secret_version: Revision
    outcome: ProbeOutcome
    duration_ms: int = Field(ge=0)
    checked_at: AwareDatetime
    retry_after_seconds: int | None = Field(default=None, ge=0, le=3600)
    proves: Literal["authentication_only"] = "authentication_only"
    # No response body, provider request ID, returned model names or headers.


class CredentialProbeData(CredentialContract):
    probe: CredentialProbeView


class CredentialImportEntry(CredentialCreate):
    """Wire input for an explicit import; values exist only in request/encryption memory."""

    source_env: str = Field(
        pattern=r"^(BID_PLATFORM_CREDENTIAL_[A-Z0-9_]{1,40}|BID_PERPLEXITY_API_KEY|BID_LLM_API_KEY)$"
    )
    reason: Literal["migration"] = "migration"

    @model_validator(mode="after")
    def source_matches_destination(self) -> Self:
        if self.source_env.startswith("BID_PLATFORM_CREDENTIAL_"):
            if self.purpose != "catalog_llm" or self.source_env != (
                "BID_PLATFORM_CREDENTIAL_" + self.name.upper()
            ):
                raise ValueError("catalog import must preserve its credential name")
        elif self.source_env == "BID_PERPLEXITY_API_KEY" and self.purpose != "vendor_search":
            raise ValueError("Perplexity import requires the search purpose")
        elif self.source_env == "BID_LLM_API_KEY" and self.purpose != "standalone_llm":
            raise ValueError("standalone import requires the standalone purpose")
        return self


class CredentialImportRequest(CredentialContract):
    entries: list[CredentialImportEntry] = Field(
        min_length=1, max_length=100, repr=False, exclude=True
    )
    dry_run: bool = False

    @model_validator(mode="after")
    def unique_names_and_sources(self) -> Self:
        names = [entry.name for entry in self.entries]
        sources = [entry.source_env for entry in self.entries]
        services = [entry.purpose for entry in self.entries if entry.purpose != "catalog_llm"]
        if (
            len(set(names)) != len(names)
            or len(set(sources)) != len(sources)
            or len(set(services)) != len(services)
        ):
            raise ValueError("import names, sources and service purposes must be unique")
        return self


class CredentialImportManifestEntry(CredentialSpec):
    """Non-secret CLI manifest; source_env is checked again by CredentialImportEntry."""

    source_env: str = Field(
        pattern=r"^(BID_PLATFORM_CREDENTIAL_[A-Z0-9_]{1,40}|BID_PERPLEXITY_API_KEY|BID_LLM_API_KEY)$"
    )
    active: bool = False


class CredentialImportManifest(CredentialContract):
    entries: list[CredentialImportManifestEntry] = Field(min_length=1, max_length=100)


class CredentialImportItem(CredentialContract):
    name: CredentialName
    action: Literal["would_create", "would_skip", "created", "skipped"]
    credential_id: UUID | None = None


class CredentialImportData(CredentialContract):
    dry_run: bool
    created: int = Field(ge=0)
    skipped: int = Field(ge=0)
    would_create: int = Field(ge=0)


class CredentialError(CredentialContract):
    code: ErrorCode
    message: str  # Server-owned fixed text; never str(exception) or a vendor message.
    exit_code: Literal[2, 3, 4]


class CredentialErrorData(CredentialContract):
    error: CredentialError
    probe: CredentialProbeView | None = None


class CredentialAuditDetails(CredentialContract):
    credential_id: UUID | None = None
    credential_name: CredentialName | None = None
    old_revision: Revision | None = None
    new_revision: Revision | None = None
    secret_version: Revision | None = None
    old_state: CredentialState | None = None
    new_state: CredentialState | None = None
    reason: Reason | None = None
    probe_id: UUID | None = None
    outcome: ProbeOutcome | None = None
    error_code: ErrorCode | None = None
    checked: int | None = Field(default=None, ge=0)
    rewritten: int | None = Field(default=None, ge=0)
    # Actor, action and timestamp use existing platform_audit_logs columns.


class PlatformCredentialEnvelope(CredentialSpec, SecretInput):
    """Private encryption payload, never an API response or CLI JSON output."""

    version: Literal[1] = 1
    domain: Literal["platform_credential"] = "platform_credential"
    credential_id: UUID
    secret_version: Revision


class CatalogResolveTarget(CredentialContract):
    kind: Literal["catalog_model"] = "catalog_model"
    model_id: str
    expected_model_revision: Revision
    # Service reads the credential name and endpoint from this catalog row, not caller input.


class ServiceResolveTarget(CredentialContract):
    kind: Literal["service"] = "service"
    service: Literal["vendor_search", "standalone_llm"]
    credential_id: UUID
    # The ID comes from the server's selection/queued non-secret identity, never an org request.


ResolveTarget = Annotated[CatalogResolveTarget | ServiceResolveTarget, Field(discriminator="kind")]


@dataclass(frozen=True)
class ResolvedCredential:
    """Internal, single-call value; deliberately not a Pydantic wire model."""

    credential_id: UUID
    revision: int
    secret_version: int
    provider: Provider
    endpoint: str
    api_key: SecretStr = field(repr=False)


@dataclass(frozen=True)
class PlatformOperator:
    """Created only after platform.identify; no request can instantiate authority by JSON."""

    email: str
    session_expires_at: AwareDatetime


class CredentialReadiness(CredentialContract):
    """Internal consumer check; never exposes ciphertext or becomes an org credential view."""

    credential_id: UUID | None
    state: CredentialState | None
    configured: bool
    # Presence and active state only, not proof of decryption or vendor connectivity.


class PlatformCredentialService(Protocol):
    async def list_credentials(
        self, actor: PlatformOperator, query: CredentialListQuery
    ) -> tuple[CredentialListData, list[CredentialView]]: ...

    async def show(self, actor: PlatformOperator, credential_id: UUID) -> CredentialData: ...

    async def create(self, actor: PlatformOperator, body: CredentialCreate) -> CredentialData: ...

    async def replace(
        self, actor: PlatformOperator, credential_id: UUID, body: CredentialReplace
    ) -> CredentialData: ...

    async def set_active(
        self, actor: PlatformOperator, credential_id: UUID, body: CredentialSetActive
    ) -> CredentialData: ...

    async def remove(
        self, actor: PlatformOperator, credential_id: UUID, body: CredentialRemove
    ) -> CredentialData: ...

    async def test(
        self, actor: PlatformOperator, credential_id: UUID, body: CredentialTest
    ) -> CredentialProbeData | CredentialErrorData: ...

    async def import_env(
        self, actor: PlatformOperator, body: CredentialImportRequest
    ) -> tuple[CredentialImportData, list[CredentialImportItem]]: ...


class PlatformCredentialResolver(Protocol):
    async def readiness(self, target: ResolveTarget) -> CredentialReadiness:
        """Read only safe consumer metadata, without decrypting or returning ciphertext."""
        ...

    async def resolve_for_call(self, target: ResolveTarget) -> ResolvedCredential:
        """Read committed state anew; reject missing/disabled/removed/mismatched rows."""
        ...

    async def resolve_for_probe(self, probe_id: UUID) -> ResolvedCredential:
        """Require a live operator-authorized probe bound to one revision; allow disabled."""
        ...

    async def resolve_for_operator_check(
        self,
        actor: PlatformOperator,
        credential_id: UUID,
        expected_revision: int,
        purpose: Literal["import", "activate"],
    ) -> ResolvedCredential:
        """Private platform service path; permit disabled, reject removed, never serialize."""
        ...


class CredentialProbeProvider(Protocol):
    async def authenticate(
        self, credential: ResolvedCredential, *, probe_id: UUID
    ) -> CredentialProbeView:
        """One allowlisted auth-metadata request, bounded deadline, no retries or content calls."""
        ...


class ProviderSecretsCipher(Protocol):
    def encrypt_platform(self, envelope: PlatformCredentialEnvelope) -> str: ...

    def decrypt_platform(
        self,
        ciphertext: str,
        *,
        expected: CredentialSpec,
        credential_id: UUID,
        secret_version: int,
    ) -> SecretStr: ...

    def rewrap_platform(
        self,
        ciphertext: str,
        *,
        expected: CredentialSpec,
        credential_id: UUID,
        secret_version: int,
    ) -> str | None:
        """Verify binding and preserve payload; None means already encrypted by current key."""
        ...

    def rewrap_org(self, ciphertext: str, *, org_id: UUID, config_id: UUID) -> str | None:
        """Preserve the existing org/config binding and BYOK value for every historical row."""
        ...


class RotationReport(CredentialContract):
    """New admin provider-secrets report; existing default data-only report stays compatible."""

    scope: Literal["provider-secrets"] = "provider-secrets"
    platform_checked: int = Field(ge=0)
    platform_rewritten: int = Field(ge=0)
    operator_factors_checked: int = Field(default=0, ge=0)
    operator_factors_rewritten: int = Field(default=0, ge=0)
    org_revisions_checked: int = Field(ge=0)
    org_revisions_rewritten: int = Field(ge=0)
    failed: int = Field(ge=0)
    exit_code: Literal[0, 3, 4, 5]


# Validated, secret-free examples of the shared wire envelope, not runtime handlers.
RESULT_EXAMPLES: dict[str, Result] = {
    "empty_list": Result(
        ok=True,
        command="platform credential list",
        data=CredentialListData().model_dump(mode="json"),
        items=[],
        warnings=[],
        cost=Cost(),
        duration_ms=2,
    ),
    "removed": Result(
        ok=False,
        command="platform credential test",
        data=CredentialErrorData(
            error=CredentialError(
                code="credential_removed", message="Credential has been removed", exit_code=4
            ),
        ).model_dump(mode="json", exclude_none=True),
        items=[],
        warnings=[],
        cost=Cost(),
        duration_ms=1,
    ),
}
