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
    [
        ("", {}),
        ("123:demian", {123: "demian"}),
        ("123:demian,456:mama", {123: "demian", 456: "mama"}),
        (" 123 : Demian , 456:mama ,", {123: "demian", 456: "mama"}),
    ],
)
def test_usuarios(monkeypatch: pytest.MonkeyPatch, crudo: str, esperado: dict[int, str]) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:x")
    monkeypatch.setenv("TELEGRAM_USUARIOS", crudo)
    assert Settings(_env_file=None).usuarios == esperado  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("crudo", "error"),
    [
        ("123", "id:rol"),  # falta el rol
        ("123:", "id:rol"),
        ("demian:mama", "ID numérico"),
        ("123:demian,123:mama", "dos veces"),
    ],
)
def test_usuarios_mal_formados_fallan_con_un_mensaje_claro(
    monkeypatch: pytest.MonkeyPatch, crudo: str, error: str
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:x")
    monkeypatch.setenv("TELEGRAM_USUARIOS", crudo)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match=error):
        _ = settings.usuarios


def test_la_variable_vieja_avisa_como_migrar(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un .env de antes no debe dejar el bot mudo sin explicar por qué."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:x")
    monkeypatch.setenv("TELEGRAM_ALLOWED_IDS", "123")
    monkeypatch.delenv("TELEGRAM_USUARIOS", raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="TELEGRAM_USUARIOS=id:rol"):
        _ = settings.usuarios


def test_la_base_vive_en_data_processed_que_esta_ignorada(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:x")
    assert "data/processed/" in Settings(_env_file=None).database_url  # type: ignore[call-arg]
