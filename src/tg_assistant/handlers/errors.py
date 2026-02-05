"""Global error handler for unhandled exceptions."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def error_handler(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Global error handler — last line of defense."""
    logger.exception("Unhandled exception", exc_info=context.error)

    # Try to notify the user
    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="An unexpected error occurred. Please try again.",
            )
        except Exception:
            pass  # If we can't even send the error message, just log
