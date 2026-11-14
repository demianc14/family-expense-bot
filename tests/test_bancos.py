"""`config/bancos.yaml`: el formato de cada banco y las reglas que asignan `tipo`.

Un banco nuevo es una entrada en el yaml, no código. Todo es sintético.
"""

from pathlib import Path
from typing import Any, get_args

import pytest
import yaml
from pydantic import ValidationError

from family_expense_bot.conciliacion.bancos import (
    Banco,
    ReglaTipo,
    asignar_tipo,
    cargar_bancos,
)
from family_expense_bot.conciliacion.contrato import TipoMovimiento
from family_expense_bot.storage.models import TIPOS_MOVIMIENTO

BANCO: dict[str, Any] = {
    "nombre": "Banco de prueba",
    "columnas": {"fecha": "Fecha", "descripcion": "Detalle", "monto": "Monto"},
    "formato_fecha": "%d/%m/%Y",
    "decimal": "punto",
}


def _banco(**cambios: Any) -> Banco:
    return Banco.model_validate(BANCO | cambios)


def _cargar(tmp_path: Path, contenido: dict[str, Any]) -> Any:
    ruta = tmp_path / "bancos.yaml"
    ruta.write_text(yaml.safe_dump(contenido), encoding="utf-8")
    return cargar_bancos(ruta)


# --- El yaml del repo ---


def test_el_yaml_del_repo_carga_y_trae_los_bancos_sinteticos() -> None:
    bancos = cargar_bancos()
    assert {"banco_sintetico", "banco_sintetico_coma"} <= bancos.bancos.keys()
    assert bancos.tope_bytes > 0


def test_cada_banco_conoce_su_clave() -> None:
    bancos = cargar_bancos()
    assert all(banco.clave == clave for clave, banco in bancos.bancos.items())


def test_los_tipos_del_contrato_son_los_de_la_base() -> None:
    assert get_args(TipoMovimiento) == TIPOS_MOVIMIENTO


# --- Un banco mal escrito no carga ---


def test_falla_si_el_yaml_no_existe(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="No existe la configuración de bancos"):
        cargar_bancos(tmp_path / "no-hay.yaml")


def test_rechaza_una_clave_de_banco_con_mayusculas_o_espacios(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="Banco Raro"):
        _cargar(tmp_path, {"tope_bytes": 1000, "bancos": {"Banco Raro": BANCO}})


def test_rechaza_un_campo_que_no_existe() -> None:
    with pytest.raises(ValidationError):
        _banco(separador_de_miles=",")


@pytest.mark.parametrize(
    "columnas",
    [
        {"fecha": "F", "descripcion": "D"},
        {"fecha": "F", "descripcion": "D", "monto": "M", "debito": "Db", "credito": "Cr"},
        {"fecha": "F", "descripcion": "D", "debito": "Db"},
    ],
)
def test_el_monto_va_en_una_columna_o_en_debito_y_credito(columnas: dict[str, str]) -> None:
    with pytest.raises(ValidationError, match="una columna `monto` o las dos"):
        _banco(columnas=columnas)


def test_dos_campos_no_pueden_leer_la_misma_columna() -> None:
    with pytest.raises(ValidationError, match="la misma columna"):
        _banco(columnas={"fecha": "A", "descripcion": "A", "monto": "M"})


def test_el_signo_solo_aplica_a_la_columna_de_monto() -> None:
    columnas = {"fecha": "F", "descripcion": "D", "debito": "Db", "credito": "Cr"}
    with pytest.raises(ValidationError, match="`signo` solo aplica"):
        _banco(columnas=columnas, signo="positivo_sale")


def test_el_delimitador_es_un_solo_caracter() -> None:
    with pytest.raises(ValidationError):
        _banco(delimitador=";;")


def test_rechaza_un_formato_de_fecha_sin_dia_mes_y_anio() -> None:
    with pytest.raises(ValidationError, match="formato_fecha"):
        _banco(formato_fecha="%d/%m")


def test_rechaza_un_tipo_que_no_existe() -> None:
    with pytest.raises(ValidationError):
        _banco(tipos=[{"tipo": "propina", "contiene": ["propina"]}])


def test_una_regla_sin_frases_no_carga() -> None:
    with pytest.raises(ValidationError):
        _banco(tipos=[{"tipo": "comision", "contiene": []}])
    with pytest.raises(ValidationError, match="frase vacía"):
        _banco(tipos=[{"tipo": "comision", "contiene": ["  "]}])


# --- Asignación de `tipo`: reglas en orden, sin LLM ---

REGLAS = [
    ReglaTipo(tipo="comision", contiene=["comision", "mantenimiento de cuenta"], signo="sale"),
    ReglaTipo(tipo="interes", contiene=["interes"]),
    ReglaTipo(tipo="pago", contiene=["pago tarjeta"], signo="sale"),
]


@pytest.mark.parametrize(
    ("descripcion", "centavos", "tipo"),
    [
        ("SUPERMERCADO SINTETICO", -1550, "debito"),
        ("TRANSFERENCIA RECIBIDA", 50_000, "credito"),
        ("COMISIÓN RETIRO CAJERO", -45, "comision"),
        ("Mantenimiento de cuenta", -150, "comision"),
        ("INTERES GANADO", 12, "interes"),
        ("INTERES POR SOBREGIRO", -300, "interes"),
        ("PAGO TARJETA VISA", -20_000, "pago"),
    ],
)
def test_asigna_el_tipo(descripcion: str, centavos: int, tipo: str) -> None:
    assert asignar_tipo(descripcion, centavos, REGLAS) == tipo


def test_sin_reglas_decide_el_signo() -> None:
    assert asignar_tipo("COMISION", -45, []) == "debito"
    assert asignar_tipo("COMISION", 45, []) == "credito"


def test_la_regla_con_signo_no_aplica_al_signo_contrario() -> None:
    """La devolución de una comisión es plata que entra: no es costo del banco."""
    assert asignar_tipo("REVERSO COMISION RETIRO", 45, REGLAS) == "credito"


def test_gana_la_primera_regla_que_coincide() -> None:
    reglas = [
        ReglaTipo(tipo="interes", contiene=["interes"]),
        ReglaTipo(tipo="comision", contiene=["comision"]),
    ]
    assert asignar_tipo("COMISION E INTERES", -10, reglas) == "interes"
    assert asignar_tipo("COMISION E INTERES", -10, reglas[::-1]) == "comision"


def test_las_frases_son_palabras_completas() -> None:
    """ "comisionista" no es una comisión, ni "desinteres" un interés."""
    assert asignar_tipo("PAGO A COMISIONISTA", -5000, REGLAS) == "debito"
    assert asignar_tipo("LIBRERIA DESINTERES", -900, REGLAS) == "debito"
