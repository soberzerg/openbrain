"""Forwarded message handler: extract metadata and route via Claude."""

from __future__ import annotations

import logging
import time

from telegram import MessageOriginChannel, MessageOriginChat, MessageOriginUser, Update
from telegram.ext import ContextTypes

from tg_assistant.config import Config
from tg_assistant.handlers._helpers import send_claude_response
from tg_assistant.models.database import Database
from tg_assistant.services.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


def _extract_forward_meta(update: Update) -> dict[str, str]:
    """Extract sender name, date, and source from forward_origin."""
    msg = update.effective_message
    assert msg

    meta: dict[str, str] = {
        "sender": "Unknown",
        "date": "",
        "source": "private chat",
    }

    origin = msg.forward_origin
    if origin is None:
        return meta

    if origin.date:
        meta["date"] = origin.date.strftime("%Y-%m-%d %H:%M")

    if isinstance(origin, MessageOriginUser):
        user = origin.sender_user
        meta["sender"] = user.full_name
    elif isinstance(origin, MessageOriginChat):
        meta["sender"] = origin.sender_chat.title or "Chat"
        meta["source"] = origin.sender_chat.title or "group chat"
    elif isinstance(origin, MessageOriginChannel):
        meta["sender"] = origin.chat.title or "Channel"
        meta["source"] = origin.chat.title or "channel"
    # MessageOriginHiddenUser — sender stays "Unknown"

    return meta


def _build_forward_prompt(meta: dict[str, str], content: str) -> str:
    """Build a structured prompt for Claude to process forwarded content."""
    lines = [
        "[Forwarded message]",
        f"From: {meta['sender']}",
    ]
    if meta["date"]:
        lines.append(f"Date: {meta['date']}")
    lines.append(f"Source: {meta['source']}")
    lines.append("")
    lines.append(content)
    lines.append("")
    lines.append(
        "Process this forwarded content: determine if it should be saved as a note, "
        "created as a task, or just discussed. Ask me if unclear."
    )
    return "\n".join(lines)


async def handle_forward(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process a forwarded message with smart routing via Claude."""
    assert update.effective_message and update.effective_user and update.effective_chat

    config: Config = context.bot_data["config"]
    db: Database = context.bot_data["db"]
    rate_limiter: RateLimiter = context.bot_data["rate_limiter"]

    user_id = update.effective_user.id
    msg = update.effective_message

    if not await rate_limiter.check(user_id):
        await msg.reply_text("Too many messages. Please wait a moment.")
        return

    meta = _extract_forward_meta(update)

    # Collect content — text, caption, or description of media
    content_parts: list[str] = []

    if msg.text:
        content_parts.append(msg.text)
    elif msg.caption:
        content_parts.append(msg.caption)

    # Handle forwarded media
    file_path: str | None = None
    if msg.photo:
        photo = msg.photo[-1]
        tg_file = await context.bot.get_file(photo.file_id)
        filename = f"{user_id}_{int(time.time())}_{photo.file_unique_id}.jpg"
        filepath = config.upload_dir / filename
        await tg_file.download_to_drive(str(filepath))
        file_path = str(filepath)
        content_parts.append(f"[Image saved at: {filepath}]")
        logger.info("Forwarded photo saved: %s", filepath)

    elif msg.document:
        doc = msg.document
        if not doc.file_size or doc.file_size <= 20 * 1024 * 1024:
            tg_file = await context.bot.get_file(doc.file_id)
            filename = f"{user_id}_{int(time.time())}_{doc.file_name or 'file'}"
            filepath = config.upload_dir / filename
            await tg_file.download_to_drive(str(filepath))
            file_path = str(filepath)
            content_parts.append(f"[File saved at: {filepath}]")
            logger.info("Forwarded document saved: %s", filepath)

    if not content_parts:
        content_parts.append("[No text content in forwarded message]")

    content = "\n".join(content_parts)
    prompt = _build_forward_prompt(meta, content)

    await db.log_message(
        telegram_id=user_id,
        direction="in",
        content=content,
        msg_type="forward",
        telegram_message_id=msg.message_id,
        file_path=file_path,
    )

    await send_claude_response(
        update, context, user_id, prompt,
        msg_type="forward", log_input=False, file_path=file_path,
    )
