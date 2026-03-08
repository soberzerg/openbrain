"""Agent management: CRUD and active-agent tracking."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from tg_assistant.config import Config
from tg_assistant.models.database import Database

logger = logging.getLogger(__name__)

_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")
_MAX_DESCRIPTION_LEN = 256


class AgentError(Exception):
    """Raised for agent validation or business-logic errors."""


class AgentManager:
    """Manages Claude Code agents — CRUD and per-user active agent tracking."""

    def __init__(self, db: Database, config: Config) -> None:
        self._db = db
        self._config = config

    async def initialize(self) -> None:
        """Ensure the default agent exists."""
        agent_id = await self._db.ensure_default_agent(
            "main", self._config.claude_working_dir
        )
        logger.info("Default agent id=%d, dir=%s", agent_id, self._config.claude_working_dir)

    async def get_all_agents(self) -> list[dict[str, Any]]:
        """Return all agents."""
        return await self._db.get_all_agents()

    async def add_agent(
        self,
        name: str,
        working_dir: str | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        """
        Add a new agent.

        Args:
            name: Alphanumeric name (max 32 chars).
            working_dir: Absolute path or relative (resolved under agents_base_dir).
                         If None, creates a subdirectory named after the agent.
            description: Short description (max 256 chars).

        Returns:
            The created agent dict.

        Raises:
            AgentError: On validation failure or duplicate name.
        """
        if not _NAME_PATTERN.match(name):
            raise AgentError(
                f"Invalid agent name '{name}'. "
                "Use 1-32 alphanumeric chars, hyphens, or underscores."
            )

        if len(description) > _MAX_DESCRIPTION_LEN:
            raise AgentError(f"Description too long (max {_MAX_DESCRIPTION_LEN} chars).")

        existing = await self._db.get_agent_by_name(name)
        if existing:
            raise AgentError(f"Agent '{name}' already exists.")

        resolved_dir = self._resolve_working_dir(name, working_dir)
        resolved_dir.mkdir(parents=True, exist_ok=True)

        agent_id = await self._db.create_agent(
            name=name,
            working_dir=str(resolved_dir),
            description=description,
        )
        agent = await self._db.get_agent_by_id(agent_id)
        assert agent is not None
        logger.info("Agent created: name=%s dir=%s", name, resolved_dir)
        return agent

    async def remove_agent(self, name: str) -> bool:
        """
        Remove an agent by name.

        Returns True if deleted, raises AgentError if default or not found.
        """
        agent = await self._db.get_agent_by_name(name)
        if not agent:
            raise AgentError(f"Agent '{name}' not found.")
        if agent["is_default"]:
            raise AgentError("Cannot delete the default agent.")

        deleted = await self._db.delete_agent(agent["id"])
        if deleted:
            logger.info("Agent deleted: name=%s", name)
        return deleted

    async def get_user_agent(self, telegram_id: int) -> dict[str, Any]:
        """Return the user's active agent, falling back to the default."""
        agent = await self._db.get_user_active_agent(telegram_id)
        if agent:
            return agent
        default = await self._db.get_default_agent()
        if default is None:
            raise RuntimeError("Default agent not found. Was initialize() called?")
        return default

    async def switch_agent(self, telegram_id: int, agent_name: str) -> dict[str, Any]:
        """
        Switch the user's active agent.

        Returns the newly active agent dict.
        Raises AgentError if agent not found.
        """
        agent = await self._db.get_agent_by_name(agent_name)
        if not agent:
            raise AgentError(f"Agent '{agent_name}' not found.")

        await self._db.set_user_active_agent(telegram_id, agent["id"])
        logger.info("User %d switched to agent '%s'", telegram_id, agent_name)
        return agent

    def _resolve_working_dir(self, name: str, working_dir: str | None) -> Path:
        """Resolve agent working directory to an absolute path within agents_base_dir."""
        base = self._config.agents_base_dir.resolve()

        if working_dir is None:
            return base / name

        path = Path(working_dir)
        if path.is_absolute():
            raise AgentError(
                "Absolute paths are not allowed. Use a relative subdirectory."
            )

        resolved = (base / working_dir).resolve()
        if not str(resolved).startswith(str(base)):
            raise AgentError("Path traversal is not allowed.")
        return resolved
