"""Carga de la taxonomía. Es config (`config/categories.yaml`), no código."""

from pathlib import Path

import yaml
from pydantic import BaseModel, model_validator

RUTA_POR_DEFECTO = Path(__file__).resolve().parents[3] / "config" / "categories.yaml"


class Categoria(BaseModel):
    nombre: str
    descripcion: str
    ejemplos: list[str] = []


class Taxonomia(BaseModel):
    categorias: list[Categoria]
    fallback: str

    @model_validator(mode="after")
    def _fallback_existe(self) -> "Taxonomia":
        if not self.categorias:
            raise ValueError("categories.yaml no define ninguna categoría")
        if self.fallback not in self.nombres:
            raise ValueError(f"fallback {self.fallback!r} no está en la lista de categorías")
        return self

    @property
    def nombres(self) -> tuple[str, ...]:
        return tuple(c.nombre for c in self.categorias)

    def contiene(self, nombre: str | None) -> bool:
        return nombre is not None and nombre.strip().lower() in self.nombres

    def describir_para_prompt(self) -> str:
        return "\n".join(
            f"- {c.nombre}: {c.descripcion}"
            + (f" Ej.: {', '.join(c.ejemplos)}." if c.ejemplos else "")
            for c in self.categorias
        )


def cargar_taxonomia(ruta: Path | None = None) -> Taxonomia:
    ruta = ruta or RUTA_POR_DEFECTO
    if not ruta.is_file():
        raise FileNotFoundError(f"No existe la taxonomía de categorías en {ruta}")
    return Taxonomia.model_validate(yaml.safe_load(ruta.read_text(encoding="utf-8")))
