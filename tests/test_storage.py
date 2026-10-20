from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine

from family_expense_bot.extraction.schema import Confianza, GastoExtraido
from family_expense_bot.storage.dinero import a_centavos, a_decimal
from family_expense_bot.storage.repository import (
    EsquemaDesactualizado,
    GastoSinMonto,
    RepositorioGastos,
    crear_engine,
    migrar,
)


@pytest.fixture
def repo() -> RepositorioGastos:
    engine: Engine = crear_engine("sqlite://")  # en memoria: los tests no tocan disco
    return RepositorioGastos(engine)


def _gasto(monto: str | None = "15.50", **kwargs: object) -> GastoExtraido:
    base: dict[str, object] = {
        "monto": Decimal(monto) if monto is not None else None,
        "categoria": "transporte",
        "fecha": date(2026, 3, 14),
        "atribuido_a": "demian",
        "descripcion": "taxi",
        "texto_original": "15,50 de taxi ayer",
        "remitente": "demian",
        "confianza_monto": Confianza.ALTA,
        "confianza_categoria": Confianza.ALTA,
        "confianza_fecha": Confianza.ALTA,
        "confianza_atribuido_a": Confianza.MEDIA,
    }
    return GastoExtraido.model_validate(base | kwargs)


# --- dinero ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("monto", "centavos"),
    [("15.50", 1550), ("200", 20000), ("0.01", 1), ("1200.99", 120099), ("15.005", 1501)],
)
def test_a_centavos(monto: str, centavos: int) -> None:
    assert a_centavos(Decimal(monto)) == centavos


@pytest.mark.parametrize("monto", ["0", "-5"])
def test_a_centavos_rechaza_montos_no_positivos(monto: str) -> None:
    with pytest.raises(ValueError, match="positivo"):
        a_centavos(Decimal(monto))


def test_a_centavos_rechaza_montos_que_no_entran_en_la_base() -> None:
    """SQLite guarda enteros de 64 bits: más allá, el insert reventaba con OverflowError."""
    assert a_centavos(Decimal("92233720368547758.07")) == 2**63 - 1
    with pytest.raises(ValueError, match="demasiado grande"):
        a_centavos(Decimal("92233720368547758.08"))


def test_ida_y_vuelta_sin_perder_centavos() -> None:
    for monto in ("0.01", "15.50", "1200.99"):
        assert a_decimal(a_centavos(Decimal(monto))) == Decimal(monto)


def test_sumar_centavos_no_arrastra_error_de_punto_flotante() -> None:
    total = sum(a_centavos(Decimal(m)) for m in ("0.10", "0.20"))
    assert a_decimal(total) == Decimal("0.30")


# --- repositorio ----------------------------------------------------------


def test_guardar_y_recuperar(repo: RepositorioGastos) -> None:
    guardado = repo.guardar(_gasto(), registrado_por=12345)

    recuperado = repo.obtener(guardado.id)
    assert recuperado is not None
    assert recuperado.monto == Decimal("15.50")
    assert recuperado.categoria == "transporte"
    assert recuperado.fecha == date(2026, 3, 14)
    assert recuperado.registrado_por == 12345
    assert recuperado.corregido_manualmente is False
    assert recuperado.anulado is False


def test_no_guarda_un_gasto_sin_monto(repo: RepositorioGastos) -> None:
    with pytest.raises(GastoSinMonto):
        repo.guardar(_gasto(monto=None, confianza_monto=Confianza.BAJA), registrado_por=1)
    assert repo.listar() == []


def test_obtener_inexistente_devuelve_none(repo: RepositorioGastos) -> None:
    assert repo.obtener(999) is None


def test_guarda_las_confianzas_y_la_correccion(repo: RepositorioGastos) -> None:
    """Son el insumo del reporte de calidad de la Fase 4."""
    guardado = repo.guardar(
        _gasto(confianza_categoria=Confianza.BAJA), registrado_por=1, corregido_manualmente=True
    )
    assert repo.obtener(guardado.id) is not None
    assert guardado.corregido_manualmente is True


def test_listar_filtra_por_rango_de_fechas(repo: RepositorioGastos) -> None:
    for dia in (1, 15, 28):
        repo.guardar(_gasto(fecha=date(2026, 3, dia)), registrado_por=1)

    en_rango = repo.listar(desde=date(2026, 3, 10), hasta=date(2026, 3, 20))
    assert [g.fecha.day for g in en_rango] == [15]


def test_listar_ordena_de_mas_reciente_a_mas_viejo(repo: RepositorioGastos) -> None:
    for dia in (1, 28, 15):
        repo.guardar(_gasto(fecha=date(2026, 3, dia)), registrado_por=1)

    assert [g.fecha.day for g in repo.listar()] == [28, 15, 1]


