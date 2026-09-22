"""Agregaciones mensuales y reporte de calidad.

Funciones puras sobre lo que devuelven los repositorios: nada de SQL ni de
Telegram acá, así que se testea con datos armados a mano.
"""

import calendar
import csv
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from family_expense_bot.extraction.schema import Confianza
from family_expense_bot.storage.models import Extraccion
from family_expense_bot.storage.repository import GastoRegistrado


class TotalCategoria(BaseModel):
    model_config = ConfigDict(frozen=True)

    categoria: str
    total: Decimal
    cantidad: int


class Calidad(BaseModel):
    """Qué tan bien leyó el LLM los mensajes, medido sobre TODOS los intentos."""

    model_config = ConfigDict(frozen=True)

    intentos: int
    confirmadas: int
    sin_dudas: int
    """Extracciones donde los tres campos salieron con confianza alta."""

    @property
    def tasa_confirmacion(self) -> float | None:
        """Proporción de extracciones que la persona aceptó. None si no hubo ninguna."""
        return self.confirmadas / self.intentos if self.intentos else None

    @property
    def tasa_sin_dudas(self) -> float | None:
        return self.sin_dudas / self.intentos if self.intentos else None


class ResumenMensual(BaseModel):
    model_config = ConfigDict(frozen=True)

    anio: int
    mes: int
    total: Decimal
    cantidad: int
    por_categoria: tuple[TotalCategoria, ...]
    calidad: Calidad


def rango_del_mes(anio: int, mes: int) -> tuple[date, date]:
    """Primer y último día del mes, ambos inclusive."""
    ultimo = calendar.monthrange(anio, mes)[1]
    return date(anio, mes, 1), date(anio, mes, ultimo)


def totales_por_categoria(gastos: Sequence[GastoRegistrado]) -> tuple[TotalCategoria, ...]:
    """De mayor a menor gasto: lo primero que alguien quiere ver es en qué se va la plata."""
    acumulado: dict[str, list[Decimal | int]] = {}
    for gasto in gastos:
        fila = acumulado.setdefault(gasto.categoria, [Decimal(0), 0])
        fila[0] = Decimal(fila[0]) + gasto.monto
        fila[1] = int(fila[1]) + 1

    return tuple(
        sorted(
            (
                TotalCategoria(categoria=cat, total=Decimal(t), cantidad=int(n))
                for cat, (t, n) in acumulado.items()
            ),
            key=lambda c: c.total,
            reverse=True,
        )
    )


def calcular_calidad(extracciones: Sequence[Extraccion]) -> Calidad:
    sin_dudas = sum(
        1
        for e in extracciones
        if e.tenia_monto
        and Confianza.ALTA == e.confianza_monto == e.confianza_categoria == e.confianza_fecha
    )
    return Calidad(
        intentos=len(extracciones),
        confirmadas=sum(1 for e in extracciones if e.confirmada),
        sin_dudas=sin_dudas,
    )


def resumen_mensual(
    gastos: Sequence[GastoRegistrado],
    extracciones: Sequence[Extraccion],
    anio: int,
    mes: int,
) -> ResumenMensual:
    por_categoria = totales_por_categoria(gastos)
    return ResumenMensual(
        anio=anio,
        mes=mes,
        total=sum((g.monto for g in gastos), Decimal(0)),
        cantidad=len(gastos),
        por_categoria=por_categoria,
        calidad=calcular_calidad(extracciones),
    )


COLUMNAS = ("id", "fecha", "categoria", "monto", "descripcion", "registrado_por", "creado_en")


def exportar_csv(gastos: Sequence[GastoRegistrado], ruta: Path) -> Path:
    """Export para Power BI. El archivo lleva montos reales: nunca se versiona."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("w", newline="", encoding="utf-8") as f:
        escritor = csv.writer(f)
        escritor.writerow(COLUMNAS)
        for g in gastos:
            escritor.writerow(
                [
                    g.id,
                    g.fecha.isoformat(),
                    g.categoria,
                    f"{g.monto:.2f}",
                    g.descripcion,
                    g.registrado_por,
                    g.creado_en.astimezone(UTC).isoformat(),
                ]
            )
    return ruta


def inicio_y_fin_utc(anio: int, mes: int) -> tuple[datetime, datetime]:
    """Rango [inicio, fin) en UTC para filtrar extracciones por mes."""
    inicio = datetime(anio, mes, 1, tzinfo=UTC)
    fin = (
        datetime(anio + 1, 1, 1, tzinfo=UTC)
        if mes == 12
        else datetime(anio, mes + 1, 1, tzinfo=UTC)
    )
    return inicio, fin
