"""Tests del parser. Nunca llaman a un LLM real: el cliente está falseado."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from family_expense_bot.extraction.categorias import cargar_taxonomia
from family_expense_bot.extraction.parser import ErrorDeExtraccion, extraer
from family_expense_bot.extraction.schema import Confianza

HOY = date(2026, 3, 15)
FIXTURES = yaml.safe_load((Path(__file__).parent / "fixtures" / "mensajes.yaml").read_text())
TAXONOMIA = cargar_taxonomia()


class ClienteFalso:
    """Devuelve una respuesta fija y guarda el prompt que recibió."""

    def __init__(self, respuesta: str) -> None:
        self.respuesta = respuesta
        self.prompt: str | None = None

    def completar_json(self, prompt: str, schema: dict[str, object]) -> str:
        self.prompt = prompt
        return self.respuesta


@pytest.mark.parametrize("caso", FIXTURES, ids=[c["id"] for c in FIXTURES])
def test_extraccion_sobre_mensajes_de_ejemplo(caso: dict[str, Any]) -> None:
    espera = caso["espera"]
    gasto = extraer(caso["texto"], ClienteFalso(caso["respuesta_llm"]), TAXONOMIA, HOY)

    assert gasto.monto == (Decimal(espera["monto"]) if espera["monto"] is not None else None)
    assert gasto.categoria == espera["categoria"]
    assert gasto.fecha == date.fromisoformat(espera["fecha"])
    assert gasto.confianza_monto == Confianza(espera["confianza_monto"])
    assert gasto.confianza_categoria == Confianza(espera["confianza_categoria"])
    assert gasto.confianza_fecha == Confianza(espera["confianza_fecha"])
    assert set(gasto.campos_dudosos) == set(espera["campos_dudosos"])
    assert gasto.texto_original == caso["texto"]


def test_nada_es_listo_para_guardar_si_queda_alguna_duda() -> None:
    for caso in FIXTURES:
        gasto = extraer(caso["texto"], ClienteFalso(caso["respuesta_llm"]), TAXONOMIA, HOY)
        assert gasto.listo_para_guardar is (not gasto.campos_dudosos and gasto.monto is not None)


def test_un_monto_no_respaldado_siempre_baja_la_confianza() -> None:
    """Aunque el LLM jure que está seguro: es dinero real."""
    respuesta = (
        '{"monto": "999", "categoria": "mercado", "fecha": "2026-03-15",'
        ' "descripcion": "x", "confianza_monto": "alta",'
        ' "confianza_categoria": "alta", "confianza_fecha": "alta"}'
    )
    gasto = extraer("compré pan", ClienteFalso(respuesta), TAXONOMIA, HOY)
    assert gasto.confianza_monto is Confianza.BAJA
    assert not gasto.listo_para_guardar
    assert any("no aparece en el mensaje" in nota for nota in gasto.notas)


def test_monto_negativo_o_cero_se_descarta() -> None:
    respuesta = (
        '{"monto": "0", "categoria": "mercado", "fecha": "2026-03-15",'
        ' "descripcion": "x", "confianza_monto": "alta",'
        ' "confianza_categoria": "alta", "confianza_fecha": "alta"}'
    )
    gasto = extraer("0 en pan", ClienteFalso(respuesta), TAXONOMIA, HOY)
    assert gasto.monto is None
    assert gasto.confianza_monto is Confianza.BAJA


def test_respuesta_que_no_es_json_falla_explicito() -> None:
    with pytest.raises(ErrorDeExtraccion):
        extraer("200 mercado", ClienteFalso("lo siento, no entendí"), TAXONOMIA, HOY)


def test_respuesta_json_sin_los_campos_del_contrato_falla() -> None:
    with pytest.raises(ErrorDeExtraccion):
        extraer("200 mercado", ClienteFalso('{"monto": "200"}'), TAXONOMIA, HOY)


def test_el_prompt_lleva_la_taxonomia_y_la_fecha_de_hoy() -> None:
    """Las categorías son config: el parser no las hardcodea, las inyecta."""
    cliente = ClienteFalso(
        '{"monto": "200", "categoria": "mercado", "fecha": null, "descripcion": "x",'
        ' "confianza_monto": "alta", "confianza_categoria": "alta", "confianza_fecha": "alta"}'
    )
    extraer("200 mercado", cliente, TAXONOMIA, HOY)

    assert cliente.prompt is not None
    assert "2026-03-15" in cliente.prompt
    for nombre in TAXONOMIA.nombres:
        assert nombre in cliente.prompt


def test_el_gasto_extraido_es_inmutable() -> None:
    gasto = extraer(
        "200 mercado",
        ClienteFalso(
            '{"monto": "200", "categoria": "mercado", "fecha": null, "descripcion": "x",'
            ' "confianza_monto": "alta", "confianza_categoria": "alta", "confianza_fecha": "alta"}'
        ),
        TAXONOMIA,
        HOY,
    )
    with pytest.raises(ValidationError):
        gasto.monto = Decimal("1")  # type: ignore[misc]
