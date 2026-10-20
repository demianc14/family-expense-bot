"""Persistencia. Sin lógica de negocio: no decide qué es confiable, solo guarda.

Quien llama es responsable de que el gasto ya esté confirmado por una persona.
Lo único que este módulo impone son invariantes de integridad: no se guarda un
gasto sin monto, y nada se borra de verdad.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from family_expense_bot.extraction.schema import GastoExtraido
from family_expense_bot.storage.dinero import a_centavos, a_decimal
from family_expense_bot.storage.models import Base, Extraccion, Gasto


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
            origen=fila.origen,
        )


class GastoSinMonto(ValueError):
    """No se guarda un gasto sin monto: sería una fila inútil y engañosa."""


class EsquemaDesactualizado(RuntimeError):
    """La base existe pero no coincide con el modelo actual."""


MIGRACIONES = {
    "gastos.origen": "ALTER TABLE gastos ADD COLUMN origen VARCHAR(12) NOT NULL DEFAULT 'chat'",
}
"""Columnas agregadas después de que ya había bases con datos reales.

Cada una tiene un default, así que las filas existentes quedan bien sin tocarlas
(todo lo anterior a la Mini App entró por el chat).
"""


def migrar(url: str) -> list[str]:
    """Agrega a una base existente las columnas de `MIGRACIONES` que le falten.

    Idempotente: correrla dos veces no hace nada la segunda. Devuelve lo que agregó.
    """
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    agregadas: list[str] = []
    with engine.begin() as conexion:
        for columna, sentencia in MIGRACIONES.items():
            tabla, _, nombre = columna.partition(".")
            if nombre not in {c["name"] for c in inspector.get_columns(tabla)}:
                conexion.execute(text(sentencia))
                agregadas.append(columna)
    verificar_esquema(engine)
    return agregadas


def crear_engine(url: str) -> Engine:
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    verificar_esquema(engine)
    return engine


def verificar_esquema(engine: Engine) -> None:
    """Falla al arrancar si a la base le faltan columnas del modelo.

    `create_all` crea las tablas que faltan, pero no toca las que ya existen.
    Sin esta verificación, una base vieja no avisa nada al arrancar y el bot
    se cae en el primer insert, en medio de una conversación con alguien.
    """
    inspector = inspect(engine)
    faltantes: list[str] = []
    for tabla in Base.metadata.sorted_tables:
        existentes = {c["name"] for c in inspector.get_columns(tabla.name)}
        faltantes += [f"{tabla.name}.{c.name}" for c in tabla.columns if c.name not in existentes]
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
    ) -> GastoRegistrado:
        """Guarda un gasto **ya confirmado**. La confirmación ocurre antes, en el bot."""
        if gasto.monto is None:
            raise GastoSinMonto(f"El gasto de {gasto.texto_original!r} no tiene monto")

        fila = Gasto(
            monto_centavos=a_centavos(gasto.monto),
            categoria=gasto.categoria,
            atribuido_a=gasto.atribuido_a,
            fecha=gasto.fecha or date.today(),
            descripcion=gasto.descripcion,
            texto_original=gasto.texto_original,
            registrado_por=registrado_por,
            confianza_monto=gasto.confianza_monto,
            confianza_categoria=gasto.confianza_categoria,
            confianza_fecha=gasto.confianza_fecha,
            confianza_atribuido_a=gasto.confianza_atribuido_a,
            corregido_manualmente=corregido_manualmente,
            origen=origen,
        )
        with self._sesion.begin() as sesion:
            sesion.add(fila)
        return GastoRegistrado.desde_fila(fila)

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
