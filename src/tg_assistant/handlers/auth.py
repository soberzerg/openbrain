"""Authorization handler for unauthorized users."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def handle_unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Respond to unauthorized users and log the attempt."""
    user = update.effective_user
    if user:
        logger.warning("Unauthorized access: user_id=%d username=%s", user.id, user.username)
    if update.effective_message:
        await update.effective_message.reply_text("Access denied. This bot is private.")
