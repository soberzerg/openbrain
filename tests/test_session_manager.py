"""Tests for session manager."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from tg_assistant.services.claude_cli import ClaudeResponse
from tg_assistant.services.session_manager import (
    DEFAULT_AGENT,
    MAX_QUEUE_SIZE,
    QueueFullError,
    SessionManager,
)


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
        session = session_mgr._sessions[(111, DEFAULT_AGENT.agent_id)]
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


class TestMessageQueue:
    """Tests for the per-user message queue."""

    def test_is_busy_false_initially(self, session_mgr):
        """is_busy returns False when no lock is held."""
        assert session_mgr.is_busy(111) is False

    @pytest.mark.asyncio
    async def test_is_busy_true_when_lock_held(self, session_mgr, mock_cli):
        """is_busy returns True while CLI call is in progress."""
        busy_states: list[bool] = []

        async def slow_send(*args, **kwargs):
            busy_states.append(session_mgr.is_busy(111))
            await asyncio.sleep(0.05)
            return ClaudeResponse("OK", "test-session-id", 0.01, 100, False, 1)

        mock_cli.send.side_effect = slow_send
        await session_mgr.send_message(111, "Hello")

        assert busy_states == [True]
        assert session_mgr.is_busy(111) is False

    def test_queue_message_returns_position(self, session_mgr):
        """queue_message returns 1-based position."""
        assert session_mgr.queue_message(111, "msg 1") == 1
        assert session_mgr.queue_message(111, "msg 2") == 2
        assert session_mgr.queue_message(111, "msg 3") == 3

    def test_queue_message_separate_users(self, session_mgr):
        """Queues are per-user."""
        assert session_mgr.queue_message(111, "msg") == 1
        assert session_mgr.queue_message(222, "msg") == 1

    def test_queue_full_raises(self, session_mgr):
        """QueueFullError is raised when queue reaches max size."""
        for i in range(MAX_QUEUE_SIZE):
            session_mgr.queue_message(111, f"msg {i}")
        with pytest.raises(QueueFullError):
            session_mgr.queue_message(111, "one too many")

    def test_drain_queue_empty(self, session_mgr):
        """drain_queue returns None for empty queue."""
        assert session_mgr.drain_queue(111) is None

    def test_drain_queue_single(self, session_mgr):
        """drain_queue returns text directly for a single message."""
        session_mgr.queue_message(111, "only message")
        assert session_mgr.drain_queue(111) == "only message"
        assert session_mgr.drain_queue(111) is None  # drained

    def test_drain_queue_multiple(self, session_mgr):
        """drain_queue combines messages with [Message N] separators."""
        session_mgr.queue_message(111, "first")
        session_mgr.queue_message(111, "second")
        result = session_mgr.drain_queue(111)
        assert result == "[Message 1]: first\n\n[Message 2]: second"

    def test_get_queue_size(self, session_mgr):
        """get_queue_size returns current queue length."""
        assert session_mgr.get_queue_size(111) == 0
        session_mgr.queue_message(111, "msg")
        assert session_mgr.get_queue_size(111) == 1

    def test_mark_for_expiry_clears_queue(self, session_mgr):
        """mark_for_expiry discards queued messages."""
        session_mgr.queue_message(111, "msg 1")
        session_mgr.queue_message(111, "msg 2")
        session_mgr.mark_for_expiry(111)
        assert session_mgr.get_queue_size(111) == 0

    @pytest.mark.asyncio
    async def test_pending_expiry_expires_session(self, session_mgr, mock_cli):
        """Session is expired after CLI returns if marked for expiry."""
        await session_mgr.send_message(111, "Hello")
        assert session_mgr.get_active_session(111) is not None

        session_mgr.mark_for_expiry(111)
        await session_mgr.send_message(111, "Another")

        assert session_mgr.get_active_session(111) is None

    @pytest.mark.asyncio
    async def test_send_message_with_queue_drains(self, session_mgr, mock_cli):
        """send_message_with_queue processes queued messages after main."""
        call_count = 0

        async def counting_send(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Simulate messages arriving while CLI is busy
                session_mgr.queue_message(111, "queued 1")
                session_mgr.queue_message(111, "queued 2")
            return ClaudeResponse(f"Response {call_count}", "test-session-id", 0.01, 100, False, 1)

        mock_cli.send.side_effect = counting_send

        results: list[tuple[str, bool]] = []

        async def on_response(resp: ClaudeResponse, from_queue: bool) -> None:
            results.append((resp.text, from_queue))

        await session_mgr.send_message_with_queue(111, "main", on_response=on_response)

        assert len(results) == 2
        assert results[0] == ("Response 1", False)
        assert results[1] == ("Response 2", True)

    @pytest.mark.asyncio
    async def test_send_message_with_queue_no_queued(self, session_mgr, mock_cli):
        """send_message_with_queue works fine with no queued messages."""
        results: list[tuple[str, bool]] = []

        async def on_response(resp: ClaudeResponse, from_queue: bool) -> None:
            results.append((resp.text, from_queue))

        await session_mgr.send_message_with_queue(111, "only one", on_response=on_response)

        assert len(results) == 1
        assert results[0] == ("Hello from Claude!", False)

    @pytest.mark.asyncio
    async def test_mark_for_expiry_during_drain_is_applied(self, session_mgr, mock_cli):
        """Pending expiry set during drain callback is applied before return."""
        await session_mgr.send_message(111, "bootstrap")
        assert session_mgr.get_active_session(111) is not None

        async def on_response(resp: ClaudeResponse, from_queue: bool) -> None:
            if not from_queue:
                # Simulate /new arriving while send_message_with_queue is draining.
                session_mgr.mark_for_expiry(111)

        await session_mgr.send_message_with_queue(111, "main", on_response=on_response)

        assert session_mgr.get_active_session(111) is None
        assert (111, DEFAULT_AGENT.agent_id) not in session_mgr._pending_expiry
