"""Carga de la taxonomía. Es config (`config/categories.yaml`), no código."""

import re
import unicodedata
from pathlib import Path

import yaml
from pydantic import BaseModel, model_validator

RUTA_POR_DEFECTO = Path(__file__).resolve().parents[3] / "config" / "categories.yaml"


def normalizar(texto: str) -> str:
    """Minúsculas y sin tildes: "Común" y "comun" tienen que ser lo mismo."""
    sin_tildes = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in sin_tildes if not unicodedata.combining(c)).lower()


class Categoria(BaseModel):
    nombre: str
    descripcion: str
    ejemplos: list[str] = []
    candidata: bool = False
    """Propuesta sin confirmar: listada, pero el parser no la ofrece ni la acepta."""


class Atribucion(BaseModel):
    nombre: str
    etiqueta: str
    descripcion: str
    compartida: bool = False
    marcadores: list[str] = []
    """Frases que hacen explícito un gasto compartido. Solo aplica a la compartida."""

    como_lo_llaman: dict[str, list[str]] = {}
    """Remitente → cómo se refiere a esta persona ("mi hija", "mi ñaña")."""


def menciona_alguna(texto: str, frases: list[str]) -> bool:
    """¿El texto contiene alguna frase, como palabras completas y sin tildes?

    "común" acepta "comun", pero "casamiento" no dispara "casa".
    """
    normalizado = normalizar(texto)
    return any(re.search(rf"\b{re.escape(normalizar(f))}\b", normalizado) for f in frases)


class Bolsillo(BaseModel):
    """Una parte del ingreso mensual: cuánto se le asigna y qué gastos cuentan contra ella."""

    nombre: str
    etiqueta: str
    porcentaje: int
    se_gasta: bool = True
    """False para el ahorro: se aparta, así que ningún gasto cuenta contra él."""

    categorias: list[str] = []
    """Categorías que, por defecto, cuentan contra este bolsillo."""

    marcadores: list[str] = []
    """Frases que mandan un gasto a este bolsillo sea cual sea su categoría
    ("del fondo"). Como con la casa, nunca se deduce: el mensaje lo tiene que decir."""


class Presupuesto(BaseModel):
    bolsillos: list[Bolsillo]


