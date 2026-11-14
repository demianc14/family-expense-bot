"""Estados de cuenta sintéticos: unos que cuadran y variantes hostiles que no.

Todo es inventado y determinista (sin azar ni fecha de hoy): correrlo dos veces
escribe los mismos bytes. `tests/test_privacy.py` exige que cada CSV versionado
en `tests/fixtures/estados/` sea exactamente lo que genera este script, así que
ahí no puede colarse un estado real.

    python scripts/estado_sintetico.py              # reescribe tests/fixtures/estados/
    python scripts/estado_sintetico.py --tabla      # importa cada caso y muestra qué pasó
    python scripts/estado_sintetico.py --base RUTA  # base sintética del bot, para probar el cruce

`--tabla` corre la importación real contra una base en memoria por caso; su
salida es la tabla de `docs/conciliacion.md` (un test las compara).

`--base` crea una base nueva (se niega si el archivo existe) con gastos
inventados de marzo y los estados de marzo y abril ya importados. Algunos
gastos calzan con un movimiento el mismo día, otros a uno o dos días, uno a
cuatro, y hay movimientos sin gasto y un gasto en efectivo sin movimiento.
"""

import csv
import io
import sys
import tempfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from family_expense_bot.conciliacion.bancos import cargar_bancos
from family_expense_bot.conciliacion.contrato import EstadoRechazado, plata
from family_expense_bot.conciliacion.importar import Importacion, importar_estado
from family_expense_bot.extraction.schema import Confianza, GastoExtraido
from family_expense_bot.storage.repository import (
    RepositorioEstados,
    RepositorioGastos,
    crear_engine,
)

RAIZ = Path(__file__).resolve().parents[1]
DESTINO = RAIZ / "tests" / "fixtures" / "estados"
ID_DEMO = 42

Fila = tuple[date, str, int]
"""Fecha, descripción y centavos con signo (negativo si sale plata)."""

SALDO_INICIAL = 100_000

MARZO: list[Fila] = [
    (date(2026, 3, 2), "SUPERMERCADO SINTETICO", -8540),
    (date(2026, 3, 3), "TRANSFERENCIA RECIBIDA NOMINA", 120_000),
    (date(2026, 3, 4), "GASOLINERA SINTETICA", -2200),
    (date(2026, 3, 5), "STREAMING SINTETICO", -999),
    (date(2026, 3, 6), "RESTAURANTE EL SINTETICO", -1850),
    (date(2026, 3, 9), "BUS URBANO", -35),
    (date(2026, 3, 9), "BUS URBANO", -35),
    (date(2026, 3, 10), "PAGO TARJETA VISA", -4500),
    (date(2026, 3, 12), "CINE SINTETICO", -3500),
    (date(2026, 3, 15), "COMISION RETIRO CAJERO", -45),
    (date(2026, 3, 15), "RETIRO CAJERO", -4000),
    (date(2026, 3, 18), "FARMACIA SINTETICA", -1275),
    (date(2026, 3, 20), "TIENDA DE ROPA SINTETICA", -11_000),
    (date(2026, 3, 23), "SUPERMERCADO SINTETICO", -6410),
    (date(2026, 3, 27), "LIBRERIA SINTETICA", -4000),
    (date(2026, 3, 31), "INTERES GANADO", 12),
    (date(2026, 3, 31), "MANTENIMIENTO DE CUENTA", -150),
]

ABRIL: list[Fila] = [
    (date(2026, 4, 3), "SUPERMERCADO SINTETICO", -7215),
    (date(2026, 4, 5), "STREAMING SINTETICO", -999),
    (date(2026, 4, 15), "COMISION RETIRO CAJERO", -45),
    (date(2026, 4, 15), "RETIRO CAJERO", -2000),
    (date(2026, 4, 30), "MANTENIMIENTO DE CUENTA", -150),
]

