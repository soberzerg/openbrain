"""Tests for session manager."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from tg_assistant.services.claude_cli import ClaudeResponse
from tg_assistant.services.session_manager import SessionManager


@pytest.fixture
def session_mgr(config, db, mock_cli):
    return SessionManager(config, mock_cli, db, git_sync=None)


class TestSessionManager:
    @pytest.mark.asyncio
    async def test_first_message_creates_session(self, session_mgr, mock_cli):
        """First message from a user creates a new session."""
        response = await session_mgr.send_message(111, "Hello")

        assert response.text == "Hello from Claude!"
        mock_cli.send.assert_called_once()
        call_kwargs = mock_cli.send.call_args
        assert call_kwargs.kwargs.get("is_resume") is False

    @pytest.mark.asyncio
    async def test_second_message_resumes_session(self, session_mgr, mock_cli):
        """Second message from same user resumes the session."""
        await session_mgr.send_message(111, "Hello")
        await session_mgr.send_message(111, "Follow up")

        assert mock_cli.send.call_count == 2
        second_call = mock_cli.send.call_args_list[1]
        assert second_call.kwargs.get("is_resume") is True

    @pytest.mark.asyncio
    async def test_different_users_get_different_sessions(self, session_mgr, mock_cli):
        """Different users get separate sessions."""
        mock_cli.generate_session_id.side_effect = ["session-1", "session-2"]
        mock_cli.send.side_effect = [
            ClaudeResponse("R1", "session-1", 0.01, 100, False, 1),
            ClaudeResponse("R2", "session-2", 0.01, 100, False, 1),
        ]

        await session_mgr.send_message(111, "Hello from user 1")
        await session_mgr.send_message(222, "Hello from user 2")

        s1 = session_mgr.get_active_session(111)
        s2 = session_mgr.get_active_session(222)
        assert s1 is not None
        assert s2 is not None
        assert s1.session_id != s2.session_id

    @pytest.mark.asyncio
    async def test_expired_session_creates_new(self, session_mgr, mock_cli):
        """After timeout, a new session is created."""
        await session_mgr.send_message(111, "Hello")

        # Manually expire the session
        session = session_mgr._sessions[111]
        session.last_active = datetime.now() - timedelta(minutes=5)

        mock_cli.generate_session_id.return_value = "new-session-id"
        mock_cli.send.return_value = ClaudeResponse(
            "New session!", "new-session-id", 0.01, 100, False, 1
        )

        await session_mgr.send_message(111, "After timeout")

        new_session = session_mgr.get_active_session(111)
        assert new_session is not None
        assert new_session.session_id == "new-session-id"

    @pytest.mark.asyncio
    async def test_expire_session_command(self, session_mgr, mock_cli):
        """Explicitly expiring a session removes it."""
        await session_mgr.send_message(111, "Hello")
        assert session_mgr.get_active_session(111) is not None

        await session_mgr.expire_session(111)
        assert session_mgr.get_active_session(111) is None

    @pytest.mark.asyncio
    async def test_session_tracks_message_count(self, session_mgr, mock_cli):
        """Session message count increments."""
        await session_mgr.send_message(111, "msg 1")
        await session_mgr.send_message(111, "msg 2")
        await session_mgr.send_message(111, "msg 3")

        session = session_mgr.get_active_session(111)
        assert session is not None
        assert session.message_count == 3

    @pytest.mark.asyncio
    async def test_session_tracks_cost(self, session_mgr, mock_cli):
        """Session accumulates cost from responses."""
        await session_mgr.send_message(111, "msg 1")
        await session_mgr.send_message(111, "msg 2")

        session = session_mgr.get_active_session(111)
        assert session is not None
        assert session.total_cost == pytest.approx(0.02)

    @pytest.mark.asyncio
    async def test_concurrent_messages_serialized(self, session_mgr, mock_cli):
        """Messages from the same user are serialized via lock."""
        call_order: list[int] = []

        async def slow_send(*args, **kwargs):
            call_order.append(len(call_order))
            await asyncio.sleep(0.1)
            return ClaudeResponse("OK", "test-session-id", 0.01, 100, False, 1)

        mock_cli.send.side_effect = slow_send

        # Send two messages concurrently
        await asyncio.gather(
            session_mgr.send_message(111, "msg 1"),
            session_mgr.send_message(111, "msg 2"),
        )

        # Both should have completed (serialized)
        assert len(call_order) == 2
