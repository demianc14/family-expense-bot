"""Ingesta: CSV del banco → `EstadoLeido`. Estricta: nada se adivina.

Los CSV van escritos acá mismo para que cada caso se lea entero. Los mensajes
se comparan completos: son lo que ve Demian cuando un estado no entra.
Todo es sintético.
"""

import hashlib
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from family_expense_bot.conciliacion.bancos import Banco, cargar_bancos
from family_expense_bot.conciliacion.contrato import EstadoLeido, EstadoRechazado
from family_expense_bot.conciliacion.ingesta import centavos_declarados, leer_estado

BANCOS = cargar_bancos()
CON_SALDO = BANCOS.bancos["banco_sintetico"]
CON_COMA = BANCOS.bancos["banco_sintetico_coma"]

BUENO = """\
Fecha,Descripción,Monto,Saldo
02/03/2026,SUPERMERCADO SINTETICO,-15.50,984.50
05/03/2026,TRANSFERENCIA RECIBIDA,250.00,1234.50
09/03/2026,COMISION RETIRO CAJERO,-0.45,1234.05
"""

BUENO_COMA = """\
Banco Sintético;Estado de cuenta
Cuenta de ahorros;Marzo 2026
Fecha;Detalle;Débito;Crédito
2026-03-02;SUPERMERCADO SINTETICO;15,50;
2026-03-05;TRANSFERENCIA RECIBIDA;;250,00
2026-03-09;MANTENIMIENTO DE CUENTA;1,5;
"""


def _leer(
    tmp_path: Path, contenido: str | bytes, banco: Banco = CON_SALDO, **cambios: Any
) -> EstadoLeido:
    ruta = tmp_path / "estado.csv"
    ruta.write_bytes(contenido.encode() if isinstance(contenido, str) else contenido)
    argumentos: dict[str, Any] = {
        "cuenta": "ahorros",
        "desde": date(2026, 3, 1),
        "hasta": date(2026, 3, 31),
        "tope_bytes": BANCOS.tope_bytes,
        # Los de BUENO. La ingesta no los compara con el archivo: eso es de la verificación.
        "saldo_anterior_centavos": 100_000,
        "saldo_final_centavos": 123_405,
    } | cambios
    return leer_estado(ruta, banco, **argumentos)


def _rechazo(tmp_path: Path, contenido: str | bytes, **cambios: Any) -> list[tuple[str, str]]:
    with pytest.raises(EstadoRechazado) as error:
        _leer(tmp_path, contenido, **cambios)
    return [(p.codigo, p.mensaje) for p in error.value.problemas]


# --- Lo que entra ---


def test_lee_un_estado_con_columna_de_saldo(tmp_path: Path) -> None:
    estado = _leer(tmp_path, BUENO)
    assert (estado.banco, estado.cuenta) == ("banco_sintetico", "ahorros")
    assert (estado.periodo_desde, estado.periodo_hasta) == (date(2026, 3, 1), date(2026, 3, 31))
    assert [
        (m.linea, m.fecha, m.descripcion, m.monto_centavos, m.saldo_centavos, m.tipo)
        for m in estado.movimientos
    ] == [
        (2, date(2026, 3, 2), "SUPERMERCADO SINTETICO", -1550, 98_450, "debito"),
        (3, date(2026, 3, 5), "TRANSFERENCIA RECIBIDA", 25_000, 123_450, "credito"),
        (4, date(2026, 3, 9), "COMISION RETIRO CAJERO", -45, 123_405, "comision"),
    ]


def test_los_saldos_declarados_mandan_sobre_los_del_archivo(tmp_path: Path) -> None:
    """Si no coinciden con la columna, lo dice la verificación, no la ingesta."""
    estado = _leer(tmp_path, BUENO, saldo_anterior_centavos=100_001, saldo_final_centavos=5)
    assert (estado.saldo_anterior_centavos, estado.saldo_final_centavos) == (100_001, 5)


def test_el_hash_es_el_sha256_de_los_bytes(tmp_path: Path) -> None:
    assert _leer(tmp_path, BUENO).hash_archivo == hashlib.sha256(BUENO.encode()).hexdigest()


