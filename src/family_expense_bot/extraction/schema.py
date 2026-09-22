"""Contrato de extracción.

Dos modelos distintos a propósito:

- `RespuestaLLM` es lo que el LLM *afirma*. No es confiable por sí solo.
- `GastoExtraido` es lo que el sistema *sostiene* después de verificar esa
  afirmación contra el texto original. Solo este viaja al resto del sistema.
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Confianza(StrEnum):
    ALTA = "alta"
    MEDIA = "media"
    BAJA = "baja"


class RespuestaLLM(BaseModel):
    """Afirmación cruda del LLM. Todos los campos son texto: parsear es tarea nuestra.

    Pedir strings en vez de números o fechas evita que el LLM invente formatos
    y nos deja verificar el monto contra el mensaje original.
    """

    monto: str | None = Field(
        default=None,
        description="Solo el número del monto, tal como aparece en el mensaje. null si no hay.",
    )
    categoria: str | None = Field(
        default=None, description="Una de las categorías permitidas. null si ninguna encaja."
    )
    fecha: str | None = Field(
        default=None,
        description="Fecha del gasto en formato YYYY-MM-DD. null si el mensaje no la menciona.",
    )
    descripcion: str = Field(description="Qué se compró, en pocas palabras.")
    confianza_monto: Confianza
    confianza_categoria: Confianza
    confianza_fecha: Confianza


class GastoExtraido(BaseModel):
    """Extracción ya verificada. Inmutable: nadie la ajusta después del parser."""

    model_config = ConfigDict(frozen=True)

    monto: Decimal | None
    categoria: str
    fecha: date | None
    descripcion: str
    texto_original: str
    confianza_monto: Confianza
    confianza_categoria: Confianza
    confianza_fecha: Confianza
    notas: tuple[str, ...] = ()
    """Por qué se degradó la confianza. Es lo que el bot le muestra a la persona."""

    @property
    def campos_dudosos(self) -> tuple[str, ...]:
        dudosos = [
            campo
            for campo in ("monto", "categoria", "fecha")
            if getattr(self, f"confianza_{campo}") is not Confianza.ALTA
        ]
        if self.monto is None and "monto" not in dudosos:
            dudosos.append("monto")
        return tuple(dudosos)

    @property
    def listo_para_guardar(self) -> bool:
        """Sin dudas y con monto. Aun así, la Fase 3 pide confirmación humana."""
        return self.monto is not None and not self.campos_dudosos
