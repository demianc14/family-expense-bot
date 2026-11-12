"""Esquema de la conciliación: migración y restricciones.

Solo el esquema. Los inserts van en SQL directo a propósito: lo que se prueba
es que la base rechaza lo que no debe entrar, llegue por donde llegue.
Todos los datos son sintéticos.
"""

from pathlib import Path

import pytest
from sqlalchemy import Connection, Engine, create_engine, inspect
from sqlalchemy.exc import IntegrityError

from family_expense_bot.storage.models import TIPOS_MOVIMIENTO
from family_expense_bot.storage.repository import (
    EsquemaDesactualizado,
    crear_engine,
    migrar,
)

TABLAS = ["estados_cuenta", "movimientos", "conciliaciones"]
HASH_A = "a" * 64
HASH_B = "b" * 64
ANULADO = "anulado_en = '2026-04-02 10:00:00', motivo_anulacion = 'prueba'"


@pytest.fixture
def engine() -> Engine:
    return crear_engine("sqlite://")


def _estado(con: Connection, hash_archivo: str = HASH_A, **cambios: object) -> int:
    fila: dict[str, object] = {
        "banco": "banco_sintetico",
        "cuenta": "ahorros",
        "periodo_desde": "2026-03-01",
        "periodo_hasta": "2026-03-31",
        "saldo_anterior_centavos": 100_000,
        "saldo_final_centavos": 85_050,
        "hash_archivo": hash_archivo,
        "importado_en": "2026-04-01 09:00:00",
    } | cambios
    return _insertar(con, "estados_cuenta", fila)


def _movimiento(con: Connection, estado_id: int, linea: int = 1, **cambios: object) -> int:
    fila: dict[str, object] = {
        "estado_id": estado_id,
        "linea": linea,
        "fecha": "2026-03-14",
        "descripcion": "SUPERMERCADO SINTETICO",
        "monto_centavos": -1550,
        "tipo": "debito",
    } | cambios
    return _insertar(con, "movimientos", fila)


def _gasto(con: Connection) -> int:
    fila: dict[str, object] = {
        "monto_centavos": 1550,
        "categoria": "alimentacion",
        "atribuido_a": "demian",
        "fecha": "2026-03-14",
        "descripcion": "mercado",
        "texto_original": "15,50 mercado",
        "registrado_por": 1,
        "creado_en": "2026-03-14 12:00:00",
        "confianza_monto": "alta",
        "confianza_categoria": "alta",
        "confianza_fecha": "alta",
        "confianza_atribuido_a": "media",
        "corregido_manualmente": 0,
    }
    return _insertar(con, "gastos", fila)


def _conciliacion(con: Connection, movimiento_id: int, gasto_id: int, **cambios: object) -> int:
    fila: dict[str, object] = {
        "movimiento_id": movimiento_id,
        "gasto_id": gasto_id,
        "regla": "monto_exacto_misma_fecha",
        "creado_en": "2026-04-01 09:05:00",
    } | cambios
    return _insertar(con, "conciliaciones", fila)


def _insertar(con: Connection, tabla: str, fila: dict[str, object]) -> int:
    columnas = ", ".join(fila)
    marcas = ", ".join("?" for _ in fila)
    cursor = con.exec_driver_sql(
        f"INSERT INTO {tabla} ({columnas}) VALUES ({marcas})", tuple(fila.values())
    )
    return int(cursor.lastrowid)


def _anular(con: Connection, tabla: str, id_: int) -> None:
    con.exec_driver_sql(f"UPDATE {tabla} SET {ANULADO} WHERE id = ?", (id_,))


# --- migración ------------------------------------------------------------


def _base_de_antes(ruta: Path) -> str:
    """Una base con gastos y sin las tablas de la conciliación."""
    url = f"sqlite:///{ruta}"
    engine = crear_engine(url)
    with engine.begin() as con:
        _gasto(con)
        for tabla in reversed(TABLAS):
            con.exec_driver_sql(f"DROP TABLE {tabla}")
    engine.dispose()
    return url


def test_migrar_crea_las_tres_tablas_sin_tocar_los_gastos(tmp_path: Path) -> None:
    url = _base_de_antes(tmp_path / "gastos.db")

    assert migrar(url) == TABLAS

    engine = crear_engine(url)
    assert set(TABLAS) <= set(inspect(engine).get_table_names())
    with engine.connect() as con:
        assert con.exec_driver_sql("SELECT monto_centavos FROM gastos").all() == [(1550,)]
    assert migrar(url) == []  # idempotente


def test_migrar_crea_los_indices_unicos_parciales(tmp_path: Path) -> None:
    url = _base_de_antes(tmp_path / "gastos.db")
    migrar(url)

    with create_engine(url).connect() as con:
        sql = dict(
            con.exec_driver_sql(
                "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND name LIKE 'uq_%'"
            ).all()
        )
    assert set(sql) == {
        "uq_estados_hash_vigente",
        "uq_conciliaciones_movimiento_vigente",
        "uq_conciliaciones_gasto_vigente",
    }
    assert all("UNIQUE" in s and "WHERE anulado_en IS NULL" in s for s in sql.values())


