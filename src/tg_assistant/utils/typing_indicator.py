"""Persistent typing indicator for long-running operations."""

from __future__ import annotations

import asyncio
import logging

from telegram import Bot
from telegram.constants import ChatAction

logger = logging.getLogger(__name__)


async def keep_typing(bot: Bot, chat_id: int, interval: float = 4.0) -> None:
    """
    Send typing action repeatedly until cancelled.

    Telegram's typing indicator lasts ~5 seconds. This function resends it
    every `interval` seconds to keep it active during long Claude CLI calls.

    Usage:
        task = asyncio.create_task(keep_typing(bot, chat_id))
        try:
            result = await long_operation()
        finally:
            task.cancel()
    """
    try:
        while True:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        pass
