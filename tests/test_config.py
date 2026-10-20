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


@pytest.mark.parametrize(
    ("crudo", "esperado"),
    [
        ("", {}),
        ("demian:1200", {"demian": "1200"}),
        (" Demian : 1200.50 , mama:800,", {"demian": "1200.50", "mama": "800"}),
    ],
)
def test_ingresos(monkeypatch: pytest.MonkeyPatch, crudo: str, esperado: dict[str, str]) -> None:
    from decimal import Decimal

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:x")
    monkeypatch.setenv("INGRESOS_MENSUALES", crudo)
    ingresos = Settings(_env_file=None).ingresos  # type: ignore[call-arg]
    assert ingresos == {rol: Decimal(m) for rol, m in esperado.items()}


@pytest.mark.parametrize(
    "crudo",
    [
        "demian",
        "demian:",
        "demian:mil",
        "demian:-5",
        "demian:0",
        "demian:1.005",
        "demian:NaN",
        "demian:1,demian:2",
        ":100",
        "demian:1e3",
        "demian:1_200",
        "demian:+5",
    ],
)
def test_ingresos_mal_escritos_fallan_rapido(monkeypatch: pytest.MonkeyPatch, crudo: str) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:x")
    monkeypatch.setenv("INGRESOS_MENSUALES", crudo)
    with pytest.raises(ValueError, match="INGRESOS_MENSUALES"):
        _ = Settings(_env_file=None).ingresos  # type: ignore[call-arg]


def test_app_url_tiene_que_ser_https(monkeypatch: pytest.MonkeyPatch) -> None:
    """Telegram solo abre Mini Apps por https: mejor fallar al arrancar que en el celular."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:secreto")
    monkeypatch.setenv("APP_URL", "http://localhost:8080")
    with pytest.raises(ValidationError, match="https"):
        Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("crudo", "esperado"), [("", ""), (" https://x.example ", "https://x.example")]
)
def test_app_url_vacia_o_https(monkeypatch: pytest.MonkeyPatch, crudo: str, esperado: str) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:secreto")
    monkeypatch.setenv("APP_URL", crudo)
    assert Settings(_env_file=None).app_url == esperado  # type: ignore[call-arg]
