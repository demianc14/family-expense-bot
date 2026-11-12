"""Esquema SQL. Solo gastos e ingresos ya confirmados por una persona.

Las extracciones sin confirmar no llegan acá: viven en memoria mientras el bot
espera la respuesta, y si se rechazan se descartan.

Las tablas de la conciliación (`estados_cuenta`, `movimientos`,
`conciliaciones`) guardan lo que dice el banco, no gastos: un movimiento solo
se vuelve gasto por el flujo de confirmación de siempre.
"""

from datetime import UTC, date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
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
    atribuido_a: Mapped[str] = mapped_column(String(20), index=True)
    """Quién asume el costo. Independiente de `registrado_por`: tu mamá puede
    registrar un gasto de tu hermana."""

    fecha: Mapped[date] = mapped_column(Date, index=True)
    descripcion: Mapped[str] = mapped_column(String(500))
    subcategoria: Mapped[str | None] = mapped_column(String(40), default=None)
    """Etiqueta libre de la Mini App ("Tipti" dentro de alimentacion). No decide el bolsillo."""
    texto_original: Mapped[str] = mapped_column(Text)

    registrado_por: Mapped[int] = mapped_column(BigInteger, index=True)
    """ID de Telegram de quien lo registró."""

    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora)

    # Cómo se extrajo: insumo del reporte de calidad (Fase 4).
    confianza_monto: Mapped[str] = mapped_column(String(5))
    confianza_categoria: Mapped[str] = mapped_column(String(5))
    confianza_fecha: Mapped[str] = mapped_column(String(5))
    confianza_atribuido_a: Mapped[str] = mapped_column(String(5))
    corregido_manualmente: Mapped[bool] = mapped_column(default=False)
    """True si la persona tuvo que arreglar lo que el LLM entendió."""

    origen: Mapped[str] = mapped_column(String(12), default="chat", server_default="chat")
    """Por dónde entró: `chat` (texto libre leído por el LLM) o `formulario` (la
    Mini App, campo por campo). Los del formulario no cuentan para el reporte de
    calidad, porque ahí nadie leyó nada."""

    # Borrado lógico: nada desaparece, se marca.
    anulado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    motivo_anulacion: Mapped[str | None] = mapped_column(String(200), default=None)


