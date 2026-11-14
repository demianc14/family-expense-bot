"""El estado de cuenta cuadra o no entra.

`verificar` recibe un estado ya leído y los estados vigentes de la base, y
devuelve todos los problemas (cualquiera rechaza el estado completo) y los
avisos (no rechazan). Es una función pura: no lee archivos ni toca la base.
Todo en centavos enteros.

Qué alcanza a ver, según lo que traiga el banco:

- **Con columna de saldo** se verifica fila por fila: una fila perdida, repetida
  o con el monto cambiado rompe la cadena en esa línea.
- **Sin columna de saldo** solo se verifica el total. Una fila perdida se nota
  porque el total no cuadra, pero no se sabe cuál; y dos errores que se
  compensan (+1 y -1 centavo) pasan sin que nada los delate.
"""

from collections import defaultdict
from collections.abc import Sequence
from datetime import timedelta

from pydantic import BaseModel, ConfigDict

from family_expense_bot.conciliacion.contrato import (
    EstadoLeido,
    EstadoVigente,
    MovimientoLeido,
    Problema,
    plata,
)


class Veredicto(BaseModel):
    model_config = ConfigDict(frozen=True)

    problemas: tuple[Problema, ...] = ()
    """Con uno solo, el estado se rechaza completo."""
    avisos: tuple[str, ...] = ()
    """Para que una persona los mire. No rechazan."""

    @property
    def cuadra(self) -> bool:
        return not self.problemas


def verificar(estado: EstadoLeido, vigentes: Sequence[EstadoVigente] = ()) -> Veredicto:
    repetido = next((v for v in vigentes if v.hash_archivo == estado.hash_archivo), None)
    if repetido is not None:
        # El mismo archivo: no hay nada más que decir de él.
        mensaje = (
            f"Este archivo ya se importó: es el estado #{repetido.id} "
            f"({repetido.banco}/{repetido.cuenta}, {_periodo(repetido)})."
        )
        return Veredicto(problemas=(Problema(codigo="archivo_repetido", mensaje=mensaje),))

    de_la_cuenta = [v for v in vigentes if (v.banco, v.cuenta) == (estado.banco, estado.cuenta)]
    problemas = [
        *_periodos_solapados(estado, de_la_cuenta),
        *_fechas_fuera_del_periodo(estado),
        *_saldo_corrido(estado),
    ]
    no_cuadra = _saldo_no_cuadra(estado)
    if no_cuadra is not None:
        problemas.append(no_cuadra)

    avisos = _continuidad(estado, de_la_cuenta)
    bloque = _bloque_duplicado(estado.movimientos)
    if bloque is not None:
        primero, segundo = bloque
        repite = (
            f"Las líneas {segundo[0].linea} a {segundo[-1].linea} repiten las líneas "
            f"{primero[0].linea} a {primero[-1].linea}"
        )
        if any(p.codigo.startswith("saldo") for p in problemas):
            suma = sum(m.monto_centavos for m in segundo)
            explica = ", justo la diferencia" if suma == -_diferencia(estado) else ""
            problemas.append(
                Problema(
                    codigo="bloque_duplicado",
                    linea=segundo[0].linea,
                    mensaje=f"{repite} y suman {plata(suma)}{explica}.",
                )
            )
        else:
            avisos.append(
                f"{repite}. El estado cuadra, así que entraron como movimientos reales: "
                "revisa que no sea un bloque pegado dos veces."
            )
    return Veredicto(problemas=tuple(problemas), avisos=tuple(avisos))


def _periodo(estado: EstadoLeido | EstadoVigente) -> str:
    return f"{estado.periodo_desde} a {estado.periodo_hasta}"


def _diferencia(estado: EstadoLeido) -> int:
    """Saldo final menos el que sale de sumar los movimientos al saldo anterior."""
    calculado = estado.saldo_anterior_centavos + sum(m.monto_centavos for m in estado.movimientos)
    return estado.saldo_final_centavos - calculado


def _saldo_no_cuadra(estado: EstadoLeido) -> Problema | None:
    """Saldo anterior + créditos - débitos = saldo final, al centavo."""
    diferencia = _diferencia(estado)
    if diferencia == 0:
        return None
    calculado = estado.saldo_final_centavos - diferencia
    return Problema(
        codigo="saldo_no_cuadra",
        mensaje=f"El estado no cuadra: saldo anterior {plata(estado.saldo_anterior_centavos)} + "
        f"créditos {plata(estado.creditos_centavos)} - débitos {plata(estado.debitos_centavos)} "
        f"= {plata(calculado)}, pero el saldo final es {plata(estado.saldo_final_centavos)} "
        f"(diferencia {plata(diferencia)}).",
    )


