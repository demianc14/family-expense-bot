from pathlib import Path

import pytest
from pydantic import ValidationError

from family_expense_bot.extraction.categorias import Taxonomia, cargar_taxonomia


def test_carga_la_taxonomia_real_del_repo() -> None:
    taxonomia = cargar_taxonomia()
    assert "mercado" in taxonomia.nombres
    assert taxonomia.fallback in taxonomia.nombres


def test_contiene_normaliza_mayusculas_y_espacios() -> None:
    taxonomia = cargar_taxonomia()
    assert taxonomia.contiene(" Mercado ")
    assert not taxonomia.contiene("regalos")
    assert not taxonomia.contiene(None)


def test_describir_para_prompt_incluye_cada_categoria() -> None:
    taxonomia = cargar_taxonomia()
    descripcion = taxonomia.describir_para_prompt()
    for nombre in taxonomia.nombres:
        assert nombre in descripcion


def test_falla_si_el_fallback_no_es_una_categoria() -> None:
    with pytest.raises(ValidationError):
        Taxonomia.model_validate(
            {"fallback": "inexistente", "categorias": [{"nombre": "mercado", "descripcion": "x"}]}
        )


def test_falla_si_no_hay_categorias() -> None:
    with pytest.raises(ValidationError):
        Taxonomia.model_validate({"fallback": "otros", "categorias": []})


def test_falla_si_el_archivo_no_existe(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        cargar_taxonomia(tmp_path / "no_existe.yaml")
