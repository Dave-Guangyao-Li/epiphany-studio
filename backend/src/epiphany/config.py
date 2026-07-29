from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="EPIPHANY_",
        extra="ignore",
        populate_by_name=True,
    )

    database_url: str = "sqlite+aiosqlite:///./data/epiphany.db"
    create_schema_on_start: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    worker_enabled: bool = True
    worker_poll_interval_seconds: float = Field(default=0.25, gt=0)
    worker_max_concurrency: int = Field(default=2, ge=1, le=2)
    worker_lease_seconds: int = Field(default=30, gt=0)
    task_timeout_seconds: float = Field(default=30, gt=0)
    task_max_attempts: int = Field(default=2, ge=1, le=5)
    model_max_calls_per_run: int = Field(default=6, ge=1, le=100)
    model_provider: Literal["fake", "deepseek"] = "fake"
    deepseek_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "EPIPHANY_DEEPSEEK_API_KEY",
            "DEEPSEEK_API_KEY",
        ),
    )
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com",
        validation_alias=AliasChoices(
            "EPIPHANY_DEEPSEEK_BASE_URL",
            "DEEPSEEK_BASE_URL",
        ),
    )
    deepseek_model: Literal["deepseek-v4-flash", "deepseek-v4-pro"] = Field(
        default="deepseek-v4-flash",
        validation_alias=AliasChoices(
            "EPIPHANY_DEEPSEEK_MODEL",
            "DEEPSEEK_MODEL",
        ),
    )
    deepseek_billing_currency: Literal["CNY", "USD"] = Field(
        default="USD",
        validation_alias=AliasChoices(
            "EPIPHANY_DEEPSEEK_BILLING_CURRENCY",
            "DEEPSEEK_BILLING_CURRENCY",
        ),
    )
    deepseek_max_tokens: int = Field(default=2_000, ge=1, le=20_000)
    deepseek_editor_max_tokens: int = Field(default=6_000, ge=1, le=20_000)
    deepseek_max_source_chars: int = Field(default=24_000, ge=1, le=1_000_000)
    deepseek_max_interview_bundle_chars: int = Field(
        default=24_000,
        ge=1,
        le=1_000_000,
    )
    deepseek_max_editor_bundle_chars: int = Field(
        default=48_000,
        ge=1,
        le=1_000_000,
    )

    @field_validator("deepseek_billing_currency", mode="before")
    @classmethod
    def normalize_deepseek_billing_currency(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value


def ensure_sqlite_parent(database_url: str) -> None:
    prefix = "sqlite+aiosqlite:///"
    if not database_url.startswith(prefix):
        return

    raw_path = database_url.removeprefix(prefix)
    if raw_path in {":memory:", ""} or raw_path.startswith("file:"):
        return

    Path(raw_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