GASTOS: list[tuple[int, str, str, str]] = [
    # Día de marzo, monto, categoría y descripción, como los habría escrito alguien en el bot.
    (2, "85.40", "alimentacion", "supermercado"),
    (4, "22.00", "transporte", "gasolina"),
    (5, "18.50", "comida_fuera", "almuerzo"),  # el banco lo cobró el 6
    (9, "0.35", "transporte", "bus"),  # el banco trae dos iguales ese día
    (10, "45.00", "deudas_cuotas", "cuota de la tarjeta"),
    (11, "3.00", "transporte", "taxi en efectivo"),  # no pasa por el banco
    (14, "35.00", "entretenimiento", "cine"),  # el banco lo cobró el 12
    (18, "12.75", "personal", "farmacia"),
    (20, "110.00", "ropa", "zapatos"),
    (23, "64.10", "alimentacion", "supermercado"),
    (23, "40.00", "educacion", "libros"),  # el banco lo cobró el 27: a cuatro días
]

MARZO_DESDE, MARZO_HASTA = date(2026, 3, 1), date(2026, 3, 31)
ABRIL_DESDE, ABRIL_HASTA = date(2026, 4, 1), date(2026, 4, 30)

ENCABEZADO = ["Fecha", "Descripción", "Monto", "Saldo"]
ENCABEZADO_COMA = ["Fecha", "Detalle", "Débito", "Crédito"]
TITULO_COMA = [["Banco Sintético", "Estado de cuenta"], ["Cuenta de ahorros", "Marzo 2026"]]

Celdas = list[list[str]]


def _final(filas: list[Fila], anterior: int) -> int:
    return anterior + sum(centavos for _, _, centavos in filas)


def _con_saldo(filas: list[Fila], anterior: int) -> Celdas:
    """Como exporta `banco_sintetico`: dd/mm/aaaa, monto con signo y saldo después de cada fila."""
    celdas, saldo = [], anterior
    for fecha, descripcion, centavos in filas:
        saldo += centavos
        celdas.append([fecha.strftime("%d/%m/%Y"), descripcion, plata(centavos), plata(saldo)])
    return celdas


def _con_coma(filas: list[Fila]) -> Celdas:
    """Como exporta `banco_sintetico_coma`: fecha ISO, débito y crédito aparte, coma decimal."""
    return [
        [
            fecha.isoformat(),
            descripcion,
            plata(-centavos).replace(".", ",") if centavos < 0 else "",
            plata(centavos).replace(".", ",") if centavos > 0 else "",
        ]
        for fecha, descripcion, centavos in filas
    ]


def _csv(filas: Celdas, delimitador: str = ",", fin_de_linea: str = "\n") -> bytes:
    salida = io.StringIO(newline="")
    csv.writer(salida, delimiter=delimitador, lineterminator=fin_de_linea).writerows(filas)
    return salida.getvalue().encode("utf-8")


def _punto(celdas: Celdas, encabezado: list[str] | None = None) -> bytes:
    return _csv([encabezado or ENCABEZADO, *celdas])


def _coma(celdas: Celdas) -> bytes:
    return _csv([*TITULO_COMA, ENCABEZADO_COMA, *celdas], delimitador=";")


def _cambiar(celdas: Celdas, cambios: dict[tuple[int, int], str]) -> Celdas:
    """Una copia con algunas celdas cambiadas: (fila, columna) → texto."""
    copia = [fila.copy() for fila in celdas]
    for (fila, columna), texto in cambios.items():
        copia[fila][columna] = texto
    return copia


