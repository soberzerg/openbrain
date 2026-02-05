"""Telegram bot application setup and handler registration."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from tg_assistant.config import Config
from tg_assistant.handlers.auth import handle_unauthorized
from tg_assistant.handlers.commands import (
    cmd_daily,
    cmd_help,
    cmd_inbox,
    cmd_new_session,
    cmd_start,
    cmd_status,
    cmd_tasks,
    cmd_week,
)
from tg_assistant.handlers.errors import error_handler
from tg_assistant.handlers.router import handle_callback, handle_routed_message
from tg_assistant.models.database import Database
from tg_assistant.services.action_router import ActionRouter
from tg_assistant.services.claude_cli import ClaudeCli
from tg_assistant.services.content_classifier import ContentClassifier
from tg_assistant.services.git_sync import GitSync
from tg_assistant.services.inbox_writer import InboxWriter
from tg_assistant.services.rate_limiter import RateLimiter
from tg_assistant.services.scheduler import setup_jobs
from tg_assistant.services.session_manager import SessionManager
from tg_assistant.services.transcription import TranscriptionService

logger = logging.getLogger(__name__)


def _load_routing_config(path: str) -> dict:
    """Load routing YAML config. Returns empty dict if file not found."""
    config_path = Path(path)
    if not config_path.exists():
        logger.warning("Routing config not found: %s — using defaults", path)
        return {}
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


async def post_init(application: Application) -> None:
    """Initialize shared services after the application is built."""
    config: Config = application.bot_data["config"]
    db = Database(config.db_path)
    await db.initialize()

    cli = ClaudeCli(config)

    git_sync = None
    if config.obsidian_vault_path and config.git_auto_sync:
        git_sync = GitSync(config.obsidian_vault_path)
        logger.info("Git sync enabled for %s", config.obsidian_vault_path)

    session_mgr = SessionManager(config, cli, db, git_sync)
    rate_limiter = RateLimiter(config.rate_limit_per_minute)

    # Transcription service (optional — requires API key)
    transcription = None
    if config.openai_api_key:
        transcription = TranscriptionService(
            api_key=config.openai_api_key,
            model=config.whisper_model,
            base_url=config.whisper_base_url,
        )
        logger.info("Transcription enabled (model=%s)", config.whisper_model)
    else:
        logger.info("Transcription disabled (no OPENAI_API_KEY)")

    # Content routing services
    routing_config = _load_routing_config(config.routing_config_path)
    classifier = ContentClassifier(routing_config)
    action_router = ActionRouter(routing_config)

    # Inbox writer (optional — requires Obsidian vault path)
    inbox_writer = None
    if config.obsidian_vault_path:
        inbox_writer = InboxWriter(config.obsidian_vault_path, config.obsidian_inbox_subdir)
        logger.info("Inbox writer enabled for %s", config.obsidian_vault_path)
    else:
        logger.info("Inbox writer disabled (no OBSIDIAN_VAULT_PATH)")

    application.bot_data["db"] = db
    application.bot_data["session_manager"] = session_mgr
    application.bot_data["rate_limiter"] = rate_limiter
    application.bot_data["transcription"] = transcription
    application.bot_data["content_classifier"] = classifier
    application.bot_data["action_router"] = action_router
    application.bot_data["inbox_writer"] = inbox_writer

    # Schedule notifications
    setup_jobs(application)

    logger.info("Services initialized")


async def post_shutdown(application: Application) -> None:
    """Clean up resources on shutdown."""
    db: Database | None = application.bot_data.get("db")
    if db:
        await db.close()
        logger.info("Database connection closed")


def create_application(config: Config) -> Application:
    """Build and configure the Telegram bot application."""
    app = (
        ApplicationBuilder()
        .token(config.bot_token)
        .concurrent_updates(True)
        .read_timeout(30)
        .write_timeout(30)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Store config in bot_data for handlers to access
    app.bot_data["config"] = config

    # Auth filter: only process messages from allowed users
    auth_filter = filters.User(user_id=config.allowed_user_ids)

    # Commands (highest priority)
    app.add_handler(CommandHandler("start", cmd_start, filters=auth_filter))
    app.add_handler(CommandHandler("help", cmd_help, filters=auth_filter))
    app.add_handler(CommandHandler("new", cmd_new_session, filters=auth_filter))
    app.add_handler(CommandHandler("status", cmd_status, filters=auth_filter))
    app.add_handler(CommandHandler("tasks", cmd_tasks, filters=auth_filter))
    app.add_handler(CommandHandler("inbox", cmd_inbox, filters=auth_filter))
    app.add_handler(CommandHandler("daily", cmd_daily, filters=auth_filter))
    app.add_handler(CommandHandler("week", cmd_week, filters=auth_filter))

    # Unified content router — handles all non-command messages
    app.add_handler(
        MessageHandler(
            auth_filter & (
                filters.FORWARDED
                | filters.VOICE
                | filters.VIDEO_NOTE
                | filters.PHOTO
                | filters.Document.ALL
                | (filters.TEXT & ~filters.COMMAND)
            ),
            handle_routed_message,
        )
    )

    # Inline button callback handler
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Unauthorized access handler (catch-all for everyone else)
    app.add_handler(
        MessageHandler(
            ~auth_filter & (filters.TEXT | filters.PHOTO | filters.Document.ALL | filters.VOICE),
            handle_unauthorized,
        )
    )

    # Debug: absolute catch-all to detect if any updates arrive
    async def _debug_catch_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        msg_preview = (
            update.effective_message.text[:50]
            if update.effective_message and update.effective_message.text
            else "non-text"
        )
        logger.warning(
            "Unhandled update: type=%s user_id=%s username=%s",
            msg_preview,
            user.id if user else "?",
            user.username if user else "?",
        )

    app.add_handler(MessageHandler(filters.ALL, _debug_catch_all))

    # Global error handler
    app.add_error_handler(error_handler)

    logger.info(
        "Bot configured: %d allowed users, %d handlers",
        len(config.allowed_user_ids),
        len(app.handlers.get(0, [])),
    )

    return app