def test_lee_coma_decimal_punto_y_coma_y_debito_y_credito_aparte(tmp_path: Path) -> None:
    estado = _leer(
        tmp_path,
        BUENO_COMA,
        banco=CON_COMA,
        saldo_anterior_centavos=100_000,
        saldo_final_centavos=123_300,
    )
    assert [(m.linea, m.monto_centavos, m.saldo_centavos, m.tipo) for m in estado.movimientos] == [
        (4, -1550, None, "debito"),
        (5, 25_000, None, "credito"),
        (6, -150, None, "comision"),
    ]


def test_utf8_con_bom_entra_igual(tmp_path: Path) -> None:
    con_bom = b"\xef\xbb\xbf" + BUENO.encode()
    estado = _leer(tmp_path, con_bom)
    assert len(estado.movimientos) == 3
    assert estado.hash_archivo == hashlib.sha256(con_bom).hexdigest()


def test_fin_de_linea_de_windows_entra_igual(tmp_path: Path) -> None:
    estado = _leer(tmp_path, BUENO.replace("\n", "\r\n"))
    assert estado.movimientos[0].descripcion == "SUPERMERCADO SINTETICO"


def test_la_linea_totalmente_vacia_se_salta_y_la_numeracion_se_conserva(tmp_path: Path) -> None:
    lineas = BUENO.splitlines()
    con_vacias = "\n".join([*lineas[:2], "", ",,,", "   ", *lineas[2:], "", ""]) + "\n"
    estado = _leer(tmp_path, con_vacias)
    assert [m.linea for m in estado.movimientos] == [2, 6, 7]


def test_las_columnas_de_mas_se_ignoran_y_el_orden_no_importa(tmp_path: Path) -> None:
    contenido = "Saldo,Oficina,Monto,Fecha,Descripción\n984.50,Centro,-15.50,02/03/2026,MERCADO\n"
    (movimiento,) = _leer(tmp_path, contenido).movimientos
    assert (movimiento.monto_centavos, movimiento.saldo_centavos) == (-1550, 98_450)


def test_la_descripcion_se_guarda_literal_aunque_parezca_formula(tmp_path: Path) -> None:
    """No se neutraliza al importar, sino al exportar: ver `docs/conciliacion.md`."""
    descripciones = ["=1+1", "+593 99 000 0000", "-DESCUENTO", "@usuario", '=HYPERLINK("x")']
    filas = "".join(
        f'{i + 2:02d}/03/2026,"{d.replace(chr(34), chr(34) * 2)}",-1.00,{999 - i}.00\n'
        for i, d in enumerate(descripciones)
    )
    estado = _leer(tmp_path, "Fecha,Descripción,Monto,Saldo\n" + filas)
    assert [m.descripcion for m in estado.movimientos] == descripciones


def test_una_tarjeta_escribe_el_consumo_en_positivo(tmp_path: Path) -> None:
    tarjeta = CON_SALDO.model_copy(update={"signo": "positivo_sale"})
    contenido = "Fecha,Descripción,Monto,Saldo\n02/03/2026,MERCADO,15.50,115.50\n"
    estado = _leer(
        tmp_path,
        contenido,
        banco=tarjeta,
        saldo_anterior_centavos=10_000,
        saldo_final_centavos=11_550,
    )
    assert estado.movimientos[0].monto_centavos == -1550
    assert estado.movimientos[0].saldo_centavos == -11_550
    assert (estado.saldo_anterior_centavos, estado.saldo_final_centavos) == (-10_000, -11_550)


def test_un_estado_sin_movimientos_entra_si_trae_los_saldos(tmp_path: Path) -> None:
    estado = _leer(
        tmp_path,
        "Fecha,Descripción,Monto,Saldo\n",
        saldo_anterior_centavos=100,
        saldo_final_centavos=100,
    )
    assert estado.movimientos == ()


# --- Lo que no entra, y con qué mensaje ---


