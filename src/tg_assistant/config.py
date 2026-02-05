"""Application configuration loaded from environment variables."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
import os


@dataclass(frozen=True)
class Config:
    """Immutable application configuration."""

    # Telegram
    bot_token: str
    allowed_user_ids: list[int]

    # Claude Code CLI
    claude_cli_path: str
    claude_working_dir: str
    claude_timeout_seconds: int = 120

    # Sessions
    session_timeout_minutes: int = 30

    # Obsidian / Git
    obsidian_vault_path: str = ""
    obsidian_inbox_subdir: str = "00_Inbox"
    git_auto_sync: bool = True

    # Rate limiting
    rate_limit_per_minute: int = 10

    # Whisper (OpenAI API)
    openai_api_key: str = ""
    whisper_model: str = "whisper-1"
    whisper_base_url: str = "https://api.openai.com/v1"

    # Notifications
    notifications_enabled: bool = False
    morning_digest_hour: int = 9
    weekly_summary_day: int = 0  # 0=Monday
    weekly_summary_hour: int = 10
    timezone: str = "UTC"

    # Content routing
    routing_config_path: str = "config/routing.yaml"

    # Paths
    data_dir: Path = field(default_factory=lambda: Path("data"))
    upload_dir: Path = field(default_factory=lambda: Path("data/uploads"))
    db_path: Path = field(default_factory=lambda: Path("data/tg_assistant.db"))

    @classmethod
    def from_env(cls, env_path: str | None = None) -> Config:
        """Load configuration from .env file and environment variables."""
        load_dotenv(env_path or ".env")

        bot_token = os.getenv("BOT_TOKEN", "")
        if not bot_token:
            print("ERROR: BOT_TOKEN is required. Set it in .env file.", file=sys.stderr)
            sys.exit(1)

        raw_ids = os.getenv("ALLOWED_USER_IDS", "")
        if not raw_ids:
            print("ERROR: ALLOWED_USER_IDS is required. Set it in .env file.", file=sys.stderr)
            sys.exit(1)

        try:
            allowed_user_ids = [int(uid.strip()) for uid in raw_ids.split(",") if uid.strip()]
        except ValueError:
            print(
                "ERROR: ALLOWED_USER_IDS must be comma-separated integers.",
                file=sys.stderr,
            )
            sys.exit(1)

        claude_cli_path = os.getenv("CLAUDE_CLI_PATH", "claude")
        claude_working_dir = os.getenv("CLAUDE_WORKING_DIR", str(Path.cwd()))

        data_dir = Path(os.getenv("DATA_DIR", "data"))
        upload_dir = data_dir / "uploads"
        db_path = data_dir / "tg_assistant.db"

        config = cls(
            bot_token=bot_token,
            allowed_user_ids=allowed_user_ids,
            claude_cli_path=claude_cli_path,
            claude_working_dir=claude_working_dir,
            claude_timeout_seconds=int(os.getenv("CLAUDE_TIMEOUT_SECONDS", "120")),
            session_timeout_minutes=int(os.getenv("SESSION_TIMEOUT_MINUTES", "30")),
            obsidian_vault_path=os.getenv("OBSIDIAN_VAULT_PATH", ""),
            obsidian_inbox_subdir=os.getenv("OBSIDIAN_INBOX_SUBDIR", "00_Inbox"),
            git_auto_sync=os.getenv("GIT_AUTO_SYNC", "true").lower() == "true",
            rate_limit_per_minute=int(os.getenv("RATE_LIMIT_PER_MINUTE", "10")),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            whisper_model=os.getenv("WHISPER_MODEL", "whisper-1"),
            whisper_base_url=os.getenv("WHISPER_BASE_URL", "https://api.openai.com/v1"),
            notifications_enabled=os.getenv("NOTIFICATIONS_ENABLED", "false").lower() == "true",
            morning_digest_hour=int(os.getenv("MORNING_DIGEST_HOUR", "9")),
            weekly_summary_day=int(os.getenv("WEEKLY_SUMMARY_DAY", "0")),
            weekly_summary_hour=int(os.getenv("WEEKLY_SUMMARY_HOUR", "10")),
            timezone=os.getenv("TZ", "UTC"),
            routing_config_path=os.getenv("ROUTING_CONFIG_PATH", "config/routing.yaml"),
            data_dir=data_dir,
            upload_dir=upload_dir,
            db_path=db_path,
        )

        # Ensure runtime directories exist
        config.data_dir.mkdir(parents=True, exist_ok=True)
        config.upload_dir.mkdir(parents=True, exist_ok=True)

        return config
