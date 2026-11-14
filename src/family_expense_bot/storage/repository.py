"""Persistencia. Sin lógica de negocio: no decide qué es confiable, solo guarda.

Quien llama es responsable de que el gasto ya esté confirmado por una persona.
Lo único que este módulo impone son invariantes de integridad: no se guarda un
gasto sin monto, y nada se borra de verdad.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, create_engine, event, inspect, select, text
from sqlalchemy.orm import sessionmaker

from family_expense_bot.extraction.schema import GastoExtraido
from family_expense_bot.storage.dinero import a_centavos, a_decimal
from family_expense_bot.storage.models import Base, Extraccion, Gasto, Ingreso


def _como_utc(momento: datetime) -> datetime:
    """SQLite no guarda la zona horaria: lo que se escribió en UTC vuelve sin ella.

    Sin esto, comparar `creado_en` contra un datetime con zona lanza TypeError.
    """
    return momento if momento.tzinfo else momento.replace(tzinfo=UTC)


class GastoRegistrado(BaseModel):
    """Vista de lectura de un gasto ya guardado, desacoplada de la sesión SQL."""

    model_config = ConfigDict(frozen=True)

    id: int
    monto: Decimal
    categoria: str
    atribuido_a: str
    fecha: date
    descripcion: str
    texto_original: str
    registrado_por: int
    creado_en: datetime
    corregido_manualmente: bool
    anulado: bool
    origen: str = "chat"
    subcategoria: str | None = None

    @classmethod
    def desde_fila(cls, fila: Gasto) -> "GastoRegistrado":
        return cls(
            id=fila.id,
            monto=a_decimal(fila.monto_centavos),
            categoria=fila.categoria,
            atribuido_a=fila.atribuido_a,
            fecha=fila.fecha,
            descripcion=fila.descripcion,
            texto_original=fila.texto_original,
            registrado_por=fila.registrado_por,
            creado_en=_como_utc(fila.creado_en),
            corregido_manualmente=fila.corregido_manualmente,
            anulado=fila.anulado_en is not None,
            subcategoria=fila.subcategoria,
            origen=fila.origen,
        )


class GastoSinMonto(ValueError):
    """No se guarda un gasto sin monto: sería una fila inútil y engañosa."""


class EsquemaDesactualizado(RuntimeError):
    """La base existe pero no coincide con el modelo actual."""


MIGRACIONES = {
    "gastos.origen": "ALTER TABLE gastos ADD COLUMN origen VARCHAR(12) NOT NULL DEFAULT 'chat'",
    "gastos.subcategoria": "ALTER TABLE gastos ADD COLUMN subcategoria VARCHAR(40)",
}
"""Columnas agregadas después de que ya había bases con datos reales.

Cada una tiene un default, así que las filas existentes quedan bien sin tocarlas
(todo lo anterior a la Mini App entró por el chat; nada tenía subcategoría).