def test_el_tope_de_tamano_rechaza_antes_de_leer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ruta = tmp_path / "enorme.csv"
    with ruta.open("wb") as f:
        f.truncate(BANCOS.tope_bytes + 1)

    def _no_abrir(*_: object, **__: object) -> None:
        raise AssertionError("el archivo se abrió")

    monkeypatch.setattr(Path, "open", _no_abrir)
    monkeypatch.setattr(Path, "read_bytes", _no_abrir)
    with pytest.raises(EstadoRechazado) as error:
        leer_estado(
            ruta,
            CON_SALDO,
            cuenta="ahorros",
            desde=date(2026, 3, 1),
            hasta=date(2026, 3, 31),
            tope_bytes=BANCOS.tope_bytes,
            saldo_anterior_centavos=0,
            saldo_final_centavos=0,
        )
    assert [(p.codigo, p.mensaje) for p in error.value.problemas] == [
        ("archivo_grande", "El archivo pesa 1048577 bytes y el tope es 1048576: no se leyó.")
    ]


def test_un_archivo_que_no_existe(tmp_path: Path) -> None:
    with pytest.raises(EstadoRechazado) as error:
        leer_estado(
            tmp_path / "no-hay.csv",
            CON_SALDO,
            cuenta="ahorros",
            desde=date(2026, 3, 1),
            hasta=date(2026, 3, 31),
            tope_bytes=1000,
            saldo_anterior_centavos=0,
            saldo_final_centavos=0,
        )
    assert [p.codigo for p in error.value.problemas] == ["archivo_no_existe"]


@pytest.mark.parametrize(
    ("monto", "detalle"),
    [
        ('"-15,50"', "'-15,50'"),  # coma en un banco de punto
        ('"1,200.00"', "'1,200.00'"),  # separador de miles
        ("12.345", "'12.345'"),  # tres decimales
        ("1e3", "'1e3'"),
        ("$15.50", "'$15.50'"),
        ("(15.50)", "'(15.50)'"),
        ("+15.50", "'+15.50'"),
        ("15.", "'15.'"),
        (".50", "'.50'"),
        ("- 15.50", "'- 15.50'"),
        ("1 200.00", "'1 200.00'"),
    ],
)
def test_un_monto_que_no_es_exacto_se_rechaza_sin_adivinar(
    tmp_path: Path, monto: str, detalle: str
) -> None:
    contenido = f"Fecha,Descripción,Monto,Saldo\n02/03/2026,MERCADO,{monto},984.50\n"
    assert _rechazo(tmp_path, contenido) == [
        (
            "monto_invalido",
            f"Línea 2: el monto {detalle} no es válido: van solo dígitos y hasta dos "
            "decimales con punto, sin separador de miles.",
        )
    ]


def test_la_coma_decimal_sin_comillas_parte_la_fila_y_tambien_se_rechaza(tmp_path: Path) -> None:
    """En un CSV separado por comas, "15,50" sin comillas son dos celdas."""
    contenido = "Fecha,Descripción,Monto,Saldo\n02/03/2026,MERCADO,-15,50,984,50\n"
    assert _rechazo(tmp_path, contenido) == [
        ("columnas", "Línea 2: la fila tiene 6 columnas y el encabezado 4.")
    ]


def test_el_punto_en_un_banco_de_coma_se_rechaza(tmp_path: Path) -> None:
    contenido = BUENO_COMA.replace("15,50", "15.50")
    problemas = _rechazo(
        tmp_path, contenido, banco=CON_COMA, saldo_anterior_centavos=0, saldo_final_centavos=0
    )
    assert problemas == [
        (
            "monto_invalido",
            "Línea 4: el monto '15.50' no es válido: van solo dígitos y hasta dos "
            "decimales con coma, sin separador de miles ni signo.",
        )
    ]


def test_un_monto_en_cero_se_rechaza(tmp_path: Path) -> None:
    contenido = "Fecha,Descripción,Monto,Saldo\n02/03/2026,AJUSTE,0.00,984.50\n"
    assert _rechazo(tmp_path, contenido) == [("monto_cero", "Línea 2: el monto es cero.")]


def test_un_saldo_mal_escrito_se_rechaza(tmp_path: Path) -> None:
    contenido = "Fecha,Descripción,Monto,Saldo\n02/03/2026,MERCADO,-15.50,984.5O\n"
    assert _rechazo(tmp_path, contenido) == [
        (
            "saldo_invalido",
            "Línea 2: el saldo '984.5O' no es válido: van solo dígitos y hasta dos "
            "decimales con punto, sin separador de miles.",
        )
    ]