def test_arrancar_falla_si_a_una_tabla_nueva_le_falta_una_columna(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'gastos.db'}"
    engine = crear_engine(url)
    with engine.begin() as con:
        # Una `movimientos` de antes de que existiera `tipo`.
        con.exec_driver_sql("DROP TABLE conciliaciones")
        con.exec_driver_sql("DROP TABLE movimientos")
        con.exec_driver_sql(
            "CREATE TABLE movimientos (id INTEGER PRIMARY KEY, estado_id INTEGER, linea INTEGER, "
            "fecha DATE, descripcion TEXT, monto_centavos INTEGER, anulado_en DATETIME, "
            "motivo_anulacion TEXT)"
        )
    engine.dispose()

    with pytest.raises(EsquemaDesactualizado, match=r"movimientos\.tipo"):
        crear_engine(url)


@pytest.mark.parametrize(
    ("tabla", "indice"),
    [
        ("estados_cuenta", "uq_estados_hash_vigente"),
        ("conciliaciones", "uq_conciliaciones_movimiento_vigente"),
        ("conciliaciones", "uq_conciliaciones_gasto_vigente"),
    ],
)
def test_arrancar_falla_si_falta_un_indice_unico(tmp_path: Path, tabla: str, indice: str) -> None:
    """Sin el índice, la tabla aceptaría el mismo archivo o el mismo par dos veces."""
    url = f"sqlite:///{tmp_path / 'gastos.db'}"
    engine = crear_engine(url)
    with engine.begin() as con:
        con.exec_driver_sql(f"DROP INDEX {indice}")
    engine.dispose()

    with pytest.raises(EsquemaDesactualizado, match=rf"{tabla}\.{indice}"):
        crear_engine(url)


# --- llaves foráneas ------------------------------------------------------


def test_cada_conexion_nueva_tiene_las_llaves_foraneas_activas(tmp_path: Path) -> None:
    """El PRAGMA es por conexión: activarlo una vez no alcanza."""
    engine = crear_engine(f"sqlite:///{tmp_path / 'gastos.db'}")
    for _ in range(2):
        engine.dispose()  # tira el pool: la siguiente conexión es nueva de verdad
        with engine.connect() as con:
            assert con.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            with pytest.raises(IntegrityError, match="FOREIGN KEY"):
                _movimiento(con, estado_id=999)


def test_una_conciliacion_no_apunta_a_algo_que_no_existe(engine: Engine) -> None:
    with engine.begin() as con:
        movimiento = _movimiento(con, _estado(con))
        gasto = _gasto(con)
        with pytest.raises(IntegrityError, match="FOREIGN KEY"):
            _conciliacion(con, movimiento, gasto_id=999)
        with pytest.raises(IntegrityError, match="FOREIGN KEY"):
            _conciliacion(con, movimiento_id=999, gasto_id=gasto)


# --- estados_cuenta -------------------------------------------------------


def test_el_mismo_archivo_no_entra_dos_veces(engine: Engine) -> None:
    with engine.begin() as con:
        _estado(con, HASH_A)
        _estado(con, HASH_B)  # otro archivo sí
        with pytest.raises(IntegrityError, match="UNIQUE"):
            _estado(con, HASH_A)


def test_un_estado_anulado_se_puede_reimportar(engine: Engine) -> None:
    with engine.begin() as con:
        for _ in range(2):  # anular y reimportar más de una vez
            _anular(con, "estados_cuenta", _estado(con, HASH_A))
        _estado(con, HASH_A)
        with pytest.raises(IntegrityError, match="UNIQUE"):
            _estado(con, HASH_A)


@pytest.mark.parametrize(
    "cambios",
    [
        {"saldo_anterior_centavos": 1000.5},
        {"saldo_final_centavos": 850.25},
        {"saldo_final_centavos": "mucho"},
        {"periodo_desde": "2026-04-01", "periodo_hasta": "2026-03-01"},
        {"hash_archivo": "abc"},
        {"anulado_en": "2026-04-02 10:00:00"},
        {"motivo_anulacion": "sin fecha"},
    ],
)
def test_un_estado_mal_formado_se_rechaza(engine: Engine, cambios: dict[str, object]) -> None:
    with engine.begin() as con, pytest.raises(IntegrityError, match="CHECK"):
        _estado(con, **cambios)


def test_un_saldo_negativo_es_valido(engine: Engine) -> None:
    with engine.begin() as con:
        _estado(con, saldo_anterior_centavos=-25_000, saldo_final_centavos=-31_075)


# --- movimientos ----------------------------------------------------------


