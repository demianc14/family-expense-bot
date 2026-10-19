"""Texto que ve la persona. Funciones puras: se testean sin levantar el bot.

Lo que el bot muestra no es un resumen bonito: es la evidencia con la que
alguien decide si guardar un gasto. Por eso lo dudoso se marca, no se esconde.
"""

from collections.abc import Collection, Mapping

from family_expense_bot.dashboard.presupuesto import (
    Consulta,
    EstadoBolsillo,
    ResumenPresupuesto,
    Veredicto,
)
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
    bolsillo: str | None = None,
) -> str:
    """`corregidos`: campos que la persona ya cambió; no llevan duda ni notas.

    `bolsillo`: etiqueta del bolsillo contra el que cuenta, solo si quien asume
    el gasto tiene presupuesto.
    """
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
        *([f"Bolsillo: {bolsillo}"] if bolsillo is not None else []),
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
    bolsillo: str | None = None,
) -> str:
    return (
        f"{resumen(gasto, etiquetas, corregidos, bolsillo)}\n\n"
        "¿Lo guardo? Si no es de quien dice, tócalo abajo."
    )


def falta_monto(gasto: GastoExtraido, etiquetas: Mapping[str, str] | None = None) -> str:
    """Sin monto no hay nada que guardar: se pide de nuevo, no se inventa."""
    return (
        f"{resumen(gasto, etiquetas)}\n\n"
        "No encontré el monto, así que no puedo guardarlo. "
        "¿Me lo escribes de nuevo incluyendo el valor?"
    )


def guardado(
    registrado: GastoRegistrado,
    etiquetas: Mapping[str, str] | None = None,
    presupuesto: str | None = None,
) -> str:
    """`presupuesto`: la línea de linea_de_bolsillo(), si quien asume el gasto tiene uno."""
    quien = (etiquetas or {}).get(registrado.atribuido_a, registrado.atribuido_a)
    texto = (
        f"Guardado ✅\n\n"
        f"${registrado.monto:,.2f} en {registrado.categoria}, de {quien}, "
        f"el {registrado.fecha.strftime('%d/%m/%Y')}."
    )
    return f"{texto}\n\n{presupuesto}" if presupuesto else texto


def linea_de_bolsillo(estado: EstadoBolsillo | None, mes: int) -> str:
    """Cómo quedó el bolsillo después de guardar. None = el gasto no cuenta en ninguno."""
    if estado is None:
        return "Este gasto no cuenta en ningún bolsillo del presupuesto."
    nombre = f"{estado.etiqueta} de {MESES[mes - 1]}"
    if estado.excedido:
        return (
            f"⚠️ {nombre}: te pasaste ${-estado.disponible:,.2f} "
            f"(llevas ${estado.gastado:,.2f} de ${estado.asignado:,.2f})."
        )
    return f"{nombre}: quedan ${estado.disponible:,.2f} de ${estado.asignado:,.2f}."


def descartado() -> str:
    return "Listo, no guardé nada. ¿Me lo escribes de otra forma?"


def solo_quien_lo_escribio() -> str:
    """Alerta para quien toca los botones del gasto de otro (máx. 200 caracteres)."""
    return "Este gasto lo confirma quien lo escribió. Si es tuyo, escríbelo tú."


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
    "Escribe /reporte para ver el total del mes, /presupuesto para ver "
    "cuánto te queda en cada bolsillo y /puedo 30 concierto para saber si te "
    "conviene un gasto."
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


def presupuesto(resumen: ResumenPresupuesto) -> str:
    lineas = [
        f"Presupuesto de {MESES[resumen.mes - 1]} {resumen.anio}",
        f"Ingreso: ${resumen.ingreso:,.2f}",
        "",
    ]
    for b in resumen.bolsillos:
        cabeza = f"{b.etiqueta} ({b.porcentaje}%)"
        if not b.se_gasta:
            lineas.append(f"{cabeza}: aparta ${b.asignado:,.2f}")
        elif b.excedido:
            lineas.append(
                f"{cabeza}: ${b.gastado:,.2f} de ${b.asignado:,.2f} · "
                f"⚠️ te pasaste ${-b.disponible:,.2f}"
            )
        else:
            lineas.append(
                f"{cabeza}: ${b.gastado:,.2f} de ${b.asignado:,.2f} · quedan ${b.disponible:,.2f}"
            )
    if resumen.sin_bolsillo:
        categorias = ", ".join(resumen.categorias_sin_bolsillo)
        lineas += ["", f"Sin bolsillo: ${resumen.sin_bolsillo:,.2f} ({categorias})"]
    return "\n".join(lineas)


def sin_presupuesto() -> str:
    return (
        "No tengo tu ingreso mensual, así que no puedo armar tu presupuesto. "
        "Quien mantiene el bot lo configura en INGRESOS_MENSUALES."
    )


def _ritmo(consulta: Consulta) -> str:
    """Lo que le toca al bolsillo para lo que queda del mes. Vacío el último día."""
    n = consulta.dias_restantes
    if n == 0:
        return ""
    if n == 1:
        return f", y al día que falta del mes le tocan ${consulta.reserva:,.2f}"
    return f", y a los {n} días que faltan del mes les tocan ${consulta.reserva:,.2f}"


def respuesta_consulta(consulta: Consulta, categoria: str, categoria_dudosa: bool) -> str:
    """¿Puedo gastar esto? El veredicto lo calcula evaluar_gasto(), no el LLM."""
    b = consulta.bolsillo
    if consulta.veredicto is Veredicto.SIN_BOLSILLO or b is None:
        lineas = [
            f"Un gasto de {categoria} no cuenta en ningún bolsillo de tu presupuesto, "
            "así que no tengo con qué compararlo."
        ]
    elif consulta.veredicto is Veredicto.NO:
        assert consulta.disponible_despues is not None
        lineas = [
            f"❌ No te lo recomiendo: en {b.etiqueta} te quedan ${b.disponible:,.2f} y el gasto "
            f"es de ${consulta.monto:,.2f}; te pasarías por ${-consulta.disponible_despues:,.2f}."
        ]
        if consulta.alternativa is not None:
            lineas.append(
                f"En tu {consulta.alternativa.etiqueta} sí te alcanza (te quedan "
                f"${consulta.alternativa.disponible:,.2f}): si lo sacas de ahí, di "
                f'"{consulta.marcador_alternativa}" al registrarlo.'
            )
    elif consulta.veredicto is Veredicto.DIFIERE:
        lineas = [
            f"⏸️ Mejor difiérelo. Te alcanza, pero en {b.etiqueta} quedarías con "
            f"${consulta.disponible_despues:,.2f}{_ritmo(consulta)}."
        ]
    else:
        lineas = [
            f"✅ Sí, te alcanza. En {b.etiqueta} te quedan ${b.disponible:,.2f} de "
            f"${b.asignado:,.2f}; con este gasto quedarías con "
            f"${consulta.disponible_despues:,.2f}{_ritmo(consulta)}."
        ]
    if categoria_dudosa:
        lineas.append(f"{DUDOSO} Lo tomé como {categoria}; si es otra cosa, la respuesta cambia.")
    lineas.append("No guardé nada. Si decides hacerlo, escríbemelo como gasto.")
    return "\n\n".join(lineas)


def consulta_sin_monto() -> str:
    return (
        "No encontré cuánto sería el gasto en tu mensaje. "
        "Escríbemelo con el monto, por ejemplo: /puedo 30 concierto"
    )
