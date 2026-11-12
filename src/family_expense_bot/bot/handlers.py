"""I/O de Telegram. Sin lógica de negocio.

El flujo completo: mensaje → parser → mostrar lo entendido → botones →
solo si dice que sí, se guarda. Nada se persiste antes de ese sí.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonDefault,
    MenuButtonWebApp,
    Update,
    WebAppInfo,
)
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
from family_expense_bot.dashboard.presupuesto import (
    alerta_tras_gasto,
    estado_tras_gasto,
    evaluar_gasto,
    extras_del_mes,
    resumen_presupuesto,
)
from family_expense_bot.dashboard.report import inicio_y_fin_utc, rango_del_mes, resumen_mensual
from family_expense_bot.extraction.categorias import Taxonomia, validar_roles
from family_expense_bot.extraction.gemini import CuotaAgotada
from family_expense_bot.extraction.parser import ClienteLLM, extraer
from family_expense_bot.extraction.schema import Confianza, GastoExtraido
from family_expense_bot.storage.repository import (
    GastoRegistrado,
    RepositorioExtracciones,
    RepositorioGastos,
    RepositorioIngresos,
)

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

    ingresos: Mapping[str, Decimal] = field(default_factory=lambda: MappingProxyType({}))
    """Rol → ingreso mensual. Solo quien está acá tiene presupuesto."""

    ingresos_extra: RepositorioIngresos | None = None
    """Los ingresos registrados en la Mini App: se suman al fijo en su mes."""

    hoy: Callable[[], date] = date.today
    """Para decidir si un gasto es del mes en curso (las alertas no avisan por otro mes)."""

    def __post_init__(self) -> None:
        # Falla al arrancar, no cuando esa persona escriba por primera vez.
        validar_roles(self.taxonomia, self.usuarios, self.ingresos)


@dataclass(frozen=True)
class Pendiente:
    """Una extracción esperando el sí. Vive en memoria; nunca se persiste así."""

    gasto: GastoExtraido
    """Lo que se guardaría, con las correcciones de la persona ya aplicadas."""

    original: GastoExtraido
    """Lo que extrajo el LLM. De acá salen las confianzas del reporte de calidad."""

    autor: int
    """ID de Telegram de quien lo escribió: el único que puede confirmarlo."""

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
    if remitente is None or update.effective_user is None:
        return

    espera = await mensaje.reply_text(mensajes.PROCESANDO)
    etiquetas = servicios.taxonomia.etiquetas
    gasto = await _extraer_o_avisar(espera, mensaje.text, servicios, remitente)
    if gasto is None:
        return

    if gasto.monto is None:
        # Sin monto no hay gasto que guardar; se pide de nuevo. Cuenta como
        # intento fallido para el reporte de calidad.
        servicios.extracciones.registrar(gasto, confirmada=False)
        await espera.edit_text(mensajes.falta_monto(gasto, etiquetas))
        return

    token = uuid4().hex
    pendiente = Pendiente(gasto=gasto, original=gasto, autor=update.effective_user.id)
    _pendientes(context)[token] = pendiente
    await espera.edit_text(
        mensajes.pedir_confirmacion(gasto, etiquetas, bolsillo=_bolsillo(servicios, gasto)),
        reply_markup=_botones(token, pendiente, servicios.taxonomia),
    )


async def _extraer_o_avisar(
    espera: Any, texto: str, servicios: Servicios, remitente: str
) -> GastoExtraido | None:
    """Extrae con límite de tiempo. Si falla, lo dice en `espera` y devuelve None."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(extraer, texto, servicios.cliente, servicios.taxonomia, remitente),
            timeout=servicios.timeout_s,
        )
    except TimeoutError:
        log.warning("La extracción superó %s s", servicios.timeout_s)
        await espera.edit_text(mensajes.demoro_demasiado())
    except CuotaAgotada:
        log.warning("Cuota del LLM agotada")
        await espera.edit_text(mensajes.cuota_agotada())
    except Exception:  # el bot nunca debe morir por una respuesta rara del LLM
        log.exception("Falló la extracción")
        await espera.edit_text(mensajes.error_de_extraccion())
    return None


async def responder_confirmacion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    consulta = update.callback_query
    if consulta is None or consulta.data is None or update.effective_user is None:
        return
    if _rol(update, _servicios(context)) is None:
        # Misma allowlist que para los mensajes: en un grupo, alguien de afuera
        # no puede confirmar ni descartar el gasto de otro.
        return

    accion, _, resto = consulta.data.partition(":")
    token, _, valor = resto.partition(":")
    pendientes = _pendientes(context)
    pendiente = pendientes.get(token)
    if pendiente is not None and pendiente.autor != update.effective_user.id:
        # En un grupo, otro familiar ve los mismos botones. No toca nada: ni el
        # mensaje ni el pendiente; solo se le avisa a él.
        await consulta.answer(mensajes.solo_quien_lo_escribio(), show_alert=True)
        return
    await consulta.answer()
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
            mensajes.pedir_confirmacion(
                corregido.gasto,
                etiquetas,
                corregido.corregidos,
                bolsillo=_bolsillo(servicios, corregido.gasto),
            ),
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
    linea, alerta = _tras_guardar(servicios, registrado, servicios.hoy())
    await consulta.edit_message_text(mensajes.guardado(registrado, etiquetas, linea))
    if alerta is not None and update.effective_chat is not None:
        # Mensaje aparte, no una línea más del "Guardado": así llega como notificación.
        await context.bot.send_message(update.effective_chat.id, alerta)