@pytest.mark.parametrize("tipo", TIPOS_MOVIMIENTO)
def test_cada_tipo_de_movimiento_se_acepta(engine: Engine, tipo: str) -> None:
    with engine.begin() as con:
        _movimiento(con, _estado(con), tipo=tipo)


def test_los_tipos_son_los_cinco_acordados() -> None:
    assert set(TIPOS_MOVIMIENTO) == {"debito", "credito", "comision", "interes", "pago"}


@pytest.mark.parametrize(
    "cambios",
    [
        {"monto_centavos": -15.5},
        {"monto_centavos": 0},
        {"monto_centavos": "quince"},
        {"tipo": "transferencia"},
        {"tipo": "Debito"},
        {"linea": 0},
        {"anulado_en": "2026-04-02 10:00:00"},
        {"motivo_anulacion": "sin fecha"},
    ],
)
def test_un_movimiento_mal_formado_se_rechaza(engine: Engine, cambios: dict[str, object]) -> None:
    with engine.begin() as con:
        estado = _estado(con)
        with pytest.raises(IntegrityError, match="CHECK"):
            _movimiento(con, estado, **cambios)


def test_un_movimiento_sin_tipo_se_rechaza(engine: Engine) -> None:
    with engine.begin() as con:
        estado = _estado(con)
        with pytest.raises(IntegrityError, match="NOT NULL"):
            _movimiento(con, estado, tipo=None)


def test_el_monto_lleva_signo(engine: Engine) -> None:
    with engine.begin() as con:
        estado = _estado(con)
        _movimiento(con, estado, linea=1, monto_centavos=-1550, tipo="debito")
        _movimiento(con, estado, linea=2, monto_centavos=120_000, tipo="credito")
        montos = con.exec_driver_sql("SELECT monto_centavos FROM movimientos ORDER BY linea").all()
    assert montos == [(-1550,), (120_000,)]


def test_dos_cargos_identicos_son_dos_movimientos_pero_una_linea_no_se_repite(
    engine: Engine,
) -> None:
    with engine.begin() as con:
        estado, otro = _estado(con, HASH_A), _estado(con, HASH_B)
        _movimiento(con, estado, linea=1)
        _movimiento(con, estado, linea=2)  # mismo monto, fecha y descripción
        _movimiento(con, otro, linea=1)  # la línea se numera por estado
        with pytest.raises(IntegrityError, match="UNIQUE"):
            _movimiento(con, estado, linea=1)


# --- conciliaciones -------------------------------------------------------


def test_un_movimiento_no_se_concilia_dos_veces(engine: Engine) -> None:
    with engine.begin() as con:
        movimiento = _movimiento(con, _estado(con))
        _conciliacion(con, movimiento, _gasto(con))
        with pytest.raises(IntegrityError, match="UNIQUE"):
            _conciliacion(con, movimiento, _gasto(con))


def test_un_gasto_no_se_concilia_dos_veces(engine: Engine) -> None:
    with engine.begin() as con:
        estado, gasto = _estado(con), _gasto(con)
        _conciliacion(con, _movimiento(con, estado, linea=1), gasto)
        with pytest.raises(IntegrityError, match="UNIQUE"):
            _conciliacion(con, _movimiento(con, estado, linea=2), gasto)


def test_un_par_anulado_libera_al_movimiento_y_al_gasto(engine: Engine) -> None:
    with engine.begin() as con:
        estado, gasto = _estado(con), _gasto(con)
        movimiento = _movimiento(con, estado, linea=1)
        _anular(con, "conciliaciones", _conciliacion(con, movimiento, gasto))

        _conciliacion(con, movimiento, _gasto(con), regla="monto_exacto_3_dias")
        _conciliacion(con, _movimiento(con, estado, linea=2), gasto)
        vigentes = con.exec_driver_sql(
            "SELECT count(*) FROM conciliaciones WHERE anulado_en IS NULL"
        ).scalar_one()
    assert vigentes == 2


@pytest.mark.parametrize(
    "cambios",
    [
        {"regla": ""},
        {"anulado_en": "2026-04-02 10:00:00"},
        {"motivo_anulacion": "sin fecha"},
    ],
)
def test_una_conciliacion_mal_formada_se_rechaza(
    engine: Engine, cambios: dict[str, object]
) -> None:
    with engine.begin() as con:
        movimiento, gasto = _movimiento(con, _estado(con)), _gasto(con)
        with pytest.raises(IntegrityError, match="CHECK"):
            _conciliacion(con, movimiento, gasto, **cambios)


def test_una_conciliacion_guarda_que_regla_la_unio(engine: Engine) -> None:
    with engine.begin() as con:
        movimiento, gasto = _movimiento(con, _estado(con)), _gasto(con)
        with pytest.raises(IntegrityError, match="NOT NULL"):
            _conciliacion(con, movimiento, gasto, regla=None)
        _conciliacion(con, movimiento, gasto)
        assert con.exec_driver_sql("SELECT regla FROM conciliaciones").all() == [
            ("monto_exacto_misma_fecha",)
        ]
