"""Evaluación del modelo contra un banco de mensajes etiquetados a mano.

Mide lo que importa para dinero real: no solo cuántas veces el sistema acierta,
sino cuántas se equivoca *sin avisar* (error con confianza alta), y cuántos de
esos errores el LLM afirmó con confianza y el código atrapó. Esa última cifra es
la tesis del proyecto medida: el LLM propone, el código verifica.

Nada de I/O de consola acá: `cli.py` corre esto y lo imprime. Los tests usan un
cliente falso, como los del parser.
"""

import hashlib
import json
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from family_expense_bot.extraction.categorias import Taxonomia
from family_expense_bot.extraction.gemini import CuotaAgotada, ErrorDelLLM
from family_expense_bot.extraction.parser import PROMPT, ClienteLLM, ErrorDeExtraccion, extraer
from family_expense_bot.extraction.schema import CAMPOS, Confianza, GastoExtraido

_CONFIANZA_CRUDA = {
    "monto": "confianza_monto",
    "categoria": "confianza_categoria",
    "fecha": "confianza_fecha",
    "atribuido_a": "confianza_quien_paga",
}
"""Dónde declara el LLM su confianza para cada campo que sostiene el sistema."""


class Celda(StrEnum):
    ACIERTO_CONFIADO = "acierto confiado"
    ACIERTO_CON_DUDA = "acierto con duda"
    ERROR_ATRAPADO = "error atrapado"
    ERROR_SILENCIOSO = "error silencioso"


@dataclass(frozen=True)
class Caso:
    id: str
    remitente: str
    texto: str
    oro: dict[str, str | None]
    """Valor correcto por campo. Un campo ausente no se puntúa; None es "sin valor"."""
    debe_dudar: tuple[str, ...] = ()
    etiquetas: tuple[str, ...] = ()


@dataclass(frozen=True)
class Resultado:
    id: str
    crudo: str | None
    gasto: dict[str, Any] | None
    """`GastoExtraido` en JSON. None si el proveedor o el contrato fallaron."""
    error: str | None
    segundos: float

    def extraido(self) -> GastoExtraido | None:
        return GastoExtraido.model_validate(self.gasto) if self.gasto is not None else None


def version_prompt() -> str:
    """Identifica el prompt de una corrida: cambia el prompt, cambia el hash."""
    return hashlib.sha256(PROMPT.encode()).hexdigest()[:8]


def cargar_casos(ruta: Path, taxonomia: Taxonomia) -> tuple[date, list[Caso]]:
    """Lee el banco y falla rápido si alguna etiqueta no existe en la taxonomía."""
    datos = yaml.safe_load(ruta.read_text())
    hoy = date.fromisoformat(datos["hoy"])
    casos = [
        Caso(
            id=c["id"],
            remitente=c["remitente"],
            texto=c["texto"],
            oro={k: (str(v) if v is not None else None) for k, v in c["oro"].items()},
            debe_dudar=tuple(c.get("debe_dudar", ())),
            etiquetas=tuple(c.get("etiquetas", ())),
        )
        for c in datos["casos"]
    ]
    _validar(casos, taxonomia)
    return hoy, casos


def _validar(casos: list[Caso], taxonomia: Taxonomia) -> None:
    repetidos = [i for i, n in Counter(c.id for c in casos).items() if n > 1]
    if repetidos:
        raise ValueError(f"ids repetidos: {repetidos}")
    for c in casos:

        def falla(motivo: str, caso: Caso = c) -> ValueError:
            return ValueError(f"{caso.id}: {motivo}")

        if c.remitente not in taxonomia.personas:
            raise falla(f"remitente {c.remitente!r} no está en la taxonomía")
        if extra := set(c.oro) - set(CAMPOS):
            raise falla(f"campos desconocidos en oro: {extra}")
        if extra := set(c.debe_dudar) - set(CAMPOS):
            raise falla(f"campos desconocidos en debe_dudar: {extra}")
        if (cat := c.oro.get("categoria")) is not None and not taxonomia.contiene(cat):
            raise falla(f"categoría {cat!r} no es una categoría activa")
        if (quien := c.oro.get("atribuido_a")) is not None and not taxonomia.contiene_atribucion(
            quien
        ):
            raise falla(f"atribuido_a {quien!r} no está en la taxonomía")
        if (fecha := c.oro.get("fecha")) is not None:
            date.fromisoformat(fecha)
        if (monto := c.oro.get("monto")) is not None:
            Decimal(monto)


