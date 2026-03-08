"""Telegram bot application setup and handler registration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from telegram import BotCommand, Update
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
from tg_assistant.handlers.commands import (
    cmd_agent_add,
    cmd_agent_del,
    cmd_agent_switch,
    cmd_agents,
    cmd_daily,
    cmd_help,
    cmd_new_session,
    cmd_start,
    cmd_status,
    cmd_tasks,
    cmd_week,
)
from tg_assistant.handlers.errors import error_handler
from tg_assistant.handlers.text import handle_text
from tg_assistant.handlers.voice import handle_video_note, handle_voice
from tg_assistant.models.database import Database
from tg_assistant.services.agent_manager import AgentManager
from tg_assistant.services.claude_cli import ClaudeCli
from tg_assistant.services.rate_limiter import RateLimiter
from tg_assistant.services.scheduler import setup_jobs
from tg_assistant.services.session_manager import SessionManager
from tg_assistant.services.transcription import TranscriptionService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BotCommandDef:
    """Command definition with handler and description."""

    command: str  # command name (without /)
    description: str  # for Telegram menu (short, 3-256 chars)
    handler: Callable  # handler function
    help_text: str  # for /help command (can be longer)


# Commands in order they should appear in menu
BOT_COMMANDS = [
    BotCommandDef(
        command="start",
        description="Начать работу с ботом",
        handler=cmd_start,
        help_text="Приветственное сообщение",
    ),
    BotCommandDef(
        command="help",
        description="Показать команды",
        handler=cmd_help,
        help_text="Показать это сообщение",
    ),
    BotCommandDef(
        command="new",
        description="Новый диалог",
        handler=cmd_new_session,
        help_text="Начать новую сессию (очистить контекст)",
    ),
    BotCommandDef(
        command="status",
        description="Статистика сессии",
        handler=cmd_status,
        help_text="Информация о текущей сессии",
    ),
    BotCommandDef(
        command="agents",
        description="Список агентов",
        handler=cmd_agents,
        help_text="Показать всех агентов",
    ),
    BotCommandDef(
        command="agent",
        description="Переключить агента",
        handler=cmd_agent_switch,
        help_text="Переключить активного агента",
    ),
    BotCommandDef(
        command="agent_add",
        description="Добавить агента",
        handler=cmd_agent_add,
        help_text="Добавить нового агента",
    ),
    BotCommandDef(
        command="agent_del",
        description="Удалить агента",
        handler=cmd_agent_del,
        help_text="Удалить агента",
    ),
    BotCommandDef(
        command="tasks",
        description="Задачи на сегодня",
        handler=cmd_tasks,
        help_text="Задачи на сегодня из YouGile",
    ),
    BotCommandDef(
        command="daily",
        description="Ежедневный обзор",
        handler=cmd_daily,
        help_text="Запустить ежедневный обзор",
    ),
    BotCommandDef(
        command="week",
        description="Недельная сводка",
        handler=cmd_week,
        help_text="Сводка за неделю",
    ),
]


async def post_init(application: Application) -> None:
    """Initialize shared services after the application is built."""
    config: Config = application.bot_data["config"]
    db = Database(config.db_path)
    await db.initialize()

    cli = ClaudeCli(config)

    agent_mgr = AgentManager(db, config)
    await agent_mgr.initialize()

    session_mgr = SessionManager(config, cli, db)
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

    application.bot_data["db"] = db
    application.bot_data["agent_manager"] = agent_mgr
    application.bot_data["session_manager"] = session_mgr
    application.bot_data["rate_limiter"] = rate_limiter
    application.bot_data["transcription"] = transcription

    # Schedule notifications
    setup_jobs(application)

    # Register bot commands in Telegram menu
    await _setup_bot_commands(application.bot)

    logger.info("Services initialized")


async def post_shutdown(application: Application) -> None:
    """Clean up resources on shutdown."""
    db: Database | None = application.bot_data.get("db")
    if db:
        await db.close()
        logger.info("Database connection closed")


async def _setup_bot_commands(bot) -> None:
    """Register bot commands with Telegram API for menu display."""
    commands = [BotCommand(cmd.command, cmd.description) for cmd in BOT_COMMANDS]

    try:
        await bot.set_my_commands(commands)
        logger.info("Registered %d commands in Telegram menu", len(commands))
    except Exception as e:
        logger.error("Failed to set bot commands: %s", e)


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

    # Register commands from centralized registry (highest priority)
    for cmd_def in BOT_COMMANDS:
        app.add_handler(CommandHandler(cmd_def.command, cmd_def.handler, filters=auth_filter))

    # Voice and video notes → transcription → Claude
    app.add_handler(
        MessageHandler(
            auth_filter & filters.VOICE,
            handle_voice,
        )
    )
    app.add_handler(
        MessageHandler(
            auth_filter & filters.VIDEO_NOTE,
            handle_video_note,
        )
    )

    # All other messages → Claude directly
    app.add_handler(
        MessageHandler(
            auth_filter
            & (
                filters.FORWARDED
                | filters.PHOTO
                | filters.Document.ALL
                | (filters.TEXT & ~filters.COMMAND)
            ),
            handle_text,
        )
    )

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
