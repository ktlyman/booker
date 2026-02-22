"""Configuration for the PitchBook integration."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthMode(StrEnum):
    AUTO = "auto"
    API_KEY = "api_key"
    COOKIES = "cookies"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="PITCHBOOK_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # Authentication
    auth_mode: AuthMode = Field(
        default=AuthMode.AUTO,
        description="Auth method: 'auto' (try key then cookies), 'api_key', or 'cookies'",
    )

    # PitchBook API
    api_key: str = Field(default="", description="PitchBook API key")
    api_base_url: str = Field(
        default="https://api.pitchbook.com/v2",
        description="PitchBook API v2 base URL",
    )
    web_base_url: str = Field(
        default="https://pitchbook.com",
        description="PitchBook website base URL (used for cookie-based auth)",
    )
    api_timeout: int = Field(default=30, description="API request timeout in seconds")
    api_max_retries: int = Field(default=3, description="Max retries for failed API calls")

    # Local data store
    db_path: Path = Field(
        default=Path("pitchbook_data.db"),
        description="Path to the SQLite database file",
    )

    # Listener
    poll_interval_seconds: int = Field(
        default=300,
        description="Seconds between polling cycles for the listener",
    )

    # Claude (for agent interface)
    anthropic_api_key: str = Field(default="", description="Anthropic API key for agent queries")
    claude_model: str = Field(
        default="claude-sonnet-4-20250514",
        description="Claude model to use for agent query synthesis",
    )

    @model_validator(mode="after")
    def validate_auth_config(self) -> Settings:
        """Ensure a valid auth method is available."""
        if self.auth_mode == AuthMode.API_KEY and not self.api_key:
            raise ValueError(
                "PITCHBOOK_API_KEY is required when auth_mode='api_key'. "
                "Set PITCHBOOK_AUTH_MODE='cookies' to use Chrome cookies instead."
            )
        return self
