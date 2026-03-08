"""Session lifecycle management: create, resume, expire Claude Code sessions."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from tg_assistant.config import Config
from tg_assistant.models.database import Database
from tg_assistant.services.claude_cli import ClaudeCli, ClaudeResponse

logger = logging.getLogger(__name__)

MAX_QUEUE_SIZE = 10


@dataclass(frozen=True)
class AgentContext:
    """Immutable agent info passed to SessionManager by callers."""

    agent_id: int
    agent_name: str
    working_dir: str | None = None


# Composite key for per-(user, agent) state
SessionKey = tuple[int, int]  # (user_id, agent_id)

DEFAULT_AGENT = AgentContext(agent_id=0, agent_name="main")


@dataclass
class Session:
    """In-memory representation of an active Claude Code session."""

    session_id: str
    user_id: int
    created_at: datetime
    last_active: datetime
    message_count: int = 0
    total_cost: float = 0.0
    status: str = "active"
    agent_id: int | None = None
    agent_name: str = "main"


@dataclass
class QueuedMessage:
    """A message waiting to be sent to Claude."""

    text: str
    queued_at: datetime = field(default_factory=datetime.now)


class QueueFullError(Exception):
    """Raised when the per-user message queue is full."""


class SessionManager:
    """
    Maps (user_id, agent_id) -> claude session.

    Handles creation, resumption, and expiration of sessions.
    Uses per-(user, agent) asyncio.Lock so different agents run in parallel.
    Messages arriving while Claude is busy are queued per (user, agent).
    """

    def __init__(
        self,
        config: Config,
        cli: ClaudeCli,
        db: Database,
    ) -> None:
        self.config = config
        self.cli = cli
        self.db = db
        self.timeout = timedelta(minutes=config.session_timeout_minutes)
        self._sessions: dict[SessionKey, Session] = {}
        self._locks: dict[SessionKey, asyncio.Lock] = {}
        self._queues: dict[SessionKey, list[QueuedMessage]] = {}
        self._pending_expiry: set[SessionKey] = set()
        self._draining: set[SessionKey] = set()

    def _get_lock(self, key: SessionKey) -> asyncio.Lock:
        """Get or create a per-(user, agent) lock."""
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    def _is_expired(self, session: Session) -> bool:
        """Check if a session has timed out."""
        return datetime.now() - session.last_active > self.timeout

    def get_active_session(
        self,
        user_id: int,
        agent_id: int | None = None,
    ) -> Session | None:
        """Return the active session for a (user, agent), or None if expired/missing.

        If agent_id is None, searches for any session for the user (backward compat).
        """
        if agent_id is not None:
            key = (user_id, agent_id)
            session = self._sessions.get(key)
            if session and not self._is_expired(session):
                return session
            return None

        # Backward compat: find any session for this user
        for key, session in self._sessions.items():
            if key[0] == user_id and not self._is_expired(session):
                return session
        return None

    def get_all_user_sessions(self, user_id: int) -> list[Session]:
        """Return all active (non-expired) sessions for a user."""
        return [
            session
            for key, session in self._sessions.items()
            if key[0] == user_id and not self._is_expired(session)
        ]

    def get_active_session_count(self, user_id: int) -> int:
        """Return number of active sessions for a user."""
        return len(self.get_all_user_sessions(user_id))

    # -- Queue management --------------------------------------------------

    def is_busy(self, key: SessionKey | int) -> bool:
        """Check if a CLI call or queue drain is in progress.

        Accepts SessionKey (user_id, agent_id) or plain user_id for backward compat.
        When user_id is passed, returns True if ANY agent for that user is busy.
        """
        if isinstance(key, int):
            user_id = key
            return any(
                k in self._draining
                or (self._locks.get(k) is not None and self._locks[k].locked())
                for k in self._locks
                if k[0] == user_id
            )
        if key in self._draining:
            return True
        lock = self._locks.get(key)
        return lock is not None and lock.locked()

    def queue_message(self, key: SessionKey | int, text: str) -> int:
        """Add a message to the queue. Returns queue position (1-based).

        Raises QueueFullError if the queue already has MAX_QUEUE_SIZE messages.
        """
        if isinstance(key, int):
            key = (key, DEFAULT_AGENT.agent_id)
        queue = self._queues.setdefault(key, [])
        if len(queue) >= MAX_QUEUE_SIZE:
            raise QueueFullError(
                f"Queue full ({MAX_QUEUE_SIZE} messages). Please wait."
            )
        queue.append(QueuedMessage(text=text))
        return len(queue)

    def drain_queue(self, key: SessionKey | int) -> str | None:
        """Pop all queued messages and combine into a single prompt."""
        if isinstance(key, int):
            key = (key, DEFAULT_AGENT.agent_id)
        queue = self._queues.pop(key, [])
        if not queue:
            return None
        if len(queue) == 1:
            return queue[0].text
        parts = [
            f"[Message {i}]: {msg.text}" for i, msg in enumerate(queue, 1)
        ]
        return "\n\n".join(parts)

    def get_queue_size(self, key: SessionKey | int) -> int:
        """Return the number of queued messages."""
        if isinstance(key, int):
            key = (key, DEFAULT_AGENT.agent_id)
        return len(self._queues.get(key, []))

    def mark_for_expiry(self, key: SessionKey | int) -> None:
        """Mark session for expiration after the current CLI call."""
        if isinstance(key, int):
            key = (key, DEFAULT_AGENT.agent_id)
        self._pending_expiry.add(key)
        self._queues.pop(key, None)

    async def _apply_pending_expiry(self, key: SessionKey) -> None:
        """Expire session if it has a pending expiry request."""
        if key not in self._pending_expiry:
            return

        self._pending_expiry.discard(key)
        old_session = self._sessions.pop(key, None)
        if old_session:
            await self._do_expire(old_session)
            logger.info(
                "Session %s expired (pending) for user %d agent %s",
                old_session.session_id[:8],
                key[0],
                old_session.agent_name,
            )

    # -- Send with session lifecycle ----------------------------------------

    async def _do_send(
        self, user_id: int, text: str, agent: AgentContext
    ) -> ClaudeResponse:
        """Send to Claude CLI with session management. Caller must hold the lock."""
        key: SessionKey = (user_id, agent.agent_id)
        session = self.get_active_session(user_id, agent_id=agent.agent_id)

        if session:
            # Resume existing session
            response = await self.cli.send(
                prompt=text,
                session_id=session.session_id,
                is_resume=True,
                working_dir=agent.working_dir,
            )
            session.last_active = datetime.now()
            session.message_count += 1
            session.total_cost += response.cost_usd

            await self.db.update_session(
                session.session_id,
                message_count=session.message_count,
                total_cost=session.total_cost,
            )
        else:
            # Expire old session for this (user, agent) if exists
            old = self._sessions.pop(key, None)
            if old:
                await self._do_expire(old)

            # Create new session
            new_id = self.cli.generate_session_id()
            response = await self.cli.send(
                prompt=text,
                session_id=new_id,
                is_resume=False,
                working_dir=agent.working_dir,
            )

            session = Session(
                session_id=response.session_id or new_id,
                user_id=user_id,
                created_at=datetime.now(),
                last_active=datetime.now(),
                message_count=1,
                total_cost=response.cost_usd,
                agent_id=agent.agent_id,
                agent_name=agent.agent_name,
            )
            self._sessions[key] = session

            await self.db.create_session(
                session.session_id, user_id, agent_id=agent.agent_id
            )
            await self.db.update_session(
                session.session_id,
                message_count=session.message_count,
                total_cost=session.total_cost,
            )
            logger.info(
                "New session %s for user %d (agent=%s)",
                session.session_id[:8],
                user_id,
                agent.agent_name,
            )

        # Handle pending expiry (e.g. user sent /new while CLI was running)
        await self._apply_pending_expiry(key)

        return response

    async def send_message(
        self,
        user_id: int,
        text: str,
        agent: AgentContext | None = None,
    ) -> ClaudeResponse:
        """
        Send a message to Claude, managing session lifecycle.

        Acquires a per-(user, agent) lock so concurrent messages to the
        same agent are serialized. Different agents run in parallel.
        """
        agent = agent or DEFAULT_AGENT
        key: SessionKey = (user_id, agent.agent_id)
        lock = self._get_lock(key)

        async with lock:
            return await self._do_send(user_id, text, agent)

    async def send_message_with_queue(
        self,
        user_id: int,
        text: str,
        *,
        agent: AgentContext | None = None,
        on_response: Callable[[ClaudeResponse, bool], Awaitable[None]],
    ) -> None:
        """
        Send a message and then drain any queued messages.

        The lock is held only while calling Claude CLI.
        ``on_response(response, from_queue)`` is called outside the lock
        to avoid blocking the queue during Telegram sends / DB writes.

        A ``_draining`` flag keeps ``is_busy()`` returning True between
        lock releases so that /new correctly uses ``mark_for_expiry``.
        """
        agent = agent or DEFAULT_AGENT
        key: SessionKey = (user_id, agent.agent_id)
        lock = self._get_lock(key)
        self._draining.add(key)

        try:
            # Main message
            async with lock:
                response = await self._do_send(user_id, text, agent)
            await on_response(response, False)

            # Process messages that arrived while we were busy
            while True:
                async with lock:
                    combined = self.drain_queue(key)
                    if combined is None:
                        break
                    response = await self._do_send(user_id, combined, agent)
                await on_response(response, True)
        finally:
            # /new may arrive while we are between lock-protected sends.
            # Apply pending expiry once more before leaving draining mode.
            async with lock:
                await self._apply_pending_expiry(key)
            self._draining.discard(key)

    async def expire_session(
        self, user_id: int, agent_id: int | None = None
    ) -> None:
        """Force-expire a session.

        If agent_id is provided, expire only that agent's session.
        If agent_id is None, expire any single session for the user (backward compat).
        """
        if agent_id is not None:
            key = (user_id, agent_id)
            session = self._sessions.pop(key, None)
        else:
            # Backward compat: find any session for this user
            session = None
            for k in list(self._sessions):
                if k[0] == user_id:
                    session = self._sessions.pop(k)
                    break

        if session:
            await self._do_expire(session)
            logger.info(
                "Session %s expired for user %d (agent=%s)",
                session.session_id[:8],
                user_id,
                session.agent_name,
            )

    async def expire_all_sessions(self, user_id: int) -> int:
        """Expire all sessions for a user. Returns count of affected sessions.

        Busy sessions are marked for deferred expiry via mark_for_expiry.
        """
        session_keys = {k for k in self._sessions if k[0] == user_id}
        busy_keys = {
            k for k in self._locks if k[0] == user_id and self.is_busy(k)
        }
        all_keys = session_keys | busy_keys
        count = 0
        for key in all_keys:
            if self.is_busy(key):
                self.mark_for_expiry(key)
                count += 1
            else:
                session = self._sessions.pop(key, None)
                if session:
                    await self._do_expire(session)
                    count += 1
        if count:
            logger.info("Expired/marked %d sessions for user %d", count, user_id)
        return count

    async def _do_expire(self, session: Session) -> None:
        """Internal: mark session as expired in the database."""
        session.status = "expired"
        await self.db.update_session(session.session_id, status="expired")