class ClienteGrabador:
    """Envuelve un cliente real y guarda lo que el LLM afirmó y cuánto tardó.

    Así se compara la afirmación cruda con lo que sostuvo el parser, sin tocar
    el parser.
    """

    def __init__(self, cliente: ClienteLLM) -> None:
        self._cliente = cliente
        self.crudo: str | None = None
        self.segundos = 0.0

    def completar_json(self, prompt: str, schema: dict[str, object]) -> str:
        self.crudo = None
        inicio = time.perf_counter()
        try:
            self.crudo = self._cliente.completar_json(prompt, schema)
        finally:
            self.segundos = time.perf_counter() - inicio
        return self.crudo


def correr(
    casos: list[Caso],
    cliente: ClienteLLM,
    taxonomia: Taxonomia,
    hoy: date,
    pausa: float = 0.0,
) -> Iterator[Resultado]:
    """Un resultado por caso. Con la cuota agotada se corta: lo que salió, sirve."""
    grabador = ClienteGrabador(cliente)
    for i, caso in enumerate(casos):
        if i and pausa:
            time.sleep(pausa)  # el plan gratuito limita las llamadas por minuto
        gasto: dict[str, Any] | None = None
        error: str | None = None
        try:
            extraido = extraer(caso.texto, grabador, taxonomia, caso.remitente, hoy)
            gasto = extraido.model_dump(mode="json")
        except CuotaAgotada:
            return
        except (ErrorDelLLM, ErrorDeExtraccion) as e:
            error = str(e)
        yield Resultado(caso.id, grabador.crudo, gasto, error, grabador.segundos)


def clasificar(caso: Caso, gasto: GastoExtraido) -> dict[str, Celda]:
    """La celda de cada campo que el caso puntúa."""
    celdas: dict[str, Celda] = {}
    for campo, esperado in caso.oro.items():
        correcto = _igual(campo, getattr(gasto, campo), esperado)
        dudo = campo in gasto.campos_dudosos
        if correcto:
            celdas[campo] = Celda.ACIERTO_CON_DUDA if dudo else Celda.ACIERTO_CONFIADO
        else:
            celdas[campo] = Celda.ERROR_ATRAPADO if dudo else Celda.ERROR_SILENCIOSO
    return celdas


def _igual(campo: str, obtenido: object, esperado: str | None) -> bool:
    if esperado is None or obtenido is None:
        return esperado is None and obtenido is None
    if campo == "monto":
        return Decimal(str(obtenido)) == Decimal(esperado)
    if campo == "fecha":
        return str(obtenido) == esperado
    return obtenido == esperado


def _llm_dijo_alta(crudo: str | None, campo: str) -> bool:
    if crudo is None:
        return False
    return bool(json.loads(crudo).get(_CONFIANZA_CRUDA[campo]) == Confianza.ALTA)


@dataclass
class Resumen:
    casos: int = 0
    fallos_del_proveedor: int = 0
    celdas: dict[str, Counter[Celda]] = field(
        default_factory=lambda: {campo: Counter() for campo in CAMPOS}
    )
    rescates: Counter[str] = field(default_factory=Counter)
    """Errores que el LLM afirmó con confianza alta y el código bajó a duda."""
    debio_dudar: int = 0
    dudo_cuando_debia: int = 0
    silenciosos: list[tuple[str, str, object, str | None]] = field(default_factory=list)
    """(caso, campo, obtenido, esperado) de cada error silencioso, para leerlos uno a uno."""
    segundos: list[float] = field(default_factory=list)

    def latencia(self, percentil: float) -> float:
        """La media engaña: la latencia de Gemini tiene una cola larga."""
        if not self.segundos:
            return 0.0
        ordenados = sorted(self.segundos)
        return ordenados[min(int(len(ordenados) * percentil), len(ordenados) - 1)]


