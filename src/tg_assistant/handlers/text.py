"""Text message handler: send user text to Claude Code CLI."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from tg_assistant.handlers._helpers import send_claude_response
from tg_assistant.models.database import Database
from tg_assistant.services.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process a text message through Claude Code CLI."""
    assert update.effective_message and update.effective_user and update.effective_chat

    db: Database = context.bot_data["db"]
    rate_limiter: RateLimiter = context.bot_data["rate_limiter"]

    user_id = update.effective_user.id
    message_text = update.effective_message.text or ""

    if not await rate_limiter.check(user_id):
        await update.effective_message.reply_text(
            "Too many messages. Please wait a moment."
        )
        return

    # Update user record
    await db.upsert_user(
        telegram_id=user_id,
        username=update.effective_user.username,
        first_name=update.effective_user.first_name,
        last_name=update.effective_user.last_name or "",
    )

    await send_claude_response(
        update, context, user_id, message_text, msg_type="text"
    )
