"""Tests del flujo del bot. Sin red: Telegram y el LLM están falseados."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import Engine
from telegram.error import Conflict
from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler

from family_expense_bot.bot import mensajes
from family_expense_bot.bot.handlers import (
    Pendiente,
    Servicios,
    _botones,
    build_application,
    obtener_identidad,
    registrar_gasto,
    reportar_error,
    reporte,
    responder_confirmacion,
    start,
)
from family_expense_bot.extraction.categorias import cargar_taxonomia
from family_expense_bot.extraction.gemini import CuotaAgotada
from family_expense_bot.extraction.parser import ClienteLLM
from family_expense_bot.extraction.schema import Confianza
from family_expense_bot.storage.repository import (
    RepositorioExtracciones,
    RepositorioGastos,
    crear_engine,
)

TAXONOMIA = cargar_taxonomia()

MAMA = 111
DEMIAN = 222
DESCONOCIDO = 999
USUARIOS = {MAMA: "mama", DEMIAN: "demian"}

_INICIO = datetime(2000, 1, 1, tzinfo=UTC)
_FIN = datetime(2100, 1, 1, tzinfo=UTC)


def _respuesta(**campos: Any) -> str:
    base: dict[str, Any] = {
        "monto": "15",
        "categoria": "transporte",
        "fecha": "2026-03-14",
        "quien_paga": None,
        "descripcion": "taxi",
        "confianza_monto": "alta",
        "confianza_categoria": "alta",
        "confianza_fecha": "alta",
        "confianza_quien_paga": "alta",
    }
    return json.dumps(base | campos)


RESPUESTA_OK = _respuesta()
"""No dice de quién es: queda del remitente, con confianza media."""


class ClienteFalso:
    def __init__(self, respuesta: str = RESPUESTA_OK) -> None:
        self.respuesta = respuesta

    def completar_json(self, prompt: str, schema: dict[str, object]) -> str:
        return self.respuesta


class ClienteLento:
    """Simula una llamada que no vuelve a tiempo."""

    def completar_json(self, prompt: str, schema: dict[str, object]) -> str:
        import time

        time.sleep(5)
        return RESPUESTA_OK


class ClienteSinCuota:
    def completar_json(self, prompt: str, schema: dict[str, object]) -> str:
        raise CuotaAgotada("429")


class ClienteQueExplota:
    def completar_json(self, prompt: str, schema: dict[str, object]) -> str:
        raise RuntimeError("algo raro")


@pytest.fixture
def engine() -> Engine:
    return crear_engine("sqlite://")


@pytest.fixture
def repo(engine: Engine) -> RepositorioGastos:
    return RepositorioGastos(engine)


@pytest.fixture
def extracciones(engine: Engine) -> RepositorioExtracciones:
    return RepositorioExtracciones(engine)


def _servicios_con(engine: Engine, cliente: ClienteLLM | None = None, **kwargs: Any) -> Servicios:
    return Servicios(
        repositorio=RepositorioGastos(engine),
        extracciones=RepositorioExtracciones(engine),
        cliente=cliente or ClienteFalso(),
        taxonomia=TAXONOMIA,
        usuarios=USUARIOS,
        **kwargs,
    )


@pytest.fixture
def servicios(engine: Engine) -> Servicios:
    return _servicios_con(engine)


def _contexto(servicios: Servicios) -> MagicMock:
    return MagicMock(bot_data={"servicios": servicios}, user_data={})


def _update(texto: str, user_id: int = MAMA) -> MagicMock:
    espera = SimpleNamespace(edit_text=AsyncMock())
    mensaje = SimpleNamespace(text=texto, reply_text=AsyncMock(return_value=espera))
    update = MagicMock(effective_message=mensaje, effective_user=SimpleNamespace(id=user_id))
    update.espera = espera
    return update


def _callback(data: str, user_id: int = MAMA) -> MagicMock:
    consulta = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock())
    return MagicMock(callback_query=consulta, effective_user=SimpleNamespace(id=user_id))


async def _extraer_pendiente(contexto: MagicMock, texto: str = "15 de taxi ayer") -> str:
    """Manda un mensaje y devuelve el token del gasto que queda esperando el sí."""
    await registrar_gasto(_update(texto), contexto)
    return str(next(iter(contexto.user_data["pendientes"])))


# --- el principio no negociable -------------------------------------------


async def test_extraer_no_guarda_nada_hasta_que_la_persona_confirme(
    servicios: Servicios, repo: RepositorioGastos
) -> None:
    await registrar_gasto(_update("15 de taxi ayer"), _contexto(servicios))
    assert repo.listar() == [], "Se guardó un gasto sin confirmación"


async def test_solo_al_confirmar_se_guarda(servicios: Servicios, repo: RepositorioGastos) -> None:
    contexto = _contexto(servicios)
    token = await _extraer_pendiente(contexto)
    await responder_confirmacion(_callback(f"guardar:{token}"), contexto)

    guardados = repo.listar()
    assert len(guardados) == 1
    assert guardados[0].monto == Decimal("15")
    assert guardados[0].fecha == date(2026, 3, 14)
    assert guardados[0].registrado_por == MAMA


async def test_rechazar_descarta_y_no_guarda(servicios: Servicios, repo: RepositorioGastos) -> None:
    contexto = _contexto(servicios)
    token = await _extraer_pendiente(contexto)
    callback = _callback(f"descartar:{token}")
    await responder_confirmacion(callback, contexto)

    assert repo.listar() == []
    callback.callback_query.edit_message_text.assert_awaited_once_with(mensajes.descartado())
    assert contexto.user_data["pendientes"] == {}


async def test_no_se_puede_confirmar_dos_veces(
    servicios: Servicios, repo: RepositorioGastos
) -> None:
    """El botón queda en el chat: tocarlo de nuevo no debe duplicar el gasto."""
    contexto = _contexto(servicios)
    token = await _extraer_pendiente(contexto)

    await responder_confirmacion(_callback(f"guardar:{token}"), contexto)
    await responder_confirmacion(_callback(f"guardar:{token}"), contexto)

    assert len(repo.listar()) == 1


# --- atribución -----------------------------------------------------------


async def test_por_defecto_el_gasto_es_de_quien_escribe(
    servicios: Servicios, repo: RepositorioGastos
) -> None:
    contexto = _contexto(servicios)
    await registrar_gasto(_update("15 de taxi ayer", user_id=DEMIAN), contexto)
    token = next(iter(contexto.user_data["pendientes"]))
    await responder_confirmacion(_callback(f"guardar:{token}", user_id=DEMIAN), contexto)

    assert repo.listar()[0].atribuido_a == "demian"


async def test_la_atribucion_asumida_se_muestra_como_duda(servicios: Servicios) -> None:
    update = _update("15 de taxi ayer")
    await registrar_gasto(update, _contexto(servicios))

    texto = update.espera.edit_text.await_args.args[0]
    assert f"De: Mamá {mensajes.DUDOSO}" in texto


async def test_el_boton_cambia_la_atribucion_sin_guardar(
    servicios: Servicios, repo: RepositorioGastos
) -> None:
    """Corregir y confirmar son dos pasos: tocar "Casa" no guarda nada todavía."""
    contexto = _contexto(servicios)
    token = await _extraer_pendiente(contexto)

    callback = _callback(f"atrib:{token}:casa")
    await responder_confirmacion(callback, contexto)

    assert repo.listar() == []
    pendiente: Pendiente = contexto.user_data["pendientes"][token]
    assert pendiente.gasto.atribuido_a == "casa"
    texto = callback.callback_query.edit_message_text.await_args.args[0]
    assert f"De: Casa {mensajes.CORREGIDO}" in texto


async def test_confirmar_tras_corregir_guarda_lo_corregido_y_lo_marca(
    servicios: Servicios, repo: RepositorioGastos, extracciones: RepositorioExtracciones
) -> None:
    contexto = _contexto(servicios)
    token = await _extraer_pendiente(contexto)
    await responder_confirmacion(_callback(f"atrib:{token}:casa"), contexto)
    await responder_confirmacion(_callback(f"guardar:{token}"), contexto)

    guardado = repo.listar()[0]
    assert guardado.atribuido_a == "casa"
    assert guardado.corregido_manualmente is True

    registrada = extracciones.listar(_INICIO, _FIN)[0]
    assert registrada.corregida is True
    # La métrica mide lo que extrajo el LLM, no lo que dejó la persona.
    assert registrada.confianza_atribuido_a == Confianza.MEDIA


async def test_confirmar_sin_tocar_nada_no_cuenta_como_correccion(
    servicios: Servicios, repo: RepositorioGastos, extracciones: RepositorioExtracciones
) -> None:
    contexto = _contexto(servicios)
    token = await _extraer_pendiente(contexto)
    await responder_confirmacion(_callback(f"guardar:{token}"), contexto)

    assert repo.listar()[0].corregido_manualmente is False
    assert extracciones.listar(_INICIO, _FIN)[0].corregida is False


async def test_un_boton_de_atribucion_con_valor_desconocido_se_ignora(
    servicios: Servicios, repo: RepositorioGastos
) -> None:
    contexto = _contexto(servicios)
    token = await _extraer_pendiente(contexto)
    await responder_confirmacion(_callback(f"atrib:{token}:tia"), contexto)

    pendiente: Pendiente = contexto.user_data["pendientes"][token]
    assert pendiente.gasto.atribuido_a == "mama"
    assert not pendiente.corregidos


def test_los_botones_ofrecen_las_demas_atribuciones(servicios: Servicios) -> None:
    from family_expense_bot.extraction.parser import extraer

    gasto = extraer("15 de taxi", ClienteFalso(), TAXONOMIA, "mama", date(2026, 3, 15))
    teclado = _botones("t" * 32, Pendiente(gasto=gasto, original=gasto), TAXONOMIA)
    ofrecidas = {
        str(b.callback_data).rsplit(":", 1)[1] for fila in teclado.inline_keyboard[1:] for b in fila
    }
    assert ofrecidas == set(TAXONOMIA.nombres_atribucion) - {"mama"}


def test_los_botones_caben_en_el_limite_de_telegram() -> None:
    """callback_data no puede pasar de 64 bytes."""
    from family_expense_bot.extraction.parser import extraer

    gasto = extraer("15 de taxi", ClienteFalso(), TAXONOMIA, "mama", date(2026, 3, 15))
    teclado = _botones("a" * 32, Pendiente(gasto=gasto, original=gasto), TAXONOMIA)
    for fila in teclado.inline_keyboard:
        for boton in fila:
            assert boton.callback_data is not None
            assert len(str(boton.callback_data).encode()) <= 64


# --- autorización ---------------------------------------------------------


async def test_ignora_en_silencio_a_quien_no_esta_autorizado(servicios: Servicios) -> None:
    update = _update("15 de taxi", user_id=DESCONOCIDO)
    await registrar_gasto(update, _contexto(servicios))
    update.effective_message.reply_text.assert_not_awaited()


async def test_loguea_el_id_desconocido_para_poder_agregarlo(
    servicios: Servicios, caplog: pytest.LogCaptureFixture
) -> None:
    await registrar_gasto(_update("15 de taxi", user_id=DESCONOCIDO), _contexto(servicios))
    assert str(DESCONOCIDO) in caplog.text


async def test_start_solo_responde_a_los_autorizados(servicios: Servicios) -> None:
    autorizado = _update("/start")
    await start(autorizado, _contexto(servicios))
    autorizado.effective_message.reply_text.assert_awaited_once_with(mensajes.BIENVENIDA)

    ajeno = _update("/start", user_id=DESCONOCIDO)
    await start(ajeno, _contexto(servicios))
    ajeno.effective_message.reply_text.assert_not_awaited()


def test_sin_usuarios_no_se_autoriza_a_nadie(engine: Engine) -> None:
    """La falla segura: sin usuarios configurados, el bot no le responde a nadie."""
    servicios = Servicios(
        repositorio=RepositorioGastos(engine),
        extracciones=RepositorioExtracciones(engine),
        cliente=ClienteFalso(),
        taxonomia=TAXONOMIA,
    )
    assert dict(servicios.usuarios) == {}


@pytest.mark.parametrize("rol", ["casa", "tia", "Papá"])
def test_un_rol_invalido_falla_al_arrancar(engine: Engine, rol: str) -> None:
    """Mejor no arrancar que descubrirlo cuando esa persona escriba por primera vez."""
    with pytest.raises(ValueError, match="TELEGRAM_USUARIOS"):
        Servicios(
            repositorio=RepositorioGastos(engine),
            extracciones=RepositorioExtracciones(engine),
            cliente=ClienteFalso(),
            taxonomia=TAXONOMIA,
            usuarios={1: rol},
        )


# --- fallas del LLM -------------------------------------------------------


async def test_avisa_mientras_procesa(servicios: Servicios) -> None:
    update = _update("15 de taxi ayer")
    await registrar_gasto(update, _contexto(servicios))
    update.effective_message.reply_text.assert_awaited_once_with(mensajes.PROCESANDO)


async def test_si_el_llm_demora_demasiado_corta_y_avisa(
    engine: Engine, repo: RepositorioGastos
) -> None:
    servicios = _servicios_con(engine, ClienteLento(), timeout_s=0.1)
    update = _update("15 de taxi ayer")
    await registrar_gasto(update, _contexto(servicios))

    update.espera.edit_text.assert_awaited_once_with(mensajes.demoro_demasiado())
    assert repo.listar() == []


async def test_si_el_llm_falla_el_bot_sigue_vivo(engine: Engine, repo: RepositorioGastos) -> None:
    update = _update("15 de taxi ayer")
    await registrar_gasto(update, _contexto(_servicios_con(engine, ClienteQueExplota())))

    update.espera.edit_text.assert_awaited_once_with(mensajes.error_de_extraccion())
    assert repo.listar() == []


async def test_si_se_agota_la_cuota_lo_dice_claro(engine: Engine, repo: RepositorioGastos) -> None:
    """No es lo mismo "no te entendí" que "hoy ya no puedo": la persona actúa distinto."""
    update = _update("15 de taxi ayer")
    await registrar_gasto(update, _contexto(_servicios_con(engine, ClienteSinCuota())))

    update.espera.edit_text.assert_awaited_once_with(mensajes.cuota_agotada())
    assert repo.listar() == []


async def test_sin_monto_no_ofrece_guardar(engine: Engine) -> None:
    sin_monto = _respuesta(monto=None, confianza_monto="baja", categoria="mascotas")
    contexto = _contexto(_servicios_con(engine, ClienteFalso(sin_monto)))
    update = _update("fui al veterinario")
    await registrar_gasto(update, contexto)

    texto = update.espera.edit_text.await_args.args[0]
    assert "no puedo guardarlo" in texto
    assert contexto.user_data.get("pendientes", {}) == {}


async def test_confirmar_algo_que_ya_no_existe_no_rompe(servicios: Servicios) -> None:
    """Si el bot se reinicia, los pendientes se pierden: el botón viejo no debe explotar."""
    callback = _callback("guardar:token-inexistente")
    await responder_confirmacion(callback, _contexto(servicios))
    callback.callback_query.edit_message_text.assert_awaited_once_with(mensajes.descartado())


# --- cableado -------------------------------------------------------------


def test_build_application_registra_los_handlers(servicios: Servicios) -> None:
    app = build_application("123:fake-token", servicios)
    tipos = [type(h) for h in app.handlers[0]]
    assert CommandHandler in tipos
    assert CallbackQueryHandler in tipos
    assert MessageHandler in tipos
    assert app.bot_data["servicios"] is servicios


async def test_obtener_identidad_devuelve_username(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = AsyncMock()
    bot.__aenter__.return_value = bot
    bot.get_me.return_value = SimpleNamespace(username="CR_family_bot")
    monkeypatch.setattr("family_expense_bot.bot.handlers.Bot", lambda token: bot)

    assert await obtener_identidad("123:fake-token") == "CR_family_bot"


# --- errores del propio bot -----------------------------------------------


async def test_dos_instancias_se_explican_en_una_linea(
    servicios: Servicios, caplog: pytest.LogCaptureFixture
) -> None:
    """Es el error más fácil de provocar y el que peor se lee sin manejador."""
    contexto = _contexto(servicios)
    contexto.error = Conflict("terminated by other getUpdates request")

    await reportar_error(object(), contexto)

    assert "otra instancia" in caplog.text
    assert "Traceback" not in caplog.text


async def test_un_error_inesperado_si_deja_el_detalle(
    servicios: Servicios, caplog: pytest.LogCaptureFixture
) -> None:
    contexto = _contexto(servicios)
    contexto.error = RuntimeError("algo raro")

    await reportar_error(object(), contexto)

    assert "algo raro" in caplog.text


def test_la_aplicacion_registra_el_manejador_de_errores(servicios: Servicios) -> None:
    app = build_application("123:fake-token", servicios)
    assert reportar_error in app.error_handlers


# --- reporte y métrica de calidad -----------------------------------------


async def test_el_rechazo_queda_registrado_para_la_metrica(
    servicios: Servicios, extracciones: RepositorioExtracciones
) -> None:
    """Si solo contáramos lo guardado, la calidad siempre daría 100%."""
    contexto = _contexto(servicios)
    token = await _extraer_pendiente(contexto)
    await responder_confirmacion(_callback(f"descartar:{token}"), contexto)

    registradas = extracciones.listar(_INICIO, _FIN)
    assert len(registradas) == 1
    assert registradas[0].confirmada is False


async def test_la_confirmacion_tambien_queda_registrada(
    servicios: Servicios, extracciones: RepositorioExtracciones
) -> None:
    contexto = _contexto(servicios)
    token = await _extraer_pendiente(contexto)
    await responder_confirmacion(_callback(f"guardar:{token}"), contexto)

    registradas = extracciones.listar(_INICIO, _FIN)
    assert len(registradas) == 1
    assert registradas[0].confirmada is True


async def test_reporte_responde_con_el_total_del_mes(servicios: Servicios) -> None:
    contexto = _contexto(servicios)
    token = await _extraer_pendiente(contexto)
    await responder_confirmacion(_callback(f"guardar:{token}"), contexto)

    update = _update("/reporte")
    await reporte(update, contexto)

    texto = update.effective_message.reply_text.await_args.args[0]
    assert "Gastos de" in texto


async def test_reporte_no_responde_a_desconocidos(servicios: Servicios) -> None:
    update = _update("/reporte", user_id=DESCONOCIDO)
    await reporte(update, _contexto(servicios))
    update.effective_message.reply_text.assert_not_awaited()


# --- regresiones -------------------------------------------------------------


def _manejador_de(app: Any, update: Any) -> Any:
    """El handler que PTB elegiría para este update (el primero que lo acepta)."""
    for grupo in app.handlers.values():
        for handler in grupo:
            if handler.check_update(update):
                return handler
    return None


def _update_real(edited: bool, texto: str = "200 supermercado") -> Any:
    from telegram import Chat, Message, Update, User

    mensaje = Message(
        1, datetime.now(UTC), Chat(MAMA, "private"), from_user=User(MAMA, "M", False), text=texto
    )
    return Update(1, edited_message=mensaje) if edited else Update(1, message=mensaje)


def test_editar_un_mensaje_no_lo_procesa_como_un_gasto_nuevo(servicios: Servicios) -> None:
    """Regresión: editar un typo generaba otra confirmación y, si se aceptaban
    las dos, un gasto duplicado."""
    app = build_application("123:fake-token", servicios)
    assert _manejador_de(app, _update_real(edited=False)).callback is registrar_gasto
    assert _manejador_de(app, _update_real(edited=True)).callback is not registrar_gasto


async def test_editar_un_mensaje_avisa_que_no_cambia_nada(servicios: Servicios) -> None:
    from family_expense_bot.bot.handlers import avisar_edicion

    update = _update("200 supermercado (editado)")
    await avisar_edicion(update, _contexto(servicios))
    update.effective_message.reply_text.assert_awaited_once_with(mensajes.edicion_ignorada())


async def test_si_guardar_falla_la_persona_se_entera_y_el_intento_cuenta(
    engine: Engine, extracciones: RepositorioExtracciones
) -> None:
    """Regresión: un monto que no entra en la base (o cualquier fallo al guardar)
    dejaba el chat sin respuesta y el intento sin registrar."""
    enorme = "99999999999999999999"
    servicios = _servicios_con(engine, ClienteFalso(_respuesta(monto=enorme)))
    contexto = _contexto(servicios)
    await registrar_gasto(_update(f"{enorme} de algo"), contexto)
    token = str(next(iter(contexto.user_data["pendientes"])))

    callback = _callback(f"guardar:{token}")
    await responder_confirmacion(callback, contexto)

    callback.callback_query.edit_message_text.assert_awaited_once_with(mensajes.error_al_guardar())
    assert servicios.repositorio.listar() == []
    [intento] = extracciones.listar(_INICIO, _FIN)
    assert intento.confirmada is False
