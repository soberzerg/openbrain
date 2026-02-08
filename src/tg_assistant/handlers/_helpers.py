"""Shared handler utilities: send prompt to Claude and reply in Telegram."""

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


async def send_claude_response(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    prompt: str,
    msg_type: str = "text",
    log_input: bool = True,
    file_path: str | None = None,
) -> None:
    """
    Send a prompt to Claude via SessionManager and reply with the formatted response.

    Handles: typing indicator, message splitting, HTML formatting,
    fallback to plain text, DB logging, and error handling.
    """
    assert update.effective_message and update.effective_chat

    session_mgr: SessionManager = context.bot_data["session_manager"]
    db: Database = context.bot_data["db"]
    chat_id = update.effective_chat.id

    if log_input:
        await db.log_message(
            telegram_id=user_id,
            direction="in",
            content=prompt,
            msg_type=msg_type,
            telegram_message_id=update.effective_message.message_id,
            file_path=file_path,
        )

    typing_task = asyncio.create_task(keep_typing(context.bot, chat_id))
    try:
        response = await session_mgr.send_message(user_id, prompt)

        chunks = split_message(response.text)
        if not chunks:
            chunks = ["(empty response)"]
        for chunk in chunks:
            formatted, parse_mode = format_for_telegram(chunk)
            try:
                sent = await update.effective_message.reply_text(
                    formatted, parse_mode=parse_mode
                )
            except Exception:
                sent = await update.effective_message.reply_text(chunk)

            await db.log_message(
                telegram_id=user_id,
                direction="out",
                content=chunk,
                msg_type=msg_type,
                telegram_message_id=sent.message_id,
                session_id=response.session_id,
                cost_usd=response.cost_usd,
                duration_ms=response.duration_ms,
            )

    except ClaudeCliError as e:
        logger.error("Claude CLI error for user %d: %s", user_id, e)
        await update.effective_message.reply_text(
            "Claude Code error. Please try again or use /new to start a fresh session."
        )

    except asyncio.TimeoutError:
        await update.effective_message.reply_text(
            "Claude Code took too long to respond. Try again or /new to start fresh."
        )

    finally:
        typing_task.cancel()
