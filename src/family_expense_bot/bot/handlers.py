"""I/O de Telegram. Sin lógica de negocio.

El flujo completo: mensaje → parser → mostrar lo entendido → botones →
solo si dice que sí, se guarda. Nada se persiste antes de ese sí.
"""

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from types import MappingProxyType
from uuid import uuid4

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import Conflict
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from family_expense_bot.bot import mensajes
from family_expense_bot.dashboard.report import inicio_y_fin_utc, rango_del_mes, resumen_mensual
from family_expense_bot.extraction.categorias import Taxonomia
from family_expense_bot.extraction.gemini import CuotaAgotada
from family_expense_bot.extraction.parser import ClienteLLM, extraer
from family_expense_bot.extraction.schema import GastoExtraido
from family_expense_bot.storage.repository import RepositorioExtracciones, RepositorioGastos

log = logging.getLogger(__name__)

TIMEOUT_EXTRACCION_S = 45.0
"""Con el modelo actual una extracción toma ~3 s. El margen es para el día malo:
el corte lo impone el bot porque el timeout del SDK cuenta por intento, y con
reintentos una llamada puede pasar del minuto."""

GUARDAR = "guardar"
DESCARTAR = "descartar"
ATRIBUIR = "atrib"
BOTONES_POR_FILA = 3


@dataclass(frozen=True)
class Servicios:
    """Todo lo que los handlers necesitan, inyectado en vez de importado."""

    repositorio: RepositorioGastos
    extracciones: RepositorioExtracciones
    cliente: ClienteLLM
    taxonomia: Taxonomia
    usuarios: Mapping[int, str] = field(default_factory=lambda: MappingProxyType({}))
    """ID de Telegram → rol. Es la allowlist: quien no está acá, no existe."""

    timeout_s: float = TIMEOUT_EXTRACCION_S

    def __post_init__(self) -> None:
        # Falla al arrancar, no cuando esa persona escriba por primera vez.
        invalidos = {r for r in self.usuarios.values() if r not in self.taxonomia.personas}
        if invalidos:
            raise ValueError(
                f"TELEGRAM_USUARIOS tiene roles que no son personas de la taxonomía: "
                f"{sorted(invalidos)}. Válidos: {list(self.taxonomia.personas)} "
                f"({self.taxonomia.compartida.nombre!r} no escribe: es compartido)"
            )


@dataclass(frozen=True)
class Pendiente:
    """Una extracción esperando el sí. Vive en memoria; nunca se persiste así."""

    gasto: GastoExtraido
    """Lo que se guardaría, con las correcciones de la persona ya aplicadas."""

    original: GastoExtraido
    """Lo que extrajo el LLM. De acá salen las confianzas del reporte de calidad."""

    corregidos: frozenset[str] = frozenset()


def _servicios(context: ContextTypes.DEFAULT_TYPE) -> Servicios:
    servicios = context.bot_data.get("servicios")
    assert isinstance(servicios, Servicios), "La aplicación se construyó sin servicios"
    return servicios


def _rol(update: Update, servicios: Servicios) -> str | None:
    """El rol de quien escribe, o None si no está autorizado."""
    usuario = update.effective_user
    if usuario is None:
        return None
    rol = servicios.usuarios.get(usuario.id)
    if rol is None:
        # Se ignora en silencio, pero queda en el log: si a alguien de la familia le
        # cambia el ID, este es el único lugar donde se ve cuál agregar.
        log.warning("Mensaje ignorado de un ID no autorizado: %s", usuario.id)
    return rol


async def obtener_identidad(token: str) -> str:
    """Devuelve el username del bot. Sirve para validar el token sin levantar polling."""
    async with Bot(token) as bot:
        me = await bot.get_me()
    return me.username or ""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or _rol(update, _servicios(context)) is None:
        return
    await update.effective_message.reply_text(mensajes.BIENVENIDA)


