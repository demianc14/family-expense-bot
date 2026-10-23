"""Tests del evaluador. Nunca llaman a un LLM real: el cliente está falseado."""

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from family_expense_bot.evaluacion import (
    Caso,
    Celda,
    agregar_resultado,
    cargar_casos,
    correr,
    guardar_corrida,
    iniciar_corrida,
    leer_corrida,
    resumir,
    tabla_markdown,
)
from family_expense_bot.extraction.categorias import cargar_taxonomia
from family_expense_bot.extraction.gemini import CuotaAgotada, ErrorDelLLM

HOY = date(2026, 3, 15)
TAXONOMIA = cargar_taxonomia()
BANCO = Path(__file__).parent.parent / "evals" / "mensajes.yaml"

BASE_LLM: dict[str, Any] = {
    "monto": None,
    "categoria": None,
    "fecha": None,
    "quien_paga": None,
    "descripcion": "x",
    "confianza_monto": "alta",
    "confianza_categoria": "alta",
    "confianza_fecha": "alta",
    "confianza_quien_paga": "alta",
}


class ClienteGuionado:
    """Devuelve una respuesta por llamada, en orden; una excepción se lanza."""

    def __init__(self, *respuestas: dict[str, Any] | Exception) -> None:
        self.respuestas = list(respuestas)

    def completar_json(self, prompt: str, schema: dict[str, object]) -> str:
        siguiente = self.respuestas.pop(0)
        if isinstance(siguiente, Exception):
            raise siguiente
        return json.dumps(BASE_LLM | siguiente)


def caso(texto: str, **oro: str | None) -> Caso:
    return Caso(id=texto, remitente="demian", texto=texto, oro=oro)


def evaluar(casos: list[Caso], *respuestas: dict[str, Any] | Exception) -> Any:
    resultados = list(correr(casos, ClienteGuionado(*respuestas), TAXONOMIA, HOY))
    return resultados, resumir(casos, resultados)


def test_el_banco_es_consistente_con_la_taxonomia() -> None:
    hoy, casos = cargar_casos(BANCO, TAXONOMIA)
    assert hoy == HOY
    assert len(casos) >= 60


@pytest.mark.parametrize(
    ("respuesta", "celda"),
    [
        ({"monto": "15"}, Celda.ACIERTO_CONFIADO),
        ({"monto": "15", "confianza_monto": "media"}, Celda.ACIERTO_CON_DUDA),
        ({"monto": "16", "confianza_monto": "media"}, Celda.ERROR_ATRAPADO),
        ({"monto": "1.5"}, Celda.ERROR_SILENCIOSO),
    ],
)
def test_cada_celda(respuesta: dict[str, Any], celda: Celda) -> None:
    # El texto trae dos números: un error silencioso solo pasa si el texto respalda
    # el monto equivocado, porque si no, la verificación lo baja a duda.
    c = caso("taxi 15, y 1.5 de propina", monto="15")
    _, resumen = evaluar([c], respuesta)
    assert resumen.celdas["monto"][celda] == 1


def test_el_monto_se_compara_como_numero() -> None:
    _, resumen = evaluar([caso("café 3.50", monto="3.5")], {"monto": "3.50"})
    assert resumen.celdas["monto"][Celda.ACIERTO_CONFIADO] == 1


def test_sin_monto_esperado_y_sin_monto_obtenido_es_acierto() -> None:
    _, resumen = evaluar([caso("fui al super", monto=None)], {"monto": None})
    # Sin monto el campo siempre queda en duda: es lo correcto, no fricción extra.
    assert resumen.celdas["monto"][Celda.ACIERTO_CON_DUDA] == 1


def test_un_campo_sin_oro_no_se_puntua() -> None:
    _, resumen = evaluar([caso("taxi 15", monto="15")], {"monto": "15"})
    assert sum(resumen.celdas["categoria"].values()) == 0


def test_monto_inventado_cuenta_como_rescate_del_codigo() -> None:
    # El LLM afirma 150 con confianza alta; el texto no lo respalda.
    _, resumen = evaluar([caso("taxi 15", monto="15")], {"monto": "150"})
    assert resumen.celdas["monto"][Celda.ERROR_ATRAPADO] == 1
    assert resumen.rescates["monto"] == 1


