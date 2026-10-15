"""Presupuesto por bolsillos. Funciones puras: datos armados a mano, sin base."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from family_expense_bot.dashboard.presupuesto import repartir, resumen_presupuesto
from family_expense_bot.extraction.categorias import cargar_taxonomia
from family_expense_bot.storage.repository import GastoRegistrado

TAXONOMIA = cargar_taxonomia()


def _gasto(
    monto: str,
    categoria: str,
    texto: str = "x",
    de: str = "demian",
    fecha: date = date(2026, 10, 5),
    id: int = 1,
) -> GastoRegistrado:
    return GastoRegistrado(
        id=id,
        monto=Decimal(monto),
        categoria=categoria,
        atribuido_a=de,
        fecha=fecha,
        descripcion="x",
        texto_original=texto,
        registrado_por=1,
        creado_en=datetime(2026, 10, 5, tzinfo=UTC),
        corregido_manualmente=False,
        anulado=False,
    )


def _resumen(*gastos: GastoRegistrado, ingreso: str = "1200") -> object:
    return resumen_presupuesto(gastos, TAXONOMIA, "demian", Decimal(ingreso), 2026, 10)


@pytest.mark.parametrize(
    ("ingreso", "esperado"),
    [
        ("1200", ["360.00", "360.00", "240.00", "240.00"]),
        ("1000.01", ["300.01", "300.00", "200.00", "200.00"]),
        ("0.03", ["0.01", "0.01", "0.01", "0.00"]),
    ],
)
def test_repartir_suma_exactamente_el_ingreso(ingreso: str, esperado: list[str]) -> None:
    partes = repartir(Decimal(ingreso), TAXONOMIA.bolsillos)
    assert partes == [Decimal(e) for e in esperado]
    assert sum(partes) == Decimal(ingreso)


def test_cada_gasto_cuenta_contra_su_bolsillo() -> None:
    r = resumen_presupuesto(
        [
            _gasto("200", "alimentacion", id=1),
            _gasto("30", "comida_fuera", id=2),
            _gasto("45", "personal", id=3),
            _gasto("20", "ropa", "camisa del fondo", id=4),  # el marcador gana
            _gasto("15", "otros", id=5),
            _gasto("40", "mesada", id=6),
        ],
        TAXONOMIA,
        "demian",
        Decimal("1200"),
        2026,
        10,
    )
    por_nombre = {b.nombre: b for b in r.bolsillos}
    assert por_nombre["necesarios"].gastado == Decimal("200")
    assert por_nombre["lujos"].gastado == Decimal("30")
    assert por_nombre["fondo"].gastado == Decimal("65")
    assert por_nombre["fondo"].cantidad == 2
    assert por_nombre["ahorro"].gastado == 0, "el ahorro se aparta: nada cuenta contra él"
    assert por_nombre["ahorro"].asignado == Decimal("360.00")
    assert r.sin_bolsillo == Decimal("55")
    assert r.categorias_sin_bolsillo == ("otros", "mesada")


def test_solo_cuentan_los_gastos_de_esa_persona_en_ese_mes() -> None:
    r = resumen_presupuesto(
        [
            _gasto("100", "alimentacion", de="mama", id=1),
            _gasto("100", "alimentacion", de="casa", id=2),
            _gasto("100", "alimentacion", fecha=date(2026, 9, 30), id=3),
            _gasto("100", "alimentacion", fecha=date(2026, 11, 1), id=4),
            _gasto("7", "alimentacion", fecha=date(2026, 10, 31), id=5),
        ],
        TAXONOMIA,
        "demian",
        Decimal("1200"),
        2026,
        10,
    )
    necesarios = r.bolsillo("necesarios")
    assert necesarios is not None
    assert necesarios.gastado == Decimal("7")


def test_pasarse_del_bolsillo_queda_marcado() -> None:
    r = resumen_presupuesto(
        [_gasto("250", "entretenimiento")], TAXONOMIA, "demian", Decimal("1200"), 2026, 10
    )
    lujos = r.bolsillo("lujos")
    assert lujos is not None
    assert lujos.excedido
    assert lujos.disponible == Decimal("-10.00")
