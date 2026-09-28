"""Genera `n8n/taxonomia.json` desde `config/categories.yaml`.

El yaml sigue siendo la fuente de verdad. Pasar por `cargar_taxonomia` hace que
las mismas validaciones de la versión Python (fallback activo, una sola
atribución compartida, parentescos sin ambigüedad) corran antes de que n8n vea
nada: una taxonomía inválida no llega a exportarse.

Uso: python n8n/exportar_taxonomia.py
"""

import json
from pathlib import Path

from family_expense_bot.extraction.categorias import cargar_taxonomia

DESTINO = Path(__file__).resolve().parent / "taxonomia.json"


def taxonomia_como_json() -> str:
    datos = cargar_taxonomia().model_dump(mode="json")
    return json.dumps(datos, ensure_ascii=False, indent=2) + "\n"


if __name__ == "__main__":
    DESTINO.write_text(taxonomia_como_json(), encoding="utf-8")
    print(f"Escrito {DESTINO}")
