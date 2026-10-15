"""Presupuesto mensual por bolsillos (30/30/20/20).

Funciones puras sobre lo que devuelve el repositorio: nada de SQL ni de
Telegram acá. El reparto vive en `config/categories.yaml`; el ingreso de cada
persona, en `.env` (INGRESOS_MENSUALES), porque es un dato personal.
"""

from collections.abc import Sequence
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from family_expense_bot.dashboard.report import rango_del_mes
from family_expense_bot.extraction.categorias import Bolsillo, Taxonomia
from family_expense_bot.storage.dinero import a_centavos, a_decimal
from family_expense_bot.storage.repository import GastoRegistrado


class EstadoBolsillo(BaseModel):
    model_config = ConfigDict(frozen=True)

    nombre: str
    etiqueta: str
    porcentaje: int
    se_gasta: bool
    asignado: Decimal
    gastado: Decimal
    cantidad: int

    @property
    def disponible(self) -> Decimal:
        """Negativo si se pasó."""
        return self.asignado - self.gastado

    @property
    def excedido(self) -> bool:
        return self.gastado > self.asignado


class ResumenPresupuesto(BaseModel):
    model_config = ConfigDict(frozen=True)

    persona: str
    anio: int
    mes: int
    ingreso: Decimal
    bolsillos: tuple[EstadoBolsillo, ...]
    sin_bolsillo: Decimal
    """Gastos de la persona que no cuentan contra ningún bolsillo (otros, mesada)."""

    categorias_sin_bolsillo: tuple[str, ...]

    def bolsillo(self, nombre: str) -> EstadoBolsillo | None:
        return next((b for b in self.bolsillos if b.nombre == nombre), None)


def repartir(ingreso: Decimal, bolsillos: Sequence[Bolsillo]) -> list[Decimal]:
    """Lo asignado a cada bolsillo, en centavos exactos: siempre suma el ingreso.

    Cada bolsillo recibe su porcentaje truncado al centavo; los centavos que
    sobran del redondeo van, de a uno, a los primeros bolsillos. Sin esto,
    1000.01 repartido en 30/30/20/20 no sumaría 1000.01.
    """
    total = a_centavos(ingreso)
    partes = [total * b.porcentaje // 100 for b in bolsillos]
    for i in range(total - sum(partes)):
        partes[i % len(partes)] += 1
    return [a_decimal(p) for p in partes]


def resumen_presupuesto(
    gastos: Sequence[GastoRegistrado],
    taxonomia: Taxonomia,
    persona: str,
    ingreso: Decimal,
    anio: int,
    mes: int,
) -> ResumenPresupuesto:
    """Cuánto lleva gastado `persona` en cada bolsillo durante el mes.

    Solo cuentan los gastos atribuidos a esa persona (quien asume el costo), del
    mes pedido. `gastos` puede traer de más: se filtra acá.
    """
    desde, hasta = rango_del_mes(anio, mes)
    suyos = [g for g in gastos if g.atribuido_a == persona and desde <= g.fecha <= hasta]

    gastado: dict[str | None, list[Decimal]] = {}
    categorias_sueltas: list[str] = []
    for gasto in suyos:
        bolsillo = taxonomia.bolsillo_de(gasto.categoria, gasto.texto_original)
        gastado.setdefault(bolsillo, []).append(gasto.monto)
        if bolsillo is None and gasto.categoria not in categorias_sueltas:
            categorias_sueltas.append(gasto.categoria)

    asignados = repartir(ingreso, taxonomia.bolsillos)
    estados = tuple(
        EstadoBolsillo(
            nombre=b.nombre,
            etiqueta=b.etiqueta,
            porcentaje=b.porcentaje,
            se_gasta=b.se_gasta,
            asignado=asignado,
            gastado=sum(gastado.get(b.nombre, []), Decimal(0)),
            cantidad=len(gastado.get(b.nombre, [])),
        )
        for b, asignado in zip(taxonomia.bolsillos, asignados, strict=True)
    )
    return ResumenPresupuesto(
        persona=persona,
        anio=anio,
        mes=mes,
        ingreso=ingreso,
        bolsillos=estados,
        sin_bolsillo=sum(gastado.get(None, []), Decimal(0)),
        categorias_sin_bolsillo=tuple(categorias_sueltas),
    )
