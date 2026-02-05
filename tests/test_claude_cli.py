"""Tests for Claude Code CLI wrapper."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from tg_assistant.services.claude_cli import ClaudeCli, ClaudeCliError


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
            result = await cli.send("Hello", session_id="abc-123", is_resume=True)

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
        """CLI command includes --dangerously-skip-permissions."""
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
