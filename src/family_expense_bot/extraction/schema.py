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

CAMPOS = ("monto", "categoria", "fecha", "atribuido_a")
"""Los campos que llevan confianza propia. El orden es el que ve la persona."""


class Confianza(StrEnum):
    ALTA = "alta"
    MEDIA = "media"
    BAJA = "baja"


class RespuestaLLM(BaseModel):
    """Afirmación cruda del LLM. Todos los campos son texto: parsear es tarea nuestra.

    Pedir strings en vez de números o fechas evita que el LLM invente formatos
    y nos deja verificar el monto contra el mensaje original.

    Por lo mismo, el LLM no elige a quién se atribuye el gasto: cita cómo lo
    nombra el mensaje ("mi hija") y el parser lo resuelve con la tabla de
    parentescos. Probado contra gemini-3.5-flash-lite: elegir el rol él mismo
    fallaba en "mi hija" → `hermana`, porque el nombre del rol contradice el
    parentesco. Citar una frase lo hace bien.
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
    quien_paga: str | None = Field(
        default=None,
        description=(
            "Cómo nombra el mensaje a quien asume el costo, copiado tal cual "
            "(ej. 'mi hija', 'gasto de la casa'). null si no lo dice."
        ),
    )
    descripcion: str = Field(description="Qué se compró, en pocas palabras.")
    confianza_monto: Confianza
    confianza_categoria: Confianza
    confianza_fecha: Confianza
    confianza_quien_paga: Confianza


class Nota(BaseModel):
    """Por qué se degradó la confianza de un campo. Es lo que ve la persona."""

    model_config = ConfigDict(frozen=True)

    campo: str
    texto: str


class GastoExtraido(BaseModel):
    """Extracción ya verificada. Inmutable: nadie la ajusta después del parser.

    Las confianzas describen cómo salió la extracción, y no cambian aunque una
    persona corrija un valor después: eso se registra aparte, como corrección
    manual, y es justo lo que mide el reporte de calidad.
    """

    model_config = ConfigDict(frozen=True)

    monto: Decimal | None
    categoria: str
    fecha: date | None
    atribuido_a: str
    descripcion: str
    texto_original: str
    remitente: str
    """Rol de quien escribió el mensaje. Es el valor por defecto de `atribuido_a`."""

    confianza_monto: Confianza
    confianza_categoria: Confianza
    confianza_fecha: Confianza
    confianza_atribuido_a: Confianza
    notas: tuple[Nota, ...] = ()

    def confianza(self, campo: str) -> Confianza:
        valor = getattr(self, f"confianza_{campo}")
        assert isinstance(valor, Confianza)
        return valor

    @property
    def campos_dudosos(self) -> tuple[str, ...]:
        dudosos = [campo for campo in CAMPOS if self.confianza(campo) is not Confianza.ALTA]
        if self.monto is None and "monto" not in dudosos:
            dudosos.insert(0, "monto")
        return tuple(dudosos)

    @property
    def listo_para_guardar(self) -> bool:
        """Sin dudas y con monto. Aun así, el bot pide confirmación humana."""
        return self.monto is not None and not self.campos_dudosos

    def notas_de(self, campo: str) -> tuple[Nota, ...]:
        return tuple(n for n in self.notas if n.campo == campo)
