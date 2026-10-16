"""Presupuesto por bolsillos. Funciones puras: datos armados a mano, sin base."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from family_expense_bot.dashboard.presupuesto import (
    Veredicto,
    evaluar_gasto,
    repartir,
    resumen_presupuesto,
)
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


# --- ¿Puedo gastar esto? ---------------------------------------------------------


# Octubre tiene 31 días: el 16 faltan 15, y a lujos (240) le tocan 240*15/31 = 116.13.
HOY = date(2026, 10, 16)


def _consulta(
    monto: str,
    categoria: str = "entretenimiento",
    *gastos: GastoRegistrado,
    texto: str = "x",
    hoy: date = HOY,
) -> Any:
    r = resumen_presupuesto(gastos, TAXONOMIA, "demian", Decimal("1200"), hoy.year, hoy.month)
    bolsillo = TAXONOMIA.bolsillo_de(categoria, texto)
    return evaluar_gasto(r, TAXONOMIA, bolsillo, Decimal(monto), hoy)


def test_si_cuando_deja_lo_que_toca_para_el_resto_del_mes() -> None:
    c = _consulta("30")
    assert c.veredicto is Veredicto.SI
    assert c.dias_restantes == 15
    assert c.reserva == Decimal("116.13")
    assert c.disponible_despues == Decimal("210.00")


def test_justo_en_la_reserva_todavia_es_si() -> None:
    assert _consulta("123.87").veredicto is Veredicto.SI  # quedan 116.13 exactos


def test_difiere_si_cabe_pero_adelanta_el_gasto_del_mes() -> None:
    c = _consulta("123.88")
    assert c.veredicto is Veredicto.DIFIERE
    assert c.alternativa is None, "solo se sugiere otro bolsillo cuando no cabe"


def test_no_si_el_bolsillo_se_pasa_y_sugiere_el_fondo() -> None:
    c = _consulta("30", "entretenimiento", _gasto("230", "comida_fuera"))
    assert c.veredicto is Veredicto.NO
    assert c.disponible_despues == Decimal("-20.00")
    assert c.alternativa is not None and c.alternativa.nombre == "fondo"
    assert c.marcador_alternativa == "del fondo"


def test_no_sugiere_el_fondo_si_tampoco_alcanza_ahi() -> None:
    c = _consulta("250", "entretenimiento")
    assert c.veredicto is Veredicto.NO
    assert c.alternativa is None


def test_si_el_gasto_ya_es_del_fondo_no_se_sugiere_a_si_mismo() -> None:
    c = _consulta("300", "personal")
    assert c.veredicto is Veredicto.NO
    assert c.alternativa is None


def test_el_ultimo_dia_del_mes_basta_con_que_quepa() -> None:
    c = _consulta("239", hoy=date(2026, 10, 31))
    assert c.dias_restantes == 0
    assert c.veredicto is Veredicto.SI


def test_una_categoria_sin_bolsillo_no_se_puede_evaluar() -> None:
    c = _consulta("10", "otros")
    assert c.veredicto is Veredicto.SIN_BOLSILLO
    assert c.bolsillo is None
