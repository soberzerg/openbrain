"""Telegram bot application setup and handler registration."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from tg_assistant.config import Config
from tg_assistant.handlers.auth import handle_unauthorized
from tg_assistant.handlers.commands import cmd_help, cmd_new_session, cmd_start, cmd_status
from tg_assistant.handlers.errors import error_handler
from tg_assistant.handlers.media import handle_document, handle_photo
from tg_assistant.handlers.text import handle_text
from tg_assistant.models.database import Database
from tg_assistant.services.claude_cli import ClaudeCli
from tg_assistant.services.git_sync import GitSync
from tg_assistant.services.session_manager import SessionManager

logger = logging.getLogger(__name__)


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

    application.bot_data["db"] = db
    application.bot_data["session_manager"] = session_mgr
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

    # Media handlers
    app.add_handler(
        MessageHandler(auth_filter & filters.PHOTO, handle_photo)
    )
    app.add_handler(
        MessageHandler(auth_filter & filters.Document.ALL, handle_document)
    )

    # Text handler (catch-all for authorized users)
    app.add_handler(
        MessageHandler(
            auth_filter & filters.TEXT & ~filters.COMMAND,
            handle_text,
        )
    )

    # Unauthorized access handler (catch-all for everyone else)
    app.add_handler(
        MessageHandler(
            ~auth_filter & (filters.TEXT | filters.PHOTO | filters.Document.ALL),
            handle_unauthorized,
        )
    )

    # Debug: absolute catch-all to detect if any updates arrive
    async def _debug_catch_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        logger.warning(
            "Unhandled update: type=%s user_id=%s username=%s",
            update.effective_message.text[:50] if update.effective_message and update.effective_message.text else "non-text",
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
