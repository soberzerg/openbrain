"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


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
    claude_permission_profile: str = "full"

    # Sessions
    session_timeout_minutes: int = 30

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
    agents_base_dir: Path = field(default_factory=lambda: Path("agents"))  # resolved in from_env

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

        default_agents_dir = str(Path(claude_working_dir) / "agents")
        agents_base_dir = Path(os.getenv("AGENTS_BASE_DIR", default_agents_dir))

        config = cls(
            bot_token=bot_token,
            allowed_user_ids=allowed_user_ids,
            claude_cli_path=claude_cli_path,
            claude_working_dir=claude_working_dir,
            claude_timeout_seconds=int(os.getenv("CLAUDE_TIMEOUT_SECONDS", "120")),
            claude_permission_profile=os.getenv("CLAUDE_PERMISSION_PROFILE", "full"),
            session_timeout_minutes=int(os.getenv("SESSION_TIMEOUT_MINUTES", "30")),
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
            agents_base_dir=agents_base_dir,
        )

        # Validate permission profile
        from tg_assistant.services.claude_cli import PERMISSION_PROFILES

        if config.claude_permission_profile not in PERMISSION_PROFILES:
            available = ", ".join(sorted(PERMISSION_PROFILES))
            print(
                f"ERROR: Unknown CLAUDE_PERMISSION_PROFILE='{config.claude_permission_profile}'. "
                f"Available: {available}",
                file=sys.stderr,
            )
            sys.exit(1)

        # Ensure runtime directories exist
        config.data_dir.mkdir(parents=True, exist_ok=True)
        config.upload_dir.mkdir(parents=True, exist_ok=True)
        config.agents_base_dir.mkdir(parents=True, exist_ok=True)

        return config
