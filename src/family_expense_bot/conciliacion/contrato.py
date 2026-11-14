"""Contrato de la ingesta: lo que sale de leer un estado de cuenta.

Un `EstadoLeido` es lo que dice el archivo, ya en centavos enteros y con signo
(negativo si sale plata de la cuenta). Que exista no significa que cuadre: eso
lo decide `verificacion.verificar`, y solo entonces se guarda.
"""

from collections.abc import Iterable
from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from family_expense_bot.storage.dinero import MAXIMO_CENTAVOS

TipoMovimiento = Literal["debito", "credito", "comision", "interes", "pago"]
"""Los mismos de `storage.models.TIPOS_MOVIMIENTO` (un test los compara)."""


def plata(centavos: int) -> str:
    """Centavos a texto, sin pasar por float: -1550 → "-15.50"."""
    signo = "-" if centavos < 0 else ""
    entero, resto = divmod(abs(centavos), 100)
    return f"{signo}{entero}.{resto:02d}"


class Problema(BaseModel):
    """Una razón para rechazar el estado completo."""

    model_config = ConfigDict(frozen=True)

    codigo: str
    mensaje: str
    linea: int | None = None
    """Línea del archivo, como la muestra un editor de texto."""


class EstadoRechazado(ValueError):
    """El estado no entra. Trae todos los problemas, no solo el primero."""

    def __init__(self, problemas: Iterable[Problema]) -> None:
        self.problemas = tuple(problemas)
        super().__init__("\n".join(p.mensaje for p in self.problemas))


class MovimientoLeido(BaseModel):
    model_config = ConfigDict(frozen=True)

    linea: int = Field(ge=1)
    fecha: date
    descripcion: str = Field(min_length=1, max_length=500)
    """Tal cual la escribe el banco. No se neutraliza acá: ver `docs/conciliacion.md`."""
    monto_centavos: StrictInt
    saldo_centavos: StrictInt | None = None
    """Saldo después del movimiento, si el banco lo trae en una columna."""
    tipo: TipoMovimiento

    @field_validator("monto_centavos")
    @classmethod
    def _monto_real(cls, v: int) -> int:
        if v == 0 or abs(v) > MAXIMO_CENTAVOS:
            raise ValueError("el monto no puede ser cero ni pasar del máximo de la base")
        return v


class EstadoLeido(BaseModel):
    model_config = ConfigDict(frozen=True)

    banco: str
    cuenta: str
    periodo_desde: date
    periodo_hasta: date
    saldo_anterior_centavos: StrictInt
    saldo_final_centavos: StrictInt
    hash_archivo: str = Field(pattern=r"^[0-9a-f]{64}$")
    """SHA-256 de los bytes del archivo."""
    movimientos: tuple[MovimientoLeido, ...]
    """En el orden del archivo, del más viejo al más nuevo."""

    @model_validator(mode="after")
    def _coherente(self) -> Self:
        if self.periodo_desde > self.periodo_hasta:
            raise ValueError("el periodo empieza después de terminar")
        lineas = [m.linea for m in self.movimientos]
        if lineas != sorted(set(lineas)):
            raise ValueError("las líneas de los movimientos se repiten o están desordenadas")
        return self

    @property
    def creditos_centavos(self) -> int:
        return sum(m.monto_centavos for m in self.movimientos if m.monto_centavos > 0)

    @property
    def debitos_centavos(self) -> int:
        """En positivo: lo que salió de la cuenta."""
        return -sum(m.monto_centavos for m in self.movimientos if m.monto_centavos < 0)


class EstadoVigente(BaseModel):
    """Un estado ya importado y sin anular, sin sus movimientos."""

    model_config = ConfigDict(frozen=True)

    id: int
    banco: str
    cuenta: str
    periodo_desde: date
    periodo_hasta: date
    saldo_anterior_centavos: int
    saldo_final_centavos: int
    hash_archivo: str