def _bolsillo(servicios: Servicios, gasto: GastoExtraido) -> str | None:
    """Etiqueta del bolsillo contra el que cuenta el gasto, si quien lo asume tiene presupuesto."""
    if gasto.atribuido_a not in servicios.ingresos:
        return None
    nombre = servicios.taxonomia.bolsillo_de(gasto.categoria, gasto.texto_original)
    etiquetas = {b.nombre: b.etiqueta for b in servicios.taxonomia.bolsillos}
    return etiquetas.get(nombre or "", "ninguno")


def _extra(servicios: Servicios, persona: str, anio: int, mes: int) -> Decimal:
    """Lo registrado como ingreso extra ese mes. Cero si no hay repositorio."""
    if servicios.ingresos_extra is None:
        return Decimal(0)
    desde, hasta = rango_del_mes(anio, mes)
    return extras_del_mes(servicios.ingresos_extra.listar(desde, hasta), persona, anio, mes)


def _tras_guardar(
    servicios: Servicios, registrado: GastoRegistrado, hoy: date
) -> tuple[str | None, str | None]:
    """La línea del bolsillo para el "Guardado" y, si cruzó un umbral, la alerta.

    Un fallo acá no tapa el "Guardado": sale sin línea y sin alerta.
    """
    ingreso = servicios.ingresos.get(registrado.atribuido_a)
    if ingreso is None:
        return None, None
    try:
        desde, hasta = rango_del_mes(registrado.fecha.year, registrado.fecha.month)
        estado = estado_tras_gasto(
            servicios.repositorio.listar(desde=desde, hasta=hasta),
            servicios.taxonomia,
            registrado,
            ingreso,
            _extra(
                servicios, registrado.atribuido_a, registrado.fecha.year, registrado.fecha.month
            ),
        )
        alerta = alerta_tras_gasto(estado, registrado, servicios.taxonomia.alertas, hoy)
        return (
            mensajes.linea_de_bolsillo(estado, registrado.fecha.month),
            mensajes.alerta_bolsillo(alerta, registrado.fecha.month) if alerta else None,
        )
    except Exception:
        log.exception("No se pudo calcular el presupuesto después de guardar")
        return None, None


async def avisar_edicion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Editar un mensaje no cambia un gasto ya enviado ni crea otro.

    Antes, una edición se procesaba como mensaje nuevo: otra confirmación y, si
    se aceptaban las dos, un gasto duplicado.
    """
    if update.effective_message is None or _rol(update, _servicios(context)) is None:
        return
    await update.effective_message.reply_text(mensajes.edicion_ignorada())


async def presupuesto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cuánto lleva gastado quien escribe en cada bolsillo este mes."""
    servicios = _servicios(context)
    if update.effective_message is None:
        return
    remitente = _rol(update, servicios)
    if remitente is None:
        return
    ingreso = servicios.ingresos.get(remitente)
    if ingreso is None:
        await update.effective_message.reply_text(mensajes.sin_presupuesto())
        return

    hoy = date.today()
    desde, hasta = rango_del_mes(hoy.year, hoy.month)
    resumen = resumen_presupuesto(
        servicios.repositorio.listar(desde=desde, hasta=hasta),
        servicios.taxonomia,
        remitente,
        ingreso,
        hoy.year,
        hoy.month,
        _extra(servicios, remitente, hoy.year, hoy.month),
    )
    await update.effective_message.reply_text(mensajes.presupuesto(resumen))


