# OpenBrain

Mobile interface to [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) through Telegram. Turn your phone into a full-featured AI agent with access to MCP servers, skills, and your knowledge base.

## Features

- **Content Routing** — Auto-classify incoming content (URLs, forwarded messages, voice, photos, documents) and save to Obsidian inbox with follow-up action buttons
- **Telegram Bridge** — Chat with Claude Code directly from Telegram
- **Voice Messages** — Transcribe voice and video notes via OpenAI Whisper, save to inbox
- **Media Support** — Send photos and documents for AI analysis or inbox storage
- **Inline Actions** — Follow-up buttons after saving (summarize, create task, brainstorm)
- **Session Management** — Persistent conversations with automatic 30-min timeout
- **Git Sync** — Automatic synchronization with Obsidian vault or any git-based knowledge base
- **Shortcut Commands** — Quick access to tasks, inbox, daily review, weekly summary
- **Proactive Notifications** — Scheduled morning digest and weekly summary
- **Long Responses** — Auto-split for messages exceeding Telegram's 4096-char limit
- **SQLite Storage** — Session, message, and callback history tracking
- **Rate Limiting** — Built-in protection against API abuse

## Architecture

```
Telegram User
     |
     v
Telegram Bot API
     |
     v
[Content Classifier] -- regex + message type detection
     |
     v
[Action Router] -- YAML config: type -> action + follow-up buttons
     |
     +---> save_to_inbox (direct file write to Obsidian vault)
     +---> transcribe_and_save (Whisper API -> inbox)
     +---> chat_with_claude (Claude Code CLI)
     +---> create_task / create_reminder (keyword actions -> Claude)
     |
     v
[Response + Inline Keyboard] -- follow-up action buttons
     |
     v
[Callback Handler] -- button press -> Claude skill / direct action
```

## Prerequisites

- Python 3.10+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed
- Telegram Bot Token (from [@BotFather](https://t.me/botfather))

## Installation

```bash
git clone https://github.com/soberzerg/openbrain.git
cd openbrain

python3 -m venv .venv
source .venv/bin/activate

pip install -e .
```

For development:

```bash
pip install -e ".[dev]"
```

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Required settings:

| Variable | Description |
| --- | --- |
| `BOT_TOKEN` | Telegram bot token from @BotFather |
| `ALLOWED_USER_IDS` | Comma-separated Telegram user IDs (get yours from [@userinfobot](https://t.me/userinfobot)) |
| `CLAUDE_CLI_PATH` | Path to Claude Code CLI executable |
| `CLAUDE_WORKING_DIR` | Directory where Claude Code will execute commands |

Optional settings:

| Variable | Default | Description |
| --- | --- | --- |
| `CLAUDE_TIMEOUT_SECONDS` | `120` | Max wait time for Claude response |
| `SESSION_TIMEOUT_MINUTES` | `30` | Session inactivity timeout |
| `OBSIDIAN_VAULT_PATH` | — | Path to git-synced knowledge base |
| `OBSIDIAN_INBOX_SUBDIR` | `00_Inbox` | Subdirectory for inbox notes |
| `GIT_AUTO_SYNC` | `true` | Auto git pull/push before/after sessions |
| `RATE_LIMIT_PER_MINUTE` | `10` | Max messages per user per minute |
| `OPENAI_API_KEY` | — | OpenAI API key for Whisper transcription |
| `WHISPER_MODEL` | `whisper-1` | Whisper model ID |
| `WHISPER_BASE_URL` | `https://api.openai.com/v1` | Whisper API endpoint |
| `ROUTING_CONFIG_PATH` | `config/routing.yaml` | Content routing rules |
| `NOTIFICATIONS_ENABLED` | `false` | Enable scheduled notifications |
| `MORNING_DIGEST_HOUR` | `9` | Hour for daily digest (24h) |
| `WEEKLY_SUMMARY_DAY` | `0` | Day of week (0=Monday) |
| `WEEKLY_SUMMARY_HOUR` | `10` | Hour for weekly summary |
| `TZ` | `UTC` | Timezone for scheduling |

### Content Routing

Routing rules are defined in `config/routing.yaml`. The bot classifies incoming messages and executes a default action based on content type:

| Content | Detection | Default Action |
| --- | --- | --- |
| YouTube URL | regex | Save to inbox |
| GitHub URL | regex | Save to inbox |
| Article URL | URL (excl. YouTube/GitHub/Telegram) | Save to inbox |
| Forwarded message | `forward_origin` | Save to inbox |
| Voice / video note | `message.voice` / `video_note` | Transcribe + save |
| Photo | `message.photo` | Save to inbox |
| Document | `message.document` | Save to inbox |
| Text with keywords | regex ("task", "save", "remind") | Route by keyword |
| Plain text | fallback | Chat with Claude |

After saving, the bot offers follow-up actions via inline buttons (configurable in YAML).

## Usage

```bash
python -m tg_assistant
```

### Commands

| Command | Description |
| --- | --- |
| `/start` | Welcome message |
| `/help` | Show help |
| `/new` | Start a new session (clear context) |
| `/status` | Current session info |
| `/tasks` | Today's tasks |
| `/inbox` | Inbox status |
| `/daily` | Run daily review |
| `/week` | Weekly summary |

Send any text message to chat with Claude Code. Send photos, documents, voice messages, or URLs for automatic classification and inbox storage.

## Project Structure

```
openbrain/
├── src/tg_assistant/
│   ├── config.py                  # Settings from .env
│   ├── bot.py                     # Application setup, handler registration
│   ├── handlers/
│   │   ├── auth.py                # User authorization (whitelist)
│   │   ├── commands.py            # Bot commands (/start, /tasks, etc.)
│   │   ├── router.py              # Unified content router + callback handler
│   │   ├── _helpers.py            # Shared handler utilities
│   │   └── errors.py              # Error handling
│   ├── services/
│   │   ├── claude_cli.py          # Claude Code CLI wrapper
│   │   ├── session_manager.py     # Session lifecycle
│   │   ├── content_classifier.py  # Content type detection (regex + msg type)
│   │   ├── action_router.py       # Content type -> action mapping
│   │   ├── inbox_writer.py        # Direct file write to Obsidian vault
│   │   ├── transcription.py       # OpenAI Whisper API wrapper
│   │   ├── git_sync.py            # Git synchronization
│   │   ├── message_formatter.py   # Markdown -> Telegram HTML
│   │   ├── rate_limiter.py        # Sliding window rate limiter
│   │   └── scheduler.py           # Proactive notification jobs
│   ├── ui/
│   │   └── keyboards.py           # Inline keyboard builder
│   ├── models/
│   │   └── database.py            # SQLite schema and queries
│   └── utils/
│       ├── logging.py             # Logging setup
│       └── typing_indicator.py
├── config/
│   └── routing.yaml               # Content routing rules (YAML)
├── tests/                         # 88 pytest tests
├── deploy/                        # systemd service file
├── .env.example                   # Environment template
├── pyproject.toml
└── README.md
```

## Deployment

### systemd (Linux)

1. Edit `deploy/openbrain.service` — replace `YOUR_USERNAME` and paths
2. Copy and enable:

```bash
sudo cp deploy/openbrain.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now openbrain
```

3. Check logs:

```bash
sudo journalctl -u openbrain -f
```

## Development

### Tests

```bash
pytest
```

With coverage:

```bash
pytest --cov=tg_assistant --cov-report=html
```

### Linting

```bash
ruff check .
ruff format .
```

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make changes and add tests
4. Run `pytest && ruff check .`
5. Open a Pull Request

## License

[MIT](LICENSE)