# --- anulación (borrado lógico) -------------------------------------------


def test_anular_esconde_el_gasto_pero_no_lo_borra(repo: RepositorioGastos) -> None:
    guardado = repo.guardar(_gasto(), registrado_por=1)

    assert repo.anular(guardado.id, motivo="lo registré dos veces") is True
    assert repo.listar() == []

    con_anulados = repo.listar(incluir_anulados=True)
    assert len(con_anulados) == 1
    assert con_anulados[0].anulado is True


def test_anular_dos_veces_devuelve_false(repo: RepositorioGastos) -> None:
    guardado = repo.guardar(_gasto(), registrado_por=1)
    assert repo.anular(guardado.id, motivo="duplicado") is True
    assert repo.anular(guardado.id, motivo="duplicado") is False


def test_anular_inexistente_devuelve_false(repo: RepositorioGastos) -> None:
    assert repo.anular(999, motivo="no existe") is False


def test_anular_exige_motivo(repo: RepositorioGastos) -> None:
    guardado = repo.guardar(_gasto(), registrado_por=1)
    with pytest.raises(ValueError, match="motivo"):
        repo.anular(guardado.id, motivo="   ")


def test_creado_en_vuelve_con_zona_horaria(tmp_path: Path) -> None:
    """SQLite no guarda la zona: hay que reponerla al leer, o falla la Fase 4."""
    engine = crear_engine(f"sqlite:///{tmp_path / 'gastos.db'}")
    repo = RepositorioGastos(engine)
    guardado = repo.guardar(_gasto(), registrado_por=1)

    # Releído desde disco, no el objeto que quedó en memoria tras el insert.
    releido = RepositorioGastos(engine).obtener(guardado.id)
    assert releido is not None
    assert releido.creado_en.tzinfo is not None
    assert releido.creado_en.utcoffset() == timedelta(0)


def test_atribucion_y_remitente_son_independientes(repo: RepositorioGastos) -> None:
    """Tu mamá puede registrar un gasto de tu hermana: quien escribe no es a quien se atribuye."""
    guardado = repo.guardar(_gasto(atribuido_a="hermana"), registrado_por=222)

    recuperado = repo.obtener(guardado.id)
    assert recuperado is not None
    assert recuperado.atribuido_a == "hermana"
    assert recuperado.registrado_por == 222


# --- esquema --------------------------------------------------------------


def test_una_base_con_esquema_viejo_falla_al_arrancar(tmp_path: Path) -> None:
    """create_all no agrega columnas a tablas existentes. Sin esta verificación,
    el bot arrancaría normal y se caería en el primer insert, frente a alguien."""
    ruta = tmp_path / "vieja.db"
    vieja = create_engine(f"sqlite:///{ruta}")
    with vieja.begin() as con:
        con.exec_driver_sql(
            "CREATE TABLE gastos (id INTEGER PRIMARY KEY, monto_centavos INTEGER, categoria TEXT)"
        )
    vieja.dispose()

    with pytest.raises(EsquemaDesactualizado, match=r"gastos\.atribuido_a"):
        crear_engine(f"sqlite:///{ruta}")


def test_una_base_nueva_pasa_la_verificacion(tmp_path: Path) -> None:
    crear_engine(f"sqlite:///{tmp_path / 'nueva.db'}")
    crear_engine(f"sqlite:///{tmp_path / 'nueva.db'}")  # reabrirla tampoco falla


def test_migrar_agrega_origen_a_una_base_de_antes_de_la_app(tmp_path: Path) -> None:
    """Una base real de antes de la Mini App: tiene gastos y no tiene `origen`."""
    url = f"sqlite:///{tmp_path / 'gastos.db'}"
    RepositorioGastos(engine := crear_engine(url)).guardar(_gasto(), registrado_por=1)
    with engine.begin() as con:
        con.exec_driver_sql("ALTER TABLE gastos DROP COLUMN origen")
    engine.dispose()

    with pytest.raises(EsquemaDesactualizado, match=r"gastos\.origen.*--migrar"):
        crear_engine(url)

    assert migrar(url) == ["gastos.origen"]
    [viejo] = RepositorioGastos(crear_engine(url)).listar()
    assert viejo.origen == "chat"  # todo lo anterior entró por el chat
    assert migrar(url) == []  # idempotente


def test_el_origen_por_defecto_es_el_chat(repo: RepositorioGastos) -> None:
    assert repo.guardar(_gasto(), registrado_por=1).origen == "chat"
    assert repo.guardar(_gasto(), registrado_por=1, origen="formulario").origen == "formulario"
