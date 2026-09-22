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


@pytest.mark.parametrize(
    ("crudo", "esperado"),
    [("", set()), ("123", {123}), ("123,456", {123, 456}), (" 123 , 456 ,", {123, 456})],
)
def test_ids_autorizados(monkeypatch: pytest.MonkeyPatch, crudo: str, esperado: set[int]) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:x")
    monkeypatch.setenv("TELEGRAM_ALLOWED_IDS", crudo)
    assert Settings(_env_file=None).ids_autorizados == esperado  # type: ignore[call-arg]


def test_ids_autorizados_falla_si_no_son_numeros(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:x")
    monkeypatch.setenv("TELEGRAM_ALLOWED_IDS", "demian,456")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="TELEGRAM_ALLOWED_IDS"):
        _ = settings.ids_autorizados


def test_la_base_vive_en_data_processed_que_esta_ignorada(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:x")
    assert "data/processed/" in Settings(_env_file=None).database_url  # type: ignore[call-arg]
