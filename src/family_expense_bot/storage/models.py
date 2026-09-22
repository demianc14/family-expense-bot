"""Esquema SQL. Solo gastos ya confirmados por una persona.

Las extracciones sin confirmar no llegan acá: viven en memoria mientras el bot
espera la respuesta, y si se rechazan se descartan.
"""

from datetime import UTC, date, datetime

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _ahora() -> datetime:
    return datetime.now(UTC)


class Gasto(Base):
    __tablename__ = "gastos"
    __table_args__ = (
        CheckConstraint("monto_centavos > 0", name="monto_positivo"),
        CheckConstraint(
            "(anulado_en IS NULL) = (motivo_anulacion IS NULL)", name="anulacion_completa"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    monto_centavos: Mapped[int]
    """En centavos de dólar. Ver `storage.dinero`."""

    categoria: Mapped[str] = mapped_column(String(50), index=True)
    fecha: Mapped[date] = mapped_column(Date, index=True)
    descripcion: Mapped[str] = mapped_column(String(500))
    texto_original: Mapped[str] = mapped_column(Text)

    registrado_por: Mapped[int] = mapped_column(BigInteger, index=True)
    """ID de Telegram de quien lo registró."""

    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora)

    # Cómo se extrajo: insumo del reporte de calidad (Fase 4).
    confianza_monto: Mapped[str] = mapped_column(String(5))
    confianza_categoria: Mapped[str] = mapped_column(String(5))
    confianza_fecha: Mapped[str] = mapped_column(String(5))
    corregido_manualmente: Mapped[bool] = mapped_column(default=False)
    """True si la persona tuvo que arreglar lo que el LLM entendió."""

    # Borrado lógico: nada desaparece, se marca.
    anulado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    motivo_anulacion: Mapped[str | None] = mapped_column(String(200), default=None)
