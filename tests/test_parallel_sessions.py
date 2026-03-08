"""Tests for parallel multi-agent session support.

TDD RED phase: these tests define the desired behavior for parallel sessions.
"""

from __future__ import annotations

import asyncio

import pytest

from tg_assistant.services.claude_cli import ClaudeResponse
from tg_assistant.services.session_manager import (
    AgentContext,
    SessionManager,
)

AGENT_A = AgentContext(agent_id=1, agent_name="alpha", working_dir="/tmp/alpha")
AGENT_B = AgentContext(agent_id=2, agent_name="beta", working_dir="/tmp/beta")


@pytest.fixture
def session_mgr(config, db, mock_cli):
    return SessionManager(config, mock_cli, db)


class TestParallelSessions:
    """Core parallel session behavior."""

    @pytest.mark.asyncio
    async def test_same_user_different_agents_have_separate_sessions(
        self, session_mgr, mock_cli
    ):
        """Two agents for the same user create separate sessions."""
        mock_cli.generate_session_id.side_effect = ["sess-a", "sess-b"]
        mock_cli.send.side_effect = [
            ClaudeResponse("From A", "sess-a", 0.01, 100, False, 1),
            ClaudeResponse("From B", "sess-b", 0.01, 100, False, 1),
        ]

        await session_mgr.send_message(111, "Hi agent A", agent=AGENT_A)
        await session_mgr.send_message(111, "Hi agent B", agent=AGENT_B)

        sa = session_mgr.get_active_session(111, agent_id=AGENT_A.agent_id)
        sb = session_mgr.get_active_session(111, agent_id=AGENT_B.agent_id)
        assert sa is not None
        assert sb is not None
        assert sa.session_id != sb.session_id

    @pytest.mark.asyncio
    async def test_same_user_different_agents_run_in_parallel(
        self, session_mgr, mock_cli
    ):
        """Different agents for the same user can run concurrently (not serialized)."""
        call_count = 0

        async def timed_send(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            sid = f"parallel-{call_count}"
            await asyncio.sleep(0.1)
            return ClaudeResponse("OK", sid, 0.01, 100, False, 1)

        mock_cli.send.side_effect = timed_send
        mock_cli.generate_session_id.side_effect = ["s1", "s2"]

        import time
        t0 = time.monotonic()
        await asyncio.gather(
            session_mgr.send_message(111, "msg", agent=AGENT_A),
            session_mgr.send_message(111, "msg", agent=AGENT_B),
        )
        elapsed = time.monotonic() - t0

        # Should complete in ~0.1s (parallel), not ~0.2s (serial)
        assert elapsed < 0.18, f"Expected parallel execution, took {elapsed:.2f}s"

    @pytest.mark.asyncio
    async def test_same_user_same_agent_serialized(self, session_mgr, mock_cli):
        """Messages to the same agent are still serialized."""
        call_order: list[int] = []

        async def ordered_send(*args, **kwargs):
            call_order.append(len(call_order))
            await asyncio.sleep(0.05)
            return ClaudeResponse("OK", "test-id", 0.01, 100, False, 1)

        mock_cli.send.side_effect = ordered_send

        await asyncio.gather(
            session_mgr.send_message(111, "msg1", agent=AGENT_A),
            session_mgr.send_message(111, "msg2", agent=AGENT_A),
        )

        assert len(call_order) == 2

    @pytest.mark.asyncio
    async def test_resume_session_with_agent(self, session_mgr, mock_cli):
        """Second message to the same agent resumes the existing session."""
        await session_mgr.send_message(111, "Hello", agent=AGENT_A)
        await session_mgr.send_message(111, "Follow up", agent=AGENT_A)

        assert mock_cli.send.call_count == 2
        second_call = mock_cli.send.call_args_list[1]
        assert second_call.kwargs.get("is_resume") is True

    @pytest.mark.asyncio
    async def test_working_dir_passed_to_cli(self, session_mgr, mock_cli):
        """Agent's working_dir is passed to CLI send()."""
        await session_mgr.send_message(111, "Hi", agent=AGENT_A)

        call_kwargs = mock_cli.send.call_args.kwargs
        assert call_kwargs.get("working_dir") == "/tmp/alpha"


class TestExpireSessions:
    """Session expiry with per-agent granularity."""

    @pytest.mark.asyncio
    async def test_expire_session_one_agent(self, session_mgr, mock_cli):
        """Expiring one agent's session does not affect another."""
        mock_cli.generate_session_id.side_effect = ["sa", "sb"]
        mock_cli.send.side_effect = [
            ClaudeResponse("A", "sa", 0.01, 100, False, 1),
            ClaudeResponse("B", "sb", 0.01, 100, False, 1),
        ]

        await session_mgr.send_message(111, "Hi A", agent=AGENT_A)
        await session_mgr.send_message(111, "Hi B", agent=AGENT_B)

        await session_mgr.expire_session(111, agent_id=AGENT_A.agent_id)

        assert session_mgr.get_active_session(111, agent_id=AGENT_A.agent_id) is None
        assert session_mgr.get_active_session(111, agent_id=AGENT_B.agent_id) is not None

    @pytest.mark.asyncio
    async def test_expire_all_sessions(self, session_mgr, mock_cli):
        """expire_all_sessions removes all sessions for the user."""
        mock_cli.generate_session_id.side_effect = ["sa", "sb"]
        mock_cli.send.side_effect = [
            ClaudeResponse("A", "sa", 0.01, 100, False, 1),
            ClaudeResponse("B", "sb", 0.01, 100, False, 1),
        ]

        await session_mgr.send_message(111, "Hi A", agent=AGENT_A)
        await session_mgr.send_message(111, "Hi B", agent=AGENT_B)

        await session_mgr.expire_all_sessions(111)

        assert session_mgr.get_active_session(111, agent_id=AGENT_A.agent_id) is None
        assert session_mgr.get_active_session(111, agent_id=AGENT_B.agent_id) is None

    @pytest.mark.asyncio
    async def test_get_all_user_sessions(self, session_mgr, mock_cli):
        """get_all_user_sessions returns all active sessions for a user."""
        mock_cli.generate_session_id.side_effect = ["sa", "sb"]
        mock_cli.send.side_effect = [
            ClaudeResponse("A", "sa", 0.01, 100, False, 1),
            ClaudeResponse("B", "sb", 0.01, 100, False, 1),
        ]

        await session_mgr.send_message(111, "Hi A", agent=AGENT_A)
        await session_mgr.send_message(111, "Hi B", agent=AGENT_B)

        sessions = session_mgr.get_all_user_sessions(111)
        assert len(sessions) == 2
        names = {s.agent_name for s in sessions}
        assert names == {"alpha", "beta"}

    @pytest.mark.asyncio
    async def test_get_active_session_count(self, session_mgr, mock_cli):
        """get_active_session_count returns correct count."""
        assert session_mgr.get_active_session_count(111) == 0

        await session_mgr.send_message(111, "Hi A", agent=AGENT_A)
        assert session_mgr.get_active_session_count(111) == 1

        mock_cli.generate_session_id.return_value = "sb"
        mock_cli.send.return_value = ClaudeResponse("B", "sb", 0.01, 100, False, 1)

        await session_mgr.send_message(111, "Hi B", agent=AGENT_B)
        assert session_mgr.get_active_session_count(111) == 2


class TestPerAgentQueues:
    """Queue isolation per (user, agent)."""

    def test_queue_per_agent(self, session_mgr):
        """Queues are separate per agent."""
        key_a = (111, AGENT_A.agent_id)
        key_b = (111, AGENT_B.agent_id)

        session_mgr.queue_message(key_a, "msg for A")
        session_mgr.queue_message(key_b, "msg for B")

        assert session_mgr.get_queue_size(key_a) == 1
        assert session_mgr.get_queue_size(key_b) == 1

    def test_is_busy_per_agent(self, session_mgr):
        """Busy state is tracked per (user, agent)."""
        key_a = (111, AGENT_A.agent_id)
        key_b = (111, AGENT_B.agent_id)

        assert session_mgr.is_busy(key_a) is False
        assert session_mgr.is_busy(key_b) is False

    def test_mark_for_expiry_per_agent(self, session_mgr):
        """mark_for_expiry only clears the target agent's queue."""
        key_a = (111, AGENT_A.agent_id)
        key_b = (111, AGENT_B.agent_id)

        session_mgr.queue_message(key_a, "msg A")
        session_mgr.queue_message(key_b, "msg B")

        session_mgr.mark_for_expiry(key_a)

        assert session_mgr.get_queue_size(key_a) == 0
        assert session_mgr.get_queue_size(key_b) == 1

    @pytest.mark.asyncio
    async def test_send_message_with_queue_per_agent(self, session_mgr, mock_cli):
        """send_message_with_queue drains only the target agent's queue."""
        call_count = 0

        async def counting_send(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                key_a = (111, AGENT_A.agent_id)
                session_mgr.queue_message(key_a, "queued for A")
            return ClaudeResponse(f"R{call_count}", "test-id", 0.01, 100, False, 1)

        mock_cli.send.side_effect = counting_send

        results: list[tuple[str, bool]] = []

        async def on_response(resp: ClaudeResponse, from_queue: bool) -> None:
            results.append((resp.text, from_queue))

        await session_mgr.send_message_with_queue(
            111, "main msg", agent=AGENT_A, on_response=on_response
        )

        assert len(results) == 2
        assert results[0] == ("R1", False)
        assert results[1] == ("R2", True)


class TestBackwardCompatibility:
    """Ensure default agent works without explicit agent parameter."""

    @pytest.mark.asyncio
    async def test_send_message_with_default_agent(self, session_mgr, mock_cli):
        """send_message without agent uses DEFAULT_AGENT."""
        from tg_assistant.services.session_manager import DEFAULT_AGENT

        response = await session_mgr.send_message(111, "Hello")
        assert response.text == "Hello from Claude!"

        session = session_mgr.get_active_session(111, agent_id=DEFAULT_AGENT.agent_id)
        assert session is not None
