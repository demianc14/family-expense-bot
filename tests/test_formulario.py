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
)
from family_expense_bot.extraction.schema import Confianza

TAXONOMIA = cargar_taxonomia()
HOY = date(2026, 10, 20)


def _campos(**cambios: Any) -> CamposFormulario:
    base: dict[str, Any] = {
        "monto": "12.50",
        "categoria": "comida_fuera",
        "fecha": HOY,
        "atribuido_a": "demian",
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
        ({"monto": "12,50"}, "no es un monto"),
        ({"monto": "1e3"}, "no es un monto"),
        ({"monto": "1_200"}, "no es un monto"),
        ({"monto": "1234567890"}, "no es un monto"),
        ({"categoria": "salud"}, "no es una categoría activa"),
        ({"categoria": "inventada"}, "no es una categoría activa"),
        ({"fecha": HOY + timedelta(days=1)}, "futuro"),
        ({"fecha": HOY - timedelta(days=366)}, "más de un año"),
        ({"atribuido_a": None}, "Elige de quién"),
        ({"atribuido_a": "tia"}, "no es una persona"),
        ({"descripcion": "   "}, "descripción"),
        ({"descripcion": "x" * 201}, "muy larga"),
        ({"bolsillo": "ahorro"}, "No se puede mandar"),
        ({"bolsillo": "lujos"}, "No se puede mandar"),
    ],
)
def test_lo_que_no_cuadra_se_rechaza_con_motivo(cambio: dict[str, Any], motivo: str) -> None:
    with pytest.raises(FormularioInvalido, match=motivo):
        armar_gasto(_campos(**cambio), "demian", TAXONOMIA, HOY)


def test_la_casa_elegida_a_mano_se_acepta() -> None:
    """Elegirla con un toque es decirla, no deducirla."""
    gasto = armar_gasto(_campos(atribuido_a="casa"), "demian", TAXONOMIA, HOY)
    assert gasto.atribuido_a == "casa"
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
        ("otros", None, None),
    ],
)
def test_bolsillo_destino(categoria: str, bolsillo: str | None, esperado: str | None) -> None:
    assert bolsillo_destino(categoria, bolsillo, TAXONOMIA) == esperado