def archivos() -> dict[str, bytes]:
    """Nombre → contenido de cada CSV sintético. Es lo único que se versiona."""
    marzo = _con_saldo(MARZO, SALDO_INICIAL)
    abril = _con_saldo(ABRIL, _final(MARZO, SALDO_INICIAL))
    coma = _con_coma(MARZO)
    sin_el_pago = MARZO[:7] + MARZO[8:]
    con_bloque = MARZO[:5] + MARZO[2:5] + MARZO[5:]
    al_reves = [[fecha, detalle, credito, debito] for fecha, detalle, debito, credito in coma]
    formulas = ["=1+1", "+593 99 000 0000", "-DESCUENTO SINTETICO", "@comercio_sintetico"]
    formulas.append('=HYPERLINK("http://ejemplo.invalid";"clic")')
    mes_dia = {(i, 0): fecha.strftime("%m/%d/%Y") for i, (fecha, _, _) in enumerate(MARZO)}
    return {
        # Los que cuadran.
        "marzo.csv": _punto(marzo),
        "abril.csv": _punto(abril),
        "marzo_coma.csv": _coma(coma),
        # Formato del archivo.
        "con_bom.csv": b"\xef\xbb\xbf" + _punto(marzo),
        "reexportado.csv": _csv([ENCABEZADO, *marzo], fin_de_linea="\r\n"),
        "lineas_vacias.csv": _punto([*marzo[:4], [], *marzo[4:9], ["", "", "", ""], *marzo[9:], []])
        + b"\n",
        "encabezado_de_otro_banco.csv": _punto(marzo, ["Date", "Description", "Amount", "Balance"]),
        "sin_movimientos.csv": _punto([]),
        # Filas que no se entienden.
        "fila_a_medias.csv": _punto(_cambiar(marzo, {(6, 2): "", (6, 3): ""})),
        "coma_en_banco_de_punto.csv": _punto(_cambiar(marzo, {(0, 2): "-85,40"})),
        "separador_de_miles.csv": _punto(_cambiar(marzo, {(1, 2): "1,200.00"})),
        "tres_decimales.csv": _punto(_cambiar(marzo, {(0, 2): "-85.400"})),
        "monto_cero.csv": _punto(_cambiar(marzo, {(2, 2): "0.00"})),
        "fecha_imposible.csv": _punto(_cambiar(marzo, {(4, 0): "31/02/2026"})),
        "fecha_mes_dia.csv": _punto(_cambiar(marzo, mes_dia)),
        "descripciones_con_formula.csv": _punto(
            _cambiar(marzo, {(i, 1): texto for i, texto in enumerate(formulas)})
        ),
        # Se entienden, pero no cuadran.
        "fecha_fuera_de_periodo.csv": _punto(_cambiar(marzo, {(16, 0): "01/04/2026"})),
        "primera_fila_corrida.csv": _punto(_cambiar(marzo, {(0, 2): "-85.39"})),
        "errores_compensados.csv": _punto(_cambiar(marzo, {(0, 2): "-85.39", (3, 2): "-10.00"})),
        "errores_compensados_sin_saldo.csv": _coma(
            _cambiar(coma, {(0, 2): "85,39", (3, 2): "10,00"})
        ),
        "fila_perdida.csv": _punto(marzo[:7] + marzo[8:]),
        "fila_perdida_sin_saldo.csv": _coma(_con_coma(sin_el_pago)),
        "bloque_duplicado.csv": _punto(marzo[:5] + marzo[2:5] + marzo[5:]),
        "bloque_duplicado_sin_saldo.csv": _coma(_con_coma(con_bloque)),
        "signo_invertido.csv": _coma(al_reves),
        "abril_otro_saldo.csv": _punto(_con_saldo(ABRIL, _final(MARZO, SALDO_INICIAL) - 100)),
    }


ENORME = "archivo_enorme.csv"
"""No se versiona: pasa del tope de tamaño. `preparar` lo escribe cuando hace falta."""


_SALDOS = (SALDO_INICIAL, _final(MARZO, SALDO_INICIAL))
_SALDOS_ABRIL = (_SALDOS[1], _final(ABRIL, _SALDOS[1]))