def _saldo_corrido(estado: EstadoLeido) -> list[Problema]:
    """Con columna de saldo, cada fila encadena con la anterior y la última da el saldo final."""
    problemas = []
    saldo = estado.saldo_anterior_centavos
    ultimo: MovimientoLeido | None = None
    for m in estado.movimientos:
        if m.saldo_centavos is None:
            continue
        esperado = saldo + m.monto_centavos
        if esperado != m.saldo_centavos:
            operacion = "+" if m.monto_centavos > 0 else "-"
            problemas.append(
                Problema(
                    codigo="saldo_corrido",
                    linea=m.linea,
                    mensaje=f"Línea {m.linea}: el saldo no encadena: {plata(saldo)} {operacion} "
                    f"{plata(abs(m.monto_centavos))} = {plata(esperado)}, pero la fila dice "
                    f"{plata(m.saldo_centavos)}.",
                )
            )
        # Sigue desde lo que dice la fila: así un error se reporta una vez, donde está.
        saldo, ultimo = m.saldo_centavos, m
    if ultimo is not None and ultimo.saldo_centavos != estado.saldo_final_centavos:
        assert ultimo.saldo_centavos is not None
        problemas.append(
            Problema(
                codigo="saldo_final",
                mensaje=f"El saldo final ({plata(estado.saldo_final_centavos)}) no coincide con "
                f"el de la última fila ({plata(ultimo.saldo_centavos)}, línea {ultimo.linea}).",
            )
        )
    return problemas


def _fechas_fuera_del_periodo(estado: EstadoLeido) -> list[Problema]:
    return [
        Problema(
            codigo="fecha_fuera_de_periodo",
            linea=m.linea,
            mensaje=f"Línea {m.linea}: la fecha {m.fecha} está fuera del periodo "
            f"{_periodo(estado)}.",
        )
        for m in estado.movimientos
        if not estado.periodo_desde <= m.fecha <= estado.periodo_hasta
    ]


def _periodos_solapados(estado: EstadoLeido, de_la_cuenta: list[EstadoVigente]) -> list[Problema]:
    """El mismo mes exportado de nuevo trae otro hash: lo delata el periodo."""
    return [
        Problema(
            codigo="periodo_solapado",
            mensaje=f"El periodo {_periodo(estado)} se cruza con el del estado #{v.id} "
            f"({_periodo(v)}) de la misma cuenta.",
        )
        for v in de_la_cuenta
        if v.periodo_desde <= estado.periodo_hasta and estado.periodo_desde <= v.periodo_hasta
    ]


def _continuidad(estado: EstadoLeido, de_la_cuenta: list[EstadoVigente]) -> list[str]:
    """El saldo final de un estado es el anterior del siguiente. Si no, avisa: no rechaza."""
    avisos = []
    un_dia = timedelta(days=1)
    for v in de_la_cuenta:
        if v.periodo_hasta + un_dia == estado.periodo_desde:
            diferencia = estado.saldo_anterior_centavos - v.saldo_final_centavos
            if diferencia:
                avisos.append(
                    f"El estado #{v.id} ({_periodo(v)}) cerró con {plata(v.saldo_final_centavos)} "
                    f"y este empieza con {plata(estado.saldo_anterior_centavos)}: hay "
                    f"{plata(diferencia)} de diferencia entre los dos."
                )
        if estado.periodo_hasta + un_dia == v.periodo_desde:
            diferencia = v.saldo_anterior_centavos - estado.saldo_final_centavos
            if diferencia:
                avisos.append(
                    f"Este estado cierra con {plata(estado.saldo_final_centavos)} y el #{v.id} "
                    f"({_periodo(v)}) empieza con {plata(v.saldo_anterior_centavos)}: hay "
                    f"{plata(diferencia)} de diferencia entre los dos."
                )
    return avisos


def _bloque_duplicado(
    movimientos: Sequence[MovimientoLeido],
) -> tuple[Sequence[MovimientoLeido], Sequence[MovimientoLeido]] | None:
    """El primer bloque de dos o más filas seguidas que se repite justo después.

    Compara fecha, descripción y monto. Una sola fila repetida no cuenta: dos
    cargos idénticos el mismo día son dos movimientos.
    """
    filas = [(m.fecha, m.descripcion, m.monto_centavos) for m in movimientos]
    posiciones = defaultdict(list)
    for i, fila in enumerate(filas):
        posiciones[fila].append(i)
    for i, fila in enumerate(filas):
        # De la repetición más lejana a la más cercana: gana el bloque más largo.
        for j in reversed(posiciones[fila]):
            largo = j - i
            if largo < 2:
                break
            if j + largo <= len(filas) and filas[i:j] == filas[j : j + largo]:
                return movimientos[i:j], movimientos[j : j + largo]
    return None
