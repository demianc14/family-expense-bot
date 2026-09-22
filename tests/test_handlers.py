from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from telegram.ext import CommandHandler, MessageHandler

from family_expense_bot.bot.handlers import build_application, echo, start


def _update(text: str | None) -> MagicMock:
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    return MagicMock(effective_message=message)


async def test_echo_repite_el_texto() -> None:
    update = _update("200 dólares mercado")
    await echo(update, MagicMock())
    update.effective_message.reply_text.assert_awaited_once_with("200 dólares mercado")


async def test_echo_ignora_mensajes_sin_texto() -> None:
    update = _update(None)
    await echo(update, MagicMock())
    update.effective_message.reply_text.assert_not_awaited()


async def test_start_saluda() -> None:
    update = _update("/start")
    await start(update, MagicMock())
    update.effective_message.reply_text.assert_awaited_once()


def test_build_application_registra_handlers() -> None:
    app = build_application("123:fake-token")
    handlers = app.handlers[0]
    assert any(isinstance(h, CommandHandler) for h in handlers)
    assert any(isinstance(h, MessageHandler) for h in handlers)
