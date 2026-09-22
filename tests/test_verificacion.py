from decimal import Decimal

import pytest

from family_expense_bot.extraction.verificacion import (
    monto_respaldado_por_texto,
    montos_en_texto,
    parsear_monto,
)


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("200", "200"),
        ("200.50", "200.50"),
        ("200,50", "200.50"),  # coma decimal, como se escribe en Ecuador
        ("1.200", "1200"),  # punto de miles, no decimal
        ("1,200", "1200"),
        ("1.200,50", "1200.50"),
        ("1,200.50", "1200.50"),
        ("$35", "35"),
        (" 15 ", "15"),
    ],
)
def test_parsear_monto(texto: str, esperado: str) -> None:
    assert parsear_monto(texto) == Decimal(esperado)


@pytest.mark.parametrize("texto", ["", "mercado", "abc", "12.34.56.78", "--"])
def test_parsear_monto_rechaza_lo_que_no_es_numero(texto: str) -> None:
    assert parsear_monto(texto) is None


def test_montos_en_texto_encuentra_todos_los_numeros() -> None:
    assert montos_en_texto("20 de gasolina y 8,50 del almuerzo") == {
        Decimal("20"),
        Decimal("8.50"),
    }


def test_montos_en_texto_sin_numeros() -> None:
    assert montos_en_texto("compré cosas en el super") == set()


@pytest.mark.parametrize(
    ("monto", "texto", "respaldado"),
    [
        (Decimal("200"), "200 dólares mercado", True),
        (Decimal("12.50"), "12,50 en pan", True),
        (Decimal("1200"), "1.200 del arriendo", True),
        (Decimal("50"), "compré cosas en el super", False),  # el LLM lo inventó
        (Decimal("28"), "20 de gasolina y 8 del almuerzo", False),  # los sumó
    ],
)
def test_monto_respaldado_por_texto(monto: Decimal, texto: str, respaldado: bool) -> None:
    assert monto_respaldado_por_texto(monto, texto) is respaldado
