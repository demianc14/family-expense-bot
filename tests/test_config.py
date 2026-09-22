import pytest
from pydantic import ValidationError

from family_expense_bot.config import Settings


def test_falla_si_falta_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_falla_si_token_vacio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "   ")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_token_no_se_filtra_en_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:secreto")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert "secreto" not in repr(settings)
    assert settings.telegram_bot_token.get_secret_value() == "123:secreto"
