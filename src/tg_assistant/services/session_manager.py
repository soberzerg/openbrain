"""Session lifecycle management: create, resume, expire Claude Code sessions."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from tg_assistant.config import Config
from tg_assistant.models.database import Database
from tg_assistant.services.claude_cli import ClaudeCli, ClaudeResponse
from tg_assistant.services.git_sync import GitSync

logger = logging.getLogger(__name__)


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


class SessionManager:
    """
    Maps telegram_user_id -> claude_session_id.

    Handles creation, resumption, and expiration of sessions.
    Uses per-user asyncio.Lock to prevent concurrent CLI calls for the same user.
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

    async def send_message(self, user_id: int, text: str) -> ClaudeResponse:
        """
        Send a message to Claude, managing session lifecycle.

        Acquires a per-user lock so concurrent messages from the same user
        are serialized. Different users can execute in parallel.
        """
        lock = self._get_lock(user_id)

        async with lock:
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

            return response

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
