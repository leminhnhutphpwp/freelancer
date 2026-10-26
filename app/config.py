"""Application settings loaded from environment / .env file."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Core ---
    environment: str = "development"
    log_level: str = "INFO"

    # --- Freelancer.com ---
    freelancer_base_url: str = "https://www.freelancer.com/api"
    freelancer_access_token: str = ""
    freelancer_timeout_seconds: float = 20.0
    freelancer_max_retries: int = 3

    # --- Database ---
    database_url: str = "postgresql+asyncpg://freelancer:freelancer@localhost:5432/freelancer_assistant"
    db_echo: bool = False

    # --- Admin REST API ---
    admin_api_key: str = ""

    # --- Telegram ---
    telegram_bot_token: str = ""
    # NoDecode prevents pydantic-settings from JSON-decoding the raw env value
    # (an empty string would otherwise raise a parse error).
    telegram_allowed_chat_ids: Annotated[list[int], NoDecode] = []

    # --- Worker cadence (seconds) ---
    search_poll_interval_seconds: int = 300
    bid_poll_interval_seconds: int = 180
    thread_poll_interval_seconds: int = 120
    pending_action_ttl_seconds: int = 900
    search_page_limit: int = 100
    recent_message_window_seconds: int = 3600

    # --- Default bid parameters used when drafting a bid from Telegram ---
    default_bid_period_days: int = 7
    default_milestone_percentage: float = 50.0

    @field_validator("telegram_allowed_chat_ids", mode="before")
    @classmethod
    def _parse_chat_ids(cls, v: object) -> object:
        if isinstance(v, str):
            return [int(part) for part in v.replace(";", ",").split(",") if part.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"prod", "production"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
