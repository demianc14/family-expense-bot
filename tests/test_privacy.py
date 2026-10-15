"""Guardas de privacidad: el repo es público y maneja dinero real de terceros."""

import json
import re
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
    "n8n/.env",
    "n8n/credenciales.json",
    "n8n/.datos/database.sqlite",
    "n8n/e2e/.datos/database.sqlite",
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


SECRETOS = ("TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY", "TELEGRAM_USUARIOS", "INGRESOS_MENSUALES")
"""Variables que en `.env.example` tienen que ir vacías: son secretos o IDs personales."""


SECRETOS_N8N = (
    "N8N_ENCRYPTION_KEY",
    "N8N_WEBHOOK_URL",
    "TELEGRAM_USUARIOS",
    "TELEGRAM_ADMIN_CHAT_ID",
    "INGRESOS_MENSUALES",
)


@pytest.mark.parametrize(
    ("archivo", "secretos"), [(".env.example", SECRETOS), ("n8n/.env.example", SECRETOS_N8N)]
)
def test_env_example_no_trae_valores_secretos(archivo: str, secretos: tuple[str, ...]) -> None:
    valores = dict(
        linea.split("=", 1)
        for linea in (ROOT / archivo).read_text(encoding="utf-8").splitlines()
        if "=" in linea and not linea.lstrip().startswith("#")
    )
    assert set(secretos) <= valores.keys()
    con_valor = [clave for clave in secretos if valores[clave].strip()]
    assert not con_valor, f"{archivo} trae valores en {con_valor}"


def test_credenciales_de_ejemplo_n8n_solo_tienen_marcadores() -> None:
    credenciales = json.loads((ROOT / "n8n" / "credenciales.ejemplo.json").read_text())
    for credencial in credenciales:
        for campo in ("accessToken", "apiKey"):
            if campo in credencial["data"]:
                assert re.fullmatch(r"<[A-Z_]+>", credencial["data"][campo]), campo


PATRONES_DE_SECRETOS = {
    "token de bot de Telegram": re.compile(r"\b\d{8,10}:AA[\w-]{30,}"),
    "API key de Google": re.compile(r"\bAIza[\w-]{30,}"),
    "API key de Google (AQ.)": re.compile(r"\bAQ\.[\w-]{30,}"),
}


def test_ningun_archivo_versionado_contiene_secretos() -> None:
    """`.env.example` llegó a versionarse con un token real: esto lo habría atajado."""
    archivos = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.split("\0")
    hallazgos = []
    for nombre in filter(None, archivos):
        ruta = ROOT / nombre
        if not ruta.is_file():
            continue
        texto = ruta.read_text(encoding="utf-8", errors="ignore")
        hallazgos += [
            f"{nombre}: {tipo}" for tipo, p in PATRONES_DE_SECRETOS.items() if p.search(texto)
        ]
    assert not hallazgos, f"Posibles secretos versionados: {hallazgos}"


def test_ningun_commit_del_historial_contiene_secretos() -> None:
    """Revisa cada objeto de git, no solo la versión actual: un push publica todo el historial.

    El historial se limpió con `git filter-repo --replace-text` después de que
    `.env.example` llegara a versionarse con un token real.
    """
    objetos = subprocess.run(
        ["git", "cat-file", "--batch-all-objects", "--batch"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8", errors="ignore")
    hallazgos = [tipo for tipo, p in PATRONES_DE_SECRETOS.items() if p.search(objetos)]
    assert not hallazgos, f"El historial de git contiene: {hallazgos}"
