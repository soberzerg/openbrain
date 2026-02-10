"""Universal message handler: text, photos, documents, forwarded → Claude Code CLI."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from tg_assistant.handlers._helpers import send_claude_response
from tg_assistant.models.database import Database
from tg_assistant.services.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process any non-voice message through Claude Code CLI.

    Handles: text, photos (caption only), documents (caption only), forwarded messages.
    Extracts forwarded metadata and adds it as context.
    """
    assert update.effective_message and update.effective_user and update.effective_chat

    db: Database = context.bot_data["db"]
    rate_limiter: RateLimiter = context.bot_data["rate_limiter"]

    user_id = update.effective_user.id
    msg = update.effective_message

    if not await rate_limiter.check(user_id):
        await msg.reply_text("Too many messages. Please wait a moment.")
        return

    # Update user record
    await db.upsert_user(
        telegram_id=user_id,
        username=update.effective_user.username,
        first_name=update.effective_user.first_name,
        last_name=update.effective_user.last_name or "",
    )

    # Extract text from message or caption
    text = msg.text or msg.caption or ""

    # Add forwarded message metadata if present
    if msg.forward_from or msg.forward_sender_name or msg.forward_date:
        parts = []

        # Sender info
        if msg.forward_sender_name:
            parts.append(f"From: {msg.forward_sender_name}")
        elif msg.forward_from:
            full_name = msg.forward_from.full_name
            parts.append(f"From: {full_name}")

        # Forward date
        if msg.forward_date:
            parts.append(f"Date: {msg.forward_date.isoformat()}")

        # Separate metadata from content
        parts.append("")
        parts.append(text)
        text = "\n".join(parts)

    await send_claude_response(update, context, user_id, text, msg_type="text")