async def registrar_gasto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    mensaje = update.effective_message
    servicios = _servicios(context)
    if mensaje is None or mensaje.text is None:
        return
    remitente = _rol(update, servicios)
    if remitente is None:
        return

    espera = await mensaje.reply_text(mensajes.PROCESANDO)
    etiquetas = servicios.taxonomia.etiquetas

    try:
        gasto = await asyncio.wait_for(
            asyncio.to_thread(
                extraer, mensaje.text, servicios.cliente, servicios.taxonomia, remitente
            ),
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
        # Sin monto no hay gasto que guardar; se pide de nuevo. Cuenta como
        # intento fallido para el reporte de calidad.
        servicios.extracciones.registrar(gasto, confirmada=False)
        await espera.edit_text(mensajes.falta_monto(gasto, etiquetas))
        return

    token = uuid4().hex
    pendiente = Pendiente(gasto=gasto, original=gasto)
    _pendientes(context)[token] = pendiente
    await espera.edit_text(
        mensajes.pedir_confirmacion(gasto, etiquetas),
        reply_markup=_botones(token, pendiente, servicios.taxonomia),
    )


async def responder_confirmacion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    consulta = update.callback_query
    if consulta is None or consulta.data is None or update.effective_user is None:
        return
    await consulta.answer()

    accion, _, resto = consulta.data.partition(":")
    token, _, valor = resto.partition(":")
    pendientes = _pendientes(context)
    pendiente = pendientes.get(token)
    if pendiente is None:
        # El bot se reinició o ya se respondió: las extracciones viven en memoria.
        await consulta.edit_message_text(mensajes.descartado())
        return

    servicios = _servicios(context)
    etiquetas = servicios.taxonomia.etiquetas

    if accion == ATRIBUIR:
        if not servicios.taxonomia.contiene_atribucion(valor):
            log.warning("Botón de atribución con un valor desconocido: %r", valor)
            return
        # Corregir no guarda: vuelve a mostrar el resumen y espera el sí.
        corregido = replace(
            pendiente,
            gasto=pendiente.gasto.model_copy(update={"atribuido_a": valor}),
            corregidos=pendiente.corregidos | {"atribuido_a"},
        )
        pendientes[token] = corregido
        await consulta.edit_message_text(
            mensajes.pedir_confirmacion(corregido.gasto, etiquetas, corregido.corregidos),
            reply_markup=_botones(token, corregido, servicios.taxonomia),
        )
        return

    del pendientes[token]
    fue_corregido = bool(pendiente.corregidos)

    if accion == DESCARTAR:
        # Se registra el rechazo: sin esto el reporte de calidad solo vería
        # aciertos y diría 100% siempre.
        servicios.extracciones.registrar(
            pendiente.original, confirmada=False, corregida=fue_corregido
        )
        await consulta.edit_message_text(mensajes.descartado())
        return

    try:
        registrado = servicios.repositorio.guardar(
            pendiente.gasto,
            registrado_por=update.effective_user.id,
            corregido_manualmente=fue_corregido,
        )
    except Exception:
        # La persona dijo que sí y no se guardó: tiene que enterarse, y el intento
        # cuenta como no confirmado (no quedó registrado). Ej.: un monto que no
        # entra en la base.
        log.exception("Falló el guardado de un gasto confirmado")
        servicios.extracciones.registrar(
            pendiente.original, confirmada=False, corregida=fue_corregido
        )
        await consulta.edit_message_text(mensajes.error_al_guardar())
        return
    servicios.extracciones.registrar(pendiente.original, confirmada=True, corregida=fue_corregido)
    await consulta.edit_message_text(mensajes.guardado(registrado, etiquetas))


async def reporte(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Total del mes en curso, por categoría, ahí mismo en el chat."""
    servicios = _servicios(context)
    if update.effective_message is None or _rol(update, servicios) is None:
        return

    hoy = date.today()
    desde, hasta = rango_del_mes(hoy.year, hoy.month)
    inicio, fin = inicio_y_fin_utc(hoy.year, hoy.month)
    resumen = resumen_mensual(
        servicios.repositorio.listar(desde=desde, hasta=hasta),
        servicios.extracciones.listar(inicio, fin),
        hoy.year,
        hoy.month,
    )
    await update.effective_message.reply_text(mensajes.reporte(resumen))


async def reportar_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sin esto, python-telegram-bot vuelca un traceback por cada reintento.

    El caso más común no es un bug del código: son dos instancias del bot
    peleándose por el mismo token, y eso se explica en una línea.
    """
    error = context.error
    if isinstance(error, Conflict):
        log.error(
            "Hay otra instancia de este bot corriendo con el mismo token. "
            "Telegram solo permite una: deja viva una sola y reinicia."
        )
        return
    log.error("Error no manejado en el bot: %s", error, exc_info=error)


def _pendientes(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Pendiente]:
    """Extracciones esperando confirmación. En memoria: no se persisten nunca."""
    if context.user_data is None:
        return {}
    return context.user_data.setdefault("pendientes", {})  # type: ignore[no-any-return]


def _botones(token: str, pendiente: Pendiente, taxonomia: Taxonomia) -> InlineKeyboardMarkup:
    """Sí/No, y debajo, a quién más podría corresponder el gasto (un toque, sin reescribir)."""
    alternativas = [
        InlineKeyboardButton(a.etiqueta, callback_data=f"{ATRIBUIR}:{token}:{a.nombre}")
        for a in taxonomia.atribuciones
        if a.nombre != pendiente.gasto.atribuido_a
    ]
    filas = [
        [
            InlineKeyboardButton("Sí, guardar", callback_data=f"{GUARDAR}:{token}"),
            InlineKeyboardButton("No", callback_data=f"{DESCARTAR}:{token}"),
        ],
        *(
            alternativas[i : i + BOTONES_POR_FILA]
            for i in range(0, len(alternativas), BOTONES_POR_FILA)
        ),
    ]
    return InlineKeyboardMarkup(filas)


def build_application(token: str, servicios: Servicios | None = None) -> Application:  # type: ignore[type-arg]
    app = Application.builder().token(token).build()
    if servicios is not None:
        app.bot_data["servicios"] = servicios
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reporte", reporte))
    app.add_handler(CallbackQueryHandler(responder_confirmacion))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, registrar_gasto))
    app.add_error_handler(reportar_error)
    return app
