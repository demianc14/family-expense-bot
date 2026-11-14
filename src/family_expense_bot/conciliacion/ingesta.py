"""CSV del banco → `EstadoLeido`. Estricta: lo que no se entiende se rechaza, no se adivina.

Acá solo se lee: cada fila tiene que tener fecha, descripción y monto en el
formato que declara `config/bancos.yaml`. Que el estado cuadre lo decide
`verificacion.verificar`. Si una sola fila está mal se rechaza el archivo
completo, con todas las filas malas en el mensaje.
"""

import csv
import hashlib
import io
import re
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

from family_expense_bot.conciliacion.bancos import Banco, asignar_tipo
from family_expense_bot.conciliacion.contrato import (
    EstadoLeido,
    EstadoRechazado,
    MovimientoLeido,
    Problema,
)

MAXIMO_DESCRIPCION = 500
"""El ancho de `movimientos.descripcion`. Una más larga se rechaza: cortarla sería adivinar."""

_CUENTA = re.compile(r"[a-z][a-z0-9_-]{0,39}")
_DIGITOS = r"\d{1,15}"
"""Quince dígitos enteros caben de sobra en el entero de SQLite y no son un monto real."""


def centavos_declarados(texto: str) -> int:
    """Un saldo escrito a mano ("849.50", "-12.34"), siempre con punto, a centavos."""
    centavos = _centavos(texto.strip(), ".", con_signo=True)
    if centavos is None:
        raise ValueError(
            f"{texto!r} no es un saldo: van solo dígitos y hasta dos decimales con punto"
        )
    return centavos


def leer_estado(
    ruta: Path,
    banco: Banco,
    *,
    cuenta: str,
    desde: date,
    hasta: date,
    tope_bytes: int,
    saldo_anterior_centavos: int,
    saldo_final_centavos: int,
) -> EstadoLeido:
    """Lee el archivo completo o lanza `EstadoRechazado` con todos los problemas.

    Los saldos van como los muestra el banco (en una tarjeta, lo que se debe en
    positivo) y son obligatorios aunque el archivo traiga columna de saldo: los
    declara quien importa, mirando el estado. Si salieran del archivo, la primera
    fila no se verificaría contra nada y un archivo cortado en las puntas cuadraría.
    """
    problemas = []
    if not _CUENTA.fullmatch(cuenta) or re.search(r"\d{4}", cuenta):
        problemas.append(
            Problema(
                codigo="cuenta",
                mensaje=f"La cuenta {cuenta!r} no es una etiqueta válida: minúsculas, hasta 40 "
                "caracteres y nunca el número de cuenta.",
            )
        )
    if desde > hasta:
        problemas.append(
            Problema(
                codigo="periodo",
                mensaje=f"El periodo empieza ({desde}) después de terminar ({hasta}).",
            )
        )
    if problemas:
        raise EstadoRechazado(problemas)

    datos = _leer_bytes(ruta, tope_bytes)
    movimientos = _movimientos(_filas(datos, banco), banco)

    invertir = -1 if banco.signo == "positivo_sale" else 1

    return EstadoLeido(
        banco=banco.clave,
        cuenta=cuenta,
        periodo_desde=desde,
        periodo_hasta=hasta,
        saldo_anterior_centavos=invertir * saldo_anterior_centavos,
        saldo_final_centavos=invertir * saldo_final_centavos,
        hash_archivo=hashlib.sha256(datos).hexdigest(),
        movimientos=tuple(movimientos),
    )


