"""Async wrapper for Claude Code CLI subprocess."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass

from tg_assistant.config import Config

logger = logging.getLogger(__name__)


class ClaudeCliError(Exception):
    """Raised when Claude CLI returns an error or fails to execute."""


@dataclass(frozen=True)
class PermissionProfile:
    """Defines tool permissions for a Claude CLI session."""

    name: str
    allowed_tools: tuple[str, ...] | None = None
    permission_mode: str | None = None
    use_dangerous_skip: bool = False
    append_system_prompt: str | None = None


PERMISSION_PROFILES: dict[str, PermissionProfile] = {
    "full": PermissionProfile(
        name="full",
        use_dangerous_skip=True,
    ),
    "safe": PermissionProfile(
        name="safe",
        permission_mode="acceptEdits",
        allowed_tools=(
            "Read",
            "Grep",
            "Glob",
            "Edit",
            "Write",
            "Bash(ls:*)",
            "Bash(cat:*)",
            "Bash(head:*)",
            "Bash(tail:*)",
            "Bash(find:*)",
            "Bash(git:log)",
            "Bash(git:status)",
            "Bash(git:diff)",
            "Bash(git:show)",
            "Bash(pwd:*)",
            "Bash(echo:*)",
        ),
        append_system_prompt="You are in safe mode. Destructive commands are blocked.",
    ),
    "readonly": PermissionProfile(
        name="readonly",
        permission_mode="plan",
        allowed_tools=(
            "Read",
            "Grep",
            "Glob",
            "Bash(ls:*)",
            "Bash(cat:*)",
            "Bash(head:*)",
            "Bash(tail:*)",
            "Bash(find:*)",
            "Bash(git:log)",
            "Bash(git:status)",
            "Bash(git:diff)",
            "Bash(git:show)",
            "Bash(pwd:*)",
        ),
        append_system_prompt="You are in read-only mode. You cannot modify any files.",
    ),
}


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
        self.profile = PERMISSION_PROFILES[config.claude_permission_profile]

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
            "--output-format",
            "json",
        ]

        # Apply permission profile
        if self.profile.use_dangerous_skip:
            cmd.append("--dangerously-skip-permissions")
        else:
            if self.profile.permission_mode:
                cmd.extend(["--permission-mode", self.profile.permission_mode])
            if self.profile.allowed_tools:
                cmd.extend(["--allowedTools", " ".join(self.profile.allowed_tools)])
            if self.profile.append_system_prompt:
                cmd.extend(["--append-system-prompt", self.profile.append_system_prompt])

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
        working_dir: str | None = None,
    ) -> ClaudeResponse:
        """
        Send a prompt to Claude Code CLI and return the parsed response.

        Args:
            prompt: The text to send to Claude.
            session_id: UUID of session (for new or resumed sessions).
            is_resume: If True, uses --resume to continue existing session.
            working_dir: Override working directory (for multi-agent support).

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
        # Remove API key so CLI uses OAuth (subscription) instead of API billing
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_dir or self.working_dir,
                env=env,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=prompt.encode()),
                timeout=self.timeout,
            )

            if process.returncode != 0:
                stderr_text = stderr.decode().strip()
                stdout_text = stdout.decode().strip()
                # CLI often returns error details as JSON in stdout
                error_msg = stderr_text
                if not error_msg and stdout_text:
                    try:
                        err_data = json.loads(stdout_text)
                        error_msg = err_data.get("result", stdout_text)
                    except json.JSONDecodeError:
                        error_msg = stdout_text
                logger.error(
                    "Claude CLI error (code %d): %s",
                    process.returncode,
                    error_msg[:500] if error_msg else "(no output)",
                )
                raise ClaudeCliError(error_msg or f"CLI exited with code {process.returncode}")

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
            raise
        except json.JSONDecodeError as e:
            logger.error("Failed to parse CLI output: %s", e)
            raise ClaudeCliError(f"Failed to parse CLI output: {e}") from e
        finally:
            # Ensure process cleanup in all error paths
            if process and process.returncode is None:
                process.kill()
                await process.wait()
