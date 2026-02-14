"""Proactive notification scheduling via python-telegram-bot JobQueue."""

from __future__ import annotations

import logging
from datetime import time as dt_time
from zoneinfo import ZoneInfo

from telegram.ext import Application, ContextTypes

from tg_assistant.config import Config
from tg_assistant.services.claude_cli import ClaudeCliError, ClaudeResponse
from tg_assistant.services.message_formatter import format_for_telegram, split_message
from tg_assistant.services.session_manager import SessionManager

logger = logging.getLogger(__name__)

MORNING_DIGEST_PROMPT = "Run my daily review: today's tasks, inbox items, priorities. Brief."

WEEKLY_SUMMARY_PROMPT = (
    "Generate a weekly summary: completed tasks, key notes added, upcoming deadlines."
)


async def _send_notification(
    context: ContextTypes.DEFAULT_TYPE,
    prompt: str,
    label: str,
) -> None:
    """Send a Claude-generated notification to all allowed users."""
    config: Config = context.bot_data["config"]
    session_mgr: SessionManager = context.bot_data["session_manager"]

    for user_id in config.allowed_user_ids:
        try:

            async def _on_response(response: ClaudeResponse, from_queue: bool) -> None:
                chunks = split_message(response.text)
                for chunk in chunks:
                    formatted, parse_mode = format_for_telegram(chunk)
                    try:
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=formatted,
                            parse_mode=parse_mode,
                        )
                    except Exception:
                        await context.bot.send_message(chat_id=user_id, text=chunk)

            await session_mgr.send_message_with_queue(user_id, prompt, on_response=_on_response)
            logger.info("%s sent to user %d", label, user_id)

        except (ClaudeCliError, Exception) as e:
            logger.error("%s failed for user %d: %s", label, user_id, e)


async def _morning_digest_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job callback for morning digest."""
    await _send_notification(context, MORNING_DIGEST_PROMPT, "Morning digest")


async def _weekly_summary_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job callback for weekly summary."""
    await _send_notification(context, WEEKLY_SUMMARY_PROMPT, "Weekly summary")


def setup_jobs(application: Application) -> None:
    """Schedule recurring notification jobs if enabled."""
    config: Config = application.bot_data["config"]

    if not config.notifications_enabled:
        logger.info("Notifications disabled")
        return

    job_queue = application.job_queue
    if job_queue is None:
        logger.warning("JobQueue not available — notifications disabled")
        return

    tz = ZoneInfo(config.timezone)

    # Morning digest — daily
    job_queue.run_daily(
        _morning_digest_job,
        time=dt_time(hour=config.morning_digest_hour, tzinfo=tz),
        name="morning_digest",
    )
    logger.info(
        "Morning digest scheduled at %02d:00 %s",
        config.morning_digest_hour,
        config.timezone,
    )

    # Weekly summary — specific day of week
    job_queue.run_daily(
        _weekly_summary_job,
        time=dt_time(hour=config.weekly_summary_hour, tzinfo=tz),
        days=(config.weekly_summary_day,),
        name="weekly_summary",
    )
    logger.info(
        "Weekly summary scheduled: day=%d at %02d:00 %s",
        config.weekly_summary_day,
        config.weekly_summary_hour,
        config.timezone,
    )
