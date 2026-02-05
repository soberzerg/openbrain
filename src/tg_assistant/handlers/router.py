"""Central content router: classify messages, execute default actions, offer follow-ups."""

from __future__ import annotations

import json
import logging
import time

from telegram import Update
from telegram.ext import ContextTypes

from tg_assistant.config import Config
from tg_assistant.handlers._helpers import send_claude_response
from tg_assistant.models.database import Database
from tg_assistant.services.action_router import ActionRouter, FollowUpAction
from tg_assistant.services.content_classifier import ClassificationResult, ContentClassifier, ContentType
from tg_assistant.services.inbox_writer import InboxWriter
from tg_assistant.services.rate_limiter import RateLimiter
from tg_assistant.services.transcription import TranscriptionError, TranscriptionService
from tg_assistant.ui.keyboards import build_follow_up_keyboard

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB


async def handle_routed_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Classify incoming message, execute default action, offer follow-up buttons."""
    assert update.effective_message and update.effective_user and update.effective_chat

    rate_limiter: RateLimiter = context.bot_data["rate_limiter"]
    db: Database = context.bot_data["db"]
    classifier: ContentClassifier = context.bot_data["content_classifier"]
    action_router: ActionRouter = context.bot_data["action_router"]

    user_id = update.effective_user.id
    msg = update.effective_message

    if not rate_limiter.check(user_id):
        await msg.reply_text("Too many messages. Please wait a moment.")
        return

    # Update user record
    await db.upsert_user(
        telegram_id=user_id,
        username=update.effective_user.username,
        first_name=update.effective_user.first_name,
        last_name=update.effective_user.last_name or "",
    )

    # Classify
    classification = classifier.classify(msg)
    route = action_router.resolve(classification)

    logger.info(
        "Routed user=%d type=%s action=%s",
        user_id, classification.content_type.value, route.default_action,
    )

    # Execute based on default action
    if route.default_action == "chat_with_claude":
        await _handle_chat(update, context, user_id, classification)
    elif route.default_action == "transcribe_and_save":
        await _handle_transcribe_and_save(update, context, user_id, classification, route)
    elif route.default_action in ("save_to_inbox", "save_as_note"):
        await _handle_save_to_inbox(update, context, user_id, classification, route)
    elif route.default_action == "create_task":
        await _handle_keyword_action(update, context, user_id, classification, route)
    elif route.default_action == "create_reminder":
        await _handle_keyword_action(update, context, user_id, classification, route)
    else:
        # Unknown action — fall back to Claude chat
        await _handle_chat(update, context, user_id, classification)


async def _handle_chat(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    classification: ClassificationResult,
) -> None:
    """Pass-through to Claude (existing behavior)."""
    text = update.effective_message.text or update.effective_message.caption or ""
    await send_claude_response(update, context, user_id, text, msg_type="text")


async def _handle_save_to_inbox(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    classification: ClassificationResult,
    route: "RouteResult",
) -> None:
    """Save content to Obsidian inbox and offer follow-up buttons."""
    inbox_writer: InboxWriter | None = context.bot_data.get("inbox_writer")
    db: Database = context.bot_data["db"]
    config: Config = context.bot_data["config"]
    msg = update.effective_message

    content_type = classification.content_type
    metadata = classification.metadata.copy()

    # Collect content and download media if needed
    content, file_path = await _collect_content(update, context, classification)

    if not inbox_writer:
        # No Obsidian vault configured — fall back to Claude
        await send_claude_response(
            update, context, user_id, content,
            msg_type=content_type.value, file_path=file_path,
        )
        return

    # Save to inbox
    saved_path = await inbox_writer.save(
        content=content,
        metadata=metadata,
        content_type=content_type.value,
    )

    # Log incoming message
    await db.log_message(
        telegram_id=user_id,
        direction="in",
        content=content,
        msg_type=content_type.value,
        telegram_message_id=msg.message_id,
        file_path=file_path,
    )

    # Build follow-up keyboard
    reply_text = f"✅ Сохранено в inbox: <code>{saved_path.name}</code>"
    await _send_with_follow_ups(
        update, context, user_id, reply_text, classification, route,
        extra_params={"saved_path": str(saved_path), "content": content, "file_path": file_path},
    )


async def _handle_transcribe_and_save(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    classification: ClassificationResult,
    route: "RouteResult",
) -> None:
    """Transcribe voice/video, save to inbox, offer follow-ups."""
    config: Config = context.bot_data["config"]
    db: Database = context.bot_data["db"]
    transcription: TranscriptionService | None = context.bot_data.get("transcription")
    inbox_writer: InboxWriter | None = context.bot_data.get("inbox_writer")
    msg = update.effective_message

    if not transcription:
        await msg.reply_text("Voice messages are not configured. Set OPENAI_API_KEY in .env.")
        return

    # Download voice/video note
    file_path = await _download_voice(update, context)
    if not file_path:
        return

    # Transcribe
    try:
        text = await transcription.transcribe(file_path)
    except TranscriptionError as e:
        logger.error("Transcription failed for user %d: %s", user_id, e)
        await msg.reply_text("Could not transcribe voice message. Please try again.")
        return

    if not text:
        await msg.reply_text("Could not recognize speech in this voice message.")
        return

    # Show transcription
    await msg.reply_text(f"🎤 {text}")

    # Log
    await db.log_message(
        telegram_id=user_id,
        direction="in",
        content=text,
        msg_type="voice",
        telegram_message_id=msg.message_id,
        file_path=file_path,
    )

    # Save to inbox if writer available
    if inbox_writer:
        metadata = {"transcription": text}
        saved_path = await inbox_writer.save(
            content=text,
            metadata=metadata,
            content_type="voice",
        )
        reply_text = f"✅ Транскрипция сохранена: <code>{saved_path.name}</code>"
        await _send_with_follow_ups(
            update, context, user_id, reply_text, classification, route,
            extra_params={"saved_path": str(saved_path), "content": text, "file_path": file_path},
        )
    else:
        # No inbox — send to Claude as before
        prompt = f"[Voice transcription]: {text}"
        await send_claude_response(
            update, context, user_id, prompt,
            msg_type="voice", log_input=False, file_path=file_path,
        )


async def _handle_keyword_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    classification: ClassificationResult,
    route: "RouteResult",
) -> None:
    """Handle text with keyword-detected action (create_task, etc.) — send to Claude."""
    text = classification.metadata.get("text", update.effective_message.text or "")
    action = route.default_action

    prompt_map = {
        "create_task": f"Create a task from this: {text}",
        "create_reminder": f"Create a reminder: {text}",
        "save_to_inbox": f"Save this to inbox: {text}",
    }
    prompt = prompt_map.get(action, text)
    await send_claude_response(update, context, user_id, prompt, msg_type="text")


async def _send_with_follow_ups(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    reply_text: str,
    classification: ClassificationResult,
    route: "RouteResult",
    extra_params: dict | None = None,
) -> None:
    """Send a reply with inline follow-up buttons."""
    db: Database = context.bot_data["db"]
    msg = update.effective_message

    if not route.follow_up_actions:
        try:
            await msg.reply_text(reply_text, parse_mode="HTML")
        except Exception:
            await msg.reply_text(reply_text)
        return

    keyboard, callback_pairs = build_follow_up_keyboard(route.follow_up_actions)

    # Store callbacks in DB
    params = extra_params or {}
    params.update(classification.metadata)

    for cb_id, action in callback_pairs:
        await db.save_callback(
            callback_id=cb_id,
            telegram_id=user_id,
            content_type=classification.content_type.value,
            action_type=action.action_type,
            skill_name=action.skill,
            params=json.dumps({
                "prompt_template": action.prompt_template,
                **params,
            }, ensure_ascii=False),
        )

    full_text = f"{reply_text}\n\nЧто ещё сделать?"
    try:
        await msg.reply_text(full_text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await msg.reply_text(full_text, reply_markup=keyboard)


# -- Callback query handler --------------------------------------------------

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline button presses."""
    query = update.callback_query
    if not query or not query.data:
        return

    await query.answer()  # Acknowledge immediately

    db: Database = context.bot_data["db"]
    callback_id = query.data

    # Look up callback
    cb = await db.get_callback(callback_id)
    if not cb:
        await query.edit_message_text("⏰ Действие устарело. Отправьте контент заново.")
        return

    user_id = cb["telegram_id"]
    action_type = cb["action_type"]
    params = json.loads(cb["params"]) if cb["params"] else {}
    content = params.get("content", "")
    file_path = params.get("file_path")
    url = params.get("url", "")
    prompt_template = params.get("prompt_template", "")

    logger.info(
        "Callback user=%d action=%s type=%s",
        user_id, action_type, cb["content_type"],
    )

    # Build prompt based on action type
    if action_type in ("call_claude", "call_skill"):
        prompt = _build_callback_prompt(action_type, prompt_template, params, cb)
        # Remove buttons from original message
        try:
            original_text = query.message.text or ""
            await query.edit_message_text(f"{original_text}\n\n⏳ Обрабатываю...")
        except Exception:
            pass
        await send_claude_response(
            update, context, user_id, prompt,
            msg_type=cb["content_type"], log_input=True, file_path=file_path,
        )

    elif action_type == "create_task":
        prompt = f"Create a task from this content:\n\n{content or url}"
        try:
            original_text = query.message.text or ""
            await query.edit_message_text(f"{original_text}\n\n⏳ Создаю задачу...")
        except Exception:
            pass
        await send_claude_response(
            update, context, user_id, prompt,
            msg_type=cb["content_type"], log_input=True,
        )

    elif action_type == "save_as_note":
        inbox_writer: InboxWriter | None = context.bot_data.get("inbox_writer")
        if inbox_writer:
            saved = await inbox_writer.save(
                content=content,
                metadata=params,
                content_type=cb["content_type"],
            )
            try:
                await query.edit_message_text(f"📝 Сохранено как заметка: <code>{saved.name}</code>", parse_mode="HTML")
            except Exception:
                await query.edit_message_text(f"📝 Сохранено как заметка: {saved.name}")
        else:
            await query.edit_message_text("Obsidian vault не настроен.")

    elif action_type == "select_project":
        # TODO: Stage 3.2 — show project list
        await query.edit_message_text("🚧 Выбор проекта будет доступен в следующем обновлении.")

    else:
        await query.edit_message_text(f"Неизвестное действие: {action_type}")


