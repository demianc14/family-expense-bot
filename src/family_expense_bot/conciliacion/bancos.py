"""Formato de cada banco y reglas de `tipo`. Es config (`config/bancos.yaml`), no código."""

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from family_expense_bot.conciliacion.contrato import TipoMovimiento
from family_expense_bot.extraction.categorias import menciona_alguna

RUTA_POR_DEFECTO = Path(__file__).resolve().parents[3] / "config" / "bancos.yaml"


class Columnas(BaseModel):
    """Nombre, en el encabezado del archivo, de la columna de cada campo."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fecha: str
    descripcion: str
    monto: str | None = None
    """Una sola columna con signo. Si no, van `debito` y `credito`."""
    debito: str | None = None
    credito: str | None = None
    saldo: str | None = None
    """Saldo después de cada movimiento. Opcional, pero permite verificar fila por fila."""

    @model_validator(mode="after")
    def _monto_o_debito_y_credito(self) -> Self:
        if (self.monto is not None) == (self.debito is not None or self.credito is not None) or (
            (self.debito is None) != (self.credito is None)
        ):
            raise ValueError("va una columna `monto` o las dos `debito` y `credito`, no ambas")
        nombres = [n for n in self.model_dump().values() if n is not None]
        if len(nombres) != len(set(nombres)):
            raise ValueError("dos campos no pueden leer la misma columna")
        return self


class ReglaTipo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tipo: TipoMovimiento
    contiene: list[str] = Field(min_length=1)
    """Frases que delatan el tipo, como palabras completas y sin tildes."""
    signo: Literal["sale", "entra"] | None = None
    """Si va, la regla solo aplica a movimientos con ese signo."""

    @field_validator("contiene")
    @classmethod
    def _sin_frases_vacias(cls, v: list[str]) -> list[str]:
        if any(not frase.strip() for frase in v):
            raise ValueError("`contiene` trae una frase vacía")
        return v


class Banco(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    clave: str = ""
    """La clave del banco en el yaml; la pone `cargar_bancos`."""
    nombre: str
    codificacion: Literal["utf-8", "latin-1", "cp1252"] = "utf-8"
    delimitador: str = Field(default=",", min_length=1, max_length=1)
    filas_a_saltar: int = Field(default=0, ge=0)
    """Líneas antes del encabezado (título, nombre del titular)."""
    columnas: Columnas
    formato_fecha: str
    """Formato de `strptime`, con día, mes y año de cuatro dígitos."""
    decimal: Literal["punto", "coma"]
    signo: Literal["negativo_sale", "positivo_sale"] = "negativo_sale"
    """Cómo escribe el banco la columna `monto` (y los saldos). `positivo_sale`
    es una tarjeta: el consumo va en positivo y el saldo es lo que se debe."""
    tipos: list[ReglaTipo] = []
    """En orden: gana la primera que coincide."""

    @field_validator("formato_fecha")
    @classmethod
    def _fecha_completa(cls, v: str) -> str:
        if not all(parte in v for parte in ("%d", "%m", "%Y")):
            raise ValueError("formato_fecha necesita %d, %m y %Y (año de cuatro dígitos)")
        return v

    @model_validator(mode="after")
    def _signo_solo_con_monto(self) -> Self:
        if self.columnas.monto is None and self.signo != "negativo_sale":
            raise ValueError("`signo` solo aplica a un banco con columna `monto`")
        return self


class Bancos(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tope_bytes: int = Field(gt=0)
    """Un archivo más grande se rechaza antes de leerlo."""
    bancos: dict[str, Banco]

    @model_validator(mode="before")
    @classmethod
    def _claves(cls, datos: Any) -> Any:
        """Cada banco conoce su clave, que es la que se guarda en la base."""
        if not isinstance(datos, dict) or not isinstance(datos.get("bancos"), dict):
            return datos
        bancos = {}
        for clave, banco in datos["bancos"].items():
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", str(clave)):
                raise ValueError(
                    f"la clave de banco {clave!r} debe ir en minúsculas, sin espacios "
                    "y con 40 caracteres como máximo"
                )
            bancos[clave] = {**banco, "clave": clave} if isinstance(banco, dict) else banco
        return {**datos, "bancos": bancos}


def cargar_bancos(ruta: Path | None = None) -> Bancos:
    ruta = ruta or RUTA_POR_DEFECTO
    if not ruta.is_file():
        raise FileNotFoundError(f"No existe la configuración de bancos en {ruta}")
    return Bancos.model_validate(yaml.safe_load(ruta.read_text(encoding="utf-8")))


def asignar_tipo(
    descripcion: str, monto_centavos: int, reglas: Sequence[ReglaTipo]
) -> TipoMovimiento:
    """Qué clase de movimiento es, por las reglas del banco y en orden. Sin LLM.

    Si ninguna regla coincide decide el signo: lo que sale es `debito` y lo que
    entra, `credito`.
    """
    sale = monto_centavos < 0
    for regla in reglas:
        if regla.signo is not None and (regla.signo == "sale") != sale:
            continue
        if menciona_alguna(descripcion, regla.contiene):
            return regla.tipo
    return "debito" if sale else "credito"
