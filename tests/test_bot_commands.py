"""Tests for bot command menu registration."""

from unittest.mock import AsyncMock, Mock

import pytest

from tg_assistant.bot import BOT_COMMANDS, _setup_bot_commands


def test_bot_commands_structure():
    """All commands have required fields."""
    assert len(BOT_COMMANDS) == 8

    for cmd_def in BOT_COMMANDS:
        assert cmd_def.command
        assert cmd_def.description
        assert cmd_def.handler
        assert cmd_def.help_text

        # Command names are lowercase, no spaces
        assert cmd_def.command.islower()
        assert " " not in cmd_def.command

        # Descriptions fit Telegram limits (3-256 chars)
        assert 3 <= len(cmd_def.description) <= 256


def test_bot_commands_order():
    """Commands are in expected order."""
    commands = [cmd.command for cmd in BOT_COMMANDS]
    expected = ["start", "help", "new", "status", "tasks", "inbox", "daily", "week"]
    assert commands == expected


def test_bot_commands_descriptions_length():
    """All descriptions are within Telegram limits."""
    for cmd_def in BOT_COMMANDS:
        # Telegram requires 3-256 characters
        assert 3 <= len(cmd_def.description) <= 256
        assert 1 <= len(cmd_def.help_text) <= 256


@pytest.mark.asyncio
async def test_setup_bot_commands_success():
    """Bot commands are registered via set_my_commands."""
    mock_bot = Mock()
    mock_bot.set_my_commands = AsyncMock()

    await _setup_bot_commands(mock_bot)

    mock_bot.set_my_commands.assert_called_once()
    call_args = mock_bot.set_my_commands.call_args[0][0]

    # Verify BotCommand objects are passed
    assert len(call_args) == 8
    assert all(hasattr(cmd, "command") for cmd in call_args)
    assert all(hasattr(cmd, "description") for cmd in call_args)

    # Verify first and last commands
    assert call_args[0].command == "start"
    assert call_args[-1].command == "week"


@pytest.mark.asyncio
async def test_setup_bot_commands_error_handling():
    """Errors during command registration are logged but don't crash."""
    mock_bot = Mock()
    mock_bot.set_my_commands = AsyncMock(side_effect=Exception("API error"))

    # Should not raise
    await _setup_bot_commands(mock_bot)

    # Verify set_my_commands was called despite error
    mock_bot.set_my_commands.assert_called_once()
