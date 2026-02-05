# TG Claude Code Assistant

Telegram bot that bridges messages to Claude Code CLI. Mobile interface to a full AI agent with MCP servers, skills, and knowledge base.

## Tech Stack

- Python 3.10+, python-telegram-bot v22 (async API)
- Claude Code CLI (`claude -p --output-format json`) as the AI engine
- SQLite (aiosqlite) for sessions and message logging
- Git sync for Obsidian vault (second-brain)

## Project Structure

- `src/tg_assistant/` — main package
  - `config.py` — settings from `.env`
  - `bot.py` — Application setup, handler registration
  - `handlers/` — Telegram message handlers (text, media, commands, auth, errors)
  - `services/` — business logic (claude_cli, session_manager, git_sync, message_formatter)
  - `models/database.py` — SQLite schema and queries
  - `utils/` — logging, typing indicator
- `tests/` — pytest tests
- `deploy/` — systemd service file

## Running

```bash
python -m tg_assistant
```

## Key Patterns

- All handlers are async. Shared services (session_manager, db) live in `context.bot_data`.
- Per-user `asyncio.Lock` in SessionManager prevents concurrent Claude CLI calls for the same user.
- HTML parse mode for Telegram responses with plain text fallback.
- Sessions expire after 30 min inactivity (lazy check on next message).
