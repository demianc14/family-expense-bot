"""Verificación de un estado ya leído: cuadra o no entra.

Cada regla tiene su test con el mensaje completo. Todo es sintético.
"""

from datetime import date, timedelta
from typing import Any

from family_expense_bot.conciliacion.contrato import (
    EstadoLeido,
    EstadoVigente,
    MovimientoLeido,
)
from family_expense_bot.conciliacion.verificacion import verificar

HASH = "a" * 64
MARZO = (date(2026, 3, 1), date(2026, 3, 31))


def _estado(
    montos: list[int],
    anterior: int = 100_000,
    final: int | None = None,
    con_saldo: bool = True,
    saldos: dict[int, int] | None = None,
    fechas: dict[int, date] | None = None,
    descripciones: dict[int, str] | None = None,
    **cambios: Any,
) -> EstadoLeido:
    """Un estado que cuadra, salvo lo que se le cambie. Los índices son posiciones (desde 0)."""
    movimientos = []
    saldo = anterior
    for i, monto in enumerate(montos):
        saldo += monto
        movimientos.append(
            MovimientoLeido(
                linea=i + 2,
                fecha=(fechas or {}).get(i, date(2026, 3, 2) + timedelta(days=i)),
                descripcion=(descripciones or {}).get(i, f"COMERCIO SINTETICO {i}"),
                monto_centavos=monto,
                saldo_centavos=(saldos or {}).get(i, saldo) if con_saldo else None,
                tipo="debito" if monto < 0 else "credito",
            )
        )
    campos: dict[str, Any] = {
        "banco": "banco_sintetico",
        "cuenta": "ahorros",
        "periodo_desde": MARZO[0],
        "periodo_hasta": MARZO[1],
        "saldo_anterior_centavos": anterior,
        "saldo_final_centavos": saldo if final is None else final,
        "hash_archivo": HASH,
        "movimientos": tuple(movimientos),
    } | cambios
    return EstadoLeido(**campos)


def _vigente(**cambios: Any) -> EstadoVigente:
    campos: dict[str, Any] = {
        "id": 7,
        "banco": "banco_sintetico",
        "cuenta": "ahorros",
        "periodo_desde": date(2026, 2, 1),
        "periodo_hasta": date(2026, 2, 28),
        "saldo_anterior_centavos": 90_000,
        "saldo_final_centavos": 100_000,
        "hash_archivo": "b" * 64,
    } | cambios
    return EstadoVigente(**campos)


def _problemas(estado: EstadoLeido, *vigentes: EstadoVigente) -> list[tuple[str, str]]:
    return [(p.codigo, p.mensaje) for p in verificar(estado, vigentes).problemas]


MONTOS = [-1550, 25_000, -45, -3000]


# --- Lo que cuadra ---


def test_un_estado_que_cuadra_no_tiene_problemas_ni_avisos() -> None:
    for con_saldo in (True, False):
        veredicto = verificar(_estado(MONTOS, con_saldo=con_saldo))
        assert (veredicto.problemas, veredicto.avisos) == ((), ())


def test_un_estado_sin_movimientos_cuadra_si_el_saldo_no_cambio() -> None:
    assert _problemas(_estado([])) == []


def test_dos_cargos_identicos_el_mismo_dia_no_son_un_duplicado() -> None:
    estado = _estado(
        [-35, -35, -1200],
        fechas=dict.fromkeys(range(3), date(2026, 3, 9)),
        descripciones={0: "BUS", 1: "BUS"},
    )
    veredicto = verificar(estado)
    assert (veredicto.problemas, veredicto.avisos) == ((), ())


def test_las_fechas_en_los_bordes_del_periodo_entran() -> None:
    estado = _estado([-100, -100], fechas={0: MARZO[0], 1: MARZO[1]})
    assert _problemas(estado) == []


# --- Invariante de saldo ---


def test_el_saldo_final_corrido_un_centavo_no_cuadra() -> None:
    estado = _estado(MONTOS, final=120_406, con_saldo=False)
    assert _problemas(estado) == [
        (
            "saldo_no_cuadra",
            "El estado no cuadra: saldo anterior 1000.00 + créditos 250.00 - débitos 45.95 = "
            "1204.05, pero el saldo final es 1204.06 (diferencia 0.01).",
        )
    ]


def test_el_saldo_no_cuadra_con_saldos_negativos() -> None:
    """Una tarjeta: se debe más al final que al principio."""
    estado = _estado([-5000], anterior=-10_000, final=-14_000, con_saldo=False)
    assert _problemas(estado) == [
        (
            "saldo_no_cuadra",
            "El estado no cuadra: saldo anterior -100.00 + créditos 0.00 - débitos 50.00 = "
            "-150.00, pero el saldo final es -140.00 (diferencia 10.00).",
        )
    ]


def test_sin_movimientos_y_con_saldos_distintos_no_cuadra() -> None:
    assert _problemas(_estado([], final=100_500)) == [
        (
            "saldo_no_cuadra",
            "El estado no cuadra: saldo anterior 1000.00 + créditos 0.00 - débitos 0.00 = "
            "1000.00, pero el saldo final es 1005.00 (diferencia 5.00).",
        )
    ]


