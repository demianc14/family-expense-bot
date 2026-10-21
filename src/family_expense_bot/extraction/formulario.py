"""Gastos que entran por el formulario de la Mini App, campo por campo.

No hay LLM de por medio: cada valor lo escribió o lo eligió una persona en su
casilla. Por eso acá no se degrada la confianza como en el parser: lo que no
cuadra se rechaza con un motivo, y lo que cuadra queda en `alta`.

La app es el presupuesto personal de quien la abre, así que todo gasto es suyo:
no hay "¿de quién es?" y el formulario no acepta otra persona (un `atribuido_a`
en el cuerpo se ignora). No es deducir la atribución como en el chat, donde el
texto podía nombrar a otro: acá registrar en tu app es decir que es tuyo.

Lo que sí se mantiene de las reglas del chat:
- "Del fondo" se dice, no se deduce: es un interruptor explícito que agrega el
  marcador del yaml al texto, y `Taxonomia.bolsillo_de` lo resuelve al leer,
  igual que si se hubiera escrito en el chat.
"""

import re
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from family_expense_bot.extraction.categorias import Taxonomia
from family_expense_bot.extraction.parser import ANTIGUEDAD_MAXIMA
from family_expense_bot.extraction.schema import Confianza, GastoExtraido

LARGO_MAXIMO_DESCRIPCION = 200

_MONTO = re.compile(r"\d{1,9}(\.\d{1,2})?")
"""Estricto, como INGRESOS_MENSUALES: nada de "1e3", "1_200" ni comas. Nueve
dígitos enteros sobran para un gasto de una persona."""


class FormularioInvalido(ValueError):
    """Un campo del formulario no cuadra. El mensaje es lo que ve la persona."""


class CamposFormulario(BaseModel):
    """Lo que manda la Mini App. Todo llega como texto o como elección de una lista."""

    model_config = ConfigDict(frozen=True)

    monto: str
    categoria: str
    fecha: date
    descripcion: str
    bolsillo: str | None = None
    """Un bolsillo con marcadores ("fondo") al que la persona mandó el gasto a propósito."""


def leer_monto(crudo: str) -> Decimal:
    crudo = crudo.strip()
    if not _MONTO.fullmatch(crudo):
        raise FormularioInvalido(
            f"{crudo!r} no es un monto: van dígitos y hasta dos decimales, con punto (ej. 12.50)"
        )
    monto = Decimal(crudo)
    if monto <= 0:
        raise FormularioInvalido("El monto tiene que ser mayor que cero")
    return monto


def leer_categoria(nombre: str, taxonomia: Taxonomia) -> str:
    """Solo categorías activas: una candidata no recibe datos reales."""
    if not taxonomia.contiene(nombre):
        raise FormularioInvalido(f"{nombre!r} no es una categoría activa")
    return nombre.strip().lower()


def bolsillo_destino(categoria: str, bolsillo: str | None, taxonomia: Taxonomia) -> str | None:
    """Contra qué bolsillo cuenta, con la misma regla que `bolsillo_de` aplica al texto."""
    return taxonomia.bolsillo_de(categoria, _marcador(bolsillo, taxonomia))


def armar_gasto(
    campos: CamposFormulario, remitente: str, taxonomia: Taxonomia, hoy: date
) -> GastoExtraido:
    monto = leer_monto(campos.monto)
    categoria = leer_categoria(campos.categoria, taxonomia)

    if campos.fecha > hoy:
        raise FormularioInvalido(f"La fecha {campos.fecha} está en el futuro")
    if hoy - campos.fecha > ANTIGUEDAD_MAXIMA:
        raise FormularioInvalido(f"La fecha {campos.fecha} tiene más de un año")

    descripcion = " ".join(campos.descripcion.split())
    if not descripcion:
        raise FormularioInvalido("Escribe una descripción corta (ej. almuerzo, gasolina)")
    if len(descripcion) > LARGO_MAXIMO_DESCRIPCION:
        raise FormularioInvalido(
            f"La descripción es muy larga (máximo {LARGO_MAXIMO_DESCRIPCION} caracteres)"
        )

    marcador = _marcador(campos.bolsillo, taxonomia)
    return GastoExtraido(
        monto=monto,
        categoria=categoria,
        fecha=campos.fecha,
        atribuido_a=remitente,
        descripcion=descripcion,
        texto_original=f"{descripcion}, {marcador}" if marcador else descripcion,
        remitente=remitente,
        confianza_monto=Confianza.ALTA,
        confianza_categoria=Confianza.ALTA,
        confianza_fecha=Confianza.ALTA,
        confianza_atribuido_a=Confianza.ALTA,
    )


def _marcador(bolsillo: str | None, taxonomia: Taxonomia) -> str:
    """El primer marcador del yaml para ese bolsillo. Vacío si no se eligió ninguno."""
    if bolsillo is None:
        return ""
    elegido = next((b for b in taxonomia.bolsillos if b.nombre == bolsillo), None)
    if elegido is None or not elegido.marcadores:
        raise FormularioInvalido(f"No se puede mandar un gasto al bolsillo {bolsillo!r}")
    return elegido.marcadores[0]