def _build_callback_prompt(
    action_type: str,
    prompt_template: str,
    params: dict,
    cb: dict,
) -> str:
    """Build a prompt for Claude from callback parameters."""
    if prompt_template:
        # Substitute placeholders like {url}, {content}, {filename}
        try:
            return prompt_template.format(**params)
        except KeyError:
            return prompt_template

    # Fallback prompts
    skill = cb.get("skill_name", "")
    if action_type == "call_skill" and skill:
        content = params.get("content", params.get("url", ""))
        return f"/{skill} {content}"

    return params.get("content", params.get("url", "Process this content"))


# -- Helpers ------------------------------------------------------------------

async def _collect_content(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    classification: ClassificationResult,
) -> tuple[str, str | None]:
    """Collect text content and download media. Returns (content, file_path)."""
    config: Config = context.bot_data["config"]
    msg = update.effective_message
    user_id = update.effective_user.id
    content_parts: list[str] = []
    file_path: str | None = None

    ct = classification.content_type

    if ct == ContentType.FORWARDED:
        # Use forward metadata
        meta = classification.metadata
        if "sender" in meta:
            content_parts.append(f"From: {meta['sender']}")
        if "date" in meta:
            content_parts.append(f"Date: {meta['date']}")
        if "source" in meta:
            content_parts.append(f"Source: {meta['source']}")
        content_parts.append("")
        text = meta.get("text", msg.text or msg.caption or "")
        content_parts.append(text)

        # Download forwarded media
        if msg.photo:
            file_path = await _download_photo(update, context)
            content_parts.append(f"[Image: {file_path}]")
        elif msg.document:
            file_path = await _download_document(update, context)
            if file_path:
                content_parts.append(f"[File: {file_path}]")

    elif ct == ContentType.PHOTO:
        file_path = await _download_photo(update, context)
        caption = msg.caption or "Image"
        content_parts.append(caption)
        if file_path:
            content_parts.append(f"[Image: {file_path}]")

    elif ct == ContentType.DOCUMENT:
        file_path = await _download_document(update, context)
        caption = msg.caption or f"Document: {classification.metadata.get('filename', 'file')}"
        content_parts.append(caption)

    else:
        # Text-based (URL, keyword, etc.)
        text = msg.text or msg.caption or ""
        content_parts.append(text)

    return "\n".join(content_parts), file_path


