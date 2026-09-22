"""Tests del flujo del bot. Sin red: Telegram y el LLM están falseados."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import Conflict
from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler

from family_expense_bot.bot import mensajes
from family_expense_bot.bot.handlers import (
    Servicios,
    _botones,
    build_application,
    obtener_identidad,
    registrar_gasto,
    reportar_error,
    responder_confirmacion,
    start,
)
from family_expense_bot.extraction.categorias import cargar_taxonomia
from family_expense_bot.extraction.gemini import CuotaAgotada
from family_expense_bot.storage.repository import RepositorioGastos, crear_engine

AUTORIZADO = 111
DESCONOCIDO = 999

RESPUESTA_OK = (
    '{"monto": "15", "categoria": "transporte", "fecha": "2026-03-14",'
    ' "descripcion": "taxi", "confianza_monto": "alta",'
    ' "confianza_categoria": "alta", "confianza_fecha": "alta"}'
)
RESPUESTA_SIN_MONTO = (
    '{"monto": null, "categoria": "salud", "fecha": null,'
    ' "descripcion": "consulta", "confianza_monto": "baja",'
    ' "confianza_categoria": "alta", "confianza_fecha": "baja"}'
)


class ClienteFalso:
    def __init__(self, respuesta: str = RESPUESTA_OK) -> None:
        self.respuesta = respuesta

    def completar_json(self, prompt: str, schema: dict[str, object]) -> str:
        return self.respuesta


class ClienteLento:
    """Simula la llamada de 84 segundos que medimos contra Gemini."""

    def completar_json(self, prompt: str, schema: dict[str, object]) -> str:
        import time

        time.sleep(5)
        return RESPUESTA_OK


class ClienteSinCuota:
    def completar_json(self, prompt: str, schema: dict[str, object]) -> str:
        raise CuotaAgotada("429")


class ClienteQueExplota:
    def completar_json(self, prompt: str, schema: dict[str, object]) -> str:
        raise RuntimeError("cuota agotada")


@pytest.fixture
def repo() -> RepositorioGastos:
    return RepositorioGastos(crear_engine("sqlite://"))


@pytest.fixture
def servicios(repo: RepositorioGastos) -> Servicios:
    return Servicios(
        repositorio=repo,
        cliente=ClienteFalso(),
        taxonomia=cargar_taxonomia(),
        ids_autorizados=frozenset({AUTORIZADO}),
    )


def _contexto(servicios: Servicios) -> MagicMock:
    return MagicMock(bot_data={"servicios": servicios}, user_data={})


def _update(texto: str, user_id: int = AUTORIZADO) -> MagicMock:
    espera = SimpleNamespace(edit_text=AsyncMock())
    mensaje = SimpleNamespace(text=texto, reply_text=AsyncMock(return_value=espera))
    update = MagicMock(effective_message=mensaje, effective_user=SimpleNamespace(id=user_id))
    update.espera = espera
    return update


def _callback(data: str, user_id: int = AUTORIZADO) -> MagicMock:
    consulta = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock())
    return MagicMock(callback_query=consulta, effective_user=SimpleNamespace(id=user_id))


# --- el principio no negociable -------------------------------------------


async def test_extraer_no_guarda_nada_hasta_que_la_persona_confirme(
    servicios: Servicios, repo: RepositorioGastos
) -> None:
    await registrar_gasto(_update("15 de taxi ayer"), _contexto(servicios))
    assert repo.listar() == [], "Se guardó un gasto sin confirmación"


async def test_solo_al_confirmar_se_guarda(servicios: Servicios, repo: RepositorioGastos) -> None:
    contexto = _contexto(servicios)
    await registrar_gasto(_update("15 de taxi ayer"), contexto)

    token = next(iter(contexto.user_data["pendientes"]))
    await responder_confirmacion(_callback(f"guardar:{token}"), contexto)

    guardados = repo.listar()
    assert len(guardados) == 1
    assert guardados[0].monto == Decimal("15")
    assert guardados[0].fecha == date(2026, 3, 14)
    assert guardados[0].registrado_por == AUTORIZADO


async def test_rechazar_descarta_y_no_guarda(servicios: Servicios, repo: RepositorioGastos) -> None:
    contexto = _contexto(servicios)
    await registrar_gasto(_update("15 de taxi ayer"), contexto)

    token = next(iter(contexto.user_data["pendientes"]))
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
    await registrar_gasto(_update("15 de taxi ayer"), contexto)
    token = next(iter(contexto.user_data["pendientes"]))

    await responder_confirmacion(_callback(f"guardar:{token}"), contexto)
    await responder_confirmacion(_callback(f"guardar:{token}"), contexto)

    assert len(repo.listar()) == 1


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


# --- fallas del LLM -------------------------------------------------------


async def test_avisa_mientras_procesa(servicios: Servicios) -> None:
    update = _update("15 de taxi ayer")
    await registrar_gasto(update, _contexto(servicios))
    update.effective_message.reply_text.assert_awaited_once_with(mensajes.PROCESANDO)


async def test_si_el_llm_demora_demasiado_corta_y_avisa(repo: RepositorioGastos) -> None:
    servicios = Servicios(
        repositorio=repo,
        cliente=ClienteLento(),
        taxonomia=cargar_taxonomia(),
        ids_autorizados=frozenset({AUTORIZADO}),
        timeout_s=0.1,
    )
    update = _update("15 de taxi ayer")
    await registrar_gasto(update, _contexto(servicios))

    update.espera.edit_text.assert_awaited_once_with(mensajes.demoro_demasiado())
    assert repo.listar() == []


async def test_si_el_llm_falla_el_bot_sigue_vivo(repo: RepositorioGastos) -> None:
    servicios = Servicios(
        repositorio=repo,
        cliente=ClienteQueExplota(),
        taxonomia=cargar_taxonomia(),
        ids_autorizados=frozenset({AUTORIZADO}),
    )
    update = _update("15 de taxi ayer")
    await registrar_gasto(update, _contexto(servicios))

    update.espera.edit_text.assert_awaited_once_with(mensajes.error_de_extraccion())
    assert repo.listar() == []


async def test_si_se_agota_la_cuota_lo_dice_claro(repo: RepositorioGastos) -> None:
    """No es lo mismo "no te entendí" que "hoy ya no puedo": la persona actúa distinto."""
    servicios = Servicios(
        repositorio=repo,
        cliente=ClienteSinCuota(),
        taxonomia=cargar_taxonomia(),
        ids_autorizados=frozenset({AUTORIZADO}),
    )
    update = _update("15 de taxi ayer")
    await registrar_gasto(update, _contexto(servicios))

    update.espera.edit_text.assert_awaited_once_with(mensajes.cuota_agotada())
    assert repo.listar() == []


async def test_sin_monto_no_ofrece_guardar(repo: RepositorioGastos) -> None:
    servicios = Servicios(
        repositorio=repo,
        cliente=ClienteFalso(RESPUESTA_SIN_MONTO),
        taxonomia=cargar_taxonomia(),
        ids_autorizados=frozenset({AUTORIZADO}),
    )
    contexto = _contexto(servicios)
    update = _update("fui al doctor")
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


def test_build_application_registra_los_tres_handlers(servicios: Servicios) -> None:
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


def test_los_botones_caben_en_el_limite_de_telegram() -> None:
    """callback_data no puede pasar de 64 bytes."""
    for fila in _botones("a" * 32).inline_keyboard:
        for boton in fila:
            assert boton.callback_data is not None
            assert len(str(boton.callback_data).encode()) <= 64


def test_sin_allowlist_no_se_autoriza_a_nadie(repo: RepositorioGastos) -> None:
    """La falla segura: sin allowlist configurada, el bot no le responde a nadie."""
    servicios = Servicios(repositorio=repo, cliente=ClienteFalso(), taxonomia=cargar_taxonomia())
    assert servicios.ids_autorizados == frozenset()


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