@dataclass(frozen=True)
class Caso:
    nombre: str
    que: str
    """Qué trae el archivo, en una línea."""
    archivo: str
    espera: tuple[str, ...] = ()
    """Códigos de rechazo, sin repetir. Vacío: el estado entra."""
    avisos: int = 0
    banco: str = "banco_sintetico"
    abril: bool = False
    """El periodo es abril, no marzo."""
    saldos: tuple[int, int] = _SALDOS
    """Saldo anterior y final declarados, en centavos. Por defecto, los de marzo."""
    despues_de_marzo: bool = False
    """Se importa en una base que ya tiene `marzo.csv`."""


_COMA = {"banco": "banco_sintetico_coma"}

CASOS = [
    Caso("marzo", "Estado que cuadra, con columna de saldo", "marzo.csv"),
    Caso(
        "abril",
        "El mes siguiente; empata con marzo",
        "abril.csv",
        abril=True,
        saldos=_SALDOS_ABRIL,
    ),
    Caso(
        "marzo_coma",
        "Coma decimal, punto y coma, débito y crédito aparte, sin columna de saldo",
        "marzo_coma.csv",
        **_COMA,
    ),
    Caso(
        "saldo_un_centavo",
        "Saldo final declarado un centavo arriba",
        "marzo_coma.csv",
        ("saldo_no_cuadra",),
        banco="banco_sintetico_coma",
        saldos=(_SALDOS[0], _SALDOS[1] + 1),
    ),
    Caso(
        "fecha_fuera_de_periodo",
        "Un movimiento fechado un día después del periodo",
        "fecha_fuera_de_periodo.csv",
        ("fecha_fuera_de_periodo",),
    ),
    Caso(
        "coma_en_banco_de_punto",
        'Un monto con coma decimal ("-85,40") en un banco de punto',
        "coma_en_banco_de_punto.csv",
        ("monto_invalido",),
    ),
    Caso("lineas_vacias", "Líneas totalmente vacías en medio y al final", "lineas_vacias.csv"),
    Caso(
        "fila_a_medias",
        "Una fila sin monto ni saldo",
        "fila_a_medias.csv",
        ("fila_incompleta",),
    ),
    Caso(
        "archivo_repetido",
        "El mismo archivo por segunda vez",
        "marzo.csv",
        ("archivo_repetido",),
        despues_de_marzo=True,
    ),
    Caso(
        "reexportado",
        "El mismo mes exportado de nuevo (otros bytes: fin de línea de Windows)",
        "reexportado.csv",
        ("periodo_solapado",),
        despues_de_marzo=True,
    ),
    Caso(
        "separador_de_miles",
        'Un monto con separador de miles ("1,200.00")',
        "separador_de_miles.csv",
        ("monto_invalido",),
    ),
    Caso(
        "tres_decimales",
        'Un monto con tres decimales ("-85.400")',
        "tres_decimales.csv",
        ("monto_invalido",),
    ),
    Caso(
        "encabezado_de_otro_banco",
        "Encabezado con las columnas de otro banco",
        "encabezado_de_otro_banco.csv",
        ("encabezado",),
    ),
    Caso(
        "fecha_imposible",
        "Una fecha que no existe (31/02/2026)",
        "fecha_imposible.csv",
        ("fecha_invalida",),
    ),
    Caso(
        "fecha_mes_dia",
        "Fechas en mes/día/año en un banco de día/mes/año",
        "fecha_mes_dia.csv",
        ("fecha_invalida",),
    ),
    Caso(
        "errores_compensados",
        "Dos montos corridos +1 y -1 centavo; el total cuadra",
        "errores_compensados.csv",
        ("saldo_corrido",),
    ),
    Caso(
        "errores_compensados_sin_saldo",
        "Lo mismo en un banco sin columna de saldo (límite conocido)",
        "errores_compensados_sin_saldo.csv",
        **_COMA,
    ),
    Caso(
        "primera_fila_corrida",
        "El monto de la primera fila corrido un centavo",
        "primera_fila_corrida.csv",
        ("saldo_corrido", "saldo_no_cuadra"),
    ),
    Caso(
        "fila_perdida",
        "Falta una fila, con columna de saldo",
        "fila_perdida.csv",
        ("saldo_corrido", "saldo_no_cuadra"),
    ),
    Caso(
        "fila_perdida_sin_saldo",
        "Falta una fila, sin columna de saldo",
        "fila_perdida_sin_saldo.csv",
        ("saldo_no_cuadra",),
        **_COMA,
    ),
    Caso(
        "bloque_duplicado",
        "Tres filas pegadas dos veces, con columna de saldo",
        "bloque_duplicado.csv",
        ("saldo_corrido", "saldo_no_cuadra", "bloque_duplicado"),
    ),
    Caso(
        "bloque_duplicado_sin_saldo",
        "Tres filas pegadas dos veces, sin columna de saldo",
        "bloque_duplicado_sin_saldo.csv",
        ("saldo_no_cuadra", "bloque_duplicado"),
        **_COMA,
    ),
    Caso(
        "descripciones_con_formula",
        "Descripciones que empiezan con = + - @",
        "descripciones_con_formula.csv",
    ),
    Caso("con_bom", "UTF-8 con BOM", "con_bom.csv"),
    Caso(
        "archivo_enorme",
        "Un archivo un byte más grande que el tope",
        ENORME,
        ("archivo_grande",),
    ),
    Caso(
        "signo_invertido",
        "Débitos y créditos cambiados de columna",
        "signo_invertido.csv",
        ("saldo_no_cuadra",),
        **_COMA,
    ),
    Caso("monto_cero", "Un movimiento en cero", "monto_cero.csv", ("monto_cero",)),
    Caso(
        "sin_movimientos",
        "Sin movimientos, pero con saldo final distinto del anterior",
        "sin_movimientos.csv",
        ("saldo_no_cuadra",),
        saldos=(SALDO_INICIAL, SALDO_INICIAL + 500),
    ),
    Caso(
        "continuidad",
        "Abril empieza con un saldo distinto del que cerró marzo",
        "abril_otro_saldo.csv",
        avisos=1,
        abril=True,
        saldos=(_SALDOS_ABRIL[0] - 100, _SALDOS_ABRIL[1] - 100),
        despues_de_marzo=True,
    ),
]


