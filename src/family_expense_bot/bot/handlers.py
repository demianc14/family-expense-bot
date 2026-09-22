"""I/O de Telegram. Sin lógica de negocio: recibir, responder, nada más.

Fase 0: solo eco, para validar que el bot está conectado.
"""

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    await update.effective_message.reply_text(
        "Hola 👋 Por ahora solo repito lo que escribes (modo prueba)."
    )


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or message.text is None:
        return
    await message.reply_text(message.text)


def build_application(token: str) -> Application:  # type: ignore[type-arg]
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    return app