def _leer_bytes(ruta: Path, tope_bytes: int) -> bytes:
    """Los bytes del archivo, o un rechazo sin abrirlo si pasa del tope."""
    try:
        tamano = ruta.stat().st_size
    except OSError:
        raise EstadoRechazado(
            [Problema(codigo="archivo_no_existe", mensaje=f"No existe el archivo {ruta}.")]
        ) from None
    if tamano <= tope_bytes:
        with ruta.open("rb") as archivo:
            # Uno de más: si el archivo creció después del stat, tampoco entra.
            datos = archivo.read(tope_bytes + 1)
        tamano = len(datos)
        if tamano <= tope_bytes:
            return datos
    raise EstadoRechazado(
        [
            Problema(
                codigo="archivo_grande",
                mensaje=f"El archivo pesa {tamano} bytes y el tope es {tope_bytes}: no se leyó.",
            )
        ]
    )


def _filas(datos: bytes, banco: Banco) -> list[tuple[int, list[str]]]:
    """Cada registro del CSV con su línea en el archivo, sin espacios alrededor de las celdas."""
    # utf-8-sig lee utf-8 con o sin BOM; el hash sigue siendo el de los bytes originales.
    codificacion = "utf-8-sig" if banco.codificacion == "utf-8" else banco.codificacion
    try:
        texto = datos.decode(codificacion)
    except UnicodeDecodeError as e:
        raise EstadoRechazado(
            [
                Problema(
                    codigo="codificacion",
                    mensaje=f"El archivo no está en {banco.codificacion}: el byte {e.start + 1} "
                    "no se pudo leer.",
                )
            ]
        ) from None
    lector = csv.reader(io.StringIO(texto, newline=""), delimiter=banco.delimitador, strict=True)
    filas = []
    try:
        for fila in lector:
            filas.append((lector.line_num, [celda.strip() for celda in fila]))
    except csv.Error as e:
        raise EstadoRechazado(
            [
                Problema(
                    codigo="csv_mal_formado",
                    linea=lector.line_num,
                    mensaje=f"Línea {lector.line_num}: el CSV está mal formado ({e}).",
                )
            ]
        ) from None
    return filas


def _movimientos(filas: list[tuple[int, list[str]]], banco: Banco) -> list[MovimientoLeido]:
    if len(filas) <= banco.filas_a_saltar or not any(filas[banco.filas_a_saltar][1]):
        raise EstadoRechazado(
            [
                Problema(
                    codigo="sin_encabezado",
                    mensaje="El archivo no tiene encabezado: se esperaba en la línea "
                    f"{banco.filas_a_saltar + 1}.",
                )
            ]
        )
    linea_encabezado, encabezado = filas[banco.filas_a_saltar]
    indices = _indices(linea_encabezado, encabezado, banco)

    problemas: list[Problema] = []
    movimientos: list[MovimientoLeido] = []
    for linea, fila in filas[banco.filas_a_saltar + 1 :]:
        if not any(fila):
            continue  # línea totalmente vacía: se salta, pero la numeración sigue
        resultado = _movimiento(linea, fila, len(encabezado), indices, banco)
        if isinstance(resultado, Problema):
            problemas.append(resultado)
        else:
            movimientos.append(resultado)
    if problemas:
        raise EstadoRechazado(problemas)
    return movimientos


def _indices(linea: int, encabezado: list[str], banco: Banco) -> dict[str, int]:
    """Campo → posición de su columna. Rechaza si falta una o está repetida."""
    columnas = {campo: n for campo, n in banco.columnas.model_dump().items() if n is not None}
    faltan = [nombre for nombre in columnas.values() if nombre not in encabezado]
    if faltan:
        problema = (
            f"Línea {linea}: al encabezado le faltan las columnas {_lista(faltan, comillas=True)}; "
            f"trae {_lista(encabezado, comillas=True)}. ¿Es un estado de {banco.nombre}?"
        )
        raise EstadoRechazado([Problema(codigo="encabezado", linea=linea, mensaje=problema)])
    for nombre in columnas.values():
        if encabezado.count(nombre) > 1:
            problema = f"Línea {linea}: la columna {nombre!r} aparece dos veces en el encabezado."
            raise EstadoRechazado([Problema(codigo="encabezado", linea=linea, mensaje=problema)])
    return {campo: encabezado.index(nombre) for campo, nombre in columnas.items()}


