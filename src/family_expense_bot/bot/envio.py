"""Mandar un mensaje al chat desde fuera del bot (la Mini App). Solo I/O."""

import asyncio
import logging

from telegram import Bot

log = logging.getLogger(__name__)


def enviar(token: str, chat_id: int, texto: str) -> None:
    """Síncrono a propósito: la app lo corre en una tarea de fondo, después de responder.

    Si falla, solo queda en el log: el gasto ya se guardó y la app ya lo mostró.
    """

    async def _enviar() -> None:
        async with Bot(token) as bot:
            await bot.send_message(chat_id=chat_id, text=texto)

    try:
        asyncio.run(_enviar())
    except Exception:
        log.exception("No se pudo mandar la alerta al chat")
