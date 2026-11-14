"""Los estados sintéticos de `scripts/estado_sintetico.py`, importados de verdad.

Cada caso corre la importación completa contra una base en memoria: lo que
cuadra entra y lo hostil se rechaza por la razón esperada, sin guardar nada.
"""

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from family_expense_bot.conciliacion.contrato import EstadoRechazado
from family_expense_bot.storage.repository import crear_engine
from tests.sintetico import cargar

sintetico = cargar()
RAIZ = Path(__file__).resolve().parents[1]
INICIO, FIN = "<!-- tabla-casos:inicio -->", "<!-- tabla-casos:fin -->"


@pytest.fixture(scope="module")
def carpeta(tmp_path_factory: pytest.TempPathFactory) -> Path:
    destino = tmp_path_factory.mktemp("estados")
    sintetico.preparar(destino, con_enorme=True)
    return destino


@pytest.mark.parametrize("caso", sintetico.CASOS, ids=lambda caso: caso.nombre)
def test_cada_caso_termina_como_se_espera(caso: Any, carpeta: Path) -> None:
    resultado = sintetico.correr(caso, carpeta)
    if isinstance(resultado, EstadoRechazado):
        codigos = tuple(dict.fromkeys(p.codigo for p in resultado.problemas))
        assert codigos == caso.espera, [p.mensaje for p in resultado.problemas]
    else:
        assert caso.espera == (), "entró un estado que debía rechazarse"
        assert len(resultado.avisos) == caso.avisos, resultado.avisos


def test_hay_casos_que_entran_y_casos_que_no() -> None:
    entran = [caso for caso in sintetico.CASOS if not caso.espera]
    assert len(entran) >= 3
    assert len(sintetico.CASOS) - len(entran) >= 14


def test_cada_archivo_tiene_un_caso_y_cada_caso_su_archivo() -> None:
    usados = {caso.archivo for caso in sintetico.CASOS}
    assert usados == set(sintetico.archivos()) | {sintetico.ENORME}


def test_el_generador_es_determinista() -> None:
    assert sintetico.archivos() == sintetico.archivos()


def test_los_estados_que_cuadran_cuadran_al_centavo() -> None:
    final_marzo = sintetico.SALDO_INICIAL + sum(c for _, _, c in sintetico.MARZO)
    assert final_marzo == 171_473
    assert final_marzo + sum(c for _, _, c in sintetico.ABRIL) == 161_064


def test_los_tipos_salen_de_las_reglas_del_banco(carpeta: Path) -> None:
    resultado = sintetico.correr(sintetico.CASOS[0], carpeta)
    tipos = {"debito": 12, "credito": 1, "comision": 2, "interes": 1, "pago": 1}
    assert resultado.por_tipo == tipos


def test_la_base_sintetica_trae_gastos_y_los_dos_estados(tmp_path: Path) -> None:
    ruta = tmp_path / "sintetica.db"
    assert sintetico.base_sintetica(ruta) == (11, 22)
    with crear_engine(f"sqlite:///{ruta}").connect() as con:
        assert con.execute(text("SELECT count(*) FROM gastos")).scalar_one() == 11
        assert con.execute(text("SELECT count(*) FROM estados_cuenta")).scalar_one() == 2
        assert con.execute(text("SELECT count(*) FROM movimientos")).scalar_one() == 22
        assert con.execute(text("SELECT count(*) FROM conciliaciones")).scalar_one() == 0


def test_la_base_sintetica_no_escribe_sobre_una_base_que_existe(tmp_path: Path) -> None:
    ruta = tmp_path / "gastos.db"
    ruta.write_bytes(b"no me toques")
    with pytest.raises(SystemExit, match="ya existe"):
        sintetico.base_sintetica(ruta)
    assert ruta.read_bytes() == b"no me toques"


def test_la_tabla_de_la_documentacion_es_la_que_sale_del_comando() -> None:
    """`python scripts/estado_sintetico.py --tabla`: los mensajes del doc no se escriben a mano."""
    documento = (RAIZ / "docs" / "conciliacion.md").read_text(encoding="utf-8")
    en_el_doc = documento.split(INICIO)[1].split(FIN)[0].strip()
    assert en_el_doc == sintetico.tabla()
