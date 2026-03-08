"""Tests for Database agent-related operations and migrations."""

from __future__ import annotations

from pathlib import Path

import pytest

from tg_assistant.models.database import Database


@pytest.fixture
async def fresh_db(tmp_path: Path) -> Database:
    """Fresh database with all tables."""
    db = Database(tmp_path / "test.db")
    await db.initialize()
    yield db
    await db.close()


class TestAgentsCrud:
    async def test_create_and_get_agent(self, fresh_db: Database) -> None:
        agent_id = await fresh_db.create_agent("test", "/tmp/test", "A test agent")
        agent = await fresh_db.get_agent_by_id(agent_id)
        assert agent is not None
        assert agent["name"] == "test"
        assert agent["working_dir"] == "/tmp/test"
        assert agent["description"] == "A test agent"
        assert agent["is_default"] == 0

    async def test_get_agent_by_name(self, fresh_db: Database) -> None:
        await fresh_db.create_agent("named", "/tmp/named")
        agent = await fresh_db.get_agent_by_name("named")
        assert agent is not None
        assert agent["name"] == "named"

    async def test_get_agent_by_name_not_found(self, fresh_db: Database) -> None:
        result = await fresh_db.get_agent_by_name("ghost")
        assert result is None

    async def test_get_all_agents_ordered(self, fresh_db: Database) -> None:
        await fresh_db.create_agent("main", "/tmp/main", is_default=True)
        await fresh_db.create_agent("beta", "/tmp/beta")
        await fresh_db.create_agent("alpha", "/tmp/alpha")

        agents = await fresh_db.get_all_agents()
        names = [a["name"] for a in agents]
        # Default first, then alphabetical
        assert names == ["main", "alpha", "beta"]

    async def test_delete_agent(self, fresh_db: Database) -> None:
        agent_id = await fresh_db.create_agent("temp", "/tmp/temp")
        result = await fresh_db.delete_agent(agent_id)
        assert result is True
        assert await fresh_db.get_agent_by_id(agent_id) is None

    async def test_delete_default_agent_fails(self, fresh_db: Database) -> None:
        agent_id = await fresh_db.create_agent("main", "/tmp/main", is_default=True)
        result = await fresh_db.delete_agent(agent_id)
        assert result is False
        assert await fresh_db.get_agent_by_id(agent_id) is not None

    async def test_ensure_default_agent_creates(self, fresh_db: Database) -> None:
        agent_id = await fresh_db.ensure_default_agent("main", "/tmp/main")
        agent = await fresh_db.get_agent_by_id(agent_id)
        assert agent is not None
        assert agent["is_default"] == 1

    async def test_ensure_default_agent_idempotent(self, fresh_db: Database) -> None:
        id1 = await fresh_db.ensure_default_agent("main", "/tmp/main")
        id2 = await fresh_db.ensure_default_agent("main", "/tmp/main")
        assert id1 == id2


class TestUserAgents:
    async def test_set_and_get_user_active_agent(self, fresh_db: Database) -> None:
        agent_id = await fresh_db.create_agent("test", "/tmp/test")
        await fresh_db.set_user_active_agent(telegram_id=111, agent_id=agent_id)

        agent = await fresh_db.get_user_active_agent(111)
        assert agent is not None
        assert agent["id"] == agent_id

    async def test_get_user_active_agent_none(self, fresh_db: Database) -> None:
        result = await fresh_db.get_user_active_agent(999)
        assert result is None

    async def test_upsert_user_active_agent(self, fresh_db: Database) -> None:
        id1 = await fresh_db.create_agent("a1", "/tmp/a1")
        id2 = await fresh_db.create_agent("a2", "/tmp/a2")

        await fresh_db.set_user_active_agent(111, id1)
        await fresh_db.set_user_active_agent(111, id2)

        agent = await fresh_db.get_user_active_agent(111)
        assert agent is not None
        assert agent["id"] == id2

    async def test_delete_agent_clears_user_agents(self, fresh_db: Database) -> None:
        agent_id = await fresh_db.create_agent("doomed", "/tmp/doomed")
        await fresh_db.set_user_active_agent(111, agent_id)
        await fresh_db.delete_agent(agent_id)

        result = await fresh_db.get_user_active_agent(111)
        assert result is None


class TestSessionWithAgent:
    async def test_create_session_with_agent_id(self, fresh_db: Database) -> None:
        agent_id = await fresh_db.create_agent("test", "/tmp/test")
        await fresh_db.upsert_user(telegram_id=111)
        await fresh_db.create_session("sess-1", telegram_id=111, agent_id=agent_id)

        # Verify via raw query
        cursor = await fresh_db._db.execute(
            "SELECT agent_id FROM sessions WHERE session_id = ?", ("sess-1",)
        )
        row = await cursor.fetchone()
        assert row["agent_id"] == agent_id

    async def test_create_session_without_agent_id(self, fresh_db: Database) -> None:
        await fresh_db.upsert_user(telegram_id=111)
        await fresh_db.create_session("sess-2", telegram_id=111)

        cursor = await fresh_db._db.execute(
            "SELECT agent_id FROM sessions WHERE session_id = ?", ("sess-2",)
        )
        row = await cursor.fetchone()
        assert row["agent_id"] is None


class TestMigration:
    async def test_double_initialize_does_not_fail(self, tmp_path: Path) -> None:
        """Calling initialize() twice should not crash (migration is idempotent)."""
        db = Database(tmp_path / "test.db")
        await db.initialize()
        await db.initialize()  # Should not raise
        await db.close()
