"""Guardas de privacidad: el repo es público y maneja dinero real de terceros."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

DEBEN_IGNORARSE = [
    "data/processed/gastos.db",
    "data/processed/export.csv",
    "gastos.sqlite",
    "reporte.xlsx",
    ".env",
]


@pytest.mark.parametrize("ruta", DEBEN_IGNORARSE)
def test_gitignore_excluye_datos_reales_y_secretos(ruta: str) -> None:
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "-q", ruta], cwd=ROOT, check=False
    )
    assert result.returncode == 0, f"{ruta} NO está ignorado por .gitignore"


def test_env_example_si_se_versiona() -> None:
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "-q", ".env.example"], cwd=ROOT, check=False
    )
    assert result.returncode == 1
