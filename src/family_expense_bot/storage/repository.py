"""Persistencia. Sin lógica de negocio: no decide qué es confiable, solo guarda.

Quien llama es responsable de que el gasto ya esté confirmado por una persona.
Lo único que este módulo impone son invariantes de integridad: no se guarda un
gasto sin monto, y nada se borra de verdad.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import sessionmaker

from family_expense_bot.extraction.schema import GastoExtraido
from family_expense_bot.storage.dinero import a_centavos, a_decimal
from family_expense_bot.storage.models import Base, Gasto


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
    fecha: date
    descripcion: str
    registrado_por: int
    creado_en: datetime
    corregido_manualmente: bool
    anulado: bool

    @classmethod
    def desde_fila(cls, fila: Gasto) -> "GastoRegistrado":
        return cls(
            id=fila.id,
            monto=a_decimal(fila.monto_centavos),
            categoria=fila.categoria,
            fecha=fila.fecha,
            descripcion=fila.descripcion,
            registrado_por=fila.registrado_por,
            creado_en=_como_utc(fila.creado_en),
            corregido_manualmente=fila.corregido_manualmente,
            anulado=fila.anulado_en is not None,
        )


class GastoSinMonto(ValueError):
    """No se guarda un gasto sin monto: sería una fila inútil y engañosa."""


def crear_engine(url: str) -> Engine:
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    return engine


class RepositorioGastos:
    def __init__(self, engine: Engine) -> None:
        self._sesion = sessionmaker(engine, expire_on_commit=False)

    def guardar(
        self, gasto: GastoExtraido, registrado_por: int, corregido_manualmente: bool = False
    ) -> GastoRegistrado:
        """Guarda un gasto **ya confirmado**. La confirmación ocurre antes, en el bot."""
        if gasto.monto is None:
            raise GastoSinMonto(f"El gasto de {gasto.texto_original!r} no tiene monto")

        fila = Gasto(
            monto_centavos=a_centavos(gasto.monto),
            categoria=gasto.categoria,
            fecha=gasto.fecha or date.today(),
            descripcion=gasto.descripcion,
            texto_original=gasto.texto_original,
            registrado_por=registrado_por,
            confianza_monto=gasto.confianza_monto,
            confianza_categoria=gasto.confianza_categoria,
            confianza_fecha=gasto.confianza_fecha,
            corregido_manualmente=corregido_manualmente,
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