async def _download_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """Download the largest photo and return file path."""
    config: Config = context.bot_data["config"]
    msg = update.effective_message
    user_id = update.effective_user.id

    if not msg.photo:
        return None

    photo = msg.photo[-1]
    tg_file = await context.bot.get_file(photo.file_id)
    filename = f"{user_id}_{int(time.time())}_{photo.file_unique_id}.jpg"
    filepath = config.upload_dir / filename
    await tg_file.download_to_drive(str(filepath))
    logger.info("Photo saved: %s", filepath)
    return str(filepath)


async def _download_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """Download a document and return file path."""
    config: Config = context.bot_data["config"]
    msg = update.effective_message
    user_id = update.effective_user.id
    doc = msg.document

    if not doc:
        return None

    if doc.file_size and doc.file_size > MAX_FILE_SIZE:
        await msg.reply_text(f"File too large. Maximum size is {MAX_FILE_SIZE // (1024 * 1024)} MB.")
        return None

    tg_file = await context.bot.get_file(doc.file_id)
    filename = f"{user_id}_{int(time.time())}_{doc.file_name or 'file'}"
    filepath = config.upload_dir / filename
    await tg_file.download_to_drive(str(filepath))
    logger.info("Document saved: %s", filepath)
    return str(filepath)


async def _download_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """Download voice or video note, return file path."""
    config: Config = context.bot_data["config"]
    msg = update.effective_message
    user_id = update.effective_user.id

    if msg.voice:
        tg_file = await context.bot.get_file(msg.voice.file_id)
        filename = f"{user_id}_{int(time.time())}_{msg.voice.file_unique_id}.ogg"
        filepath = config.upload_dir / filename
        await tg_file.download_to_drive(str(filepath))
        logger.info("Voice saved: %s (duration=%ds)", filepath, msg.voice.duration)
        return str(filepath)

    if msg.video_note:
        tg_file = await context.bot.get_file(msg.video_note.file_id)
        filename = f"{user_id}_{int(time.time())}_{msg.video_note.file_unique_id}.mp4"
        filepath = config.upload_dir / filename
        await tg_file.download_to_drive(str(filepath))
        logger.info("Video note saved: %s (duration=%ds)", filepath, msg.video_note.duration)
        return str(filepath)

    return None