@pytest.mark.parametrize("fecha", ["31/02/2026", "13/25/2026", "2026-03-02", "02/03/26", "ayer"])
def test_una_fecha_imposible_o_en_otro_formato_se_rechaza(tmp_path: Path, fecha: str) -> None:
    contenido = f"Fecha,Descripción,Monto,Saldo\n{fecha},MERCADO,-15.50,984.50\n"
    assert _rechazo(tmp_path, contenido) == [
        ("fecha_invalida", f"Línea 2: la fecha {fecha!r} no tiene el formato %d/%m/%Y.")
    ]


@pytest.mark.parametrize(
    ("fila", "falta"),
    [
        (",MERCADO,-15.50,984.50", "la fecha"),
        ("02/03/2026,,-15.50,984.50", "la descripción"),
        ("02/03/2026,MERCADO,,984.50", "el monto"),
        ("02/03/2026,MERCADO,-15.50,", "el saldo"),
        ("02/03/2026,,,", "la descripción, el monto y el saldo"),
    ],
)
def test_una_fila_a_medio_llenar_se_rechaza(tmp_path: Path, fila: str, falta: str) -> None:
    contenido = f"Fecha,Descripción,Monto,Saldo\n{fila}\n"
    assert _rechazo(tmp_path, contenido) == [
        ("fila_incompleta", f"Línea 2: fila a medio llenar, falta {falta}.")
    ]


def test_debito_y_credito_en_la_misma_fila_se_rechaza(tmp_path: Path) -> None:
    contenido = BUENO_COMA.replace(";15,50;", ";15,50;15,50")
    problemas = _rechazo(
        tmp_path, contenido, banco=CON_COMA, saldo_anterior_centavos=0, saldo_final_centavos=0
    )
    assert problemas == [("debito_y_credito", "Línea 4: la fila trae débito y crédito a la vez.")]


def test_una_fila_con_columnas_de_mas_o_de_menos_se_rechaza(tmp_path: Path) -> None:
    contenido = "Fecha,Descripción,Monto,Saldo\n02/03/2026,MERCADO, CENTRO,-15.50,984.50\n"
    assert _rechazo(tmp_path, contenido) == [
        ("columnas", "Línea 2: la fila tiene 5 columnas y el encabezado 4.")
    ]


def test_un_encabezado_de_otro_banco_se_rechaza_antes_de_leer_montos(tmp_path: Path) -> None:
    contenido = "Date,Description,Amount\n02/03/2026,MERCADO,no-es-un-monto\n"
    assert _rechazo(tmp_path, contenido) == [
        (
            "encabezado",
            "Línea 1: al encabezado le faltan las columnas 'Fecha', 'Descripción', 'Monto' "
            "y 'Saldo'; trae 'Date', 'Description' y 'Amount'. ¿Es un estado de Banco Sintético?",
        )
    ]


def test_falta_una_sola_columna(tmp_path: Path) -> None:
    contenido = "Fecha,Descripción,Monto\n02/03/2026,MERCADO,-15.50\n"
    assert _rechazo(tmp_path, contenido) == [
        (
            "encabezado",
            "Línea 1: al encabezado le faltan las columnas 'Saldo'; trae 'Fecha', "
            "'Descripción' y 'Monto'. ¿Es un estado de Banco Sintético?",
        )
    ]


def test_una_columna_repetida_en_el_encabezado_se_rechaza(tmp_path: Path) -> None:
    contenido = "Fecha,Descripción,Monto,Monto,Saldo\n"
    assert _rechazo(tmp_path, contenido) == [
        ("encabezado", "Línea 1: la columna 'Monto' aparece dos veces en el encabezado.")
    ]


@pytest.mark.parametrize("contenido", ["", "\n\n", "Título\n"])
def test_un_archivo_sin_encabezado_se_rechaza(tmp_path: Path, contenido: str) -> None:
    problemas = _rechazo(
        tmp_path, contenido, banco=CON_COMA, saldo_anterior_centavos=0, saldo_final_centavos=0
    )
    assert problemas == [
        ("sin_encabezado", "El archivo no tiene encabezado: se esperaba en la línea 3.")
    ]


