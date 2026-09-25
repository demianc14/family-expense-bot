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

    telegram_usuarios: str = ""
    """Quién puede usar el bot y qué rol tiene: `id:rol,id:rol`.

    Las claves son la allowlist, así que no existe "autorizado pero sin rol".
    Va en `.env` y no en `config/`, a propósito: este repo es público y los IDs
    de la familia son datos personales.
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
