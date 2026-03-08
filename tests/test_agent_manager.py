"""Tests for AgentManager service."""

from __future__ import annotations

from pathlib import Path

import pytest

from tg_assistant.config import Config
from tg_assistant.models.database import Database
from tg_assistant.services.agent_manager import AgentError, AgentManager


@pytest.fixture
def agent_config(tmp_path: Path) -> Config:
    """Config with temporary agents_base_dir."""
    return Config(
        bot_token="test-token",
        allowed_user_ids=[111],
        claude_cli_path="/usr/bin/echo",
        claude_working_dir=str(tmp_path / "main_workdir"),
        data_dir=tmp_path / "data",
        upload_dir=tmp_path / "data" / "uploads",
        db_path=tmp_path / "data" / "test.db",
        agents_base_dir=tmp_path / "agents",
    )


@pytest.fixture
async def agent_db(agent_config: Config) -> Database:
    """Initialized test database."""
    agent_config.data_dir.mkdir(parents=True, exist_ok=True)
    database = Database(agent_config.db_path)
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
async def agent_mgr(agent_db: Database, agent_config: Config) -> AgentManager:
    """Initialized AgentManager with default agent."""
    mgr = AgentManager(agent_db, agent_config)
    await mgr.initialize()
    return mgr


class TestInitialization:
    async def test_initialize_creates_default_agent(self, agent_mgr: AgentManager) -> None:
        agents = await agent_mgr.get_all_agents()
        assert len(agents) == 1
        assert agents[0]["name"] == "main"
        assert agents[0]["is_default"] == 1

    async def test_initialize_is_idempotent(
        self, agent_db: Database, agent_config: Config
    ) -> None:
        mgr1 = AgentManager(agent_db, agent_config)
        await mgr1.initialize()

        mgr2 = AgentManager(agent_db, agent_config)
        await mgr2.initialize()

        agents = await mgr2.get_all_agents()
        assert len(agents) == 1


class TestAddAgent:
    async def test_add_agent_success(self, agent_mgr: AgentManager) -> None:
        agent = await agent_mgr.add_agent("assistant", description="My helper")
        assert agent["name"] == "assistant"
        assert agent["description"] == "My helper"
        assert agent["is_default"] == 0

    async def test_add_agent_creates_directory(
        self, agent_mgr: AgentManager, agent_config: Config
    ) -> None:
        await agent_mgr.add_agent("project1")
        expected_dir = agent_config.agents_base_dir.resolve() / "project1"
        assert expected_dir.is_dir()

    async def test_add_agent_with_relative_path(
        self, agent_mgr: AgentManager, agent_config: Config
    ) -> None:
        agent = await agent_mgr.add_agent("custom", working_dir="custom_dir")
        expected = str(agent_config.agents_base_dir.resolve() / "custom_dir")
        assert agent["working_dir"] == expected

    async def test_add_agent_with_absolute_path_fails(
        self, agent_mgr: AgentManager, tmp_path: Path
    ) -> None:
        abs_dir = tmp_path / "absolute_agent"
        with pytest.raises(AgentError, match="Absolute paths are not allowed"):
            await agent_mgr.add_agent("abs", working_dir=str(abs_dir))

    async def test_add_agent_path_traversal_fails(
        self, agent_mgr: AgentManager
    ) -> None:
        with pytest.raises(AgentError, match="Path traversal is not allowed"):
            await agent_mgr.add_agent("evil", working_dir="../../etc")

    async def test_add_agent_duplicate_name_fails(self, agent_mgr: AgentManager) -> None:
        await agent_mgr.add_agent("dup")
        with pytest.raises(AgentError, match="already exists"):
            await agent_mgr.add_agent("dup")

    async def test_add_agent_invalid_name_fails(self, agent_mgr: AgentManager) -> None:
        with pytest.raises(AgentError, match="Invalid agent name"):
            await agent_mgr.add_agent("bad name!")

    async def test_add_agent_empty_name_fails(self, agent_mgr: AgentManager) -> None:
        with pytest.raises(AgentError, match="Invalid agent name"):
            await agent_mgr.add_agent("")

    async def test_add_agent_long_description_fails(self, agent_mgr: AgentManager) -> None:
        with pytest.raises(AgentError, match="Description too long"):
            await agent_mgr.add_agent("x", description="a" * 300)


class TestRemoveAgent:
    async def test_remove_agent_success(self, agent_mgr: AgentManager) -> None:
        await agent_mgr.add_agent("temp")
        result = await agent_mgr.remove_agent("temp")
        assert result is True

        agents = await agent_mgr.get_all_agents()
        assert all(a["name"] != "temp" for a in agents)

    async def test_remove_default_agent_fails(self, agent_mgr: AgentManager) -> None:
        with pytest.raises(AgentError, match="Cannot delete"):
            await agent_mgr.remove_agent("main")

    async def test_remove_nonexistent_agent_fails(self, agent_mgr: AgentManager) -> None:
        with pytest.raises(AgentError, match="not found"):
            await agent_mgr.remove_agent("ghost")


class TestUserAgent:
    async def test_get_user_agent_defaults_to_main(self, agent_mgr: AgentManager) -> None:
        agent = await agent_mgr.get_user_agent(telegram_id=111)
        assert agent["name"] == "main"
        assert agent["is_default"] == 1

    async def test_switch_agent(self, agent_mgr: AgentManager) -> None:
        await agent_mgr.add_agent("project")
        agent = await agent_mgr.switch_agent(111, "project")
        assert agent["name"] == "project"

        active = await agent_mgr.get_user_agent(111)
        assert active["name"] == "project"

    async def test_switch_to_nonexistent_agent_fails(self, agent_mgr: AgentManager) -> None:
        with pytest.raises(AgentError, match="not found"):
            await agent_mgr.switch_agent(111, "nope")

    async def test_switch_back_to_default(self, agent_mgr: AgentManager) -> None:
        await agent_mgr.add_agent("other")
        await agent_mgr.switch_agent(111, "other")
        await agent_mgr.switch_agent(111, "main")

        active = await agent_mgr.get_user_agent(111)
        assert active["name"] == "main"

    async def test_delete_agent_resets_user_to_default(
        self, agent_mgr: AgentManager
    ) -> None:
        await agent_mgr.add_agent("doomed")
        await agent_mgr.switch_agent(111, "doomed")
        await agent_mgr.remove_agent("doomed")

        active = await agent_mgr.get_user_agent(111)
        assert active["name"] == "main"
