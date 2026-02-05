"""Photo and document handlers: download, save, pass to Claude Code."""

from __future__ import annotations

import asyncio
import logging
import time

from telegram import Update
from telegram.ext import ContextTypes

from tg_assistant.config import Config
from tg_assistant.models.database import Database
from tg_assistant.services.claude_cli import ClaudeCliError
from tg_assistant.services.message_formatter import format_for_telegram, split_message
from tg_assistant.services.session_manager import SessionManager
from tg_assistant.utils.typing_indicator import keep_typing

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Download photo and send to Claude for analysis."""
    assert update.effective_message and update.effective_user and update.effective_chat

    config: Config = context.bot_data["config"]
    session_mgr: SessionManager = context.bot_data["session_manager"]
    db: Database = context.bot_data["db"]

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    # Get the largest photo resolution
    photos = update.effective_message.photo
    if not photos:
        return

    photo = photos[-1]
    tg_file = await context.bot.get_file(photo.file_id)

    # Save to uploads directory
    filename = f"{user_id}_{int(time.time())}_{photo.file_unique_id}.jpg"
    filepath = config.upload_dir / filename
    await tg_file.download_to_drive(str(filepath))

    logger.info("Photo saved: %s", filepath)

    # Build prompt with file reference
    caption = update.effective_message.caption or "Analyze this image"
    prompt = f"{caption}\n\n[Image saved at: {filepath}]"

    # Log incoming
    await db.log_message(
        telegram_id=user_id,
        direction="in",
        content=caption,
        msg_type="photo",
        telegram_message_id=update.effective_message.message_id,
        file_path=str(filepath),
    )

    # Send to Claude
    typing_task = asyncio.create_task(keep_typing(context.bot, chat_id))
    try:
        response = await session_mgr.send_message(user_id, prompt)
        chunks = split_message(response.text)
        for chunk in chunks:
            formatted, parse_mode = format_for_telegram(chunk)
            try:
                await update.effective_message.reply_text(
                    formatted, parse_mode=parse_mode
                )
            except Exception:
                await update.effective_message.reply_text(chunk)

    except (ClaudeCliError, asyncio.TimeoutError) as e:
        logger.error("Error processing photo for user %d: %s", user_id, e)
        await update.effective_message.reply_text(
            "Could not process the image. Try again or /new."
        )
    finally:
        typing_task.cancel()


async def handle_document(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Download document and pass to Claude for processing."""
    assert update.effective_message and update.effective_user and update.effective_chat

    config: Config = context.bot_data["config"]
    session_mgr: SessionManager = context.bot_data["session_manager"]
    db: Database = context.bot_data["db"]

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    document = update.effective_message.document

    if not document:
        return

    # Size check
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

    typing_task = asyncio.create_task(keep_typing(context.bot, chat_id))
    try:
        response = await session_mgr.send_message(user_id, prompt)
        chunks = split_message(response.text)
        for chunk in chunks:
            formatted, parse_mode = format_for_telegram(chunk)
            try:
                await update.effective_message.reply_text(
                    formatted, parse_mode=parse_mode
                )
            except Exception:
                await update.effective_message.reply_text(chunk)

    except (ClaudeCliError, asyncio.TimeoutError) as e:
        logger.error("Error processing document for user %d: %s", user_id, e)
        await update.effective_message.reply_text(
            "Could not process the file. Try again or /new."
        )
    finally:
        typing_task.cancel()
