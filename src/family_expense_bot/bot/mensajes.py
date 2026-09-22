"""Texto que ve la persona. Funciones puras: se testean sin levantar el bot.

Lo que el bot muestra no es un resumen bonito: es la evidencia con la que
alguien decide si guardar un gasto. Por eso lo dudoso se marca, no se esconde.
"""

from family_expense_bot.extraction.schema import Confianza, GastoExtraido
from family_expense_bot.storage.repository import GastoRegistrado

DUDOSO = "❓"


def _marca(confianza: Confianza) -> str:
    return "" if confianza is Confianza.ALTA else f" {DUDOSO}"


def resumen(gasto: GastoExtraido) -> str:
    monto = f"${gasto.monto:,.2f}" if gasto.monto is not None else "—"
    fecha = gasto.fecha.strftime("%d/%m/%Y") if gasto.fecha else "—"

    lineas = [
        "Entendí esto:",
        "",
        f"Monto: {monto}{_marca(gasto.confianza_monto)}",
        f"Categoría: {gasto.categoria}{_marca(gasto.confianza_categoria)}",
        f"Fecha: {fecha}{_marca(gasto.confianza_fecha)}",
        f"Descripción: {gasto.descripcion}",
    ]
    if gasto.notas:
        lineas += ["", *(f"{DUDOSO} {nota}" for nota in gasto.notas)]
    return "\n".join(lineas)


def pedir_confirmacion(gasto: GastoExtraido) -> str:
    return f"{resumen(gasto)}\n\n¿Lo guardo?"


def falta_monto(gasto: GastoExtraido) -> str:
    """Sin monto no hay nada que guardar: se pide de nuevo, no se inventa."""
    return (
        f"{resumen(gasto)}\n\n"
        "No encontré el monto, así que no puedo guardarlo. "
        "¿Me lo escribes de nuevo incluyendo el valor?"
    )


def guardado(registrado: GastoRegistrado) -> str:
    return (
        f"Guardado ✅\n\n"
        f"${registrado.monto:,.2f} en {registrado.categoria} "
        f"el {registrado.fecha.strftime('%d/%m/%Y')}."
    )


def descartado() -> str:
    return "Listo, no guardé nada. ¿Me lo escribes de otra forma?"


def error_de_extraccion() -> str:
    return "No pude entender ese mensaje. ¿Me lo escribes de otra forma?"


def cuota_agotada() -> str:
    """El plan gratuito de Gemini permite pocas extracciones por día."""
    return (
        "Por hoy ya no puedo leer más mensajes automáticamente "
        "(se acabó la cuota gratuita del servicio). Intenta mañana."
    )


def demoro_demasiado() -> str:
    return "El servicio está lento y no alcanzó a responder. ¿Lo intentas de nuevo?"


PROCESANDO = "Leyendo tu mensaje..."

BIENVENIDA = (
    "Hola 👋 Escríbeme tus gastos como los dirías normalmente, "
    'por ejemplo "200 dólares mercado" o "pagué 15 de taxi ayer".\n\n'
    "Te muestro lo que entendí y solo guardo si me dices que sí."
)