def test_el_signo_invertido_no_cuadra() -> None:
    """Un banco configurado al revés: los débitos entran como créditos."""
    estado = _estado([1550, -25_000], final=123_450, con_saldo=False)
    assert [codigo for codigo, _ in _problemas(estado)] == ["saldo_no_cuadra"]


# --- Saldo corrido (solo con columna de saldo) ---


def test_dos_errores_que_se_compensan_los_delata_el_saldo_corrido() -> None:
    """+1 y -1 centavo: el total cuadra, pero cada fila tiene que encadenar con la anterior."""
    buenos = _estado(MONTOS)
    saldos = {i: m.saldo_centavos or 0 for i, m in enumerate(buenos.movimientos)}
    estado = _estado([-1549, 25_000, -46, -3000], saldos=saldos)
    assert _problemas(estado) == [
        (
            "saldo_corrido",
            "Línea 2: el saldo no encadena: 1000.00 - 15.49 = 984.51, pero la fila dice 984.50.",
        ),
        (
            "saldo_corrido",
            "Línea 4: el saldo no encadena: 1234.50 - 0.46 = 1234.04, pero la fila dice 1234.05.",
        ),
    ]


def test_sin_columna_de_saldo_los_errores_compensados_pasan() -> None:
    """Límite documentado: sin saldo por fila solo se verifica el total."""
    assert _problemas(_estado([-1549, 25_000, -46, -3000], con_saldo=False)) == []


def test_el_saldo_corrido_con_un_credito() -> None:
    estado = _estado([25_000], saldos={0: 125_001}, final=125_000)
    assert _problemas(estado) == [
        (
            "saldo_corrido",
            "Línea 2: el saldo no encadena: 1000.00 + 250.00 = 1250.00, pero la fila dice 1250.01.",
        ),
        (
            "saldo_final",
            "El saldo final (1250.00) no coincide con el de la última fila (1250.01, línea 2).",
        ),
    ]


def test_el_saldo_anterior_declarado_tiene_que_encadenar_con_la_primera_fila() -> None:
    bueno = _estado(MONTOS)
    estado = bueno.model_copy(update={"saldo_anterior_centavos": 100_100})
    assert _problemas(estado) == [
        (
            "saldo_corrido",
            "Línea 2: el saldo no encadena: 1001.00 - 15.50 = 985.50, pero la fila dice 984.50.",
        ),
        (
            "saldo_no_cuadra",
            "El estado no cuadra: saldo anterior 1001.00 + créditos 250.00 - débitos 45.95 = "
            "1205.05, pero el saldo final es 1204.05 (diferencia -1.00).",
        ),
    ]


# --- Fechas ---


def test_una_fecha_fuera_del_periodo_se_rechaza() -> None:
    estado = _estado(MONTOS, fechas={0: date(2026, 2, 28), 3: date(2026, 4, 1)})
    assert _problemas(estado) == [
        (
            "fecha_fuera_de_periodo",
            "Línea 2: la fecha 2026-02-28 está fuera del periodo 2026-03-01 a 2026-03-31.",
        ),
        (
            "fecha_fuera_de_periodo",
            "Línea 5: la fecha 2026-04-01 está fuera del periodo 2026-03-01 a 2026-03-31.",
        ),
    ]


# --- Bloque de filas duplicado ---


def test_un_bloque_duplicado_que_descuadra_el_estado_se_senala() -> None:
    """Dos exports pegados o un bloque copiado dos veces: el saldo final no lo incluye."""
    montos = [-1550, -45, -3000, -45, -3000, 25_000]
    descripciones = {1: "COMISION", 2: "FARMACIA", 3: "COMISION", 4: "FARMACIA"}
    fechas = {1: date(2026, 3, 5), 2: date(2026, 3, 6), 3: date(2026, 3, 5), 4: date(2026, 3, 6)}
    estado = _estado(
        montos, final=120_405, con_saldo=False, descripciones=descripciones, fechas=fechas
    )
    assert _problemas(estado) == [
        (
            "saldo_no_cuadra",
            "El estado no cuadra: saldo anterior 1000.00 + créditos 250.00 - débitos 76.40 = "
            "1173.60, pero el saldo final es 1204.05 (diferencia 30.45).",
        ),
        (
            "bloque_duplicado",
            "Las líneas 5 a 6 repiten las líneas 3 a 4 y suman -30.45, justo la diferencia.",
        ),
    ]


def test_un_bloque_duplicado_con_saldo_por_fila_tambien_rompe_la_cadena() -> None:
    montos = [-1550, -45, -3000, -45, -3000]
    descripciones = {1: "COMISION", 2: "FARMACIA", 3: "COMISION", 4: "FARMACIA"}
    fechas = {1: date(2026, 3, 5), 2: date(2026, 3, 6), 3: date(2026, 3, 5), 4: date(2026, 3, 6)}
    # El bloque se copió con sus saldos, como al pegar dos exports.
    saldos = {3: 98_405, 4: 95_405}
    estado = _estado(
        montos, final=95_405, saldos=saldos, descripciones=descripciones, fechas=fechas
    )
    assert [codigo for codigo, _ in _problemas(estado)] == [
        "saldo_corrido",
        "saldo_no_cuadra",
        "bloque_duplicado",
    ]


