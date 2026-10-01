from pathlib import Path

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BID_", extra="ignore", env_ignore_empty=True)
    database_url: SecretStr
    encryption_key: SecretStr
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
    llm_max_output_tokens: int = 16000
    llm_timeout_seconds: float = 600
    llm_batch_chars: int = 30000
    llm_effort: str | None = "high"
    llm_anthropic_fallback: bool = True
    llm_json_mode: str = "json_schema"
    llm_input_usd_per_mtok: float | None = None
    llm_output_usd_per_mtok: float | None = None

    @classmethod
    def load(cls):
        # BaseSettings supplies required fields from environment at runtime.
        return cls()  # pyright: ignore[reportCallIssue]

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

    @field_validator("llm_json_mode")
    @classmethod
    def json_mode(cls, value: str) -> str:
        if value not in {"json_schema", "json_object"}:
            raise ValueError("llm_json_mode must be json_schema or json_object")
        return value

    @model_validator(mode="after")
    def llm_complete(self):
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
        if self.llm_batch_chars < 1000 or self.llm_max_output_tokens < 1024:
            raise ValueError("LLM batch or output limits are too small")
        return self