def test_un_error_que_el_llm_ya_dudaba_no_es_rescate() -> None:
    _, resumen = evaluar([caso("taxi 15", monto="15")], {"monto": "150", "confianza_monto": "baja"})
    assert resumen.celdas["monto"][Celda.ERROR_ATRAPADO] == 1
    assert resumen.rescates["monto"] == 0


def test_el_error_silencioso_queda_listado() -> None:
    _, resumen = evaluar(
        [caso("taxi 15", categoria="transporte")], {"monto": "15", "categoria": "comida_fuera"}
    )
    assert resumen.silenciosos == [("taxi 15", "categoria", "comida_fuera", "transporte")]
    assert "comida_fuera" in tabla_markdown(resumen, "falso")


def test_debe_dudar_cuenta_si_dudo() -> None:
    c = Caso(
        id="dos",
        remitente="demian",
        texto="20 de gasolina y 15 de almuerzo",
        oro={},
        debe_dudar=("monto", "categoria"),
    )
    _, resumen = evaluar([c], {"monto": "20", "confianza_monto": "baja", "categoria": "transporte"})
    assert (resumen.dudo_cuando_debia, resumen.debio_dudar) == (1, 2)


def test_falla_del_proveedor_no_es_acierto_ni_error() -> None:
    _, resumen = evaluar([caso("taxi 15", monto="15")], ErrorDelLLM("timeout"))
    assert resumen.fallos_del_proveedor == 1
    assert sum(resumen.celdas["monto"].values()) == 0


def test_respuesta_fuera_de_contrato_es_falla_del_proveedor() -> None:
    _, resumen = evaluar([caso("taxi 15", monto="15")], {"confianza_monto": "muchisima"})
    assert resumen.fallos_del_proveedor == 1


def test_cuota_agotada_corta_y_conserva_lo_corrido() -> None:
    casos = [caso("taxi 15", monto="15"), caso("café 3", monto="3")]
    resultados, _ = evaluar(casos, {"monto": "15"}, CuotaAgotada("429"))
    assert [r.id for r in resultados] == ["taxi 15"]


def test_recalcular_desde_la_corrida_da_la_misma_tabla(tmp_path: Path) -> None:
    casos = [caso("taxi 15", monto="15", categoria="transporte"), caso("café 3", monto="3")]
    resultados, resumen = evaluar(
        casos, {"monto": "15", "categoria": "transporte"}, {"monto": "30"}
    )
    ruta = tmp_path / "corrida.jsonl"
    guardar_corrida(ruta, "falso", HOY, resultados)

    meta, leidos = leer_corrida(ruta)
    assert meta["modelo"] == "falso"
    assert tabla_markdown(resumir(casos, leidos), "falso") == tabla_markdown(resumen, "falso")


def test_banco_con_categoria_inactiva_falla(tmp_path: Path) -> None:
    banco = tmp_path / "banco.yaml"
    banco.write_text(
        'hoy: "2026-03-15"\n'
        "casos:\n"
        "  - { id: a, remitente: mama, texto: farmacia 5, oro: { categoria: salud } }\n"
    )
    with pytest.raises(ValueError, match="salud"):
        cargar_casos(banco, TAXONOMIA)


def test_una_corrida_cortada_se_retoma(tmp_path: Path) -> None:
    ruta = tmp_path / "corrida.jsonl"
    casos = [caso("taxi 15", monto="15"), caso("café 3", monto="3")]
    assert iniciar_corrida(ruta, "falso", HOY) == []
    primero, *_ = correr(casos[:1], ClienteGuionado({"monto": "15"}), TAXONOMIA, HOY)
    agregar_resultado(ruta, primero)

    retomados = iniciar_corrida(ruta, "falso", HOY)
    assert [r.id for r in retomados] == ["taxi 15"]


def test_una_corrida_de_otro_dia_empieza_de_cero(tmp_path: Path) -> None:
    ruta = tmp_path / "corrida.jsonl"
    resultados, _ = evaluar([caso("taxi 15", monto="15")], {"monto": "15"})
    guardar_corrida(ruta, "falso", HOY, resultados)
    assert iniciar_corrida(ruta, "falso", date(2026, 3, 16)) == []
