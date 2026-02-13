"""Bot command handlers: /start, /help, /new, /status, /tasks, /inbox, /daily, /week."""

from __future__ import annotations

import logging
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from tg_assistant.handlers._helpers import send_claude_response
from tg_assistant.models.database import Database
from tg_assistant.services.session_manager import SessionManager

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "Отправьте любое сообщение, и я обработаю его через Claude Code.\n\n"
    "<b>Команды:</b>\n"
    "/new — Начать новую сессию (очистить контекст)\n"
    "/status — Информация о текущей сессии\n"
    "/tasks — Задачи на сегодня из YouGile\n"
    "/inbox — Список элементов из Obsidian inbox\n"
    "/daily — Запустить ежедневный обзор\n"
    "/week — Сводка за неделю\n"
    "/help — Показать это сообщение"
)

# Pre-built prompts for shortcut commands
_SHORTCUT_PROMPTS = {
    "tasks": "Show my tasks for today from YouGile. Brief format.",
    "inbox": "Show what's in my Obsidian inbox. List recent unprocessed items.",
    "daily": "Run my daily review: today's tasks, inbox items, priorities. Brief.",
    "week": "Generate a weekly summary: completed tasks, key notes added, upcoming deadlines.",
}


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Welcome message."""
    assert update.effective_message and update.effective_user
    db: Database = context.bot_data["db"]
    user = update.effective_user

    await db.upsert_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name or "",
    )

    await update.effective_message.reply_text(
        f"Welcome, {user.first_name}! 🤖\n\n{HELP_TEXT}",
        parse_mode="HTML",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show help text."""
    assert update.effective_message
    await update.effective_message.reply_text(HELP_TEXT, parse_mode="HTML")


async def cmd_new_session(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Force-expire current session and start fresh."""
    assert update.effective_message and update.effective_user
    session_mgr: SessionManager = context.bot_data["session_manager"]
    user_id = update.effective_user.id

    if session_mgr.is_busy(user_id):
        session_mgr.mark_for_expiry(user_id)
        await update.effective_message.reply_text(
            "Claude is still processing. Queued messages cleared.\n"
            "Session will reset after the current task finishes."
        )
        return

    await session_mgr.expire_session(user_id)
    await update.effective_message.reply_text(
        "Session reset. Next message starts a new conversation."
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current session status and stats."""
    assert update.effective_message and update.effective_user
    session_mgr: SessionManager = context.bot_data["session_manager"]
    db: Database = context.bot_data["db"]
    user_id = update.effective_user.id

    session = session_mgr.get_active_session(user_id)
    stats = await db.get_user_stats(user_id)

    lines: list[str] = []
    if session:
        elapsed = datetime.now() - session.last_active
        minutes = int(elapsed.total_seconds() // 60)
        lines.append(f"<b>Active session:</b> {session.session_id[:8]}...")
        lines.append(f"Last activity: {minutes}m ago")
        lines.append(f"Messages in session: {session.message_count}")
        lines.append(f"Session cost: ${session.total_cost:.4f}")
    else:
        lines.append("No active session. Send a message to start one.")

    if session_mgr.is_busy(user_id):
        lines.append("")
        lines.append("\u23f3 Claude is processing a request...")
        queue_size = session_mgr.get_queue_size(user_id)
        if queue_size > 0:
            lines.append(f"Queued messages: {queue_size}")

    lines.append("")
    lines.append("<b>Total stats:</b>")
    lines.append(f"Sessions: {stats['total_sessions']}")
    lines.append(f"Messages: {stats['total_messages']}")
    lines.append(f"Total cost: ${stats['total_cost']:.4f}")

    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show today's tasks from YouGile."""
    assert update.effective_user
    await send_claude_response(
        update, context, update.effective_user.id, _SHORTCUT_PROMPTS["tasks"]
    )


async def cmd_inbox(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show Obsidian inbox items."""
    assert update.effective_user
    await send_claude_response(
        update, context, update.effective_user.id, _SHORTCUT_PROMPTS["inbox"]
    )


async def cmd_daily(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run daily review."""
    assert update.effective_user
    await send_claude_response(
        update, context, update.effective_user.id, _SHORTCUT_PROMPTS["daily"]
    )


async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate weekly summary."""
    assert update.effective_user
    await send_claude_response(
        update, context, update.effective_user.id, _SHORTCUT_PROMPTS["week"]
    )
