"""Text message handler: send user text to Claude Code CLI."""

from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes

from tg_assistant.models.database import Database
from tg_assistant.services.claude_cli import ClaudeCliError
from tg_assistant.services.message_formatter import format_for_telegram, split_message
from tg_assistant.services.session_manager import SessionManager
from tg_assistant.utils.typing_indicator import keep_typing

logger = logging.getLogger(__name__)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process a text message through Claude Code CLI."""
    assert update.effective_message and update.effective_user and update.effective_chat

    session_mgr: SessionManager = context.bot_data["session_manager"]
    db: Database = context.bot_data["db"]

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    message_text = update.effective_message.text or ""

    # Update user record
    await db.upsert_user(
        telegram_id=user_id,
        username=update.effective_user.username,
        first_name=update.effective_user.first_name,
        last_name=update.effective_user.last_name or "",
    )

    # Log incoming message
    await db.log_message(
        telegram_id=user_id,
        direction="in",
        content=message_text,
        msg_type="text",
        telegram_message_id=update.effective_message.message_id,
    )

    # Start typing indicator
    typing_task = asyncio.create_task(keep_typing(context.bot, chat_id))

    try:
        response = await session_mgr.send_message(user_id, message_text)

        # Format and send response
        chunks = split_message(response.text)
        for chunk in chunks:
            formatted, parse_mode = format_for_telegram(chunk)
            try:
                sent = await update.effective_message.reply_text(
                    formatted, parse_mode=parse_mode
                )
            except Exception:
                # Fallback to plain text if HTML parsing fails
                sent = await update.effective_message.reply_text(chunk)

            await db.log_message(
                telegram_id=user_id,
                direction="out",
                content=chunk,
                msg_type="text",
                telegram_message_id=sent.message_id,
                session_id=response.session_id,
                cost_usd=response.cost_usd,
                duration_ms=response.duration_ms,
            )

    except ClaudeCliError as e:
        logger.error("Claude CLI error for user %d: %s", user_id, e)
        await update.effective_message.reply_text(
            f"Claude Code error. Please try again or use /new to start a fresh session."
        )

    except asyncio.TimeoutError:
        await update.effective_message.reply_text(
            "Claude Code took too long to respond. Try again or /new to start fresh."
        )

    finally:
        typing_task.cancel()
