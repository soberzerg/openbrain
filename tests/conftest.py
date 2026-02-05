"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tg_assistant.config import Config
from tg_assistant.models.database import Database


@pytest.fixture
def config(tmp_path: Path) -> Config:
    """Create a test config with temporary paths."""
    return Config(
        bot_token="test-token",
        allowed_user_ids=[111, 222],
        claude_cli_path="/usr/bin/echo",
        claude_working_dir=str(tmp_path),
        claude_timeout_seconds=10,
        session_timeout_minutes=1,
        data_dir=tmp_path / "data",
        upload_dir=tmp_path / "data" / "uploads",
        db_path=tmp_path / "data" / "test.db",
    )


@pytest.fixture
async def db(config: Config) -> Database:
    """Create an initialized test database."""
    config.data_dir.mkdir(parents=True, exist_ok=True)
    database = Database(config.db_path)
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
def mock_cli() -> AsyncMock:
    """Mock ClaudeCli with a default successful response."""
    from tg_assistant.services.claude_cli import ClaudeResponse

    cli = AsyncMock()
    cli.generate_session_id.return_value = "test-session-id"
    cli.send.return_value = ClaudeResponse(
        text="Hello from Claude!",
        session_id="test-session-id",
        cost_usd=0.01,
        duration_ms=1500,
        is_error=False,
        num_turns=1,
    )
    return cli
