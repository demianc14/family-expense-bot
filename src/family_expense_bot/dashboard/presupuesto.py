"""Presupuesto mensual por bolsillos (30/30/20/20).

Funciones puras sobre lo que devuelve el repositorio: nada de SQL ni de
Telegram acá. El reparto vive en `config/categories.yaml`; el ingreso de cada
persona, en `.env` (INGRESOS_MENSUALES), porque es un dato personal.
"""

import calendar
from collections.abc import Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

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


def estado_tras_gasto(
    gastos: Sequence[GastoRegistrado],
    taxonomia: Taxonomia,
    gasto: GastoRegistrado,
    ingreso: Decimal,
) -> EstadoBolsillo | None:
    """Cómo quedó, en el mes del gasto, el bolsillo contra el que cuenta. None si en ninguno."""
    resumen = resumen_presupuesto(
        gastos, taxonomia, gasto.atribuido_a, ingreso, gasto.fecha.year, gasto.fecha.month
    )
    nombre = taxonomia.bolsillo_de(gasto.categoria, gasto.texto_original)
    return resumen.bolsillo(nombre) if nombre else None


# --- ¿Puedo gastar esto? ---------------------------------------------------------


class Veredicto(StrEnum):
    SI = "si"
    DIFIERE = "difiere"
    """Cabe, pero deja el bolsillo por debajo de lo que toca para los días que faltan."""
    NO = "no"
    """Con este gasto, el bolsillo se pasa."""
    SIN_BOLSILLO = "sin_bolsillo"
    """La categoría no cuenta en ningún bolsillo: no hay contra qué comparar."""


class Consulta(BaseModel):
    """Si conviene un gasto que todavía no se hizo. Nada de esto se guarda."""

    model_config = ConfigDict(frozen=True)

    monto: Decimal
    veredicto: Veredicto
    bolsillo: EstadoBolsillo | None
    disponible_despues: Decimal | None
    dias_restantes: int
    """Días del mes después de hoy."""

    reserva: Decimal | None
    """Lo que, a ritmo parejo, le toca al bolsillo para los días que faltan."""

    alternativa: EstadoBolsillo | None = None
    """Otro bolsillo con marcadores donde el gasto sí cabe (el fondo personal)."""

    marcador_alternativa: str | None = None


def evaluar_gasto(
    resumen: ResumenPresupuesto,
    taxonomia: Taxonomia,
    bolsillo: str | None,
    monto: Decimal,
    hoy: date,
) -> Consulta:
    """Regla del ritmo del mes, determinista: el LLM no ve estos números.

    NO si el bolsillo se pasa. DIFIERE si cabe pero deja menos de lo que le toca
    al bolsillo para los días que faltan (asignado * días restantes / días del
    mes). SÍ en otro caso. Si no cabe, sugiere otro bolsillo con marcadores
    donde sí quepa.
    """
    dias_del_mes = calendar.monthrange(hoy.year, hoy.month)[1]
    dias_restantes = dias_del_mes - hoy.day
    estado = resumen.bolsillo(bolsillo) if bolsillo else None
    if estado is None or not estado.se_gasta:
        return Consulta(
            monto=monto,
            veredicto=Veredicto.SIN_BOLSILLO,
            bolsillo=None,
            disponible_despues=None,
            dias_restantes=dias_restantes,
            reserva=None,
        )

    despues = estado.disponible - monto
    reserva = (estado.asignado * dias_restantes / dias_del_mes).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    if despues < 0:
        veredicto = Veredicto.NO
    elif despues < reserva:
        veredicto = Veredicto.DIFIERE
    else:
        veredicto = Veredicto.SI

    alternativa, marcador = None, None
    if veredicto is Veredicto.NO:
        for b in taxonomia.bolsillos:
            otro = resumen.bolsillo(b.nombre)
            if b.marcadores and b.nombre != estado.nombre and otro and otro.disponible >= monto:
                alternativa, marcador = otro, b.marcadores[0]
                break

    return Consulta(
        monto=monto,
        veredicto=veredicto,
        bolsillo=estado,
        disponible_despues=despues,
        dias_restantes=dias_restantes,
        reserva=reserva,
        alternativa=alternativa,
        marcador_alternativa=marcador,
    )
