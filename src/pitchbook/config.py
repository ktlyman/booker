"""Configuration for the PitchBook integration."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="PITCHBOOK_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # PitchBook API
    api_key: str = Field(description="PitchBook API key")
    api_base_url: str = Field(
        default="https://api.pitchbook.com/v2",
        description="PitchBook API v2 base URL",
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
