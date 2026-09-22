"""Configuración de runtime leída del entorno / .env. Falla rápido si falta algo."""

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: SecretStr

    @field_validator("telegram_bot_token")
    @classmethod
    def _no_vacio(cls, v: SecretStr) -> SecretStr:
        if not v.get_secret_value().strip():
            raise ValueError("TELEGRAM_BOT_TOKEN está vacío; configúralo en .env")
        return v
