"""Entry point: python -m tg_assistant."""

from tg_assistant.bot import create_application
from tg_assistant.config import Config
from tg_assistant.utils.logging import setup_logging


def main() -> None:
    setup_logging()
    config = Config.from_env()
    app = create_application(config)
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "edited_message"],
    )


if __name__ == "__main__":
    main()
