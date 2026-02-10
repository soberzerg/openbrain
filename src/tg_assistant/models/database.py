"""SQLite database for sessions and message logging."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id   INTEGER PRIMARY KEY,
    username      TEXT,
    first_name    TEXT,
    last_name     TEXT,
    role          TEXT NOT NULL DEFAULT 'user',
    first_seen    TEXT NOT NULL DEFAULT (datetime('now')),
    last_active   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL UNIQUE,
    telegram_id   INTEGER NOT NULL REFERENCES users(telegram_id),
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    last_active   TEXT NOT NULL DEFAULT (datetime('now')),
    status        TEXT NOT NULL DEFAULT 'active',
    message_count INTEGER NOT NULL DEFAULT 0,
    total_cost    REAL NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_sessions_telegram_id ON sessions(telegram_id);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);

CREATE TABLE IF NOT EXISTS messages (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_message_id INTEGER,
    session_id          TEXT REFERENCES sessions(session_id),
    telegram_id         INTEGER NOT NULL REFERENCES users(telegram_id),
    direction           TEXT NOT NULL,
    content             TEXT,
    msg_type            TEXT NOT NULL DEFAULT 'text',
    file_path           TEXT,
    timestamp           TEXT NOT NULL DEFAULT (datetime('now')),
    cost_usd            REAL DEFAULT 0.0,
    duration_ms         INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_telegram_id ON messages(telegram_id);
"""


class Database:
    """Async SQLite database wrapper."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Open connection and create tables."""
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row

        # Enable WAL mode for better concurrent access
        await self._db.execute("PRAGMA journal_mode=WAL")

        await self._db.executescript(SCHEMA_SQL)
        await self._db.commit()
        logger.info("Database initialized at %s", self.db_path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    async def upsert_user(
        self,
        telegram_id: int,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> None:
        """Insert or update a user record."""
        assert self._db
        await self._db.execute(
            """
            INSERT INTO users (telegram_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                last_active = datetime('now')
            """,
            (telegram_id, username, first_name, last_name),
        )
        await self._db.commit()

    async def create_session(
        self, session_id: str, telegram_id: int
    ) -> None:
        """Record a new Claude Code session."""
        assert self._db
        await self._db.execute(
            "INSERT INTO sessions (session_id, telegram_id) VALUES (?, ?)",
            (session_id, telegram_id),
        )
        await self._db.commit()

    async def update_session(
        self,
        session_id: str,
        *,
        status: str | None = None,
        message_count: int | None = None,
        total_cost: float | None = None,
    ) -> None:
        """Update session metadata."""
        assert self._db

        # Build update clause with whitelisted columns only
        updates: list[str] = ["last_active = datetime('now')"]
        params: list[Any] = []

        # Whitelist of allowed column names
        allowed_updates = {
            "status": status,
            "message_count": message_count,
            "total_cost": total_cost,
        }

        for column_name, value in allowed_updates.items():
            if value is not None:
                updates.append(f"{column_name} = ?")
                params.append(value)

        params.append(session_id)
        query = f"UPDATE sessions SET {', '.join(updates)} WHERE session_id = ?"
        await self._db.execute(query, params)
        await self._db.commit()

    async def log_message(
        self,
        telegram_id: int,
        direction: str,
        content: str,
        msg_type: str = "text",
        telegram_message_id: int | None = None,
        session_id: str | None = None,
        file_path: str | None = None,
        cost_usd: float = 0.0,
        duration_ms: int = 0,
    ) -> None:
        """Log an incoming or outgoing message."""
        assert self._db
        await self._db.execute(
            """
            INSERT INTO messages
                (telegram_message_id, session_id, telegram_id, direction,
                 content, msg_type, file_path, cost_usd, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                telegram_message_id,
                session_id,
                telegram_id,
                direction,
                content,
                msg_type,
                file_path,
                cost_usd,
                duration_ms,
            ),
        )
        await self._db.commit()

    async def get_user_stats(self, telegram_id: int) -> dict[str, Any]:
        """Get aggregate stats for a user."""
        assert self._db
        cursor = await self._db.execute(
            """
            SELECT
                COUNT(DISTINCT s.session_id) as total_sessions,
                COALESCE(SUM(s.total_cost), 0) as total_cost,
                COUNT(m.id) as total_messages
            FROM users u
            LEFT JOIN sessions s ON s.telegram_id = u.telegram_id
            LEFT JOIN messages m ON m.telegram_id = u.telegram_id AND m.direction = 'in'
            WHERE u.telegram_id = ?
            """,
            (telegram_id,),
        )
        row = await cursor.fetchone()
        if row:
            return {
                "total_sessions": row["total_sessions"],
                "total_cost": row["total_cost"],
                "total_messages": row["total_messages"],
            }
        return {"total_sessions": 0, "total_cost": 0.0, "total_messages": 0}
