"""Punto de entrada: `family-expense-bot` levanta el bot en modo polling."""

import logging

from family_expense_bot.bot.handlers import build_application
from family_expense_bot.config import Settings


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
    )
    # httpx loguea la URL completa de cada request, que incluye el token del bot.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    settings = Settings()  # valores vienen del entorno
    app = build_application(settings.telegram_bot_token.get_secret_value())
    app.run_polling()


if __name__ == "__main__":
    main()
