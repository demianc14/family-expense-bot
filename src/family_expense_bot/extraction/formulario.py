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
- La categoría de respaldo (`otros`) no pertenece a ningún bolsillo, así que el
  formulario exige elegir uno: se guarda igual, como marcador en el texto.

La subcategoría es una etiqueta libre y opcional ("Tipti" dentro de
alimentacion). No decide nada: el bolsillo y el presupuesto salen de la
categoría.
"""

import re
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from family_expense_bot.extraction.categorias import Taxonomia
from family_expense_bot.extraction.parser import ANTIGUEDAD_MAXIMA
from family_expense_bot.extraction.schema import Confianza, GastoExtraido
from family_expense_bot.storage.repository import IngresoNuevo

LARGO_MAXIMO_DESCRIPCION = 200
LARGO_MAXIMO_SUBCATEGORIA = 40

_MONTO = re.compile(r"\d{1,9}([.,]\d{1,2})?")
"""Estricto: nada de "1e3", "1_200" ni separador de miles. Coma o punto solo para
los centavos, porque muchos teclados numéricos solo traen la coma. "1,200" se
rechaza en vez de adivinar si son mil doscientos o uno con veinte. Nueve
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
    """Un bolsillo con marcadores al que la persona mandó el gasto a propósito."""

    subcategoria: str | None = None


def leer_monto(crudo: str) -> Decimal:
    crudo = crudo.strip()
    if not _MONTO.fullmatch(crudo):
        raise FormularioInvalido(
            f"{crudo!r} no es un monto: van dígitos y hasta dos decimales, "
            "con coma o punto (ej. 12,50), sin separador de miles"
        )
    monto = Decimal(crudo.replace(",", "."))
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
    if categoria == taxonomia.fallback and bolsillo is None:
        raise FormularioInvalido(
            f"Con {categoria!r} elige de qué bolsillo sale: esa categoría no tiene uno propio"
        )
    return taxonomia.bolsillo_de(categoria, _marcador(bolsillo, taxonomia, categoria))


def leer_subcategoria(crudo: str | None) -> str | None:
    """Etiqueta libre y opcional. Vacía = sin subcategoría."""
    limpia = " ".join((crudo or "").split())
    if len(limpia) > LARGO_MAXIMO_SUBCATEGORIA:
        raise FormularioInvalido(
            f"La subcategoría es muy larga (máximo {LARGO_MAXIMO_SUBCATEGORIA} caracteres)"
        )
    return limpia or None


def armar_gasto(
    campos: CamposFormulario, remitente: str, taxonomia: Taxonomia, hoy: date
) -> GastoExtraido:
    monto = leer_monto(campos.monto)
    categoria = leer_categoria(campos.categoria, taxonomia)
    _leer_fecha(campos.fecha, hoy)
    descripcion = _leer_descripcion(campos.descripcion, "almuerzo, gasolina")

    bolsillo_destino(categoria, campos.bolsillo, taxonomia)  # `otros` exige un bolsillo
    marcador = _marcador(campos.bolsillo, taxonomia, categoria)
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


class CamposIngreso(BaseModel):
    """Un ingreso extra desde la Mini App. Sin bolsillo: se reparte como el sueldo."""

    model_config = ConfigDict(frozen=True)

    monto: str
    categoria: str
    fecha: date
    descripcion: str
    subcategoria: str | None = None


def armar_ingreso(
    campos: CamposIngreso, persona: str, taxonomia: Taxonomia, hoy: date
) -> IngresoNuevo:
    """Mismas reglas que un gasto: monto estricto, fecha razonable y descripción corta.

    El ingreso es siempre de quien lo registra, igual que los gastos de la app.
    """
    if not taxonomia.contiene_ingreso(campos.categoria):
        raise FormularioInvalido(f"{campos.categoria!r} no es una categoría de ingreso")
    return IngresoNuevo(
        monto=leer_monto(campos.monto),
        categoria=campos.categoria.strip().lower(),
        subcategoria=leer_subcategoria(campos.subcategoria),
        persona=persona,
        fecha=_leer_fecha(campos.fecha, hoy),
        descripcion=_leer_descripcion(campos.descripcion, "un trabajo extra, un regalo"),
    )


def _leer_fecha(fecha: date, hoy: date) -> date:
    if fecha > hoy:
        raise FormularioInvalido(f"La fecha {fecha} está en el futuro")
    if hoy - fecha > ANTIGUEDAD_MAXIMA:
        raise FormularioInvalido(f"La fecha {fecha} tiene más de un año")
    return fecha


def _leer_descripcion(crudo: str, ejemplo: str) -> str:
    descripcion = " ".join(crudo.split())
    if not descripcion:
        raise FormularioInvalido(f"Escribe una descripción corta (ej. {ejemplo})")
    if len(descripcion) > LARGO_MAXIMO_DESCRIPCION:
        raise FormularioInvalido(
            f"La descripción es muy larga (máximo {LARGO_MAXIMO_DESCRIPCION} caracteres)"
        )
    return descripcion


def _marcador(bolsillo: str | None, taxonomia: Taxonomia, categoria: str) -> str:
    """El primer marcador del yaml para ese bolsillo. Vacío si no se eligió ninguno.

    Con la categoría de respaldo también valen los `marcadores_otros` ("de lujos").
    """
    if bolsillo is None:
        return ""
    elegido = next((b for b in taxonomia.bolsillos if b.nombre == bolsillo), None)
    if elegido is not None and elegido.marcadores:
        return elegido.marcadores[0]
    if elegido is not None and categoria == taxonomia.fallback and elegido.marcadores_otros:
        return elegido.marcadores_otros[0]
    raise FormularioInvalido(f"No se puede mandar un gasto al bolsillo {bolsillo!r}")
