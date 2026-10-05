import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Annotated

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class PDFSettings(BaseSettings):
    """PDF limits can also be loaded by standalone parsing without service credentials."""

    model_config = SettingsConfigDict(env_prefix="BID_", extra="ignore", env_ignore_empty=True)
    pdf_timeout_seconds: float = Field(default=600, gt=0, allow_inf_nan=False)
    pdf_memory_bytes: int = Field(default=1024 * 1024 * 1024, gt=0)
    pdf_cpu_seconds: int = Field(default=300, ge=1)
    pdf_output_bytes: int = Field(default=256 * 1024 * 1024, gt=0)


class Settings(PDFSettings):
    database_url: SecretStr
    encryption_key: SecretStr
    # Retired data keys, comma separated: still decrypt, never encrypt.
    encryption_key_previous: Annotated[list[SecretStr], NoDecode] = []
    # Sessions and signed links only; never used for stored data.
    token_key: SecretStr
    secrets_key: SecretStr | None = None
    data_dir: Path = Path("data")
    storage: str = "local"
    s3_endpoint: str | None = None
    s3_bucket: str = "bid"
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None
    session_seconds: int = 3600
    max_upload_bytes: int = 40 * 1024 * 1024
    max_pages: int = 1000
    ocr_language: str = "chi_sim+eng"
    ocr_data_dir: str | None = None
    # Platform-provided extraction model. "disabled" makes extraction fail explicitly.
    llm_provider: str = "disabled"
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None
    llm_max_output_tokens: int = 32000
    llm_timeout_seconds: float = 600
    llm_batch_chars: int = 8000
    # Complete serialized rubric request, including prompts, schema and vendor options.
    rubric_max_request_bytes: int = Field(default=192 * 1024, ge=1000)
    llm_concurrency: int = 4
    # Drafting sends short requirement quotes, not document pages, so its batches may be
    # this many times the extraction budget of the reasoning level.
    drafting_batch_scale: int = Field(default=4, ge=1, le=16)
    llm_effort: str | None = "high"
    llm_anthropic_fallback: bool = True
    llm_json_mode: str = "json_schema"
    # JSON object merged into every model request, e.g. {"thinking": {"type": "disabled"}}.
    llm_request_options: str | None = None
    llm_input_usd_per_mtok: float | None = None
    llm_output_usd_per_mtok: float | None = None
    # Platform operators come from deployment config only, so the app cannot promote anyone.
    platform_admin_emails: str | None = None
    platform_totp_secrets: SecretStr | None = None
    platform_session_seconds: int = 1800
    # Sale prices, charges, balances and card values are all in this ISO 4217 currency.
    billing_currency: str = "USD"
    # Shared model-job guards; retries of the same job consume the same budget.
    job_max_charge: Decimal = Field(default=Decimal("10"), gt=0, allow_inf_nan=False)
    job_max_vendor_calls: int = Field(default=64, ge=1)
    # Per first-pass batch; the call ceiling is the larger of this times the batches and the
    # fixed ceiling above.
    job_vendor_calls_per_batch: float = Field(default=4, ge=1)
    job_lease_seconds: int = Field(default=900, ge=3)
    job_heartbeat_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)
    # Export profiles may be tightened by deployment but never exceed approved limits.
    export_max_requirements: int = Field(default=2000, ge=1, le=2000)
    export_max_attachments: int = Field(default=300, ge=1, le=300)
    export_max_output_bytes: int = Field(default=512 * 1024 * 1024, ge=1, le=512 * 1024 * 1024)
    export_max_expanded_bytes: int = Field(default=1024 * 1024 * 1024, ge=1, le=1024 * 1024 * 1024)
    export_memory_bytes: int = Field(default=1024 * 1024 * 1024, ge=1, le=1024 * 1024 * 1024)
    export_deadline_seconds: float = Field(default=900, gt=0, le=900, allow_inf_nan=False)
    # Operator-run SearXNG base URL for vendor-source search; unset disables search.
    search_url: str | None = None
    # Perplexity Search API key; when set, vendor search uses it instead of SearXNG.
    perplexity_api_key: SecretStr | None = None
    # Private Gotenberg (LibreOffice) base URL for export page previews; unset disables them.
    converter_url: str | None = None
    converter_timeout_seconds: float = Field(default=180, gt=0, le=600, allow_inf_nan=False)
    preview_max_pages: int = Field(default=1000, ge=1, le=2000)
    # Built console from web/dist; served under /app when set.
    web_dir: Path | None = None

    @classmethod
    def load(cls):
        # BaseSettings supplies required fields from environment at runtime.
        return cls()  # pyright: ignore[reportCallIssue]

    @field_validator("encryption_key_previous", mode="before")
    @classmethod
    def split_keys(cls, value):
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def distinct_keys(self):
        current = self.encryption_key.get_secret_value()
        previous = [item.get_secret_value() for item in self.encryption_key_previous]
        token = self.token_key.get_secret_value()
        for value in (current, token, *previous):
            try:
                Fernet(value.encode())
            except (ValueError, TypeError):
                raise ValueError("Encryption and token keys must be Fernet keys") from None
        if current in previous or len(set(previous)) != len(previous):
            raise ValueError("BID_ENCRYPTION_KEY_PREVIOUS must list distinct retired keys")
        others = {current, *previous}
        if self.secrets_key is not None:
            others.add(self.secrets_key.get_secret_value())
        if token in others:
            raise ValueError("BID_TOKEN_KEY must differ from every other key")
        return self

    @field_validator("database_url")
    @classmethod
    def postgres_only(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith("postgresql+psycopg://"):
            raise ValueError("a PostgreSQL psycopg URL is required; file databases are unsupported")
        return value

    @field_validator("storage")
    @classmethod
    def storage_backend(cls, value: str) -> str:
        if value not in {"local", "s3"}:
            raise ValueError("storage must be local or s3")
        return value

    @field_validator("llm_provider")
    @classmethod
    def llm_backend(cls, value: str) -> str:
        if value not in {"disabled", "anthropic", "openai"}:
            raise ValueError("llm_provider must be disabled, anthropic or openai")
        return value

    @field_validator("billing_currency")
    @classmethod
    def currency_code(cls, value: str) -> str:
        value = value.strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", value):
            raise ValueError("billing_currency must be a three-letter ISO 4217 code")
        return value

    @field_validator("llm_json_mode")
    @classmethod
    def json_mode(cls, value: str) -> str:
        if value not in {"json_schema", "json_object"}:
            raise ValueError("llm_json_mode must be json_schema or json_object")
        return value

    @model_validator(mode="after")
    def llm_complete(self):
        if self.job_heartbeat_seconds * 3 > self.job_lease_seconds:
            raise ValueError("Job heartbeat must be at most one third of the lease duration")
        # A selected provider with missing settings must stop startup, not degrade.
        if self.llm_provider == "anthropic":
            self.llm_model = self.llm_model or "claude-opus-5-5"
            if self.llm_api_key is None:
                raise ValueError("BID_LLM_API_KEY is required for the anthropic provider")
        if self.llm_provider == "openai":
            if not self.llm_model:
                raise ValueError("BID_LLM_MODEL is required for the openai provider")
            if self.llm_api_key is None and not self.llm_base_url:
                raise ValueError("BID_LLM_API_KEY is required unless BID_LLM_BASE_URL is set")
        if (
            self.llm_batch_chars < 1000
            or self.llm_max_output_tokens < 1024
            or self.llm_concurrency < 1
        ):
            raise ValueError("LLM batch or output limits are too small")
        try:
            self.request_options()
        except ValueError:
            raise ValueError("BID_LLM_REQUEST_OPTIONS must be a JSON object") from None
        secrets = self.platform_totp()
        missing = set(self.platform_admins()) - set(secrets)
        if missing:
            raise ValueError("Every platform admin needs a BID_PLATFORM_TOTP_SECRETS entry")
        return self

    def request_options(self) -> dict:
        """Extra vendor parameters; the adapter's own fields always take precedence."""
        if not self.llm_request_options:
            return {}
        value = json.loads(self.llm_request_options)
        if not isinstance(value, dict):
            raise ValueError("BID_LLM_REQUEST_OPTIONS must be a JSON object")
        return value

    def platform_admins(self) -> list[str]:
        return [
            email.strip().lower()
            for email in (self.platform_admin_emails or "").split(",")
            if email.strip()
        ]

    def platform_totp(self) -> dict[str, str]:
        """Parse "email:BASE32SECRET" pairs; malformed entries stop startup."""
        from app.core.totp import decode_secret

        raw = self.platform_totp_secrets.get_secret_value() if self.platform_totp_secrets else ""
        pairs = {}
        for entry in filter(None, (item.strip() for item in raw.split(","))):
            email, _, secret = entry.rpartition(":")
            try:
                if len(decode_secret(secret)) < 10:
                    raise ValueError
            except ValueError:
                raise ValueError("BID_PLATFORM_TOTP_SECRETS has an invalid entry") from None
            pairs[email.strip().lower()] = secret.strip()
        return pairs
