import base64
import binascii
import json
import os
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def canonical_fernet_key(value: str) -> bytes:
    """Reject permissive Fernet encodings so roots have a single comparable identity."""
    try:
        encoded = value.encode("ascii")
        decoded = base64.b64decode(encoded, altchars=b"-_", validate=True)
        if len(decoded) != 32 or base64.urlsafe_b64encode(decoded) != encoded:
            raise ValueError
    except (ValueError, UnicodeError, binascii.Error):
        raise ValueError("Root keys must use canonical Fernet encoding") from None
    return decoded


class PDFSettings(BaseSettings):
    """PDF limits can also be loaded by standalone parsing without service credentials."""

    model_config = SettingsConfigDict(
        env_prefix="BID_", extra="ignore", env_ignore_empty=True, hide_input_in_errors=True
    )
    pdf_timeout_seconds: float = Field(default=600, gt=0, allow_inf_nan=False)
    pdf_memory_bytes: int = Field(default=1024 * 1024 * 1024, gt=0)
    pdf_cpu_seconds: int = Field(default=300, ge=1)
    pdf_output_bytes: int = Field(default=256 * 1024 * 1024, gt=0)


# Origins of the desktop vendor templates (desktop/native_settings_templates.go).
VENDOR_TEMPLATE_ORIGINS = (
    "https://open.bigmodel.cn",
    "https://api.deepseek.com",
    "https://dashscope.aliyuncs.com",
    "https://api.moonshot.cn",
    "https://api.minimaxi.com",
    "https://api.siliconflow.cn",
    "https://openrouter.ai",
)


def https_origin(value: str) -> str:
    """`https://host[:port]` with nothing after it but an optional slash."""
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        parsed.port  # noqa: B018 -- rejects a malformed port
    except ValueError:
        raise ValueError(f"{value!r} must be an https:// origin") from None
    return f"https://{parsed.netloc.lower()}"


class DeploymentSettings(BaseSettings):
    """What `init-db` needs to record the deployment mode; no service credentials."""

    model_config = SettingsConfigDict(
        env_prefix="BID_", extra="ignore", env_ignore_empty=True, hide_input_in_errors=True
    )
    deployment: Literal["personal", "enterprise"] = "personal"
    # Written into the installation row at init; required there in enterprise mode.
    company_name: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("company_name", mode="before")
    @classmethod
    def strip_company(cls, value):
        return value.strip() if isinstance(value, str) else value


