# OpenBrain

Mobile interface to [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) through Telegram. Turn your phone into a full-featured AI agent with access to MCP servers, skills, and your knowledge base.

## Features

- **Telegram Bridge** — Chat with Claude Code directly from Telegram
- **Session Management** — Persistent conversations with automatic 30-min timeout
- **Media Support** — Send photos and documents for AI analysis
- **Git Sync** — Automatic synchronization with Obsidian vault or any git-based knowledge base
- **Long Responses** — Auto-split for messages exceeding Telegram's 4096-char limit
- **SQLite Storage** — Session and message history tracking
- **Rate Limiting** — Built-in protection against API abuse

## Architecture

```
Telegram User → Telegram Bot API → OpenBrain → Claude Code CLI
                                                     ↓
                                          MCP Servers, File System, Skills
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
| `GIT_AUTO_SYNC` | `true` | Auto git pull/push before/after sessions |
| `RATE_LIMIT_PER_MINUTE` | `10` | Max messages per user per minute |

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

Send any text message to chat with Claude Code. Send photos or documents for AI analysis.

## Project Structure

```
openbrain/
├── src/tg_assistant/
│   ├── config.py              # Settings from .env
│   ├── bot.py                 # Application setup, handler registration
│   ├── handlers/
│   │   ├── auth.py            # User authorization (whitelist)
│   │   ├── commands.py        # Bot commands
│   │   ├── text.py            # Text message handling
│   │   ├── media.py           # Photo/document handling
│   │   └── errors.py          # Error handling
│   ├── services/
│   │   ├── claude_cli.py      # Claude Code CLI wrapper
│   │   ├── session_manager.py # Session lifecycle
│   │   ├── git_sync.py        # Git synchronization
│   │   └── message_formatter.py # Message formatting
│   ├── models/
│   │   └── database.py        # SQLite schema and queries
│   └── utils/
│       ├── logging.py         # Logging setup
│       └── typing_indicator.py
├── tests/                     # 34 pytest tests
├── deploy/                    # systemd service file
├── .env.example               # Environment template
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
