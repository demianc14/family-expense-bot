"""Extracción de gastos desde texto libre.

El LLM propone; este módulo verifica y decide. Nada de Telegram acá: esto se
testea sin levantar el bot y sin llamar a ningún LLM real.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Protocol

from pydantic import ValidationError

from family_expense_bot.extraction.categorias import Taxonomia
from family_expense_bot.extraction.schema import Confianza, GastoExtraido, Nota, RespuestaLLM
from family_expense_bot.extraction.verificacion import monto_respaldado_por_texto, parsear_monto

ANTIGUEDAD_MAXIMA = timedelta(days=365)

_COMO_SE_LLAMA = {
    "monto": "el monto",
    "categoria": "la categoría",
    "fecha": "la fecha",
    "atribuido_a": "de quién es el gasto",
}

PROMPT = """Extraes gastos de mensajes de Telegram de una familia ecuatoriana.
Los montos siempre están en dólares estadounidenses.

Hoy es {hoy}.

Categorías permitidas (usa exactamente uno de estos nombres):
{categorias}

Reglas:
- `monto`: copia el número tal como aparece en el mensaje. No lo calcules ni lo redondees.
- `fecha`: resuelve expresiones como "ayer" o "el lunes" contra la fecha de hoy. Si el
  mensaje no menciona ninguna fecha, devuelve null.