class Settings(PDFSettings, DeploymentSettings):
    database_url: SecretStr
    access_key: SecretStr = Field(min_length=32)
    encryption_key: SecretStr
    # Retired data keys, comma separated: still decrypt, never encrypt.
    encryption_key_previous: Annotated[list[SecretStr], NoDecode] = []
    # Sessions and signed links; not data encryption.
    token_key: SecretStr
    secrets_key: SecretStr | None = None
    secrets_key_previous: Annotated[list[SecretStr], NoDecode] = []
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
    # Standalone/eval model parameters; jobs use their fixed provider configuration.
    llm_provider: str = "disabled"
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None
    llm_max_output_tokens: int = 32000
    llm_timeout_seconds: float = 600
    llm_batch_chars: int = 8000
    # Serialized score request characters; independent of extraction/reasoning batches.
    score_batch_chars: int = Field(default=64000, ge=1000)
    # Complete serialized rubric request, including prompts, schema and vendor options.
    rubric_max_request_bytes: int = Field(default=192 * 1024, ge=1000)
    llm_concurrency: int = 8
    # Drafting sends short requirement quotes, not document pages, so its batches may be
    # this many times the extraction budget of the reasoning level.
    drafting_batch_scale: int = Field(default=4, ge=1, le=16)
    llm_effort: str | None = "high"
    llm_anthropic_fallback: bool = True
    llm_json_mode: str = "json_schema"
    # JSON object merged into every model request, e.g. {"thinking": {"type": "disabled"}}.
    llm_request_options: str | None = None
    # Shared model-job call ceilings; retries of the same job count toward the same ceiling.
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
    # Operator-run SearXNG base URL, used only when search_provider explicitly selects it.
    search_url: str | None = None
    search_provider: str = "disabled"
    # Legacy direct field retained only so startup can reject leaked deployment values.
    perplexity_api_key: SecretStr | None = None
    # Private Gotenberg (LibreOffice) base URL for export page previews; unset disables them.
    converter_url: str | None = None
    # Desktop alternative when no Gotenberg is configured: the bundled bid-docx-preview
    # binary, which lays the DOCX out in the system WebKit (desktop/preview).
    preview_renderer: str | None = None
    review_office_profile: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$"
    )
    converter_timeout_seconds: float = Field(default=180, gt=0, le=600, allow_inf_nan=False)
    preview_max_pages: int = Field(default=1000, ge=1, le=2000)
    # Enterprise gateway only (docs/plan/enterprise-gateway.md#deployment-mode).
    public_url: str | None = None
    session_hours: int = Field(default=12, ge=1, le=72)
    session_idle_minutes: int = Field(default=120, ge=10, le=720)
    # An empty BID_PERSONAL_MODEL_ENDPOINTS disables personal models.
    personal_model_endpoints: Annotated[list[str], NoDecode] = list(VENDOR_TEMPLATE_ORIGINS)
    password_min_length: int = Field(default=12, ge=12, le=128)

    @classmethod
    def load(cls):
        # BaseSettings supplies required fields from environment at runtime.
        cls._refuse_vendor_credentials(cls._legacy_vendor_environment())
        settings = cls()  # pyright: ignore[reportCallIssue]
        settings.assert_vendor_credentials_absent()
        return settings

    @model_validator(mode="before")
    @classmethod
    def empty_endpoint_list(cls, data):
        # env_ignore_empty would otherwise turn an empty list back into the default.
        if (
            isinstance(data, dict)
            and "personal_model_endpoints" not in data
            and os.environ.get("BID_PERSONAL_MODEL_ENDPOINTS") == ""
        ):
            return {**data, "personal_model_endpoints": []}
        return data

    @field_validator("personal_model_endpoints", mode="before")
    @classmethod
    def split_endpoints(cls, value):
        if isinstance(value, str):
            value = [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("personal_model_endpoints")
    @classmethod
    def endpoint_origins(cls, value: list[str]) -> list[str]:
        origins = [https_origin(item) for item in value]
        return list(dict.fromkeys(origins))

    @field_validator("public_url")
    @classmethod
    def public_origin(cls, value: str | None) -> str | None:
        return https_origin(value) if value is not None else None

    @model_validator(mode="after")
    def enterprise_complete(self):
        if self.deployment == "enterprise" and self.public_url is None:
            raise ValueError("BID_PUBLIC_URL is required in enterprise mode")
        return self

    @field_validator("encryption_key_previous", "secrets_key_previous", mode="before")
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
                canonical_fernet_key(value)
            except (ValueError, TypeError):
                raise ValueError("Encryption and token keys must be Fernet keys") from None
        if current in previous or len(set(previous)) != len(previous):
            raise ValueError("BID_ENCRYPTION_KEY_PREVIOUS must list distinct retired keys")
        secret_roots = [item.get_secret_value() for item in self.secrets_key_previous]
        if self.secrets_key is not None:
            secret_roots.insert(0, self.secrets_key.get_secret_value())
        for value in secret_roots:
            try:
                canonical_fernet_key(value)
            except (ValueError, TypeError):
                raise ValueError("Provider secret keys must be Fernet keys") from None
        if len(set(secret_roots)) != len(secret_roots):
            raise ValueError("BID_SECRETS_KEY_PREVIOUS must list distinct retired keys")
        if set(secret_roots) & {current, *previous, token} or token in {current, *previous}:
            raise ValueError("Data, token and provider secret roots must remain distinct")
        return self

    @field_validator("database_url")
    @classmethod
    def postgres_only(cls, value: SecretStr) -> SecretStr:
        if value is None:
            return value
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
        if self.llm_provider == "anthropic":
            self.llm_model = self.llm_model or "claude-opus-5-5"
        if self.llm_provider == "openai" and not self.llm_model:
            raise ValueError("BID_LLM_MODEL is required for the openai provider")
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
        return self

    def request_options(self) -> dict:
        """Extra vendor parameters; the adapter's own fields always take precedence."""
        if not self.llm_request_options:
            return {}
        value = json.loads(self.llm_request_options)
        if not isinstance(value, dict):
            raise ValueError("BID_LLM_REQUEST_OPTIONS must be a JSON object")
        return value

    @field_validator("search_provider")
    @classmethod
    def search_backend(cls, value: str) -> str:
        if value not in {"disabled", "perplexity", "searxng"}:
            raise ValueError("search_provider must be disabled, perplexity or searxng")
        return value

    @field_validator("llm_base_url", "search_url", "converter_url", "s3_endpoint")
    @classmethod
    def no_endpoint_credentials(cls, value: str | None) -> str | None:
        from urllib.parse import urlsplit

        if value is not None:
            try:
                parsed = urlsplit(value)
                if (
                    parsed.username is not None
                    or parsed.password is not None
                    or parsed.query
                    or parsed.fragment
                ):
                    raise ValueError
            except ValueError:
                raise ValueError(
                    "Service endpoints must not contain authentication values"
                ) from None
        return value

    @staticmethod
    def _legacy_vendor_environment() -> set[str]:
        return {
            name
            for name in os.environ
            if (
                name.startswith("BID_PLATFORM_CREDENTIAL_")
                or name in {"BID_LLM_API_KEY", "BID_PERPLEXITY_API_KEY"}
            )
        }

    @staticmethod
    def _refuse_vendor_credentials(names: set[str]) -> None:
        from app.core.errors import ServiceError

        if names:
            raise ServiceError(
                "credential_env_forbidden",
                "Remove legacy vendor credential configuration: " + ", ".join(sorted(names)),
                503,
                4,
            )

    def assert_vendor_credentials_absent(self) -> None:
        names = self._legacy_vendor_environment()
        for field, name in (
            (self.llm_api_key, "BID_LLM_API_KEY"),
            (self.perplexity_api_key, "BID_PERPLEXITY_API_KEY"),
        ):
            if field is not None and field.get_secret_value():
                names.add(name)
        self._refuse_vendor_credentials(names)
