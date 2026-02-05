"""Inline keyboard builder for follow-up action buttons."""

from __future__ import annotations

import secrets

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from tg_assistant.services.action_router import FollowUpAction


def generate_callback_id() -> str:
    """Generate a short, URL-safe callback ID (8 chars)."""
    return secrets.token_urlsafe(6)


def build_follow_up_keyboard(
    actions: list[FollowUpAction],
) -> tuple[InlineKeyboardMarkup, list[tuple[str, FollowUpAction]]]:
    """Build an inline keyboard from follow-up actions.

    Returns:
        (keyboard, callback_pairs) where callback_pairs is a list of
        (callback_id, action) tuples to be stored in the database.
    """
    if not actions:
        return InlineKeyboardMarkup([]), []

    buttons: list[InlineKeyboardButton] = []
    callback_pairs: list[tuple[str, FollowUpAction]] = []

    for action in actions:
        cb_id = generate_callback_id()
        buttons.append(InlineKeyboardButton(text=action.label, callback_data=cb_id))
        callback_pairs.append((cb_id, action))

    # Arrange buttons: up to 2 per row
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i : i + 2])

    return InlineKeyboardMarkup(rows), callback_pairs