- `quien_paga`: si el mensaje dice que OTRA persona asumió el costo, copia tal cual cómo
  la nombra ("mi hija", "mi esposo", "mi ñaña"). Si dice que el gasto es compartido o de
  la casa, copia esa frase ("gasto de la casa", "compartido"). No resuelvas parentescos:
  solo copia las palabras.
  - Si quien paga es quien escribe, o el mensaje no lo dice, devuelve null.
  - Si alguien aparece solo como beneficiario ("la pensión del colegio de ...", "el regalo
    para ...") sin decir de qué bolsillo salió, devuelve null con confianza `baja`.
  - Nunca deduzcas que es de la casa por el tipo de gasto.
- Declara confianza `baja` cuando el mensaje sea ambiguo, incompleto o tenga varios gastos.
  Preferimos preguntarle a la persona antes que adivinar: es dinero real.

Mensaje:
{texto}"""


class ErrorDeExtraccion(Exception):
    """El LLM devolvió algo que no respeta el contrato."""


class ClienteLLM(Protocol):
    def completar_json(self, prompt: str, schema: dict[str, object]) -> str: ...


def construir_prompt(texto: str, taxonomia: Taxonomia, hoy: date) -> str:
    """Al LLM no le llega quién escribe ni quiénes son la familia: solo cita frases."""
    return PROMPT.format(
        hoy=hoy.isoformat(), categorias=taxonomia.describir_para_prompt(), texto=texto
    )


def extraer(
    texto: str,
    cliente: ClienteLLM,
    taxonomia: Taxonomia,
    remitente: str,
    hoy: date | None = None,
) -> GastoExtraido:
    """`remitente` es el rol de quien escribe, ya resuelto desde su ID de Telegram."""
    if remitente not in taxonomia.personas:
        # Falla antes de gastar una llamada al LLM: es un error de configuración.
        raise ValueError(
            f"Remitente {remitente!r} no es una persona de la taxonomía {taxonomia.personas}"
        )

    hoy = hoy or date.today()
    crudo = cliente.completar_json(
        construir_prompt(texto, taxonomia, hoy), RespuestaLLM.model_json_schema()
    )
    try:
        respuesta = RespuestaLLM.model_validate_json(crudo)
    except ValidationError as e:
        raise ErrorDeExtraccion(f"El LLM no respetó el contrato: {e}") from e

    notas: list[Nota] = []
    monto, confianza_monto = _verificar_monto(respuesta, texto, notas)
    categoria, confianza_categoria = _verificar_categoria(respuesta, taxonomia, notas)
    fecha, confianza_fecha = _verificar_fecha(respuesta, hoy, notas)
    atribuido_a, confianza_atribuido_a = _verificar_atribucion(
        respuesta, texto, remitente, taxonomia, notas
    )
    _explicar_dudas_sin_nota(
        {
            "monto": confianza_monto,
            "categoria": confianza_categoria,
            "fecha": confianza_fecha,
            "atribuido_a": confianza_atribuido_a,
        },
        notas,
    )

    return GastoExtraido(
        monto=monto,
        categoria=categoria,
        fecha=fecha,
        atribuido_a=atribuido_a,
        descripcion=respuesta.descripcion.strip(),
        texto_original=texto,
        remitente=remitente,
        confianza_monto=confianza_monto,
        confianza_categoria=confianza_categoria,
        confianza_fecha=confianza_fecha,
        confianza_atribuido_a=confianza_atribuido_a,
        notas=tuple(notas),
    )


def _explicar_dudas_sin_nota(confianzas: dict[str, Confianza], notas: list[Nota]) -> None:
    """Cuando el propio LLM declara duda, ninguna regla nuestra deja nota.

    Sin esto la persona ve un ❓ sin explicación y tiene que adivinar qué revisar.
    """
    for campo, confianza in confianzas.items():
        if confianza is not Confianza.ALTA and not any(n.campo == campo for n in notas):
            notas.append(Nota(campo=campo, texto=f"No estoy seguro de {_COMO_SE_LLAMA[campo]}."))


def _verificar_monto(
    respuesta: RespuestaLLM, texto: str, notas: list[Nota]
) -> tuple[Decimal | None, Confianza]:
    def nota(t: str) -> None:
        notas.append(Nota(campo="monto", texto=t))

    if respuesta.monto is None:
        nota("El mensaje no trae un monto.")
        return None, Confianza.BAJA

    monto = parsear_monto(respuesta.monto)
    if monto is None:
        nota(f"No pude leer {respuesta.monto!r} como un número.")
        return None, Confianza.BAJA
    if monto <= 0:
        nota(f"El monto {monto} no es positivo.")
        return None, Confianza.BAJA
    if not monto_respaldado_por_texto(monto, texto):
        # El LLM devolvió un número que nadie escribió: no se acepta, por seguro
        # que se declare.
        nota(f"El monto {monto} no aparece en el mensaje original.")
        return monto, Confianza.BAJA
    return monto, respuesta.confianza_monto


def _verificar_categoria(
    respuesta: RespuestaLLM, taxonomia: Taxonomia, notas: list[Nota]
) -> tuple[str, Confianza]:
    if not taxonomia.contiene(respuesta.categoria):
        notas.append(
            Nota(
                campo="categoria",
                texto=(
                    f"La categoría {respuesta.categoria!r} no está en la lista; "
                    f"queda como {taxonomia.fallback!r}."
                ),
            )
        )
        return taxonomia.fallback, Confianza.BAJA
    assert respuesta.categoria is not None  # contiene() ya lo garantizó
    return respuesta.categoria.strip().lower(), respuesta.confianza_categoria


def _verificar_fecha(
    respuesta: RespuestaLLM, hoy: date, notas: list[Nota]
) -> tuple[date | None, Confianza]:
    def nota(t: str) -> None:
        notas.append(Nota(campo="fecha", texto=t))

    if respuesta.fecha is None:
        nota("El mensaje no menciona fecha; se asume hoy.")
        return hoy, Confianza.MEDIA

    try:
        fecha = date.fromisoformat(respuesta.fecha)
    except ValueError:
        nota(f"No pude leer {respuesta.fecha!r} como fecha; se asume hoy.")
        return hoy, Confianza.BAJA

    if fecha > hoy:
        nota(f"La fecha {fecha} está en el futuro.")
        return fecha, Confianza.BAJA
    if hoy - fecha > ANTIGUEDAD_MAXIMA:
        nota(f"La fecha {fecha} tiene más de un año.")
        return fecha, Confianza.BAJA
    return fecha, respuesta.confianza_fecha


def _verificar_atribucion(
    respuesta: RespuestaLLM,
    texto: str,
    remitente: str,
    taxonomia: Taxonomia,
    notas: list[Nota],
) -> tuple[str, Confianza]:
    """Quién asume el costo. El LLM cita; acá se resuelve y se verifica.

    Por defecto, quien escribe, pero nunca con confianza alta: es una suposición
    y la persona tiene que verla. La atribución compartida jamás sale de ese
    valor por defecto. Y, como con el monto, nada que el LLM afirme se acepta
    con confianza si el mensaje no lo respalda.
    """

    def nota(t: str) -> None:
        notas.append(Nota(campo="atribuido_a", texto=t))

    cita = respuesta.quien_paga
    confianza = respuesta.confianza_quien_paga
    if cita is None or not cita.strip():
        if confianza is Confianza.BAJA:
            # El LLM vio a alguien en el mensaje pero no pudo decidir quién paga.
            nota("No queda claro quién asume este gasto; por ahora quedó como tuyo.")
            return remitente, Confianza.BAJA
        nota("El mensaje no dice de quién es el gasto; asumí que es tuyo porque lo escribiste tú.")
        return remitente, Confianza.MEDIA

    compartida = taxonomia.compartida.nombre
    if taxonomia.es_compartida(cita):
        if not taxonomia.menciona_gasto_compartido(texto):
            nota("El mensaje no dice explícitamente que sea un gasto compartido.")
            return compartida, Confianza.BAJA
        return compartida, confianza

    personas = taxonomia.personas_en(cita, remitente)
    if len(personas) > 1:
        nota(f"{cita!r} nombra a más de una persona; por ahora quedó como tuyo.")
        return remitente, Confianza.BAJA
    if not personas:
        nota(f"{cita!r} no es nadie de la lista; por ahora quedó como tuyo.")
        return remitente, Confianza.BAJA

    persona = personas[0]
    if not taxonomia.menciona_a(persona, remitente, texto):
        # La cita no está en el mensaje: el LLM la inventó.
        nota(f"El mensaje no menciona a {taxonomia.etiquetas[persona]}.")
        return persona, Confianza.BAJA
    return persona, confianza
