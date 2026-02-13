"""Tests for Claude Code CLI wrapper."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from tg_assistant.config import Config
from tg_assistant.services.claude_cli import (
    PERMISSION_PROFILES,
    ClaudeCli,
    ClaudeCliError,
)


@pytest.fixture
def cli(config):
    return ClaudeCli(config)


def _make_process(stdout_data: dict, returncode: int = 0, stderr: str = ""):
    """Create a mock asyncio subprocess."""
    process = AsyncMock()
    process.returncode = returncode
    process.communicate = AsyncMock(
        return_value=(json.dumps(stdout_data).encode(), stderr.encode())
    )
    process.kill = AsyncMock()
    process.wait = AsyncMock()
    return process


GOOD_RESPONSE = {
    "type": "result",
    "subtype": "success",
    "result": "Hello from Claude!",
    "session_id": "abc-123",
    "total_cost_usd": 0.05,
    "duration_ms": 1500,
    "is_error": False,
    "num_turns": 1,
}


class TestClaudeCliSend:
    @pytest.mark.asyncio
    async def test_send_new_session(self, cli):
        """CLI is called with --session-id for new sessions."""
        process = _make_process(GOOD_RESPONSE)
        with patch("asyncio.create_subprocess_exec", return_value=process) as mock_exec:
            result = await cli.send("Hello", session_id="abc-123", is_resume=False)

            assert result.text == "Hello from Claude!"
            assert result.session_id == "abc-123"
            assert result.cost_usd == 0.05

            cmd_args = mock_exec.call_args[0]
            assert "--session-id" in cmd_args
            assert "abc-123" in cmd_args

    @pytest.mark.asyncio
    async def test_send_resume_session(self, cli):
        """CLI is called with -r for resumed sessions."""
        process = _make_process(GOOD_RESPONSE)
        with patch("asyncio.create_subprocess_exec", return_value=process) as mock_exec:
            await cli.send("Hello", session_id="abc-123", is_resume=True)

            cmd_args = mock_exec.call_args[0]
            assert "-r" in cmd_args
            assert "abc-123" in cmd_args
            assert "--session-id" not in cmd_args

    @pytest.mark.asyncio
    async def test_send_no_session(self, cli):
        """CLI is called without session flags when no session_id given."""
        process = _make_process(GOOD_RESPONSE)
        with patch("asyncio.create_subprocess_exec", return_value=process) as mock_exec:
            await cli.send("Hello")

            cmd_args = mock_exec.call_args[0]
            assert "--session-id" not in cmd_args
            assert "-r" not in cmd_args

    @pytest.mark.asyncio
    async def test_non_zero_exit_code(self, cli):
        """ClaudeCliError is raised on non-zero exit."""
        process = _make_process({}, returncode=1, stderr="something went wrong")
        with patch("asyncio.create_subprocess_exec", return_value=process):
            with pytest.raises(ClaudeCliError, match="something went wrong"):
                await cli.send("Hello")

    @pytest.mark.asyncio
    async def test_invalid_json(self, cli):
        """ClaudeCliError is raised on invalid JSON output."""
        process = AsyncMock()
        process.returncode = 0
        process.communicate = AsyncMock(return_value=(b"not json", b""))
        with patch("asyncio.create_subprocess_exec", return_value=process):
            with pytest.raises(ClaudeCliError, match="parse"):
                await cli.send("Hello")

    @pytest.mark.asyncio
    async def test_timeout(self, cli):
        """TimeoutError is raised and process is killed on timeout."""
        process = AsyncMock()
        process.kill = AsyncMock()
        process.wait = AsyncMock()

        async def slow_communicate(input=None):
            await asyncio.sleep(100)
            return b"", b""

        process.communicate = slow_communicate

        with patch("asyncio.create_subprocess_exec", return_value=process):
            with pytest.raises(asyncio.TimeoutError):
                await cli.send("Hello")

            process.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_command_includes_permissions_flag(self, cli):
        """Default (full) profile includes --dangerously-skip-permissions."""
        process = _make_process(GOOD_RESPONSE)
        with patch("asyncio.create_subprocess_exec", return_value=process) as mock_exec:
            await cli.send("Hello")

            cmd_args = mock_exec.call_args[0]
            assert "--dangerously-skip-permissions" in cmd_args

    @pytest.mark.asyncio
    async def test_command_includes_json_format(self, cli):
        """CLI command includes --output-format json."""
        process = _make_process(GOOD_RESPONSE)
        with patch("asyncio.create_subprocess_exec", return_value=process) as mock_exec:
            await cli.send("Hello")

            cmd_args = mock_exec.call_args[0]
            assert "--output-format" in cmd_args
            idx = list(cmd_args).index("--output-format")
            assert cmd_args[idx + 1] == "json"


class TestGenerateSessionId:
    def test_returns_uuid_format(self, cli):
        sid = cli.generate_session_id()
        assert len(sid) == 36  # UUID format: 8-4-4-4-12
        assert sid.count("-") == 4

    def test_unique_ids(self, cli):
        ids = {cli.generate_session_id() for _ in range(100)}
        assert len(ids) == 100


class TestPermissionProfiles:
    """Test permission profile definitions."""

    def test_full_profile(self):
        profile = PERMISSION_PROFILES["full"]
        assert profile.use_dangerous_skip is True
        assert profile.allowed_tools is None
        assert profile.permission_mode is None

    def test_safe_profile(self):
        profile = PERMISSION_PROFILES["safe"]
        assert profile.use_dangerous_skip is False
        assert profile.permission_mode == "acceptEdits"
        assert "Read" in profile.allowed_tools
        assert "Edit" in profile.allowed_tools
        assert "Write" in profile.allowed_tools
        assert "Bash(git:log)" in profile.allowed_tools

    def test_readonly_profile(self):
        profile = PERMISSION_PROFILES["readonly"]
        assert profile.use_dangerous_skip is False
        assert profile.permission_mode == "plan"
        assert "Read" in profile.allowed_tools
        assert "Grep" in profile.allowed_tools
        assert "Glob" in profile.allowed_tools
        assert "Edit" not in profile.allowed_tools
        assert "Write" not in profile.allowed_tools

    def test_unknown_profile_raises(self):
        with pytest.raises(KeyError):
            PERMISSION_PROFILES["nonexistent"]


def _make_config_with_profile(tmp_path, profile: str) -> Config:
    """Create a Config with a specific permission profile."""
    (tmp_path / "data" / "uploads").mkdir(parents=True, exist_ok=True)
    return Config(
        bot_token="test-token",
        allowed_user_ids=[111],
        claude_cli_path="/usr/bin/echo",
        claude_working_dir=str(tmp_path),
        claude_permission_profile=profile,
        data_dir=tmp_path / "data",
        upload_dir=tmp_path / "data" / "uploads",
        db_path=tmp_path / "data" / "test.db",
    )


class TestBuildCommandWithProfiles:
    """Test CLI command building with different profiles."""

    def test_full_profile_command(self, tmp_path):
        cli = ClaudeCli(_make_config_with_profile(tmp_path, "full"))
        cmd = cli._build_command()
        assert "--dangerously-skip-permissions" in cmd
        assert "--allowedTools" not in cmd
        assert "--permission-mode" not in cmd

    def test_safe_profile_command(self, tmp_path):
        cli = ClaudeCli(_make_config_with_profile(tmp_path, "safe"))
        cmd = cli._build_command()
        assert "--dangerously-skip-permissions" not in cmd
        assert "--permission-mode" in cmd
        idx = cmd.index("--permission-mode")
        assert cmd[idx + 1] == "acceptEdits"
        assert "--allowedTools" in cmd
        tools_idx = cmd.index("--allowedTools")
        tools_str = cmd[tools_idx + 1]
        assert "Read" in tools_str
        assert "Edit" in tools_str
        assert "Write" in tools_str
        assert "--append-system-prompt" in cmd

    def test_readonly_profile_command(self, tmp_path):
        cli = ClaudeCli(_make_config_with_profile(tmp_path, "readonly"))
        cmd = cli._build_command()
        assert "--dangerously-skip-permissions" not in cmd
        assert "--permission-mode" in cmd
        idx = cmd.index("--permission-mode")
        assert cmd[idx + 1] == "plan"
        assert "--allowedTools" in cmd
        tools_idx = cmd.index("--allowedTools")
        tools_str = cmd[tools_idx + 1]
        assert "Read" in tools_str
        assert "Grep" in tools_str
        assert "Edit" not in tools_str
        assert "Write" not in tools_str

    def test_profile_with_session_flags(self, tmp_path):
        cli = ClaudeCli(_make_config_with_profile(tmp_path, "safe"))
        cmd = cli._build_command(session_id="test-123", is_resume=False)
        assert "--session-id" in cmd
        assert "test-123" in cmd
        assert "--allowedTools" in cmd
        assert "--permission-mode" in cmd
