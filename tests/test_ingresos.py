"""Ingresos extra: validación, almacenamiento y cómo se suman al presupuesto."""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest

from family_expense_bot.bot import mensajes
from family_expense_bot.dashboard.presupuesto import extras_del_mes, resumen_presupuesto
from family_expense_bot.extraction.categorias import cargar_taxonomia
from family_expense_bot.extraction.formulario import (
    CamposIngreso,
    FormularioInvalido,
    armar_ingreso,
)
from family_expense_bot.storage.repository import (
    IngresoNuevo,
    RepositorioIngresos,
    crear_engine,
)

TAXONOMIA = cargar_taxonomia()
HOY = date(2026, 10, 20)


def _campos(**cambios: Any) -> CamposIngreso:
    base: dict[str, Any] = {
        "monto": "150",
        "categoria": "trabajo_extra",
        "fecha": HOY,
        "descripcion": "un logo",
    }
    return CamposIngreso(**(base | cambios))


def _nuevo(monto: str = "150", persona: str = "demian", fecha: date = HOY) -> IngresoNuevo:
    return IngresoNuevo(
        monto=Decimal(monto),
        categoria="regalo",
        subcategoria=None,
        persona=persona,
        fecha=fecha,
        descripcion="x",
    )


@pytest.fixture
def repo() -> RepositorioIngresos:
    return RepositorioIngresos(crear_engine("sqlite://"))


# --- taxonomía y validación --------------------------------------------------------


def test_no_hay_categoria_sueldo() -> None:
    """El sueldo es el ingreso fijo de .env: registrarlo lo contaría dos veces."""
    nombres = [c.nombre for c in TAXONOMIA.ingresos]
    assert "sueldo" not in nombres
    assert {"trabajo_extra", "regalo", "otros"} <= set(nombres)


def test_un_ingreso_valido_es_de_quien_lo_registra() -> None:
    ingreso = armar_ingreso(
        _campos(monto="150,50", subcategoria=" Fiverr "), "demian", TAXONOMIA, HOY
    )
    assert ingreso == IngresoNuevo(
        monto=Decimal("150.50"),
        categoria="trabajo_extra",
        subcategoria="Fiverr",
        persona="demian",
        fecha=HOY,
        descripcion="un logo",
    )


@pytest.mark.parametrize(
    ("cambio", "motivo"),
    [
        ({"monto": "0"}, "mayor que cero"),
        ({"monto": "1,200"}, "no es un monto"),
        ({"categoria": "alimentacion"}, "no es una categoría de ingreso"),
        ({"categoria": "sueldo"}, "no es una categoría de ingreso"),
        ({"fecha": HOY + timedelta(days=1)}, "futuro"),
        ({"fecha": HOY - timedelta(days=366)}, "más de un año"),
        ({"descripcion": " "}, "descripción"),
        ({"subcategoria": "x" * 41}, "subcategoría es muy larga"),
    ],
)
def test_lo_que_no_cuadra_se_rechaza(cambio: dict[str, Any], motivo: str) -> None:
    with pytest.raises(FormularioInvalido, match=motivo):
        armar_ingreso(_campos(**cambio), "demian", TAXONOMIA, HOY)


# --- repositorio ---------------------------------------------------------------------


def test_guardar_listar_y_anular(repo: RepositorioIngresos) -> None:
    guardado = repo.guardar(_nuevo(), registrado_por=1)
    assert [i.id for i in repo.listar()] == [guardado.id]
    assert repo.anular(guardado.id, "duplicado")
    assert repo.listar() == []
    anulado = repo.obtener(guardado.id)
    assert anulado is not None and anulado.anulado  # nada se borra
    assert not repo.anular(guardado.id, "otra vez")


def test_reemplazar_es_todo_o_nada(repo: RepositorioIngresos) -> None:
    viejo = repo.guardar(_nuevo("100"), registrado_por=1)
    assert repo.reemplazar(viejo.id, _nuevo("120"), registrado_por=2) is None  # de otra persona
    assert [i.monto for i in repo.listar()] == [Decimal("100")]
    nuevo = repo.reemplazar(viejo.id, _nuevo("120"), registrado_por=1)
    assert nuevo is not None
    assert [i.monto for i in repo.listar()] == [Decimal("120")]
    assert repo.reemplazar(viejo.id, _nuevo("130"), registrado_por=1) is None  # ya anulado


def test_listar_filtra_por_fecha(repo: RepositorioIngresos) -> None:
    repo.guardar(_nuevo(fecha=date(2026, 9, 30)), registrado_por=1)
    de_octubre = repo.guardar(_nuevo(fecha=date(2026, 10, 1)), registrado_por=1)
    assert [i.id for i in repo.listar(date(2026, 10, 1), date(2026, 10, 31))] == [de_octubre.id]


# --- presupuesto ---------------------------------------------------------------------


def test_extras_del_mes_cuenta_solo_lo_de_esa_persona_y_ese_mes(repo: RepositorioIngresos) -> None:
    repo.guardar(_nuevo("100"), registrado_por=1)
    repo.guardar(_nuevo("50.50"), registrado_por=1)
    repo.guardar(_nuevo("999", persona="mama"), registrado_por=2)
    repo.guardar(_nuevo("999", fecha=date(2026, 9, 30)), registrado_por=1)
    borrado = repo.guardar(_nuevo("999"), registrado_por=1)
    repo.anular(borrado.id, "error")
    assert extras_del_mes(repo.listar(), "demian", 2026, 10) == Decimal("150.50")


def test_el_extra_se_suma_al_fijo_y_se_reparte_30_30_20_20() -> None:
    resumen = resumen_presupuesto(
        [], TAXONOMIA, "demian", Decimal("1200"), 2026, 10, Decimal("200")
    )
    assert resumen.ingreso == Decimal("1400")
    assert resumen.ingreso_extra == Decimal("200")
    assert [b.asignado for b in resumen.bolsillos] == [
        Decimal("420.00"),
        Decimal("420.00"),
        Decimal("280.00"),
        Decimal("280.00"),
    ]


def test_sin_extra_el_presupuesto_es_el_de_siempre() -> None:
    """La paridad con n8n depende de esto: n8n no tiene ingresos extra."""
    con_cero = resumen_presupuesto([], TAXONOMIA, "demian", Decimal("1200"), 2026, 10, Decimal(0))
    sin_param = resumen_presupuesto([], TAXONOMIA, "demian", Decimal("1200"), 2026, 10)
    assert con_cero == sin_param


def test_el_chat_muestra_de_donde_sale_el_ingreso() -> None:
    resumen = resumen_presupuesto(
        [], TAXONOMIA, "demian", Decimal("1200"), 2026, 10, Decimal("150")
    )
    assert "Ingreso: $1,350.00 ($1,200.00 fijo + $150.00 extra)" in mensajes.presupuesto(resumen)
    sin_extra = resumen_presupuesto([], TAXONOMIA, "demian", Decimal("1200"), 2026, 10)
    assert "Ingreso: $1,200.00\n" in mensajes.presupuesto(sin_extra)
