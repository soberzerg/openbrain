"""Async wrapper for Claude Code CLI subprocess."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass

from tg_assistant.config import Config

logger = logging.getLogger(__name__)


class ClaudeCliError(Exception):
    """Raised when Claude CLI returns an error or fails to execute."""


@dataclass
class ClaudeResponse:
    """Parsed response from Claude Code CLI."""

    text: str
    session_id: str
    cost_usd: float
    duration_ms: int
    is_error: bool
    num_turns: int


class ClaudeCli:
    """Async interface to Claude Code CLI (--print mode)."""

    def __init__(self, config: Config) -> None:
        self.cli_path = config.claude_cli_path
        self.working_dir = config.claude_working_dir
        self.timeout = config.claude_timeout_seconds
        self.obsidian_path = config.obsidian_vault_path
        self.upload_dir = str(config.upload_dir.resolve())

    @staticmethod
    def generate_session_id() -> str:
        """Generate a new UUID for a Claude Code session."""
        return str(uuid.uuid4())

    def _build_command(
        self,
        session_id: str | None = None,
        is_resume: bool = False,
    ) -> list[str]:
        """Build the CLI command arguments (prompt is passed via stdin)."""
        cmd = [
            self.cli_path,
            "-p",
            "--output-format", "json",
            "--dangerously-skip-permissions",
        ]

        if is_resume and session_id:
            cmd.extend(["-r", session_id])
        elif session_id:
            cmd.extend(["--session-id", session_id])

        # Add additional directories for Claude to access
        if self.obsidian_path:
            cmd.extend(["--add-dir", self.obsidian_path])
        cmd.extend(["--add-dir", self.upload_dir])

        return cmd

    async def send(
        self,
        prompt: str,
        session_id: str | None = None,
        is_resume: bool = False,
    ) -> ClaudeResponse:
        """
        Send a prompt to Claude Code CLI and return the parsed response.

        Args:
            prompt: The text to send to Claude.
            session_id: UUID of session (for new or resumed sessions).
            is_resume: If True, uses --resume to continue existing session.

        Raises:
            ClaudeCliError: If the CLI returns an error or produces invalid output.
            asyncio.TimeoutError: If the CLI does not respond within the timeout.
        """
        cmd = self._build_command(session_id, is_resume)
        logger.info(
            "Calling Claude CLI: session=%s resume=%s prompt_len=%d",
            session_id,
            is_resume,
            len(prompt),
        )

        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.working_dir,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=prompt.encode()),
                timeout=self.timeout,
            )

            if process.returncode != 0:
                error_text = stderr.decode().strip()
                logger.error("Claude CLI error (code %d): %s", process.returncode, error_text)
                raise ClaudeCliError(
                    f"CLI exited with code {process.returncode}: {error_text}"
                )

            data = json.loads(stdout.decode())

            response = ClaudeResponse(
                text=data.get("result", ""),
                session_id=data.get("session_id", session_id or ""),
                cost_usd=data.get("total_cost_usd", 0.0),
                duration_ms=data.get("duration_ms", 0),
                is_error=data.get("is_error", False),
                num_turns=data.get("num_turns", 0),
            )

            if response.is_error:
                logger.warning("Claude returned an error result: %s", response.text[:200])

            logger.info(
                "Claude CLI response: session=%s cost=$%.4f turns=%d len=%d",
                response.session_id,
                response.cost_usd,
                response.num_turns,
                len(response.text),
            )
            return response

        except asyncio.TimeoutError:
            logger.error("Claude CLI timed out after %ds", self.timeout)
            if process:
                process.kill()
                await process.wait()
            raise

        except json.JSONDecodeError as e:
            logger.error("Failed to parse CLI output: %s", e)
            raise ClaudeCliError(f"Failed to parse CLI output: {e}") from e
