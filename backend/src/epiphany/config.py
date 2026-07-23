from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="EPIPHANY_",
        extra="ignore",
    )

    database_url: str = "sqlite+aiosqlite:///./data/epiphany.db"
    create_schema_on_start: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    worker_enabled: bool = True
    worker_poll_interval_seconds: float = Field(default=0.25, gt=0)
    worker_lease_seconds: int = Field(default=30, gt=0)
    task_timeout_seconds: float = Field(default=30, gt=0)
    task_max_attempts: int = Field(default=2, ge=1, le=5)


def ensure_sqlite_parent(database_url: str) -> None:
    prefix = "sqlite+aiosqlite:///"
    if not database_url.startswith(prefix):
        return

    raw_path = database_url.removeprefix(prefix)
    if raw_path in {":memory:", ""} or raw_path.startswith("file:"):
        return

    Path(raw_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