def test_un_bloque_repetido_en_un_estado_que_cuadra_es_solo_un_aviso() -> None:
    """Bus y café dos veces el mismo día, en ese orden: el saldo confirma que son reales."""
    estado = _estado(
        [-35, -250, -35, -250],
        fechas=dict.fromkeys(range(4), date(2026, 3, 9)),
        descripciones={0: "BUS", 1: "CAFE", 2: "BUS", 3: "CAFE"},
    )
    veredicto = verificar(estado)
    assert veredicto.problemas == ()
    assert veredicto.avisos == (
        "Las líneas 4 a 5 repiten las líneas 2 a 3. El estado cuadra, así que entraron "
        "como movimientos reales: revisa que no sea un bloque pegado dos veces.",
    )


def test_el_bloque_duplicado_que_no_explica_la_diferencia_lo_dice() -> None:
    estado = _estado(
        [-35, -250, -35, -250],
        final=0,
        con_saldo=False,
        fechas=dict.fromkeys(range(4), date(2026, 3, 9)),
        descripciones={0: "BUS", 1: "CAFE", 2: "BUS", 3: "CAFE"},
    )
    assert _problemas(estado)[1] == (
        "bloque_duplicado",
        "Las líneas 4 a 5 repiten las líneas 2 a 3 y suman -2.85.",
    )


# --- Duplicados contra lo ya importado ---


def test_el_mismo_archivo_dos_veces_se_rechaza_y_no_se_revisa_nada_mas() -> None:
    estado = _estado(MONTOS, final=1)  # además no cuadra, pero eso ya no importa
    repetido = _vigente(hash_archivo=HASH, periodo_desde=MARZO[0], periodo_hasta=MARZO[1])
    assert _problemas(estado, repetido) == [
        (
            "archivo_repetido",
            "Este archivo ya se importó: es el estado #7 (banco_sintetico/ahorros, "
            "2026-03-01 a 2026-03-31).",
        )
    ]


def test_el_mismo_archivo_en_otra_cuenta_tambien_se_rechaza() -> None:
    repetido = _vigente(hash_archivo=HASH, cuenta="visa")
    assert [codigo for codigo, _ in _problemas(_estado(MONTOS), repetido)] == ["archivo_repetido"]


def test_un_periodo_que_se_cruza_con_otro_estado_de_la_cuenta_se_rechaza() -> None:
    """El mismo mes exportado de nuevo: otros bytes, otro hash, los mismos movimientos."""
    otro = _vigente(periodo_desde=date(2026, 3, 15), periodo_hasta=date(2026, 4, 14))
    assert _problemas(_estado(MONTOS), otro) == [
        (
            "periodo_solapado",
            "El periodo 2026-03-01 a 2026-03-31 se cruza con el del estado #7 "
            "(2026-03-15 a 2026-04-14) de la misma cuenta.",
        )
    ]


def test_el_periodo_de_otra_cuenta_o_de_otro_banco_no_estorba() -> None:
    en_marzo = {"periodo_desde": MARZO[0], "periodo_hasta": MARZO[1]}
    otros = (_vigente(cuenta="visa", **en_marzo), _vigente(banco="otro_banco", **en_marzo))
    assert _problemas(_estado(MONTOS), *otros) == []


# --- Continuidad entre estados: aviso, no rechazo ---


def test_si_el_estado_anterior_cerro_con_otro_saldo_avisa() -> None:
    veredicto = verificar(_estado(MONTOS), [_vigente(saldo_final_centavos=99_950)])
    assert veredicto.problemas == ()
    assert veredicto.avisos == (
        "El estado #7 (2026-02-01 a 2026-02-28) cerró con 999.50 y este empieza con 1000.00: "
        "hay 0.50 de diferencia entre los dos.",
    )


def test_si_el_estado_siguiente_empieza_con_otro_saldo_avisa() -> None:
    siguiente = _vigente(
        periodo_desde=date(2026, 4, 1), periodo_hasta=date(2026, 4, 30), saldo_anterior_centavos=1
    )
    veredicto = verificar(_estado(MONTOS), [siguiente])
    assert veredicto.avisos == (
        "Este estado cierra con 1204.05 y el #7 (2026-04-01 a 2026-04-30) empieza con 0.01: "
        "hay -1204.04 de diferencia entre los dos.",
    )


def test_si_los_saldos_empatan_no_avisa() -> None:
    assert verificar(_estado(MONTOS), [_vigente()]).avisos == ()


def test_la_continuidad_solo_mira_el_estado_que_termina_justo_antes() -> None:
    lejos = _vigente(
        periodo_desde=date(2026, 1, 1), periodo_hasta=date(2026, 1, 31), saldo_final_centavos=5
    )
    otra_cuenta = _vigente(cuenta="visa", saldo_final_centavos=5)
    assert verificar(_estado(MONTOS), [lejos, otra_cuenta]).avisos == ()
