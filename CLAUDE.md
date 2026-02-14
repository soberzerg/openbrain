# TG Claude Code Assistant

Telegram bot that bridges messages to Claude Code CLI. Direct mobile interface to Claude agent.

## Tech Stack

- Python 3.10+, python-telegram-bot v22 (async API)
- Claude Code CLI (`claude -p --output-format json`) as the AI engine
- SQLite (aiosqlite) for sessions and message logging
- OpenAI Whisper API for voice transcription
- Git sync for Obsidian vault (optional)

## Project Structure

- `src/tg_assistant/` — main package
  - `config.py` — settings from `.env`
  - `bot.py` — Application setup, handler registration
  - `handlers/` — Telegram message handlers
    - `text.py` — universal handler for text, photos, documents, forwarded messages
    - `voice.py` — voice and video note transcription
    - `commands.py` — /start, /help, /new, /status, /tasks, /inbox, /daily, /week
    - `_helpers.py` — shared `send_claude_response()` utility
  - `services/` — business logic
    - `claude_cli.py` — Claude CLI subprocess wrapper
    - `session_manager.py` — session lifecycle and locking
    - `transcription.py` — Whisper API integration
    - `rate_limiter.py` — per-user rate limiting
    - `message_formatter.py` — Markdown → HTML conversion
  - `models/database.py` — SQLite schema (users, sessions, messages)
  - `utils/` — logging, typing indicator
- `tests/` — pytest tests (50 tests)
- `deploy/` — systemd service file

## Running

```bash
python -m tg_assistant
```

## Architecture

**Direct bridge:** All messages go straight to Claude, no routing or classification.

**Message flow:**
- Text, photos (caption only), documents (caption only), forwarded → `handle_text()` → Claude
- Voice/video notes → download → transcribe → show 🎤 text → Claude

**Key services:**
- SessionManager: per-user `asyncio.Lock` prevents concurrent CLI calls
- TranscriptionService: OpenAI Whisper API (optional, requires `OPENAI_API_KEY`)
- RateLimiter: sliding window per user
- Database: SQLite with users, sessions, messages tables

## Key Patterns

- All handlers are async. Shared services (session_manager, db) live in `context.bot_data`.
- Per-user `asyncio.Lock` in SessionManager prevents concurrent Claude CLI calls for the same user.
- HTML parse mode for Telegram responses with plain text fallback.
- Sessions expire after 30 min inactivity (lazy check on next message).
- Voice transcriptions shown to user with 🎤 prefix before sending to Claude.