Las tablas nuevas (ingresos, y las tres de la conciliación) no necesitan entrada:
`create_all` las crea enteras, con sus restricciones e índices.
"""


def activar_llaves_foraneas(engine: Engine) -> Engine:
    """Activa las llaves foráneas en cada conexión que abra el engine.

    SQLite las ignora salvo que cada conexión lo pida con un PRAGMA; sin esto,
    una conciliación podría apuntar a un gasto o a un movimiento que no existe.
    Hay que llamarla antes de la primera conexión.
    """
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _al_conectar(conexion: Any, _registro: Any) -> None:
            cursor = conexion.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def _engine(url: str) -> Engine:
    return activar_llaves_foraneas(create_engine(url))


def migrar(url: str) -> list[str]:
    """Lleva una base existente al esquema actual, sin tocar lo que ya tiene.

    Crea las tablas que falten y agrega las columnas de `MIGRACIONES`.
    Idempotente: correrla dos veces no hace nada la segunda. Devuelve lo que
    agregó: tablas por su nombre y columnas como `tabla.columna`.
    """
    engine = _engine(url)
    antes = set(inspect(engine).get_table_names())
    Base.metadata.create_all(engine)
    agregadas = [t.name for t in Base.metadata.sorted_tables if t.name not in antes]
    inspector = inspect(engine)
    with engine.begin() as conexion:
        for columna, sentencia in MIGRACIONES.items():
            tabla, _, nombre = columna.partition(".")
            if nombre not in {c["name"] for c in inspector.get_columns(tabla)}:
                conexion.execute(text(sentencia))
                agregadas.append(columna)
    verificar_esquema(engine)
    return agregadas


def crear_engine(url: str) -> Engine:
    engine = _engine(url)
    Base.metadata.create_all(engine)
    verificar_esquema(engine)
    return engine


def verificar_esquema(engine: Engine) -> None:
    """Falla al arrancar si a la base le faltan columnas o índices únicos del modelo.

    `create_all` crea las tablas que faltan, pero no toca las que ya existen.
    Sin esta verificación, una base vieja no avisa nada al arrancar y el bot
    se cae en el primer insert, en medio de una conversación con alguien.

    Los índices únicos cuentan porque son invariantes (un archivo no se importa
    dos veces, un movimiento no se concilia dos veces): una tabla sin ellos
    aceptaría en silencio lo que el modelo prohíbe.
    """
    inspector = inspect(engine)
    faltantes: list[str] = []
    for tabla in Base.metadata.sorted_tables:
        existentes = {c["name"] for c in inspector.get_columns(tabla.name)}
        faltantes += [f"{tabla.name}.{c.name}" for c in tabla.columns if c.name not in existentes]
        indices = {i["name"] for i in inspector.get_indexes(tabla.name)}
        faltantes += [
            f"{tabla.name}.{i.name}" for i in tabla.indexes if i.unique and i.name not in indices
        ]
    if faltantes:
        raise EsquemaDesactualizado(
            f"La base en {engine.url} tiene un esquema viejo; le faltan: {', '.join(faltantes)}. "
            "Respáldala y corre `family-expense-bot --migrar`; si alguna no tiene migración, "
            "muévela para que se cree de nuevo."
        )


class RepositorioGastos:
    def __init__(self, engine: Engine) -> None:
        self._sesion = sessionmaker(engine, expire_on_commit=False)

    def guardar(
        self,
        gasto: GastoExtraido,
        registrado_por: int,
        corregido_manualmente: bool = False,
        origen: str = "chat",
        subcategoria: str | None = None,
    ) -> GastoRegistrado:
        """Guarda un gasto **ya confirmado**. La confirmación ocurre antes, en el bot.

        La subcategoría va aparte del gasto porque no es parte de la extracción:
        solo la escribe una persona en la Mini App.
        """
        if gasto.monto is None:
            raise GastoSinMonto(f"El gasto de {gasto.texto_original!r} no tiene monto")

        fila = self._fila(gasto, registrado_por, corregido_manualmente, origen, subcategoria)
        with self._sesion.begin() as sesion:
            sesion.add(fila)
        return GastoRegistrado.desde_fila(fila)

    def reemplazar(
        self,
        id_gasto: int,
        gasto: GastoExtraido,
        registrado_por: int,
        origen: str,
        subcategoria: str | None = None,
    ) -> GastoRegistrado | None:
        """Editar sin perder historia: anula el viejo y guarda el nuevo, todo o nada.

        Devuelve None si el viejo no existe, ya estaba anulado o lo registró otra
        persona. El viejo queda con el motivo `editado → #<nuevo>`.
        """
        if gasto.monto is None:
            raise GastoSinMonto(f"El gasto de {gasto.texto_original!r} no tiene monto")
        with self._sesion.begin() as sesion:
            viejo = sesion.get(Gasto, id_gasto)
            if (
                viejo is None
                or viejo.anulado_en is not None
                or viejo.registrado_por != registrado_por
            ):
                return None
            nuevo = self._fila(gasto, registrado_por, False, origen, subcategoria)
            sesion.add(nuevo)
            sesion.flush()  # para conocer el id del nuevo antes de anotarlo en el viejo
            viejo.anulado_en = datetime.now(UTC)
            viejo.motivo_anulacion = f"editado → #{nuevo.id}"
        return GastoRegistrado.desde_fila(nuevo)

    def subcategorias_usadas(self, registrado_por: int) -> dict[str, list[str]]:
        """Categoría → subcategorías que esa persona ya usó, la más reciente primero.

        Sin repetir mayúsculas: "Tipti" y "tipti" son la misma, con la última grafía.
        """
        consulta = (
            select(Gasto.categoria, Gasto.subcategoria)
            .where(Gasto.registrado_por == registrado_por)
            .where(Gasto.subcategoria.is_not(None))
            .where(Gasto.anulado_en.is_(None))
            .order_by(Gasto.creado_en.desc(), Gasto.id.desc())
        )
        usadas: dict[str, list[str]] = {}
        vistas: set[tuple[str, str]] = set()
        with self._sesion() as sesion:
            for categoria, subcategoria in sesion.execute(consulta):
                if subcategoria is None:  # el WHERE ya las saca; esto es para el tipo
                    continue
                clave = (categoria, subcategoria.casefold())
                if clave not in vistas:
                    vistas.add(clave)
                    usadas.setdefault(categoria, []).append(subcategoria)
        return usadas

    @staticmethod
    def _fila(
        gasto: GastoExtraido,
        registrado_por: int,
        corregido_manualmente: bool,
        origen: str,
        subcategoria: str | None,
    ) -> Gasto:
        assert gasto.monto is not None
        return Gasto(
            monto_centavos=a_centavos(gasto.monto),
            categoria=gasto.categoria,
            atribuido_a=gasto.atribuido_a,
            fecha=gasto.fecha or date.today(),
            descripcion=gasto.descripcion,
            subcategoria=subcategoria,
            texto_original=gasto.texto_original,
            registrado_por=registrado_por,
            confianza_monto=gasto.confianza_monto,
            confianza_categoria=gasto.confianza_categoria,
            confianza_fecha=gasto.confianza_fecha,
            confianza_atribuido_a=gasto.confianza_atribuido_a,
            corregido_manualmente=corregido_manualmente,
            origen=origen,
        )

    def obtener(self, id_gasto: int) -> GastoRegistrado | None:
        with self._sesion() as sesion:
            fila = sesion.get(Gasto, id_gasto)
            return GastoRegistrado.desde_fila(fila) if fila else None

    def listar(
        self,
        desde: date | None = None,
        hasta: date | None = None,
        incluir_anulados: bool = False,
    ) -> Sequence[GastoRegistrado]:
        consulta = select(Gasto).order_by(Gasto.fecha.desc(), Gasto.id.desc())
        if desde is not None:
            consulta = consulta.where(Gasto.fecha >= desde)
        if hasta is not None:
            consulta = consulta.where(Gasto.fecha <= hasta)
        if not incluir_anulados:
            consulta = consulta.where(Gasto.anulado_en.is_(None))

        with self._sesion() as sesion:
            return [GastoRegistrado.desde_fila(f) for f in sesion.scalars(consulta)]

    def anular(self, id_gasto: int, motivo: str) -> bool:
        """Borrado lógico. Devuelve False si no existe o ya estaba anulado."""
        if not motivo.strip():
            raise ValueError("Anular un gasto exige un motivo")

        with self._sesion.begin() as sesion:
            fila = sesion.get(Gasto, id_gasto)
            if fila is None or fila.anulado_en is not None:
                return False
            fila.anulado_en = datetime.now(UTC)
            fila.motivo_anulacion = motivo.strip()
            return True


class RepositorioExtracciones:
    """Registro de intentos de extracción. Insumo del reporte de calidad."""

    def __init__(self, engine: Engine) -> None:
        self._sesion = sessionmaker(engine, expire_on_commit=False)

    def registrar(self, gasto: GastoExtraido, confirmada: bool, corregida: bool = False) -> None:
        """Registra las confianzas **originales** del LLM, aunque se haya corregido algo."""
        with self._sesion.begin() as sesion:
            sesion.add(
                Extraccion(
                    confirmada=confirmada,
                    corregida=corregida,
                    tenia_monto=gasto.monto is not None,
                    confianza_monto=gasto.confianza_monto,
                    confianza_categoria=gasto.confianza_categoria,
                    confianza_fecha=gasto.confianza_fecha,
                    confianza_atribuido_a=gasto.confianza_atribuido_a,
                )
            )

    def listar(self, desde: datetime, hasta: datetime) -> Sequence[Extraccion]:
        consulta = (
            select(Extraccion)
            .where(Extraccion.creado_en >= desde, Extraccion.creado_en < hasta)
            .order_by(Extraccion.id)
        )
        with self._sesion() as sesion:
            return list(sesion.scalars(consulta))


class IngresoNuevo(BaseModel):
    """Un ingreso extra ya validado y confirmado, listo para guardar."""

    model_config = ConfigDict(frozen=True)

    monto: Decimal
    categoria: str
    subcategoria: str | None
    persona: str
    fecha: date
    descripcion: str


class IngresoRegistrado(IngresoNuevo):
    id: int
    registrado_por: int
    creado_en: datetime
    anulado: bool

    @classmethod
    def desde_fila(cls, fila: Ingreso) -> "IngresoRegistrado":
        return cls(
            id=fila.id,
            monto=a_decimal(fila.monto_centavos),
            categoria=fila.categoria,
            subcategoria=fila.subcategoria,
            persona=fila.persona,
            fecha=fila.fecha,
            descripcion=fila.descripcion,
            registrado_por=fila.registrado_por,
            creado_en=_como_utc(fila.creado_en),
            anulado=fila.anulado_en is not None,
        )


class RepositorioIngresos:
    """Ingresos extra. Mismas reglas que los gastos: nada se borra, editar es reemplazar."""

    def __init__(self, engine: Engine) -> None:
        self._sesion = sessionmaker(engine, expire_on_commit=False)

    def guardar(self, ingreso: IngresoNuevo, registrado_por: int) -> IngresoRegistrado:
        fila = self._fila(ingreso, registrado_por)
        with self._sesion.begin() as sesion:
            sesion.add(fila)
        return IngresoRegistrado.desde_fila(fila)

    def obtener(self, id_ingreso: int) -> IngresoRegistrado | None:
        with self._sesion() as sesion:
            fila = sesion.get(Ingreso, id_ingreso)
            return IngresoRegistrado.desde_fila(fila) if fila else None

    def listar(
        self, desde: date | None = None, hasta: date | None = None
    ) -> Sequence[IngresoRegistrado]:
        """Los vigentes (no anulados), el más reciente primero."""
        consulta = (
            select(Ingreso)
            .where(Ingreso.anulado_en.is_(None))
            .order_by(Ingreso.fecha.desc(), Ingreso.id.desc())
        )
        if desde is not None:
            consulta = consulta.where(Ingreso.fecha >= desde)
        if hasta is not None:
            consulta = consulta.where(Ingreso.fecha <= hasta)
        with self._sesion() as sesion:
            return [IngresoRegistrado.desde_fila(f) for f in sesion.scalars(consulta)]

    def anular(self, id_ingreso: int, motivo: str) -> bool:
        if not motivo.strip():
            raise ValueError("Anular un ingreso exige un motivo")
        with self._sesion.begin() as sesion:
            fila = sesion.get(Ingreso, id_ingreso)
            if fila is None or fila.anulado_en is not None:
                return False
            fila.anulado_en = datetime.now(UTC)
            fila.motivo_anulacion = motivo.strip()
            return True

    def reemplazar(
        self, id_ingreso: int, ingreso: IngresoNuevo, registrado_por: int
    ) -> IngresoRegistrado | None:
        """Como `RepositorioGastos.reemplazar`: anula el viejo y guarda el nuevo, todo o nada."""
        with self._sesion.begin() as sesion:
            viejo = sesion.get(Ingreso, id_ingreso)
            if viejo is None or viejo.anulado_en is not None:
                return None
            if viejo.registrado_por != registrado_por:
                return None
            nuevo = self._fila(ingreso, registrado_por)
            sesion.add(nuevo)
            sesion.flush()
            viejo.anulado_en = datetime.now(UTC)
            viejo.motivo_anulacion = f"editado → #{nuevo.id}"
        return IngresoRegistrado.desde_fila(nuevo)

    def subcategorias_usadas(self, registrado_por: int) -> dict[str, list[str]]:
        consulta = (
            select(Ingreso.categoria, Ingreso.subcategoria)
            .where(Ingreso.registrado_por == registrado_por)
            .where(Ingreso.subcategoria.is_not(None))
            .where(Ingreso.anulado_en.is_(None))
            .order_by(Ingreso.creado_en.desc(), Ingreso.id.desc())
        )
        usadas: dict[str, list[str]] = {}
        vistas: set[tuple[str, str]] = set()
        with self._sesion() as sesion:
            for categoria, subcategoria in sesion.execute(consulta):
                if subcategoria is None:
                    continue
                clave = (categoria, subcategoria.casefold())
                if clave not in vistas:
                    vistas.add(clave)
                    usadas.setdefault(categoria, []).append(subcategoria)
        return usadas

    @staticmethod
    def _fila(ingreso: IngresoNuevo, registrado_por: int) -> Ingreso:
        return Ingreso(
            monto_centavos=a_centavos(ingreso.monto),
            categoria=ingreso.categoria,
            subcategoria=ingreso.subcategoria,
            persona=ingreso.persona,
            fecha=ingreso.fecha,
            descripcion=ingreso.descripcion,
            registrado_por=registrado_por,
        )
