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
from tg_assistant.services.git_sync import GitSync

logger = logging.getLogger(__name__)

MAX_QUEUE_SIZE = 10


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


@dataclass
class QueuedMessage:
    """A message waiting to be sent to Claude."""

    text: str
    queued_at: datetime = field(default_factory=datetime.now)


class QueueFullError(Exception):
    """Raised when the per-user message queue is full."""


class SessionManager:
    """
    Maps telegram_user_id -> claude_session_id.

    Handles creation, resumption, and expiration of sessions.
    Uses per-user asyncio.Lock to prevent concurrent CLI calls for the same user.
    Messages arriving while Claude is busy are queued and processed after.
    """

    def __init__(
        self,
        config: Config,
        cli: ClaudeCli,
        db: Database,
        git_sync: GitSync | None = None,
    ) -> None:
        self.config = config
        self.cli = cli
        self.db = db
        self.git_sync = git_sync
        self.timeout = timedelta(minutes=config.session_timeout_minutes)
        self._sessions: dict[int, Session] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._queues: dict[int, list[QueuedMessage]] = {}
        self._pending_expiry: set[int] = set()
        self._draining: set[int] = set()

    def _get_lock(self, user_id: int) -> asyncio.Lock:
        """Get or create a per-user lock."""
        if user_id not in self._locks:
            self._locks[user_id] = asyncio.Lock()
        return self._locks[user_id]

    def _is_expired(self, session: Session) -> bool:
        """Check if a session has timed out."""
        return datetime.now() - session.last_active > self.timeout

    def get_active_session(self, user_id: int) -> Session | None:
        """Return the active session for a user, or None if expired/missing."""
        session = self._sessions.get(user_id)
        if session and not self._is_expired(session):
            return session
        return None

    # -- Queue management --------------------------------------------------

    def is_busy(self, user_id: int) -> bool:
        """Check if a user's CLI call or queue drain is in progress."""
        if user_id in self._draining:
            return True
        lock = self._locks.get(user_id)
        return lock is not None and lock.locked()

    def queue_message(self, user_id: int, text: str) -> int:
        """Add a message to the user's queue. Returns queue position (1-based).

        Raises QueueFullError if the queue already has MAX_QUEUE_SIZE messages.
        """
        queue = self._queues.setdefault(user_id, [])
        if len(queue) >= MAX_QUEUE_SIZE:
            raise QueueFullError(
                f"Queue full ({MAX_QUEUE_SIZE} messages). Please wait."
            )
        queue.append(QueuedMessage(text=text))
        return len(queue)

    def drain_queue(self, user_id: int) -> str | None:
        """Pop all queued messages and combine into a single prompt.

        Returns None if the queue is empty.
        """
        queue = self._queues.pop(user_id, [])
        if not queue:
            return None
        if len(queue) == 1:
            return queue[0].text
        parts = [f"[Message {i}]: {msg.text}" for i, msg in enumerate(queue, 1)]
        return "\n\n".join(parts)

    def get_queue_size(self, user_id: int) -> int:
        """Return the number of queued messages for a user."""
        return len(self._queues.get(user_id, []))

    def mark_for_expiry(self, user_id: int) -> None:
        """Mark user's session for expiration after the current CLI call."""
        self._pending_expiry.add(user_id)
        # Also discard any queued messages
        self._queues.pop(user_id, None)

    # -- Send with session lifecycle ----------------------------------------

    async def _do_send(self, user_id: int, text: str) -> ClaudeResponse:
        """Send to Claude CLI with session management. Caller must hold the lock."""
        session = self.get_active_session(user_id)

        if session:
            # Resume existing session
            response = await self.cli.send(
                prompt=text,
                session_id=session.session_id,
                is_resume=True,
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
            # Expire old session if exists
            old = self._sessions.pop(user_id, None)
            if old:
                await self._do_expire(old)

            # Git pull before new session
            if self.git_sync:
                await self.git_sync.pull()

            # Create new session
            new_id = self.cli.generate_session_id()
            response = await self.cli.send(
                prompt=text,
                session_id=new_id,
                is_resume=False,
            )

            session = Session(
                session_id=response.session_id or new_id,
                user_id=user_id,
                created_at=datetime.now(),
                last_active=datetime.now(),
                message_count=1,
                total_cost=response.cost_usd,
            )
            self._sessions[user_id] = session

            await self.db.create_session(session.session_id, user_id)
            await self.db.update_session(
                session.session_id,
                message_count=session.message_count,
                total_cost=session.total_cost,
            )
            logger.info(
                "New session %s for user %d", session.session_id[:8], user_id
            )

        # Handle pending expiry (e.g. user sent /new while CLI was running)
        if user_id in self._pending_expiry:
            self._pending_expiry.discard(user_id)
            old_session = self._sessions.pop(user_id, None)
            if old_session:
                await self._do_expire(old_session)
                logger.info(
                    "Session %s expired (pending) for user %d",
                    old_session.session_id[:8],
                    user_id,
                )

        return response

    async def send_message(self, user_id: int, text: str) -> ClaudeResponse:
        """
        Send a message to Claude, managing session lifecycle.

        Acquires a per-user lock so concurrent messages from the same user
        are serialized. Different users can execute in parallel.
        """
        lock = self._get_lock(user_id)

        async with lock:
            return await self._do_send(user_id, text)

    async def send_message_with_queue(
        self,
        user_id: int,
        text: str,
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
        lock = self._get_lock(user_id)
        self._draining.add(user_id)

        try:
            # Main message
            async with lock:
                response = await self._do_send(user_id, text)
            await on_response(response, False)

            # Process messages that arrived while we were busy
            while True:
                async with lock:
                    combined = self.drain_queue(user_id)
                    if combined is None:
                        break
                    response = await self._do_send(user_id, combined)
                await on_response(response, True)
        finally:
            self._draining.discard(user_id)

    async def expire_session(self, user_id: int) -> None:
        """Force-expire a user's current session."""
        session = self._sessions.pop(user_id, None)
        if session:
            await self._do_expire(session)
            logger.info("Session %s expired for user %d", session.session_id[:8], user_id)

    async def _do_expire(self, session: Session) -> None:
        """Internal: mark session as expired and trigger git push."""
        session.status = "expired"
        await self.db.update_session(session.session_id, status="expired")

        if self.git_sync:
            await self.git_sync.push_if_changed()