def resumir(casos: list[Caso], resultados: list[Resultado]) -> Resumen:
    por_id = {c.id: c for c in casos}
    resumen = Resumen()
    for r in resultados:
        caso = por_id[r.id]
        resumen.casos += 1
        resumen.segundos.append(r.segundos)
        gasto = r.extraido()
        if gasto is None:
            # Ni acierto ni error: sin extracción, el bot avisa y no muestra nada.
            resumen.fallos_del_proveedor += 1
            continue
        for campo, celda in clasificar(caso, gasto).items():
            resumen.celdas[campo][celda] += 1
            if celda is Celda.ERROR_SILENCIOSO:
                resumen.silenciosos.append((caso.id, campo, getattr(gasto, campo), caso.oro[campo]))
            if celda is Celda.ERROR_ATRAPADO and _llm_dijo_alta(r.crudo, campo):
                resumen.rescates[campo] += 1
        for campo in caso.debe_dudar:
            resumen.debio_dudar += 1
            resumen.dudo_cuando_debia += campo in gasto.campos_dudosos
    return resumen


def tabla_markdown(resumen: Resumen, modelo: str, prompt: str | None = None) -> str:
    """`prompt` es la versión de la corrida; por defecto, la del prompt actual."""
    validos = resumen.casos - resumen.fallos_del_proveedor
    lineas = [
        f"**{modelo}** · prompt `{prompt or version_prompt()}` · {resumen.casos} casos, "
        f"{resumen.fallos_del_proveedor} sin respuesta válida · "
        f"latencia p50 {resumen.latencia(0.5):.1f} s, p90 {resumen.latencia(0.9):.1f} s",
        "",
        "| Campo | Puntuados | Acierto confiado | Acierto con duda | Error atrapado "
        "| Error silencioso | Rescatados por el código |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for campo in CAMPOS:
        celdas = resumen.celdas[campo]
        total = sum(celdas.values())
        lineas.append(
            f"| {campo} | {total} | "
            + " | ".join(_pct(celdas[c], total) for c in Celda)
            + f" | {resumen.rescates[campo]} |"
        )
    lineas += [
        "",
        f"Dudó cuando debía: {resumen.dudo_cuando_debia} de {resumen.debio_dudar} "
        f"({_porcentaje(resumen.dudo_cuando_debia, resumen.debio_dudar)}) · "
        f"casos con respuesta válida: {validos}",
    ]
    if resumen.silenciosos:
        lineas += ["", "Errores silenciosos:", ""]
        lineas += [
            f"- `{caso}` · {campo}: obtuvo {obtenido!r}, esperaba {esperado!r}"
            for caso, campo, obtenido, esperado in resumen.silenciosos
        ]
    return "\n".join(lineas)


def _pct(parte: int, total: int) -> str:
    return f"{parte} ({_porcentaje(parte, total)})" if total else "sin casos"


def _porcentaje(parte: int, total: int) -> str:
    return f"{parte / total:.0%}" if total else "sin casos"


def guardar_corrida(ruta: Path, modelo: str, hoy: date, resultados: list[Resultado]) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    meta = {"modelo": modelo, "prompt": version_prompt(), "hoy": hoy.isoformat()}
    with ruta.open("w") as f:
        f.write(json.dumps({"meta": meta}, ensure_ascii=False) + "\n")
        for r in resultados:
            fila = {
                "id": r.id,
                "crudo": r.crudo,
                "gasto": r.gasto,
                "error": r.error,
                "segundos": round(r.segundos, 3),
            }
            f.write(json.dumps(fila, ensure_ascii=False) + "\n")


def leer_corrida(ruta: Path) -> tuple[dict[str, str], list[Resultado]]:
    """Recalcula una tabla sin volver a gastar cuota."""
    primera, *filas = ruta.read_text().splitlines()
    meta: dict[str, str] = json.loads(primera)["meta"]
    resultados = [
        Resultado(d["id"], d["crudo"], d["gasto"], d["error"], d["segundos"])
        for d in map(json.loads, filas)
    ]
    return meta, resultados
