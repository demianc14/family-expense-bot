"""Texto que ve la persona. Funciones puras: se testean sin levantar el bot.

Lo que el bot muestra no es un resumen bonito: es la evidencia con la que
alguien decide si guardar un gasto. Por eso lo dudoso se marca, no se esconde.
"""

from collections.abc import Collection, Mapping

from family_expense_bot.dashboard.report import ResumenMensual
from family_expense_bot.extraction.schema import Confianza, GastoExtraido
from family_expense_bot.storage.repository import GastoRegistrado

DUDOSO = "❓"
CORREGIDO = "✏️"


def _marca(gasto: GastoExtraido, campo: str, corregidos: Collection[str]) -> str:
    if campo in corregidos:
        return f" {CORREGIDO}"
    return "" if gasto.confianza(campo) is Confianza.ALTA else f" {DUDOSO}"


def resumen(
    gasto: GastoExtraido,
    etiquetas: Mapping[str, str] | None = None,
    corregidos: Collection[str] = (),
) -> str:
    """`corregidos`: campos que la persona ya cambió; no llevan duda ni notas."""
    etiquetas = etiquetas or {}
    monto = f"${gasto.monto:,.2f}" if gasto.monto is not None else "—"
    fecha = gasto.fecha.strftime("%d/%m/%Y") if gasto.fecha else "—"
    quien = etiquetas.get(gasto.atribuido_a, gasto.atribuido_a)

    lineas = [
        "Entendí esto:",
        "",
        f"Monto: {monto}{_marca(gasto, 'monto', corregidos)}",
        f"Categoría: {gasto.categoria}{_marca(gasto, 'categoria', corregidos)}",
        f"Fecha: {fecha}{_marca(gasto, 'fecha', corregidos)}",
        f"De: {quien}{_marca(gasto, 'atribuido_a', corregidos)}",
        f"Descripción: {gasto.descripcion}",
    ]
    notas = [n for n in gasto.notas if n.campo not in corregidos]
    if notas:
        lineas += ["", *(f"{DUDOSO} {n.texto}" for n in notas)]
    return "\n".join(lineas)


def pedir_confirmacion(
    gasto: GastoExtraido,
    etiquetas: Mapping[str, str] | None = None,
    corregidos: Collection[str] = (),
) -> str:
    return (
        f"{resumen(gasto, etiquetas, corregidos)}\n\n"
        "¿Lo guardo? Si no es de quien dice, tócalo abajo."
    )


def falta_monto(gasto: GastoExtraido, etiquetas: Mapping[str, str] | None = None) -> str:
    """Sin monto no hay nada que guardar: se pide de nuevo, no se inventa."""
    return (
        f"{resumen(gasto, etiquetas)}\n\n"
        "No encontré el monto, así que no puedo guardarlo. "
        "¿Me lo escribes de nuevo incluyendo el valor?"
    )


def guardado(registrado: GastoRegistrado, etiquetas: Mapping[str, str] | None = None) -> str:
    quien = (etiquetas or {}).get(registrado.atribuido_a, registrado.atribuido_a)
    return (
        f"Guardado ✅\n\n"
        f"${registrado.monto:,.2f} en {registrado.categoria}, de {quien}, "
        f"el {registrado.fecha.strftime('%d/%m/%Y')}."
    )


def descartado() -> str:
    return "Listo, no guardé nada. ¿Me lo escribes de otra forma?"


def error_al_guardar() -> str:
    return (
        "⚠️ Dijiste que sí, pero no pude guardar el gasto: no quedó registrado. "
        "Escríbemelo de nuevo; si vuelve a pasar, avísale a quien mantiene el bot."
    )


def edicion_ignorada() -> str:
    """Editar no cambia un gasto ya enviado: corregir es decir que no y reescribir."""
    return (
        "Vi que editaste un mensaje, pero no cambio gastos por ediciones. "
        "Si el gasto quedó mal, toca No en su confirmación y escríbemelo de nuevo."
    )


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
    'por ejemplo "200 dólares supermercado" o "pagué 15 de taxi ayer".\n\n'
    "Si no dices de quién es, asumo que es tuyo. Para gastos compartidos, "
    'dilo: "gasolina de la casa".\n\n'
    "Te muestro lo que entendí y solo guardo si me dices que sí.\n\n"
    "Escribe /reporte para ver el total del mes."
)


MESES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def reporte(resumen: ResumenMensual) -> str:
    titulo = f"Gastos de {MESES[resumen.mes - 1]} {resumen.anio}"
    if resumen.cantidad == 0:
        return f"{titulo}\n\nTodavía no hay gastos registrados este mes."

    lineas = [titulo, "", f"Total: ${resumen.total:,.2f} en {resumen.cantidad} gasto(s)", ""]
    ancho = max(len(c.categoria) for c in resumen.por_categoria)
    for c in resumen.por_categoria:
        lineas.append(f"  {c.categoria.ljust(ancho)}  ${c.total:>9,.2f}  ({c.cantidad})")

    calidad = resumen.calidad
    if calidad.tasa_acierto_directo is not None:
        lineas += [
            "",
            f"De {calidad.intentos} mensaje(s) que leí, {calidad.confirmadas_sin_correccion} "
            f"({calidad.tasa_acierto_directo:.0%}) los entendí bien a la primera.",
        ]
    return "\n".join(lineas)