async def consultar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/puedo 30 concierto: ¿conviene este gasto, según el presupuesto del mes?

    No guarda nada ni cuenta como intento para el reporte de calidad: no es un
    registro. El monto pasa por la misma verificación que un gasto real (tiene
    que estar escrito en el mensaje) y el veredicto lo calcula el código.
    """
    servicios = _servicios(context)
    mensaje = update.effective_message
    if mensaje is None:
        return
    remitente = _rol(update, servicios)
    if remitente is None:
        return
    ingreso = servicios.ingresos.get(remitente)
    if ingreso is None:
        await mensaje.reply_text(mensajes.sin_presupuesto())
        return
    texto = " ".join(context.args or [])
    if not texto.strip():
        await mensaje.reply_text(mensajes.consulta_sin_monto())
        return

    espera = await mensaje.reply_text(mensajes.PROCESANDO)
    gasto = await _extraer_o_avisar(espera, texto, servicios, remitente)
    if gasto is None:
        return
    if gasto.monto is None or gasto.confianza_monto is Confianza.BAJA:
        await espera.edit_text(mensajes.consulta_sin_monto())
        return

    hoy = date.today()
    desde, hasta = rango_del_mes(hoy.year, hoy.month)
    resumen = resumen_presupuesto(
        servicios.repositorio.listar(desde=desde, hasta=hasta),
        servicios.taxonomia,
        remitente,
        ingreso,
        hoy.year,
        hoy.month,
        _extra(servicios, remitente, hoy.year, hoy.month),
    )
    bolsillo = servicios.taxonomia.bolsillo_de(gasto.categoria, gasto.texto_original)
    consulta = evaluar_gasto(resumen, servicios.taxonomia, bolsillo, gasto.monto, hoy)
    await espera.edit_text(
        mensajes.respuesta_consulta(
            consulta, gasto.categoria, gasto.confianza_categoria is not Confianza.ALTA
        )
    )


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
    """Extracciones esperando confirmación. En memoria: no se persisten nunca.

    En `bot_data` y no en `user_data`: python-telegram-bot da un `user_data` por
    persona, así que el clic de otro familiar en un grupo no encontraba el gasto
    y lo daba por descartado. Acá se encuentra, y `Pendiente.autor` decide.
    """
    return context.bot_data.setdefault("pendientes", {})  # type: ignore[no-any-return]


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


def build_application(
    token: str, servicios: Servicios | None = None, app_url: str = ""
) -> Application:  # type: ignore[type-arg]
    async def registrar_menu(app: Application) -> None:  # type: ignore[type-arg]
        await _registrar_menu(app.bot, app_url)
        if app_url:
            # En segundo plano: el sondeo tarda y el polling no tiene por qué esperarlo.
            avisar_a = servicios.usuarios.keys() if servicios else ()
            app.create_task(_verificar_app(app.bot, app_url, avisar_a))

    app = Application.builder().token(token).post_init(registrar_menu).build()
    if servicios is not None:
        app.bot_data["servicios"] = servicios
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reporte", reporte))
    app.add_handler(CommandHandler("presupuesto", presupuesto))
    app.add_handler(CommandHandler("puedo", consultar))
    app.add_handler(CallbackQueryHandler(responder_confirmacion))
    app.add_handler(
        MessageHandler(
            filters.UpdateType.MESSAGE & filters.TEXT & ~filters.COMMAND, registrar_gasto
        )
    )
    app.add_handler(
        MessageHandler(filters.UpdateType.EDITED_MESSAGE & filters.TEXT, avisar_edicion)
    )
    app.add_error_handler(reportar_error)
    return app


TEXTO_MENU = "Mi presupuesto"


async def _registrar_menu(bot: Bot, app_url: str) -> None:
    """El botón de menú del chat abre la Mini App. Sin APP_URL vuelve al menú normal.

    Se registra en cada arranque por si APP_URL cambió. Si falla, el bot sigue:
    la app es un extra, el registro de gastos no depende de ella.
    """
    boton = MenuButtonWebApp(TEXTO_MENU, WebAppInfo(app_url)) if app_url else MenuButtonDefault()
    try:
        await bot.set_chat_menu_button(menu_button=boton)
    except Exception:
        log.exception("No se pudo registrar el botón de la Mini App")


INTENTOS_SONDEO = 3
ESPERA_SONDEO_S = 10.0
"""Con docker compose el bot y la app arrancan juntos: la app puede tardar unos
segundos en responder, y eso no es una caída."""

Sondeo = Callable[[str], Awaitable[None]]


async def _sondear(url: str) -> None:
    """Pide la página como la pediría Telegram, por la URL pública. Lanza si no carga."""
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as cliente:
        (await cliente.get(url)).raise_for_status()


async def _problema_de_app_url(
    app_url: str, sondear: Sondeo = _sondear, espera_s: float = ESPERA_SONDEO_S
) -> str | None:
    """Por qué el botón del menú no va a abrir la app, o None si responde."""
    if (urlsplit(app_url).hostname or "").endswith(".trycloudflare.com"):
        # Responde hoy, pero muere con la próxima siesta de la Mac: ya pasó.
        return "APP_URL es un túnel rápido de cloudflared, que cambia en cada arranque"
    error: Exception | None = None
    for intento in range(INTENTOS_SONDEO):
        if intento:
            await asyncio.sleep(espera_s)
        try:
            await sondear(app_url)
            return None
        except Exception as e:
            error = e
    return f"APP_URL no responde ({type(error).__name__})"


async def _verificar_app(
    bot: Bot,
    app_url: str,
    avisar_a: Iterable[int],
    sondear: Sondeo = _sondear,
    espera_s: float = ESPERA_SONDEO_S,
) -> None:
    """Una app caída no se nota hasta que alguien toca el botón: se avisa al arrancar."""
    problema = await _problema_de_app_url(app_url, sondear, espera_s)
    if problema is None:
        log.info("Mini App verificada: responde en APP_URL")
        return
    log.warning("La Mini App no va a abrir: %s", problema)
    for chat_id in avisar_a:  # solo la allowlist
        try:
            await bot.send_message(chat_id, mensajes.app_no_responde(problema))
        except Exception:
            log.exception("No se pudo avisar que la Mini App no responde")
