"""Shared handler utilities: send prompt to Claude and reply in Telegram."""

from __future__ import annotations

import asyncio
import logging

from telegram import Bot, Update
from telegram.ext import ContextTypes

from tg_assistant.models.database import Database
from tg_assistant.services.claude_cli import ClaudeCliError, ClaudeResponse
from tg_assistant.services.message_formatter import format_for_telegram, split_message
from tg_assistant.services.session_manager import QueueFullError, SessionManager
from tg_assistant.utils.typing_indicator import keep_typing

logger = logging.getLogger(__name__)


async def _send_response_to_chat(
    bot: Bot,
    chat_id: int,
    db: Database,
    user_id: int,
    response: ClaudeResponse,
    msg_type: str,
) -> None:
    """Format and send a Claude response as Telegram messages."""
    chunks = split_message(response.text)
    if not chunks:
        chunks = ["(empty response)"]
    for chunk in chunks:
        formatted, parse_mode = format_for_telegram(chunk)
        try:
            sent = await bot.send_message(chat_id=chat_id, text=formatted, parse_mode=parse_mode)
        except Exception:
            logger.warning("Failed to send formatted message", exc_info=True)
            sent = await bot.send_message(chat_id=chat_id, text=chunk)

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

    If Claude is already busy processing a request for this user, the message
    is queued and acknowledged immediately. Queued messages are processed
    automatically after the current task finishes.
    """
    assert update.effective_message and update.effective_chat

    session_mgr: SessionManager = context.bot_data["session_manager"]
    db: Database = context.bot_data["db"]
    chat_id = update.effective_chat.id

    # If Claude is busy, queue the message and acknowledge immediately
    if session_mgr.is_busy(user_id):
        try:
            position = session_mgr.queue_message(user_id, prompt)
        except QueueFullError:
            await update.effective_message.reply_text(
                "Too many queued messages. Please wait for the current task to finish."
            )
            return

        if log_input:
            await db.log_message(
                telegram_id=user_id,
                direction="in",
                content=prompt,
                msg_type=msg_type,
                telegram_message_id=update.effective_message.message_id,
                file_path=file_path,
            )

        if position == 1:
            text = "Got it. Claude is busy \u2014 will process this next."
        else:
            text = f"Queued (#{position}). Will process after the current task."
        await update.effective_message.reply_text(text)
        return

    # Not busy — log and process this message
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

    async def on_response(response: ClaudeResponse, from_queue: bool) -> None:
        if from_queue:
            await context.bot.send_message(
                chat_id=chat_id, text="\U0001f4e8 Processing queued messages..."
            )
        await _send_response_to_chat(context.bot, chat_id, db, user_id, response, msg_type)

    try:
        await session_mgr.send_message_with_queue(user_id, prompt, on_response=on_response)

    except ClaudeCliError as e:
        logger.error("Claude CLI error for user %d: %s", user_id, e)
        error_msg = str(e)
        if "credit balance" in error_msg.lower() or "billing" in error_msg.lower():
            reply = "Claude usage limit reached. Please try again later."
        else:
            reply = "Claude Code error. Please try again or use /new to start a fresh session."
        await update.effective_message.reply_text(reply)

    except asyncio.TimeoutError:
        await update.effective_message.reply_text(
            "Claude Code took too long to respond. Try again or /new to start fresh."
        )

    finally:
        typing_task.cancel()