class Taxonomia(BaseModel):
    categorias: list[Categoria]
    fallback: str
    atribuciones: list[Atribucion]
    presupuesto: Presupuesto | None = None
    """Cómo se reparte el ingreso mensual. Los montos de ingreso viven en .env."""

    @model_validator(mode="after")
    def _consistente(self) -> "Taxonomia":
        if not self.categorias:
            raise ValueError("categories.yaml no define ninguna categoría")
        if self.fallback not in self.nombres:
            raise ValueError(
                f"fallback {self.fallback!r} no está entre las categorías activas "
                "(¿quedó marcada como candidata?)"
            )
        _sin_duplicados("categoría", [c.nombre for c in self.categorias])
        _sin_duplicados("atribución", [a.nombre for a in self.atribuciones])

        compartidas = [a for a in self.atribuciones if a.compartida]
        if len(compartidas) != 1:
            raise ValueError(
                f"Tiene que haber exactamente una atribución compartida, hay {len(compartidas)}"
            )
        if not compartidas[0].marcadores:
            raise ValueError(
                f"La atribución compartida {compartidas[0].nombre!r} necesita marcadores: "
                "sin ellos nunca podría aceptarse con confianza"
            )
        if not self.personas:
            raise ValueError("categories.yaml no define ninguna persona en atribuciones")
        self._parentescos_consistentes()
        if self.presupuesto is not None:
            self._presupuesto_consistente(self.presupuesto)
        return self

    def _presupuesto_consistente(self, presupuesto: Presupuesto) -> None:
        bolsillos = presupuesto.bolsillos
        _sin_duplicados("bolsillo", [b.nombre for b in bolsillos])
        if any(b.porcentaje <= 0 for b in bolsillos):
            raise ValueError("Cada bolsillo necesita un porcentaje positivo")
        total = sum(b.porcentaje for b in bolsillos)
        if total != 100:
            raise ValueError(f"Los porcentajes del presupuesto suman {total}, no 100")

        todas = {c.nombre for c in self.categorias}
        asignadas: list[str] = []
        for b in bolsillos:
            if not b.se_gasta and (b.categorias or b.marcadores):
                raise ValueError(
                    f"El bolsillo {b.nombre!r} no se gasta: no puede tener categorías ni marcadores"
                )
            desconocidas = set(b.categorias) - todas
            if desconocidas:
                raise ValueError(
                    f"El bolsillo {b.nombre!r} usa categorías que no existen: "
                    f"{sorted(desconocidas)}"
                )
            asignadas += b.categorias
        repetidas = {c for c in asignadas if asignadas.count(c) > 1}
        if repetidas:
            raise ValueError(f"Categorías en más de un bolsillo: {sorted(repetidas)}")
        marcadores = [normalizar(m) for b in bolsillos for m in b.marcadores]
        if len(set(marcadores)) != len(marcadores):
            raise ValueError("Un mismo marcador no puede mandar a dos bolsillos")

    def _parentescos_consistentes(self) -> None:
        for a in self.atribuciones:
            desconocidos = set(a.como_lo_llaman) - set(self.personas)
            if desconocidos:
                raise ValueError(
                    f"{a.nombre}.como_lo_llaman usa remitentes que no son personas: "
                    f"{sorted(desconocidos)}"
                )
            if a.nombre in a.como_lo_llaman:
                raise ValueError(f"{a.nombre}.como_lo_llaman no puede incluirse a sí misma")
        # Una misma expresión, dicha por el mismo remitente, no puede apuntar a dos
        # personas: sería ambigua por construcción.
        for remitente in self.personas:
            vistas: dict[str, str] = {}
            for a in self.atribuciones:
                for expresion in a.como_lo_llaman.get(remitente, []):
                    clave = normalizar(expresion)
                    if clave in vistas and vistas[clave] != a.nombre:
                        raise ValueError(
                            f"Desde {remitente}, {expresion!r} apunta a {vistas[clave]} "
                            f"y a {a.nombre}"
                        )
                    vistas[clave] = a.nombre

    # --- categorías ------------------------------------------------------

    @property
    def activas(self) -> tuple[Categoria, ...]:
        return tuple(c for c in self.categorias if not c.candidata)

    @property
    def nombres(self) -> tuple[str, ...]:
        """Solo las categorías activas: son las únicas que pueden recibir datos."""
        return tuple(c.nombre for c in self.activas)

    @property
    def candidatas(self) -> tuple[str, ...]:
        return tuple(c.nombre for c in self.categorias if c.candidata)

    def contiene(self, nombre: str | None) -> bool:
        return nombre is not None and nombre.strip().lower() in self.nombres

    def describir_para_prompt(self) -> str:
        return "\n".join(
            f"- {c.nombre}: {c.descripcion}"
            + (f" Ej.: {', '.join(c.ejemplos)}." if c.ejemplos else "")
            for c in self.activas
        )

    # --- atribuciones ----------------------------------------------------

    @property
    def compartida(self) -> Atribucion:
        return next(a for a in self.atribuciones if a.compartida)

    @property
    def personas(self) -> tuple[str, ...]:
        """Atribuciones que pueden ser remitentes. La compartida nunca escribe."""
        return tuple(a.nombre for a in self.atribuciones if not a.compartida)

    @property
    def nombres_atribucion(self) -> tuple[str, ...]:
        return tuple(a.nombre for a in self.atribuciones)

    @property
    def etiquetas(self) -> dict[str, str]:
        return {a.nombre: a.etiqueta for a in self.atribuciones}

    def contiene_atribucion(self, nombre: str | None) -> bool:
        return nombre is not None and nombre.strip().lower() in self.nombres_atribucion

    def menciona_gasto_compartido(self, texto: str) -> bool:
        """¿El mensaje dice explícitamente que el gasto es compartido?"""
        return menciona_alguna(texto, self.compartida.marcadores)

    # --- presupuesto -----------------------------------------------------

    @property
    def bolsillos(self) -> tuple[Bolsillo, ...]:
        return tuple(self.presupuesto.bolsillos) if self.presupuesto else ()

    def bolsillo_de(self, categoria: str, texto: str) -> str | None:
        """Contra qué bolsillo cuenta un gasto. None si contra ninguno.

        Un marcador en el mensaje ("del fondo") gana sobre la categoría, pero solo
        si apunta a un único bolsillo; si no, manda la categoría.
        """
        por_marcador = [b.nombre for b in self.bolsillos if menciona_alguna(texto, b.marcadores)]
        if len(por_marcador) == 1:
            return por_marcador[0]
        return next((b.nombre for b in self.bolsillos if categoria in b.categorias), None)

    def expresiones(self, persona: str, remitente: str) -> list[str]:
        """Cómo llama `remitente` a `persona`."""
        atribucion = next((a for a in self.atribuciones if a.nombre == persona), None)
        return atribucion.como_lo_llaman.get(remitente, []) if atribucion else []

    def menciona_a(self, persona: str, remitente: str, texto: str) -> bool:
        """¿El mensaje nombra a `persona` como la llamaría quien lo escribió?"""
        normalizado = normalizar(texto)
        return any(
            re.search(rf"\b{re.escape(normalizar(e))}\b", normalizado)
            for e in self.expresiones(persona, remitente)
        )

    def personas_en(self, cita: str, remitente: str) -> list[str]:
        """Qué personas nombra `cita`, leída como la escribió `remitente`.

        Es la resolución determinista de parentescos: "mi hija" escrito por mama
        es `hermana`; escrito por demian no es nadie de la lista.
        """
        return [p for p in self.personas if p != remitente and self.menciona_a(p, remitente, cita)]

    def es_compartida(self, cita: str) -> bool:
        """¿La cita nombra la atribución compartida (por su nombre o un marcador)?"""
        return normalizar(cita).strip() == self.compartida.nombre or self.menciona_gasto_compartido(
            cita
        )


def _sin_duplicados(tipo: str, nombres: list[str]) -> None:
    repetidos = {n for n in nombres if nombres.count(n) > 1}
    if repetidos:
        raise ValueError(f"{tipo} repetida en categories.yaml: {sorted(repetidos)}")


def cargar_taxonomia(ruta: Path | None = None) -> Taxonomia:
    ruta = ruta or RUTA_POR_DEFECTO
    if not ruta.is_file():
        raise FileNotFoundError(f"No existe la taxonomía de categorías en {ruta}")
    return Taxonomia.model_validate(yaml.safe_load(ruta.read_text(encoding="utf-8")))
