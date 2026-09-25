from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from family_expense_bot.dashboard.report import (
    calcular_calidad,
    exportar_csv,
    inicio_y_fin_utc,
    rango_del_mes,
    resumen_mensual,
    totales_por_categoria,
)
from family_expense_bot.extraction.schema import Confianza
from family_expense_bot.storage.models import Extraccion
from family_expense_bot.storage.repository import GastoRegistrado


def _gasto(monto: str, categoria: str = "mercado", dia: int = 5, id: int = 1) -> GastoRegistrado:
    return GastoRegistrado(
        id=id,
        monto=Decimal(monto),
        categoria=categoria,
        atribuido_a="casa",
        fecha=date(2026, 3, dia),
        descripcion="compra",
        registrado_por=1,
        creado_en=datetime(2026, 3, dia, 12, 0, tzinfo=UTC),
        corregido_manualmente=False,
        anulado=False,
    )


def _extraccion(
    confirmada: bool,
    todo_alto: bool = True,
    tenia_monto: bool = True,
    corregida: bool = False,
    atribucion: Confianza = Confianza.ALTA,
) -> Extraccion:
    c = Confianza.ALTA if todo_alto else Confianza.BAJA
    return Extraccion(
        confirmada=confirmada,
        corregida=corregida,
        tenia_monto=tenia_monto,
        confianza_monto=c,
        confianza_categoria=c,
        confianza_fecha=c,
        confianza_atribuido_a=atribucion,
    )


# --- rangos ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("anio", "mes", "ultimo_dia"),
    [(2026, 1, 31), (2026, 2, 28), (2024, 2, 29), (2026, 4, 30), (2026, 12, 31)],
)
def test_rango_del_mes(anio: int, mes: int, ultimo_dia: int) -> None:
    desde, hasta = rango_del_mes(anio, mes)
    assert desde == date(anio, mes, 1)
    assert hasta == date(anio, mes, ultimo_dia)


def test_diciembre_cierra_en_enero_del_ano_siguiente() -> None:
    """El caso que rompe un `mes + 1` ingenuo."""
    inicio, fin = inicio_y_fin_utc(2026, 12)
    assert inicio == datetime(2026, 12, 1, tzinfo=UTC)
    assert fin == datetime(2027, 1, 1, tzinfo=UTC)


# --- agregación -----------------------------------------------------------


def test_suma_por_categoria_ordenada_de_mayor_a_menor() -> None:
    totales = totales_por_categoria(
        [
            _gasto("10.50", "mercado"),
            _gasto("4.00", "transporte"),
            _gasto("20.00", "mercado"),
            _gasto("100.00", "hogar"),
        ]
    )
    assert [(c.categoria, c.total, c.cantidad) for c in totales] == [
        ("hogar", Decimal("100.00"), 1),
        ("mercado", Decimal("30.50"), 2),
        ("transporte", Decimal("4.00"), 1),
    ]


def test_sin_gastos_el_resumen_no_explota() -> None:
    resumen = resumen_mensual([], [], 2026, 3)
    assert resumen.total == Decimal(0)
    assert resumen.cantidad == 0
    assert resumen.por_categoria == ()
    assert resumen.calidad.tasa_confirmacion is None
    assert resumen.calidad.tasa_acierto_directo is None


def test_los_centavos_no_se_pierden_al_sumar() -> None:
    resumen = resumen_mensual([_gasto("0.10"), _gasto("0.20")], [], 2026, 3)
    assert resumen.total == Decimal("0.30")


# --- calidad --------------------------------------------------------------


def test_la_calidad_cuenta_los_rechazos_no_solo_los_aciertos() -> None:
    """Sin los rechazos, cualquier medición daría 100%: es el punto de la tabla."""
    calidad = calcular_calidad(
        [_extraccion(True), _extraccion(True), _extraccion(False), _extraccion(False)]
    )
    assert calidad.intentos == 4
    assert calidad.confirmadas == 2
    assert calidad.tasa_confirmacion == 0.5


def test_sin_dudas_exige_todas_las_confianzas_altas() -> None:
    calidad = calcular_calidad(
        [_extraccion(True, todo_alto=True), _extraccion(True, todo_alto=False)]
    )
    assert calidad.sin_dudas == 1
    assert calidad.tasa_sin_dudas == 0.5


def test_una_atribucion_asumida_ya_no_cuenta_como_sin_dudas() -> None:
    """El cuarto campo también tiene que estar en alta."""
    calidad = calcular_calidad([_extraccion(True, atribucion=Confianza.MEDIA)])
    assert calidad.sin_dudas == 0


def test_acierto_directo_excluye_las_corregidas() -> None:
    """La métrica del brief: aceptadas tal cual, sin tocar nada."""
    calidad = calcular_calidad(
        [
            _extraccion(True),  # aceptada tal cual
            _extraccion(True, corregida=True),  # aceptada tras cambiar la atribución
            _extraccion(False),  # rechazada
            _extraccion(False, corregida=True),  # corrigió y aun así rechazó
        ]
    )
    assert calidad.confirmadas == 2
    assert calidad.corregidas == 1  # solo cuentan las corregidas que se confirmaron
    assert calidad.confirmadas_sin_correccion == 1
    assert calidad.tasa_acierto_directo == 0.25


def test_una_extraccion_sin_monto_nunca_cuenta_como_sin_dudas() -> None:
    calidad = calcular_calidad([_extraccion(False, todo_alto=True, tenia_monto=False)])
    assert calidad.sin_dudas == 0


# --- export ---------------------------------------------------------------


def test_exportar_csv_escribe_encabezado_y_filas(tmp_path: Path) -> None:
    ruta = exportar_csv([_gasto("15.50", "transporte")], tmp_path / "sub" / "gastos.csv")

    lineas = ruta.read_text(encoding="utf-8").splitlines()
    assert lineas[0].startswith("id,fecha,categoria,atribuido_a,monto")
    assert "15.50" in lineas[1]
    assert "transporte" in lineas[1]


def test_exportar_csv_sin_gastos_deja_solo_el_encabezado(tmp_path: Path) -> None:
    ruta = exportar_csv([], tmp_path / "gastos.csv")
    assert len(ruta.read_text(encoding="utf-8").splitlines()) == 1
