"""Configuración de runtime leída del entorno / .env. Falla rápido si falta algo."""

from decimal import Decimal, InvalidOperation

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from family_expense_bot.extraction.gemini import MODELO_POR_DEFECTO


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: SecretStr
    gemini_api_key: SecretStr | None = None
    gemini_modelo: str = MODELO_POR_DEFECTO
    database_url: str = "sqlite:///data/processed/gastos.db"

    telegram_usuarios: str = ""
    """Quién puede usar el bot y qué rol tiene: `id:rol,id:rol`.

    Las claves son la allowlist, así que no existe "autorizado pero sin rol".
    Va en `.env` y no en `config/`, a propósito: este repo es público y los IDs
    de la familia son datos personales.
    """

    ingresos_mensuales: str = ""
    """Ingreso mensual de cada persona para el presupuesto: `rol:monto,rol:monto`.

    Va en `.env` por la misma razón que los IDs: es un dato personal y el repo
    es público. Vacío = nadie tiene presupuesto.
    """

    telegram_allowed_ids: str = ""
    """Obsoleta: reemplazada por TELEGRAM_USUARIOS. Solo se lee para avisar."""

    @field_validator("telegram_bot_token")
    @classmethod
    def _no_vacio(cls, v: SecretStr) -> SecretStr:
        if not v.get_secret_value().strip():
            raise ValueError("TELEGRAM_BOT_TOKEN está vacío; configúralo en .env")
        return v

    @property
    def usuarios(self) -> dict[int, str]:
        """ID de Telegram → rol. Los roles se validan contra la taxonomía al arrancar."""
        if self.telegram_allowed_ids.strip() and not self.telegram_usuarios.strip():
            raise ValueError(
                "TELEGRAM_ALLOWED_IDS ya no se usa: cada ID necesita un rol. "
                "Cámbialo en .env por TELEGRAM_USUARIOS=id:rol (ej. 123456:demian)"
            )

        usuarios: dict[int, str] = {}
        for parte in self.telegram_usuarios.split(","):
            if not parte.strip():
                continue
            id_crudo, sep, rol = parte.partition(":")
            if not sep or not rol.strip():
                raise ValueError(f"TELEGRAM_USUARIOS: {parte.strip()!r} no tiene la forma id:rol")
            try:
                id_telegram = int(id_crudo.strip())
            except ValueError as e:
                raise ValueError(
                    f"TELEGRAM_USUARIOS: {id_crudo.strip()!r} no es un ID numérico"
                ) from e
            if id_telegram in usuarios:
                raise ValueError(f"TELEGRAM_USUARIOS: el ID {id_telegram} aparece dos veces")
            usuarios[id_telegram] = rol.strip().lower()
        return usuarios

    @property
    def ingresos(self) -> dict[str, Decimal]:
        """Rol → ingreso mensual. Los roles se validan contra la taxonomía al arrancar."""
        ingresos: dict[str, Decimal] = {}
        for parte in self.ingresos_mensuales.split(","):
            if not parte.strip():
                continue
            rol, sep, crudo = parte.partition(":")
            rol = rol.strip().lower()
            if not sep or not rol:
                raise ValueError(
                    f"INGRESOS_MENSUALES: {parte.strip()!r} no tiene la forma rol:monto"
                )
            try:
                monto = Decimal(crudo.strip())
            except InvalidOperation as e:
                raise ValueError(f"INGRESOS_MENSUALES: {crudo.strip()!r} no es un monto") from e
            if not monto.is_finite() or monto <= 0 or monto != monto.quantize(Decimal("0.01")):
                raise ValueError(
                    f"INGRESOS_MENSUALES: el ingreso de {rol} debe ser positivo y con "
                    "hasta dos decimales"
                )
            if rol in ingresos:
                raise ValueError(f"INGRESOS_MENSUALES: {rol} aparece dos veces")
            ingresos[rol] = monto
        return ingresos
