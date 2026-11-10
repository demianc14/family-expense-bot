"""Gastos del formulario de la Mini App. Funciones puras: sin base ni HTTP."""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest

from family_expense_bot.extraction.categorias import cargar_taxonomia
from family_expense_bot.extraction.formulario import (
    CamposFormulario,
    FormularioInvalido,
    armar_gasto,
    bolsillo_destino,
    leer_monto,
    leer_subcategoria,
)
from family_expense_bot.extraction.schema import Confianza

TAXONOMIA = cargar_taxonomia()
HOY = date(2026, 10, 20)


def _campos(**cambios: Any) -> CamposFormulario:
    base: dict[str, Any] = {
        "monto": "12.50",
        "categoria": "comida_fuera",
        "fecha": HOY,
        "descripcion": "almuerzo",
    }
    return CamposFormulario(**(base | cambios))


def test_un_formulario_completo_queda_en_confianza_alta() -> None:
    gasto = armar_gasto(_campos(), "demian", TAXONOMIA, HOY)
    assert gasto.monto == Decimal("12.50")
    assert gasto.categoria == "comida_fuera"
    assert gasto.texto_original == "almuerzo"
    assert gasto.listo_para_guardar
    assert {gasto.confianza(c) for c in ("monto", "categoria", "fecha", "atribuido_a")} == {
        Confianza.ALTA
    }


@pytest.mark.parametrize(
    ("cambio", "motivo"),
    [
        ({"monto": "0"}, "mayor que cero"),
        ({"monto": "-5"}, "no es un monto"),
        ({"monto": "12.505"}, "no es un monto"),
        ({"monto": "1,200"}, "no es un monto"),
        ({"monto": "1.200,50"}, "no es un monto"),
        ({"monto": "12,505"}, "no es un monto"),
        ({"categoria": "otros"}, "elige de qué bolsillo sale"),
        ({"categoria": "otros", "bolsillo": "ahorro"}, "No se puede mandar"),
        ({"monto": "1e3"}, "no es un monto"),
        ({"monto": "1_200"}, "no es un monto"),
        ({"monto": "1234567890"}, "no es un monto"),
        ({"categoria": "salud"}, "no es una categoría activa"),
        ({"categoria": "inventada"}, "no es una categoría activa"),
        ({"fecha": HOY + timedelta(days=1)}, "futuro"),
        ({"fecha": HOY - timedelta(days=366)}, "más de un año"),
        ({"descripcion": "   "}, "descripción"),
        ({"descripcion": "x" * 201}, "muy larga"),
        ({"bolsillo": "ahorro"}, "No se puede mandar"),
        ({"bolsillo": "lujos"}, "No se puede mandar"),
    ],
)
def test_lo_que_no_cuadra_se_rechaza_con_motivo(cambio: dict[str, Any], motivo: str) -> None:
    with pytest.raises(FormularioInvalido, match=motivo):
        armar_gasto(_campos(**cambio), "demian", TAXONOMIA, HOY)


def test_el_gasto_es_siempre_de_quien_abre_la_app() -> None:
    """La app es un presupuesto personal: un `atribuido_a` en el cuerpo se ignora."""
    campos = CamposFormulario.model_validate(
        {"monto": "12.50", "categoria": "hogar", "fecha": HOY, "descripcion": "focos"}
        | {"atribuido_a": "casa"}
    )
    gasto = armar_gasto(campos, "demian", TAXONOMIA, HOY)
    assert gasto.atribuido_a == "demian"
    assert gasto.confianza_atribuido_a is Confianza.ALTA


def test_del_fondo_agrega_el_marcador_y_lo_resuelve_bolsillo_de() -> None:
    gasto = armar_gasto(
        _campos(categoria="entretenimiento", descripcion="concierto", bolsillo="fondo"),
        "demian",
        TAXONOMIA,
        HOY,
    )
    assert gasto.texto_original == "concierto, del fondo"
    # Se resuelve al leer, con la misma regla que un mensaje del chat.
    assert TAXONOMIA.bolsillo_de(gasto.categoria, gasto.texto_original) == "fondo"


def test_sin_interruptor_manda_la_categoria() -> None:
    gasto = armar_gasto(_campos(categoria="entretenimiento"), "demian", TAXONOMIA, HOY)
    assert TAXONOMIA.bolsillo_de(gasto.categoria, gasto.texto_original) == "lujos"


def test_espacios_de_mas_se_limpian() -> None:
    gasto = armar_gasto(_campos(descripcion="  almuerzo   con  amigos "), "demian", TAXONOMIA, HOY)
    assert gasto.descripcion == "almuerzo con amigos"


@pytest.mark.parametrize(
    ("categoria", "bolsillo", "esperado"),
    [
        ("comida_fuera", None, "lujos"),
        ("alimentacion", None, "necesarios"),
        ("comida_fuera", "fondo", "fondo"),
        ("otros", "lujos", "lujos"),
        ("otros", "necesarios", "necesarios"),
        ("otros", "fondo", "fondo"),
    ],
)
def test_bolsillo_destino(categoria: str, bolsillo: str | None, esperado: str | None) -> None:
    assert bolsillo_destino(categoria, bolsillo, TAXONOMIA) == esperado


@pytest.mark.parametrize(
    ("crudo", "esperado"), [("12,50", "12.50"), ("12.50", "12.50"), ("7,5", "7.5"), (" 30 ", "30")]
)
def test_el_monto_acepta_coma_o_punto_para_los_centavos(crudo: str, esperado: str) -> None:
    """Muchos teclados numéricos del celular solo traen la coma."""
    assert leer_monto(crudo) == Decimal(esperado)


def test_otros_con_bolsillo_lo_deja_en_el_texto_y_lo_resuelve_bolsillo_de() -> None:
    gasto = armar_gasto(
        _campos(categoria="otros", descripcion="regalo raro", bolsillo="lujos"),
        "demian",
        TAXONOMIA,
        HOY,
    )
    assert gasto.texto_original == "regalo raro, de lujos"
    assert TAXONOMIA.bolsillo_de(gasto.categoria, gasto.texto_original) == "lujos"


def test_los_marcadores_de_otros_no_mueven_otra_categoria() -> None:
    """ "de lujos" solo le da bolsillo a `otros`: en alimentacion manda la categoría."""
    assert TAXONOMIA.bolsillo_de("alimentacion", "pan, de lujos") == "necesarios"
    assert TAXONOMIA.bolsillo_de("otros", "cosa, de lujos") == "lujos"
    assert TAXONOMIA.bolsillo_de("otros", "un reloj de lujo") is None


@pytest.mark.parametrize(
    ("crudo", "esperado"), [(None, None), ("", None), ("   ", None), ("  Tipti  ya ", "Tipti ya")]
)
def test_la_subcategoria_es_opcional_y_se_limpia(crudo: str | None, esperado: str | None) -> None:
    assert leer_subcategoria(crudo) == esperado


def test_la_subcategoria_tiene_tope() -> None:
    with pytest.raises(FormularioInvalido, match="subcategoría es muy larga"):
        leer_subcategoria("x" * 41)