class Ingreso(Base):
    """Un ingreso extra confirmado en la Mini App. Se suma al fijo de .env en su mes."""

    __tablename__ = "ingresos"
    __table_args__ = (
        CheckConstraint("monto_centavos > 0", name="ingreso_positivo"),
        CheckConstraint(
            "(anulado_en IS NULL) = (motivo_anulacion IS NULL)", name="ingreso_anulacion_completa"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    monto_centavos: Mapped[int]
    categoria: Mapped[str] = mapped_column(String(50), index=True)
    subcategoria: Mapped[str | None] = mapped_column(String(40), default=None)
    persona: Mapped[str] = mapped_column(String(20), index=True)
    """De quién es el ingreso: el rol de quien lo registró en su app."""
    fecha: Mapped[date] = mapped_column(Date, index=True)
    descripcion: Mapped[str] = mapped_column(String(500))
    registrado_por: Mapped[int] = mapped_column(BigInteger, index=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora)
    anulado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    motivo_anulacion: Mapped[str | None] = mapped_column(String(200), default=None)


class Extraccion(Base):
    """Registro de cada extracción, se haya guardado o no.

    Sin esta tabla el reporte de calidad mentiría: la tabla `gastos` solo
    contiene los aciertos, así que medir sobre ella daría 100% siempre.

    A propósito no guarda montos ni el texto del mensaje: son gastos que nadie
    confirmó, y para medir la calidad basta el veredicto y las confianzas.
    """

    __tablename__ = "extracciones"

    id: Mapped[int] = mapped_column(primary_key=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora, index=True)

    confirmada: Mapped[bool]
    """False si la persona dijo que no, o si no se pudo ni ofrecer guardar."""

    corregida: Mapped[bool] = mapped_column(default=False)
    """True si la persona cambió algo con los botones antes de confirmar."""

    tenia_monto: Mapped[bool]
    confianza_monto: Mapped[str] = mapped_column(String(5))
    confianza_categoria: Mapped[str] = mapped_column(String(5))
    confianza_fecha: Mapped[str] = mapped_column(String(5))
    confianza_atribuido_a: Mapped[str] = mapped_column(String(5))


# --- conciliación de estados de cuenta (Fase G) ---------------------------

TIPOS_MOVIMIENTO = ("debito", "credito", "comision", "interes", "pago")
"""Qué clase de movimiento es. Lo asigna la ingesta con reglas por banco
(`config/bancos.yaml`), nunca el LLM."""

_VIGENTE = text("anulado_en IS NULL")


def _entero(columna: str) -> str:
    """SQLite guarda 12.5 en una columna INTEGER sin quejarse. Los centavos son enteros."""
    return f"typeof({columna}) = 'integer'"


class EstadoCuenta(Base):
    """Un estado de cuenta importado. Solo entra si cuadra (lo verifica la ingesta)."""

    __tablename__ = "estados_cuenta"
    __table_args__ = (
        CheckConstraint("periodo_desde <= periodo_hasta", name="estado_periodo_ordenado"),
        CheckConstraint(
            f"{_entero('saldo_anterior_centavos')} AND {_entero('saldo_final_centavos')}",
            name="estado_saldos_enteros",
        ),
        CheckConstraint("length(hash_archivo) = 64", name="estado_hash_sha256"),
        CheckConstraint(
            "(anulado_en IS NULL) = (motivo_anulacion IS NULL)", name="estado_anulacion_completa"
        ),
        # El mismo archivo no se importa dos veces, pero uno anulado sí se puede reimportar.
        Index("uq_estados_hash_vigente", "hash_archivo", unique=True, sqlite_where=_VIGENTE),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    banco: Mapped[str] = mapped_column(String(40), index=True)
    """Clave del banco en `config/bancos.yaml`."""
    cuenta: Mapped[str] = mapped_column(String(40))
    """Etiqueta de la cuenta ("ahorros", "visa"). Nunca el número de cuenta."""
    periodo_desde: Mapped[date] = mapped_column(Date)
    periodo_hasta: Mapped[date] = mapped_column(Date)
    saldo_anterior_centavos: Mapped[int]
    saldo_final_centavos: Mapped[int]
    """Con signo: una tarjeta o un sobregiro dejan saldo negativo."""
    hash_archivo: Mapped[str] = mapped_column(String(64))
    """SHA-256 del archivo, en hexadecimal."""
    importado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora)
    anulado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    motivo_anulacion: Mapped[str | None] = mapped_column(String(200), default=None)


class Movimiento(Base):
    """Una línea de un estado de cuenta."""

    __tablename__ = "movimientos"
    __table_args__ = (
        UniqueConstraint("estado_id", "linea", name="uq_movimientos_estado_linea"),
        CheckConstraint("linea >= 1", name="movimiento_linea_positiva"),
        CheckConstraint(
            f"{_entero('monto_centavos')} AND monto_centavos <> 0", name="movimiento_monto_entero"
        ),
        CheckConstraint(
            "tipo IN ({})".format(", ".join(f"'{t}'" for t in TIPOS_MOVIMIENTO)),
            name="movimiento_tipo_valido",
        ),
        CheckConstraint(
            "(anulado_en IS NULL) = (motivo_anulacion IS NULL)",
            name="movimiento_anulacion_completa",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    estado_id: Mapped[int] = mapped_column(ForeignKey("estados_cuenta.id"), index=True)
    linea: Mapped[int]
    """Posición dentro del archivo, desde 1: dos cargos idénticos el mismo día
    son dos movimientos, no un duplicado."""
    fecha: Mapped[date] = mapped_column(Date, index=True)
    descripcion: Mapped[str] = mapped_column(String(500))
    monto_centavos: Mapped[int]
    """Con signo: negativo si sale plata de la cuenta, positivo si entra."""
    tipo: Mapped[str] = mapped_column(String(10), index=True)
    anulado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    motivo_anulacion: Mapped[str | None] = mapped_column(String(200), default=None)


class Conciliacion(Base):
    """Un movimiento del banco unido a un gasto registrado, y qué regla los unió.

    Uno a uno: mientras el par esté vigente, ni el movimiento ni el gasto pueden
    estar en otro. Un par anulado libera a los dos.
    """

    __tablename__ = "conciliaciones"
    __table_args__ = (
        CheckConstraint("regla <> ''", name="conciliacion_con_regla"),
        CheckConstraint(
            "(anulado_en IS NULL) = (motivo_anulacion IS NULL)",
            name="conciliacion_anulacion_completa",
        ),
        Index(
            "uq_conciliaciones_movimiento_vigente",
            "movimiento_id",
            unique=True,
            sqlite_where=_VIGENTE,
        ),
        Index("uq_conciliaciones_gasto_vigente", "gasto_id", unique=True, sqlite_where=_VIGENTE),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    movimiento_id: Mapped[int] = mapped_column(ForeignKey("movimientos.id"))
    gasto_id: Mapped[int] = mapped_column(ForeignKey("gastos.id"))
    regla: Mapped[str] = mapped_column(String(40), index=True)
    """Nombre de la regla determinista que unió el par."""
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora)
    anulado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    motivo_anulacion: Mapped[str | None] = mapped_column(String(200), default=None)
