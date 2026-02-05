"""Voice message handler: transcribe and send to Claude Code."""

from __future__ import annotations

import logging
import time

from telegram import Update
from telegram.ext import ContextTypes

from tg_assistant.config import Config
from tg_assistant.handlers._helpers import send_claude_response
from tg_assistant.models.database import Database
from tg_assistant.services.rate_limiter import RateLimiter
from tg_assistant.services.transcription import TranscriptionError, TranscriptionService

logger = logging.getLogger(__name__)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Transcribe voice message and send text to Claude."""
    assert update.effective_message and update.effective_user and update.effective_chat

    config: Config = context.bot_data["config"]
    db: Database = context.bot_data["db"]
    rate_limiter: RateLimiter = context.bot_data["rate_limiter"]
    transcription: TranscriptionService | None = context.bot_data.get("transcription")

    user_id = update.effective_user.id
    voice = update.effective_message.voice

    if not voice:
        return

    if not rate_limiter.check(user_id):
        await update.effective_message.reply_text(
            "Too many messages. Please wait a moment."
        )
        return

    if not transcription:
        await update.effective_message.reply_text(
            "Voice messages are not configured. Set OPENAI_API_KEY in .env."
        )
        return

    # Download voice file
    tg_file = await context.bot.get_file(voice.file_id)
    filename = f"{user_id}_{int(time.time())}_{voice.file_unique_id}.ogg"
    filepath = config.upload_dir / filename
    await tg_file.download_to_drive(str(filepath))

    logger.info("Voice saved: %s (duration=%ds)", filepath, voice.duration)

    # Transcribe
    try:
        text = await transcription.transcribe(filepath)
    except TranscriptionError as e:
        logger.error("Transcription failed for user %d: %s", user_id, e)
        await update.effective_message.reply_text(
            "Could not transcribe voice message. Please try again."
        )
        return

    if not text:
        await update.effective_message.reply_text(
            "Could not recognize speech in this voice message."
        )
        return

    # Show transcription to user
    await update.effective_message.reply_text(f"🎤 {text}")

    # Log incoming voice
    await db.log_message(
        telegram_id=user_id,
        direction="in",
        content=text,
        msg_type="voice",
        telegram_message_id=update.effective_message.message_id,
        file_path=str(filepath),
    )

    # Send transcription to Claude
    prompt = f"[Voice transcription]: {text}"
    await send_claude_response(
        update, context, user_id, prompt,
        msg_type="voice", log_input=False, file_path=str(filepath),
    )


async def handle_video_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Transcribe video note (round video) and send to Claude."""
    assert update.effective_message and update.effective_user and update.effective_chat

    config: Config = context.bot_data["config"]
    db: Database = context.bot_data["db"]
    rate_limiter: RateLimiter = context.bot_data["rate_limiter"]
    transcription: TranscriptionService | None = context.bot_data.get("transcription")

    user_id = update.effective_user.id
    video_note = update.effective_message.video_note

    if not video_note:
        return

    if not rate_limiter.check(user_id):
        await update.effective_message.reply_text(
            "Too many messages. Please wait a moment."
        )
        return

    if not transcription:
        await update.effective_message.reply_text(
            "Voice messages are not configured. Set OPENAI_API_KEY in .env."
        )
        return

    tg_file = await context.bot.get_file(video_note.file_id)
    filename = f"{user_id}_{int(time.time())}_{video_note.file_unique_id}.mp4"
    filepath = config.upload_dir / filename
    await tg_file.download_to_drive(str(filepath))

    logger.info("Video note saved: %s (duration=%ds)", filepath, video_note.duration)

    try:
        text = await transcription.transcribe(filepath)
    except TranscriptionError as e:
        logger.error("Transcription failed for user %d: %s", user_id, e)
        await update.effective_message.reply_text(
            "Could not transcribe video note. Please try again."
        )
        return

    if not text:
        await update.effective_message.reply_text(
            "Could not recognize speech in this video note."
        )
        return

    await update.effective_message.reply_text(f"🎤 {text}")

    await db.log_message(
        telegram_id=user_id,
        direction="in",
        content=text,
        msg_type="voice",
        telegram_message_id=update.effective_message.message_id,
        file_path=str(filepath),
    )

    prompt = f"[Voice transcription]: {text}"
    await send_claude_response(
        update, context, user_id, prompt,
        msg_type="voice", log_input=False, file_path=str(filepath),
    )
