"""Photo and document handlers: download, save, pass to Claude Code."""

from __future__ import annotations

import logging
import time

from telegram import Update
from telegram.ext import ContextTypes

from tg_assistant.config import Config
from tg_assistant.handlers._helpers import send_claude_response
from tg_assistant.models.database import Database
from tg_assistant.services.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Download photo and send to Claude for analysis."""
    assert update.effective_message and update.effective_user and update.effective_chat

    config: Config = context.bot_data["config"]
    db: Database = context.bot_data["db"]
    rate_limiter: RateLimiter = context.bot_data["rate_limiter"]

    user_id = update.effective_user.id

    if not rate_limiter.check(user_id):
        await update.effective_message.reply_text(
            "Too many messages. Please wait a moment."
        )
        return

    photos = update.effective_message.photo
    if not photos:
        return

    photo = photos[-1]
    tg_file = await context.bot.get_file(photo.file_id)

    filename = f"{user_id}_{int(time.time())}_{photo.file_unique_id}.jpg"
    filepath = config.upload_dir / filename
    await tg_file.download_to_drive(str(filepath))

    logger.info("Photo saved: %s", filepath)

    caption = update.effective_message.caption or "Analyze this image"
    prompt = f"{caption}\n\n[Image saved at: {filepath}]"

    await db.log_message(
        telegram_id=user_id,
        direction="in",
        content=caption,
        msg_type="photo",
        telegram_message_id=update.effective_message.message_id,
        file_path=str(filepath),
    )

    await send_claude_response(
        update, context, user_id, prompt,
        msg_type="photo", log_input=False, file_path=str(filepath),
    )


async def handle_document(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Download document and pass to Claude for processing."""
    assert update.effective_message and update.effective_user and update.effective_chat

    config: Config = context.bot_data["config"]
    db: Database = context.bot_data["db"]
    rate_limiter: RateLimiter = context.bot_data["rate_limiter"]

    user_id = update.effective_user.id
    document = update.effective_message.document

    if not document:
        return

    if not rate_limiter.check(user_id):
        await update.effective_message.reply_text(
            "Too many messages. Please wait a moment."
        )
        return

    if document.file_size and document.file_size > MAX_FILE_SIZE:
        await update.effective_message.reply_text(
            f"File too large. Maximum size is {MAX_FILE_SIZE // (1024 * 1024)} MB."
        )
        return

    tg_file = await context.bot.get_file(document.file_id)
    filename = f"{user_id}_{int(time.time())}_{document.file_name or 'file'}"
    filepath = config.upload_dir / filename
    await tg_file.download_to_drive(str(filepath))

    logger.info("Document saved: %s", filepath)

    caption = update.effective_message.caption or f"Process this file: {document.file_name}"
    prompt = f"{caption}\n\n[File saved at: {filepath}]"

    await db.log_message(
        telegram_id=user_id,
        direction="in",
        content=caption,
        msg_type="document",
        telegram_message_id=update.effective_message.message_id,
        file_path=str(filepath),
    )

    await send_claude_response(
        update, context, user_id, prompt,
        msg_type="document", log_input=False, file_path=str(filepath),
    )
