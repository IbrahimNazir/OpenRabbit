"""Application configuration via pydantic-settings.

Loads all settings from environment variables (or .env file).
See .env.example for documented variable names and defaults.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Central configuration for the OpenRabbit application."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- GitHub App ---
    github_app_id: str = ""
    github_app_private_key_path: str = "./private-key.pem"
    github_webhook_secret: str = ""

    # --- AI / LLM ---
    anthropic_api_key: str = ""

    # --- Database ---
    database_url: str = "postgresql+asyncpg://openrabbit:devpassword@localhost:5432/openrabbit"
    sync_database_url: str = (
        "postgresql+psycopg2://openrabbit:devpassword@localhost:5432/openrabbit"
    )

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"

    # --- Vector DB ---
    qdrant_url: str = "http://localhost:6333"

    # --- Application ---
    log_level: str = "INFO"
    admin_secret: str = ""
    smee_url: str = ""

    @property
    def github_private_key(self) -> str:
        """Read the GitHub App private key from file."""
        key_path = Path(self.github_app_private_key_path)
        if not key_path.exists():
            logger.warning(
                "GitHub App private key not found at %s — GitHub API calls will fail",
                key_path,
            )
            return ""
        return key_path.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings singleton."""
    return Settings()
