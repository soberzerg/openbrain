"""Shared handler utilities: send prompt to Claude and reply in Telegram."""

from __future__ import annotations

import asyncio
import html
import logging

from telegram import Bot, Update
from telegram.ext import ContextTypes

from tg_assistant.models.database import Database
from tg_assistant.services.agent_manager import AgentManager
from tg_assistant.services.claude_cli import ClaudeCliError, ClaudeResponse
from tg_assistant.services.message_formatter import format_for_telegram, split_message
from tg_assistant.services.session_manager import (
    DEFAULT_AGENT,
    AgentContext,
    QueueFullError,
    SessionManager,
)
from tg_assistant.utils.typing_indicator import keep_typing

logger = logging.getLogger(__name__)


async def resolve_agent(
    context: ContextTypes.DEFAULT_TYPE, user_id: int
) -> AgentContext:
    """Resolve the active agent for a user into an AgentContext."""
    agent_mgr: AgentManager | None = context.bot_data.get("agent_manager")
    if not agent_mgr:
        return DEFAULT_AGENT

    agent = await agent_mgr.get_user_agent(user_id)
    return AgentContext(
        agent_id=agent["id"],
        agent_name=agent["name"],
        working_dir=agent["working_dir"],
    )


async def _send_response_to_chat(
    bot: Bot,
    chat_id: int,
    db: Database,
    user_id: int,
    response: ClaudeResponse,
    msg_type: str,
    *,
    agent_label: str | None = None,
) -> None:
    """Format and send a Claude response as Telegram messages."""
    chunks = split_message(response.text)
    if not chunks:
        chunks = ["(empty response)"]

    for i, chunk in enumerate(chunks):
        # Prepend agent label to the first chunk if multi-session
        if agent_label and i == 0:
            chunk = f"[{agent_label}]\n{chunk}"

        formatted, parse_mode = format_for_telegram(chunk)
        try:
            sent = await bot.send_message(
                chat_id=chat_id, text=formatted, parse_mode=parse_mode
            )
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
    agent: AgentContext | None = None,
) -> None:
    """
    Send a prompt to Claude via SessionManager and reply with the formatted response.

    If Claude is already busy processing a request for this user/agent, the message
    is queued and acknowledged immediately. Queued messages are processed
    automatically after the current task finishes.
    """
    assert update.effective_message and update.effective_chat

    session_mgr: SessionManager = context.bot_data["session_manager"]
    db: Database = context.bot_data["db"]
    chat_id = update.effective_chat.id

    # Resolve agent if not provided
    if agent is None:
        agent = await resolve_agent(context, user_id)

    key = (user_id, agent.agent_id)

    # If this agent is busy, queue the message
    if session_mgr.is_busy(key):
        try:
            position = session_mgr.queue_message(key, prompt)
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

        agent_prefix = (
            f" [{html.escape(agent.agent_name)}]"
            if session_mgr.get_active_session_count(user_id) > 1
            else ""
        )
        if position == 1:
            text = f"Got it{agent_prefix}. Claude is busy \u2014 will process this next."
        else:
            text = (
                f"Queued{agent_prefix} (#{position}). "
                "Will process after the current task."
            )
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
        # Compute label at response time (session count may change during async work)
        live_count = session_mgr.get_active_session_count(user_id)
        label = agent.agent_name if live_count > 1 else None
        await _send_response_to_chat(
            context.bot,
            chat_id,
            db,
            user_id,
            response,
            msg_type,
            agent_label=label,
        )

    try:
        await session_mgr.send_message_with_queue(
            user_id, prompt, agent=agent, on_response=on_response
        )

    except ClaudeCliError as e:
        logger.error("Claude CLI error for user %d: %s", user_id, e)
        error_msg = str(e)
        if "credit balance" in error_msg.lower() or "billing" in error_msg.lower():
            reply = "Claude usage limit reached. Please try again later."
        else:
            reply = (
                "Claude Code error. Please try again or use /new to start a fresh session."
            )
        await update.effective_message.reply_text(reply)

    except asyncio.TimeoutError:
        await update.effective_message.reply_text(
            "Claude Code took too long to respond. Try again or /new to start fresh."
        )

    finally:
        typing_task.cancel()