def _movimiento(
    linea: int, fila: list[str], ancho: int, indices: dict[str, int], banco: Banco
) -> MovimientoLeido | Problema:
    def problema(codigo: str, mensaje: str) -> Problema:
        return Problema(codigo=codigo, linea=linea, mensaje=f"Línea {linea}: {mensaje}")

    if len(fila) != ancho:
        return problema("columnas", f"la fila tiene {len(fila)} columnas y el encabezado {ancho}.")
    celda = {campo: fila[i] for campo, i in indices.items()}
    con_signo = "monto" in celda
    texto_monto = celda["monto"] if con_signo else (celda["debito"] or celda["credito"])
    if not con_signo and celda["debito"] and celda["credito"]:
        return problema("debito_y_credito", "la fila trae débito y crédito a la vez.")

    faltan = [
        nombre
        for nombre, valor in (
            ("la fecha", celda["fecha"]),
            ("la descripción", celda["descripcion"]),
            ("el monto", texto_monto),
            ("el saldo", celda.get("saldo", "-")),
        )
        if not valor
    ]
    if faltan:
        return problema("fila_incompleta", f"fila a medio llenar, falta {_lista(faltan)}.")

    try:
        fecha = datetime.strptime(celda["fecha"], banco.formato_fecha).date()
    except ValueError:
        return problema(
            "fecha_invalida",
            f"la fecha {celda['fecha']!r} no tiene el formato {banco.formato_fecha}.",
        )

    separador = "." if banco.decimal == "punto" else ","
    formato = f"van solo dígitos y hasta dos decimales con {banco.decimal}, sin separador de miles"
    monto = _centavos(texto_monto, separador, con_signo)
    if monto is None:
        sin_signo = "" if con_signo else " ni signo"
        return problema(
            "monto_invalido", f"el monto {texto_monto!r} no es válido: {formato}{sin_signo}."
        )
    if monto == 0:
        return problema("monto_cero", "el monto es cero.")
    if not con_signo and celda["debito"]:
        monto = -monto

    saldo = None
    if "saldo" in celda:
        saldo = _centavos(celda["saldo"], separador, con_signo=True)
        if saldo is None:
            return problema(
                "saldo_invalido", f"el saldo {celda['saldo']!r} no es válido: {formato}."
            )

    if banco.signo == "positivo_sale":
        monto, saldo = -monto, None if saldo is None else -saldo

    if len(celda["descripcion"]) > MAXIMO_DESCRIPCION:
        return problema(
            "descripcion_larga",
            f"la descripción tiene {len(celda['descripcion'])} caracteres y el máximo es "
            f"{MAXIMO_DESCRIPCION}.",
        )

    return MovimientoLeido(
        linea=linea,
        fecha=fecha,
        descripcion=celda["descripcion"],
        monto_centavos=monto,
        saldo_centavos=saldo,
        tipo=asignar_tipo(celda["descripcion"], monto, banco.tipos),
    )


def _centavos(texto: str, separador: str, con_signo: bool) -> int | None:
    """Un monto exacto a centavos, o None si no lo es. Enteros de punta a punta, sin float."""
    signo = "-?" if con_signo else ""
    coincide = re.fullmatch(rf"({signo})({_DIGITOS})(?:{re.escape(separador)}(\d{{1,2}}))?", texto)
    if coincide is None:
        return None
    negativo, entero, decimales = coincide.groups()
    centavos = int(entero) * 100 + int((decimales or "").ljust(2, "0"))
    return -centavos if negativo else centavos


def _lista(partes: Sequence[str], comillas: bool = False) -> str:
    """ "a, b y c". Con comillas para lo que viene del archivo."""
    partes = [repr(p) for p in partes] if comillas else list(partes)
    if len(partes) == 1:
        return partes[0]
    return f"{', '.join(partes[:-1])} y {partes[-1]}"
