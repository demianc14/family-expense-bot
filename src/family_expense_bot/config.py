"""Configuración de runtime leída del entorno / .env. Falla rápido si falta algo."""

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from family_expense_bot.extraction.gemini import MODELO_POR_DEFECTO


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: SecretStr
    gemini_api_key: SecretStr | None = None
    gemini_modelo: str = MODELO_POR_DEFECTO
    database_url: str = "sqlite:///data/processed/gastos.db"

    telegram_allowed_ids: str = ""
    """IDs de Telegram autorizados, separados por coma.

    Va en `.env` y no en `config/`, a propósito: este repo es público y los IDs
    de la familia son datos personales.
    """

    @field_validator("telegram_bot_token")
    @classmethod
    def _no_vacio(cls, v: SecretStr) -> SecretStr:
        if not v.get_secret_value().strip():
            raise ValueError("TELEGRAM_BOT_TOKEN está vacío; configúralo en .env")
        return v

    @property
    def ids_autorizados(self) -> frozenset[int]:
        crudos = (parte.strip() for parte in self.telegram_allowed_ids.split(","))
        try:
            return frozenset(int(c) for c in crudos if c)
        except ValueError as e:
            raise ValueError(
                f"TELEGRAM_ALLOWED_IDS debe ser una lista de números separados por coma, "
                f"llegó {self.telegram_allowed_ids!r}"
            ) from e
