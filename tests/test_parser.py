"""Tests del parser. Nunca llaman a un LLM real: el cliente está falseado."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from family_expense_bot.extraction.categorias import cargar_taxonomia
from family_expense_bot.extraction.parser import ErrorDeExtraccion, extraer
from family_expense_bot.extraction.schema import CAMPOS, Confianza

HOY = date(2026, 3, 15)
FIXTURES = yaml.safe_load((Path(__file__).parent / "fixtures" / "mensajes.yaml").read_text())
TAXONOMIA = cargar_taxonomia()

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
"""Cada fixture declara solo lo que le importa; el resto sale de acá."""


def respuesta(**campos: Any) -> str:
    return json.dumps(BASE_LLM | campos)


class ClienteFalso:
    """Devuelve una respuesta fija y guarda el prompt que recibió."""

    def __init__(self, respuesta: str) -> None:
        self.respuesta = respuesta
        self.prompt: str | None = None

    def completar_json(self, prompt: str, schema: dict[str, object]) -> str:
        self.prompt = prompt
        return self.respuesta


def _extraer(texto: str, respuesta_llm: str, remitente: str = "demian") -> Any:
    return extraer(texto, ClienteFalso(respuesta_llm), TAXONOMIA, remitente, HOY)


@pytest.mark.parametrize("caso", FIXTURES, ids=[c["id"] for c in FIXTURES])
def test_extraccion_sobre_mensajes_de_ejemplo(caso: dict[str, Any]) -> None:
    espera = caso["espera"]
    gasto = _extraer(caso["texto"], respuesta(**caso["respuesta_llm"]), caso["remitente"])

    assert gasto.monto == (Decimal(espera["monto"]) if espera["monto"] is not None else None)
    assert gasto.categoria == espera["categoria"]
    assert gasto.fecha == date.fromisoformat(espera["fecha"])
    assert gasto.atribuido_a == espera["atribuido_a"]
    assert gasto.remitente == caso["remitente"]
    assert gasto.texto_original == caso["texto"]

    for campo, nivel in espera["confianza"].items():
        assert gasto.confianza(campo) == Confianza(nivel), f"confianza de {campo}"

    esperados = {c for c, n in espera["confianza"].items() if n != "alta"}
    assert set(gasto.campos_dudosos) == esperados


@pytest.mark.parametrize("caso", FIXTURES, ids=[c["id"] for c in FIXTURES])
def test_cada_duda_viene_con_su_explicacion(caso: dict[str, Any]) -> None:
    """Un ❓ sin nota obliga a la persona a adivinar qué está mal."""
    gasto = _extraer(caso["texto"], respuesta(**caso["respuesta_llm"]), caso["remitente"])
    for campo in gasto.campos_dudosos:
        assert gasto.notas_de(campo), f"{campo} quedó en duda sin nota"


def test_los_fixtures_cubren_todos_los_campos_con_confianza() -> None:
    for caso in FIXTURES:
        assert set(caso["espera"]["confianza"]) == set(CAMPOS), caso["id"]


# --- el monto ---------------------------------------------------------------


def test_un_monto_no_respaldado_siempre_baja_la_confianza() -> None:
    """Aunque el LLM jure que está seguro: es dinero real."""
    gasto = _extraer("compré pan", respuesta(monto="999", categoria="alimentacion"))
    assert gasto.confianza_monto is Confianza.BAJA
    assert not gasto.listo_para_guardar
    assert any("no aparece en el mensaje" in n.texto for n in gasto.notas_de("monto"))


def test_monto_negativo_o_cero_se_descarta() -> None:
    gasto = _extraer("0 en pan", respuesta(monto="0", categoria="alimentacion"))
    assert gasto.monto is None
    assert gasto.confianza_monto is Confianza.BAJA


# --- la atribución ----------------------------------------------------------


def test_sin_mencion_el_gasto_es_de_quien_escribe_pero_con_duda() -> None:
    """Es una suposición razonable, y por eso mismo la persona tiene que verla."""
    gasto = _extraer(
        "30 de gasolina", respuesta(monto="30", categoria="transporte"), remitente="mama"
    )
    assert gasto.atribuido_a == "mama"
    assert gasto.confianza_atribuido_a is Confianza.MEDIA
    assert "atribuido_a" in gasto.campos_dudosos
    assert any("lo escribiste tú" in n.texto for n in gasto.notas_de("atribuido_a"))


def test_la_casa_nunca_sale_del_remitente() -> None:
    """Ningún remitente produce la atribución compartida por defecto."""
    for remitente in TAXONOMIA.personas:
        gasto = _extraer("30 de gasolina", respuesta(monto="30", categoria="transporte"), remitente)
        assert gasto.atribuido_a != TAXONOMIA.compartida.nombre


def test_un_remitente_que_no_es_persona_falla_antes_de_llamar_al_llm() -> None:
    cliente = ClienteFalso(respuesta(monto="30"))
    with pytest.raises(ValueError, match="Remitente"):
        extraer("30 de gasolina", cliente, TAXONOMIA, "casa", HOY)
    assert cliente.prompt is None, "Se gastó una llamada al LLM con un remitente inválido"


def test_el_parentesco_lo_resuelve_el_codigo_no_el_llm() -> None:
    """La misma cita significa personas distintas según quién escribe."""
    cita = respuesta(monto="45", categoria="ropa", quien_paga="mi hija")
    desde_mama = _extraer("mi hija pagó 45 de ropa", cita, remitente="mama")
    desde_demian = _extraer("mi hija pagó 45 de ropa", cita, remitente="demian")

    assert desde_mama.atribuido_a == "hermana"
    assert desde_mama.confianza_atribuido_a is Confianza.ALTA
    # Demian no tiene hijos en la lista: la cita no es nadie, queda del remitente.
    assert desde_demian.atribuido_a == "demian"
    assert desde_demian.confianza_atribuido_a is Confianza.BAJA


def test_una_cita_con_dos_personas_queda_en_duda() -> None:
    gasto = _extraer(
        "mi hija y mi esposo pagaron 45",
        respuesta(monto="45", quien_paga="mi hija y mi esposo"),
        remitente="mama",
    )
    assert gasto.atribuido_a == "mama"
    assert gasto.confianza_atribuido_a is Confianza.BAJA
    assert any("más de una persona" in n.texto for n in gasto.notas_de("atribuido_a"))


def test_casa_con_confianza_alta_exige_un_marcador_en_el_texto() -> None:
    sin = _extraer("300 de pensión", respuesta(monto="300", quien_paga="casa"))
    con = _extraer("300 de pensión de la casa", respuesta(monto="300", quien_paga="casa"))
    assert sin.confianza_atribuido_a is Confianza.BAJA
    assert con.confianza_atribuido_a is Confianza.ALTA


# --- el contrato ------------------------------------------------------------


def test_respuesta_que_no_es_json_falla_explicito() -> None:
    with pytest.raises(ErrorDeExtraccion):
        _extraer("200 supermercado", "lo siento, no entendí")


def test_respuesta_json_sin_los_campos_del_contrato_falla() -> None:
    with pytest.raises(ErrorDeExtraccion):
        _extraer("200 supermercado", '{"monto": "200"}')


def test_el_prompt_lleva_la_taxonomia_y_la_fecha() -> None:
    """Las categorías son config: el parser no las hardcodea, las inyecta."""
    cliente = ClienteFalso(respuesta(monto="200", categoria="alimentacion"))
    extraer("200 supermercado", cliente, TAXONOMIA, "mama", HOY)

    assert cliente.prompt is not None
    assert "2026-03-15" in cliente.prompt
    for nombre in TAXONOMIA.nombres:
        assert nombre in cliente.prompt


def test_al_llm_no_le_llega_quien_escribe_ni_la_familia() -> None:
    """El LLM solo cita frases; los parentescos los resuelve el código.

    Además, cuanto menos sepa el proveedor del LLM sobre la familia, mejor.
    """
    cliente = ClienteFalso(respuesta(monto="200", categoria="alimentacion"))
    extraer("200 supermercado", cliente, TAXONOMIA, "mama", HOY)

    assert cliente.prompt is not None
    for persona in TAXONOMIA.personas:
        assert persona not in cliente.prompt


def test_el_prompt_no_ofrece_categorias_candidatas() -> None:
    """Una candidata sin confirmar no puede recibir datos: el LLM ni la ve."""
    cliente = ClienteFalso(respuesta(monto="200", categoria="alimentacion"))
    extraer("200 supermercado", cliente, TAXONOMIA, "mama", HOY)

    assert cliente.prompt is not None
    for candidata in TAXONOMIA.candidatas:
        assert f"- {candidata}:" not in cliente.prompt


def test_el_gasto_extraido_es_inmutable() -> None:
    gasto = _extraer("200 supermercado", respuesta(monto="200", categoria="alimentacion"))
    with pytest.raises(ValidationError):
        gasto.monto = Decimal("1")