def preparar(carpeta: Path, con_enorme: bool = False) -> None:
    """Escribe los CSV en `carpeta`. El enorme solo si se pide: no va al repo."""
    carpeta.mkdir(parents=True, exist_ok=True)
    for nombre, contenido in archivos().items():
        (carpeta / nombre).write_bytes(contenido)
    if con_enorme:
        marzo = archivos()["marzo.csv"]
        relleno = cargar_bancos().tope_bytes + 1 - len(marzo)
        (carpeta / ENORME).write_bytes(marzo + b"\n" * relleno)


def correr(caso: Caso, carpeta: Path) -> Importacion | EstadoRechazado:
    """Importa el caso de verdad, en una base en memoria nueva. Devuelve lo que pasó."""
    bancos = cargar_bancos()
    repositorio = RepositorioEstados(crear_engine("sqlite://"))

    def importar(archivo: str, un_caso: Caso) -> Importacion:
        anterior, final = un_caso.saldos
        return importar_estado(
            carpeta / archivo,
            bancos.bancos[un_caso.banco],
            repositorio,
            cuenta="ahorros",
            desde=ABRIL_DESDE if un_caso.abril else MARZO_DESDE,
            hasta=ABRIL_HASTA if un_caso.abril else MARZO_HASTA,
            tope_bytes=bancos.tope_bytes,
            saldo_anterior_centavos=anterior,
            saldo_final_centavos=final,
        )

    if caso.despues_de_marzo:
        importar("marzo.csv", CASOS[0])
    try:
        return importar(caso.archivo, caso)
    except EstadoRechazado as rechazo:
        return rechazo


