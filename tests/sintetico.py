"""Carga `scripts/estado_sintetico.py` para los tests: `scripts/` no es un paquete."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

RUTA = Path(__file__).resolve().parents[1] / "scripts" / "estado_sintetico.py"


def cargar() -> ModuleType:
    nombre = "estado_sintetico"
    if nombre not in sys.modules:
        spec = importlib.util.spec_from_file_location(nombre, RUTA)
        assert spec is not None and spec.loader is not None
        modulo = importlib.util.module_from_spec(spec)
        sys.modules[nombre] = modulo  # los dataclass buscan su módulo acá
        spec.loader.exec_module(modulo)
    return sys.modules[nombre]