def test_un_archivo_en_otra_codificacion_se_rechaza(tmp_path: Path) -> None:
    assert _rechazo(tmp_path, BUENO.encode("latin-1")) == [
        ("codificacion", "El archivo no está en utf-8: el byte 16 no se pudo leer.")
    ]


def test_un_csv_mal_formado_se_rechaza(tmp_path: Path) -> None:
    contenido = 'Fecha,Descripción,Monto,Saldo\n02/03/2026,"MERCADO,-15.50,984.50\n'
    assert [codigo for codigo, _ in _rechazo(tmp_path, contenido)] == ["csv_mal_formado"]


def test_una_descripcion_demasiado_larga_se_rechaza_en_vez_de_cortarse(tmp_path: Path) -> None:
    contenido = f"Fecha,Descripción,Monto,Saldo\n02/03/2026,{'X' * 501},-15.50,984.50\n"
    assert _rechazo(tmp_path, contenido) == [
        ("descripcion_larga", "Línea 2: la descripción tiene 501 caracteres y el máximo es 500.")
    ]


@pytest.mark.parametrize("falta", ["saldo_anterior_centavos", "saldo_final_centavos"])
@pytest.mark.parametrize("banco", [CON_SALDO, CON_COMA], ids=lambda banco: banco.clave)
def test_los_dos_saldos_declarados_son_obligatorios(
    tmp_path: Path, banco: Banco, falta: str
) -> None:
    """Aunque el banco traiga columna de saldo: nunca se deducen del archivo.

    Si el saldo anterior saliera de la primera fila, esa fila no se verificaría
    contra nada y un archivo cortado en las puntas cuadraría.
    """
    ruta = tmp_path / "estado.csv"
    ruta.write_text(BUENO, encoding="utf-8")
    argumentos: dict[str, Any] = {
        "cuenta": "ahorros",
        "desde": date(2026, 3, 1),
        "hasta": date(2026, 3, 31),
        "tope_bytes": BANCOS.tope_bytes,
        "saldo_anterior_centavos": 100_000,
        "saldo_final_centavos": 123_405,
    }
    del argumentos[falta]
    with pytest.raises(TypeError, match=falta):
        leer_estado(ruta, banco, **argumentos)


def test_un_periodo_al_reves_se_rechaza(tmp_path: Path) -> None:
    assert _rechazo(tmp_path, BUENO, desde=date(2026, 3, 31), hasta=date(2026, 3, 1)) == [
        ("periodo", "El periodo empieza (2026-03-31) después de terminar (2026-03-01).")
    ]


@pytest.mark.parametrize("cuenta", ["2200123456", "ahorros 1234", "Ahorros", "", "a" * 41])
def test_la_cuenta_es_una_etiqueta_nunca_el_numero(tmp_path: Path, cuenta: str) -> None:
    assert _rechazo(tmp_path, BUENO, cuenta=cuenta) == [
        (
            "cuenta",
            f"La cuenta {cuenta!r} no es una etiqueta válida: minúsculas, hasta 40 "
            "caracteres y nunca el número de cuenta.",
        )
    ]


def test_reporta_todas_las_filas_malas_no_solo_la_primera(tmp_path: Path) -> None:
    contenido = (
        "Fecha,Descripción,Monto,Saldo\n"
        "31/02/2026,MERCADO,-15.50,984.50\n"
        '03/03/2026,TAXI,"1,200.00",0.00\n'
    )
    assert [codigo for codigo, _ in _rechazo(tmp_path, contenido)] == [
        "fecha_invalida",
        "monto_invalido",
    ]


# --- Saldos escritos a mano ---


@pytest.mark.parametrize(
    ("texto", "centavos"),
    [("1000", 100_000), ("849.5", 84_950), ("-12.34", -1234), ("0", 0), (" 15.50 ", 1550)],
)
def test_centavos_declarados(texto: str, centavos: int) -> None:
    assert centavos_declarados(texto) == centavos


@pytest.mark.parametrize("texto", ["1,5", "1,200.00", "12.345", "abc", "", "1e3", "+5"])
def test_centavos_declarados_rechaza_lo_que_no_es_exacto(texto: str) -> None:
    with pytest.raises(ValueError, match="no es un saldo"):
        centavos_declarados(texto)