MAXIMO_MENSAJES = 3


def tabla() -> str:
    """Qué pasa con cada caso y con qué mensaje exacto, en Markdown."""
    lineas = ["| Caso | Qué trae | Resultado | Mensaje exacto |", "|---|---|---|---|"]
    with tempfile.TemporaryDirectory() as temporal:
        preparar(Path(temporal), con_enorme=True)
        for caso in CASOS:
            resultado = correr(caso, Path(temporal))
            if isinstance(resultado, EstadoRechazado):
                veredicto = "Rechazado"
                mensajes = [p.mensaje for p in resultado.problemas]
            else:
                veredicto = "Entra con aviso" if resultado.avisos else "Entra"
                mensajes = list(resultado.avisos) or [f"{resultado.movimientos} movimientos"]
            de_mas = len(mensajes) - MAXIMO_MENSAJES
            if de_mas > 0:
                mensajes = [*mensajes[:MAXIMO_MENSAJES], f"(y {de_mas} más)"]
            celda = "<br>".join(mensajes).replace("|", "\\|")
            lineas.append(f"| `{caso.nombre}` | {caso.que} | {veredicto} | {celda} |")
    return "\n".join(lineas)


def base_sintetica(ruta: Path) -> tuple[int, int]:
    """Una base nueva del bot con gastos inventados y marzo y abril importados.

    Devuelve cuántos gastos y cuántos movimientos quedaron. Nunca escribe sobre
    una base que ya existe.
    """
    if ruta.exists():
        raise SystemExit(f"{ruta} ya existe: no se escribe sobre una base que ya existe.")
    ruta.parent.mkdir(parents=True, exist_ok=True)
    engine = crear_engine(f"sqlite:///{ruta}")
    gastos = RepositorioGastos(engine)
    for dia, monto, categoria, descripcion in GASTOS:
        gastos.guardar(
            GastoExtraido(
                monto=Decimal(monto),
                categoria=categoria,
                fecha=date(2026, 3, dia),
                atribuido_a="demian",
                descripcion=descripcion,
                texto_original=f"{monto} {descripcion}",
                remitente="demian",
                confianza_monto=Confianza.ALTA,
                confianza_categoria=Confianza.ALTA,
                confianza_fecha=Confianza.ALTA,
                confianza_atribuido_a=Confianza.ALTA,
            ),
            registrado_por=ID_DEMO,
        )
    bancos = cargar_bancos()
    movimientos = 0
    with tempfile.TemporaryDirectory() as temporal:
        preparar(Path(temporal))
        for archivo, desde, hasta, (anterior, final) in (
            ("marzo.csv", MARZO_DESDE, MARZO_HASTA, _SALDOS),
            ("abril.csv", ABRIL_DESDE, ABRIL_HASTA, _SALDOS_ABRIL),
        ):
            movimientos += importar_estado(
                Path(temporal) / archivo,
                bancos.bancos["banco_sintetico"],
                RepositorioEstados(engine),
                cuenta="ahorros",
                desde=desde,
                hasta=hasta,
                tope_bytes=bancos.tope_bytes,
                saldo_anterior_centavos=anterior,
                saldo_final_centavos=final,
            ).movimientos
    return len(GASTOS), movimientos


def main() -> None:
    if "--tabla" in sys.argv:
        print(tabla())
    elif "--base" in sys.argv:
        if sys.argv[-1] == "--base":
            raise SystemExit("Falta la ruta: --base RUTA")
        ruta = Path(sys.argv[sys.argv.index("--base") + 1])
        gastos, movimientos = base_sintetica(ruta)
        print(f"{ruta}: {gastos} gastos y {movimientos} movimientos sintéticos (marzo y abril)")
    else:
        preparar(DESTINO)
        print(f"{len(archivos())} estados sintéticos en {DESTINO.relative_to(RAIZ)}")


if __name__ == "__main__":
    main()
