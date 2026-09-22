"""I/O de Telegram. Sin lógica de negocio.

El flujo completo: mensaje → parser → mostrar lo entendido → botón sí/no →
solo si dice que sí, se guarda. Nada se persiste antes de ese sí.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from uuid import uuid4

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from family_expense_bot.bot import mensajes
from family_expense_bot.extraction.categorias import Taxonomia
from family_expense_bot.extraction.gemini import CuotaAgotada
from family_expense_bot.extraction.parser import ClienteLLM, extraer
from family_expense_bot.extraction.schema import GastoExtraido
from family_expense_bot.storage.repository import RepositorioGastos

log = logging.getLogger(__name__)

TIMEOUT_EXTRACCION_S = 45.0
"""Con el modelo actual una extracción toma ~3 s. El margen es para el día malo:
el corte lo impone el bot porque el timeout del SDK cuenta por intento, y con
reintentos una llamada puede pasar del minuto."""

GUARDAR = "guardar"
DESCARTAR = "descartar"


@dataclass(frozen=True)
class Servicios:
    """Todo lo que los handlers necesitan, inyectado en vez de importado."""

    repositorio: RepositorioGastos
    cliente: ClienteLLM
    taxonomia: Taxonomia
    ids_autorizados: frozenset[int] = field(default_factory=frozenset)
    timeout_s: float = TIMEOUT_EXTRACCION_S


def _servicios(context: ContextTypes.DEFAULT_TYPE) -> Servicios:
    servicios = context.bot_data.get("servicios")
    assert isinstance(servicios, Servicios), "La aplicación se construyó sin servicios"
    return servicios


def _autorizado(update: Update, servicios: Servicios) -> bool:
    usuario = update.effective_user
    if usuario is None:
        return False
    if usuario.id in servicios.ids_autorizados:
        return True
    # Se ignora en silencio, pero queda en el log: si a alguien de la familia le
    # cambia el ID, este es el único lugar donde se ve cuál agregar.
    log.warning("Mensaje ignorado de un ID no autorizado: %s", usuario.id)
    return False


async def obtener_identidad(token: str) -> str:
    """Devuelve el username del bot. Sirve para validar el token sin levantar polling."""
    async with Bot(token) as bot:
        me = await bot.get_me()
    return me.username or ""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or not _autorizado(update, _servicios(context)):
        return
    await update.effective_message.reply_text(mensajes.BIENVENIDA)


async def registrar_gasto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    mensaje = update.effective_message
    servicios = _servicios(context)
    if mensaje is None or mensaje.text is None or not _autorizado(update, servicios):
        return

    espera = await mensaje.reply_text(mensajes.PROCESANDO)

    try:
        gasto = await asyncio.wait_for(
            asyncio.to_thread(extraer, mensaje.text, servicios.cliente, servicios.taxonomia),
            timeout=servicios.timeout_s,
        )
    except TimeoutError:
        log.warning("La extracción superó %s s", servicios.timeout_s)
        await espera.edit_text(mensajes.demoro_demasiado())
        return
    except CuotaAgotada:
        log.warning("Cuota del LLM agotada")
        await espera.edit_text(mensajes.cuota_agotada())
        return
    except Exception:  # el bot nunca debe morir por una respuesta rara del LLM
        log.exception("Falló la extracción")
        await espera.edit_text(mensajes.error_de_extraccion())
        return

    if gasto.monto is None:
        # Sin monto no hay gasto que guardar; se pide de nuevo.
        await espera.edit_text(mensajes.falta_monto(gasto))
        return

    token = uuid4().hex
    _pendientes(context)[token] = gasto
    await espera.edit_text(mensajes.pedir_confirmacion(gasto), reply_markup=_botones(token))


async def responder_confirmacion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    consulta = update.callback_query
    if consulta is None or consulta.data is None or update.effective_user is None:
        return
    await consulta.answer()

    accion, _, token = consulta.data.partition(":")
    gasto = _pendientes(context).pop(token, None)
    if gasto is None:
        # El bot se reinició o ya se respondió: las extracciones viven en memoria.
        await consulta.edit_message_text(mensajes.descartado())
        return

    if accion == DESCARTAR:
        await consulta.edit_message_text(mensajes.descartado())
        return

    registrado = _servicios(context).repositorio.guardar(
        gasto, registrado_por=update.effective_user.id
    )
    await consulta.edit_message_text(mensajes.guardado(registrado))


def _pendientes(context: ContextTypes.DEFAULT_TYPE) -> dict[str, GastoExtraido]:
    """Extracciones esperando confirmación. En memoria: no se persisten nunca."""
    if context.user_data is None:
        return {}
    return context.user_data.setdefault("pendientes", {})  # type: ignore[no-any-return]


def _botones(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Sí, guardar", callback_data=f"{GUARDAR}:{token}"),
                InlineKeyboardButton("No", callback_data=f"{DESCARTAR}:{token}"),
            ]
        ]
    )


def build_application(token: str, servicios: Servicios | None = None) -> Application:  # type: ignore[type-arg]
    app = Application.builder().token(token).build()
    if servicios is not None:
        app.bot_data["servicios"] = servicios
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(responder_confirmacion))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, registrar_gasto))
    return app
