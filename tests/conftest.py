"""Shared fixtures for PitchBook tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from pitchbook.config import Settings
from pitchbook.store import PitchBookStore


@pytest.fixture()
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture()
def store(tmp_db: Path) -> PitchBookStore:
    return PitchBookStore(tmp_db)


@pytest.fixture()
def settings(tmp_db: Path) -> Settings:
    """Settings with a fake API key pointing to a temp database."""
    return Settings(
        api_key="test-key-not-real",
        db_path=tmp_db,
        anthropic_api_key="test-anthropic-key",
    )
