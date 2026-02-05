"""Git sync for Obsidian vault (auto pull/push)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class GitSync:
    """Auto-sync a git repository (pull before session, push after changes)."""

    def __init__(self, repo_path: str) -> None:
        self.repo_path = repo_path

    async def _run(self, *args: str) -> tuple[int, str, str]:
        """Run a git command and return (returncode, stdout, stderr)."""
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=self.repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return proc.returncode, stdout.decode().strip(), stderr.decode().strip()

    async def pull(self) -> bool:
        """Pull latest changes. Returns True on success."""
        code, stdout, stderr = await self._run("pull", "--rebase", "--autostash")
        if code != 0:
            logger.error("Git pull failed: %s", stderr)
            return False
        logger.info("Git pull: %s", stdout or "up to date")
        return True

    async def push_if_changed(self) -> bool:
        """Stage all changes, commit, and push. Returns True if anything was pushed."""
        code, stdout, _ = await self._run("status", "--porcelain")
        if code != 0 or not stdout:
            return False

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        commands = [
            ("add", "-A"),
            ("commit", "-m", f"auto: telegram bot sync {timestamp}"),
            ("push",),
        ]

        for args in commands:
            code, _, stderr = await self._run(*args)
            if code != 0:
                logger.error("Git %s failed: %s", args[0], stderr)
                return False

        logger.info("Git push: synced changes")
        return True
