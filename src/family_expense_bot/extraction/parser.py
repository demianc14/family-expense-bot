"""Extracción de gastos desde texto libre.

El LLM propone; este módulo verifica y decide. Nada de Telegram acá: esto se
testea sin levantar el bot y sin llamar a ningún LLM real.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Protocol

from pydantic import ValidationError

from family_expense_bot.extraction.categorias import Taxonomia
from family_expense_bot.extraction.schema import Confianza, GastoExtraido, RespuestaLLM
from family_expense_bot.extraction.verificacion import monto_respaldado_por_texto, parsear_monto

ANTIGUEDAD_MAXIMA = timedelta(days=365)

PROMPT = """Extraes gastos de mensajes de WhatsApp/Telegram de una familia ecuatoriana.
Los montos siempre están en dólares estadounidenses.

Hoy es {hoy}.

Categorías permitidas (usa exactamente uno de estos nombres):
{categorias}

Reglas:
- `monto`: copia el número tal como aparece en el mensaje. No lo calcules ni lo redondees.
- `fecha`: resuelve expresiones como "ayer" o "el lunes" contra la fecha de hoy. Si el
  mensaje no menciona ninguna fecha, devuelve null.
- Declara confianza `baja` cuando el mensaje sea ambiguo, incompleto o tenga varios gastos.
  Preferimos preguntarle a la persona antes que adivinar: es dinero real.

Mensaje:
{texto}"""


class ErrorDeExtraccion(Exception):
    """El LLM devolvió algo que no respeta el contrato."""


class ClienteLLM(Protocol):
    def completar_json(self, prompt: str, schema: dict[str, object]) -> str: ...


def construir_prompt(texto: str, taxonomia: Taxonomia, hoy: date) -> str:
    return PROMPT.format(
        hoy=hoy.isoformat(), categorias=taxonomia.describir_para_prompt(), texto=texto
    )


def extraer(
    texto: str, cliente: ClienteLLM, taxonomia: Taxonomia, hoy: date | None = None
) -> GastoExtraido:
    hoy = hoy or date.today()
    crudo = cliente.completar_json(
        construir_prompt(texto, taxonomia, hoy), RespuestaLLM.model_json_schema()
    )
    try:
        respuesta = RespuestaLLM.model_validate_json(crudo)
    except ValidationError as e:
        raise ErrorDeExtraccion(f"El LLM no respetó el contrato: {e}") from e

    notas: list[str] = []
    monto, confianza_monto = _verificar_monto(respuesta, texto, notas)
    categoria, confianza_categoria = _verificar_categoria(respuesta, taxonomia, notas)
    fecha, confianza_fecha = _verificar_fecha(respuesta, hoy, notas)

    return GastoExtraido(
        monto=monto,
        categoria=categoria,
        fecha=fecha,
        descripcion=respuesta.descripcion.strip(),
        texto_original=texto,
        confianza_monto=confianza_monto,
        confianza_categoria=confianza_categoria,
        confianza_fecha=confianza_fecha,
        notas=tuple(notas),
    )


def _verificar_monto(
    respuesta: RespuestaLLM, texto: str, notas: list[str]
) -> tuple[Decimal | None, Confianza]:
    if respuesta.monto is None:
        notas.append("El mensaje no trae un monto.")
        return None, Confianza.BAJA

    monto = parsear_monto(respuesta.monto)
    if monto is None:
        notas.append(f"No pude leer {respuesta.monto!r} como un número.")
        return None, Confianza.BAJA
    if monto <= 0:
        notas.append(f"El monto {monto} no es positivo.")
        return None, Confianza.BAJA
    if not monto_respaldado_por_texto(monto, texto):
        # El LLM devolvió un número que nadie escribió: no se acepta, por seguro
        # que se declare.
        notas.append(f"El monto {monto} no aparece en el mensaje original.")
        return monto, Confianza.BAJA
    return monto, respuesta.confianza_monto


def _verificar_categoria(
    respuesta: RespuestaLLM, taxonomia: Taxonomia, notas: list[str]
) -> tuple[str, Confianza]:
    if not taxonomia.contiene(respuesta.categoria):
        notas.append(
            f"La categoría {respuesta.categoria!r} no está en la taxonomía; "
            f"queda como {taxonomia.fallback!r}."
        )
        return taxonomia.fallback, Confianza.BAJA
    assert respuesta.categoria is not None  # contiene() ya lo garantizó
    return respuesta.categoria.strip().lower(), respuesta.confianza_categoria


def _verificar_fecha(
    respuesta: RespuestaLLM, hoy: date, notas: list[str]
) -> tuple[date | None, Confianza]:
    if respuesta.fecha is None:
        notas.append("El mensaje no menciona fecha; se asume hoy.")
        return hoy, Confianza.MEDIA

    try:
        fecha = date.fromisoformat(respuesta.fecha)
    except ValueError:
        notas.append(f"No pude leer {respuesta.fecha!r} como fecha; se asume hoy.")
        return hoy, Confianza.BAJA

    if fecha > hoy:
        notas.append(f"La fecha {fecha} está en el futuro.")
        return fecha, Confianza.BAJA
    if hoy - fecha > ANTIGUEDAD_MAXIMA:
        notas.append(f"La fecha {fecha} tiene más de un año.")
        return fecha, Confianza.BAJA
    return fecha, respuesta.confianza_fecha
