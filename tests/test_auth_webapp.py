"""Firma del initData de la Mini App. Sin red: el initData se firma acá, como lo haría Telegram."""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest

from family_expense_bot.app.auth import InitDataInvalido, validar_init_data

TOKEN = "123456:token-de-prueba"
AHORA = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def firmar(campos: dict[str, str], token: str = TOKEN) -> str:
    """Lo que Telegram le entrega a la Mini App: los campos más su hash."""
    data_check_string = "\n".join(f"{k}={campos[k]}" for k in sorted(campos))
    clave = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    firma = hmac.new(clave, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**campos, "hash": firma})


def init_data(id_telegram: object = 42, firmado_en: datetime = AHORA, token: str = TOKEN) -> str:
    return firmar(
        {
            "auth_date": str(int(firmado_en.timestamp())),
            "query_id": "AAH",
            "user": json.dumps({"id": id_telegram, "first_name": "Prueba"}),
        },
        token,
    )


def test_initdata_valido_devuelve_el_id() -> None:
    assert validar_init_data(init_data(), TOKEN, AHORA) == 42


def test_firmado_con_otro_token_se_rechaza() -> None:
    with pytest.raises(InitDataInvalido, match="firma"):
        validar_init_data(init_data(token="999:otro-bot"), TOKEN, AHORA)


def test_cambiar_el_user_rompe_la_firma() -> None:
    original = init_data(id_telegram=42)
    alterado = original.replace("%22id%22%3A+42", "%22id%22%3A+7")
    assert alterado != original
    with pytest.raises(InitDataInvalido, match="firma"):
        validar_init_data(alterado, TOKEN, AHORA)


def test_initdata_vencido_se_rechaza() -> None:
    viejo = init_data(firmado_en=AHORA - timedelta(hours=25))
    with pytest.raises(InitDataInvalido, match="vencido"):
        validar_init_data(viejo, TOKEN, AHORA)


def test_dentro_del_plazo_sirve() -> None:
    assert validar_init_data(init_data(firmado_en=AHORA - timedelta(hours=23)), TOKEN, AHORA) == 42


@pytest.mark.parametrize("crudo", ["", "auth_date=1&user=%7B%7D", "basura sin forma"])
def test_sin_hash_o_mal_formado_se_rechaza(crudo: str) -> None:
    with pytest.raises(InitDataInvalido):
        validar_init_data(crudo, TOKEN, AHORA)


def test_campos_repetidos_se_rechazan() -> None:
    with pytest.raises(InitDataInvalido, match="repetidos"):
        validar_init_data(init_data() + "&auth_date=1", TOKEN, AHORA)


def test_firmado_pero_sin_user_se_rechaza() -> None:
    sin_user = firmar({"auth_date": str(int(AHORA.timestamp()))})
    with pytest.raises(InitDataInvalido, match="sin user"):
        validar_init_data(sin_user, TOKEN, AHORA)


@pytest.mark.parametrize("id_raro", ["42", True, None])
def test_user_id_no_numerico_se_rechaza(id_raro: object) -> None:
    with pytest.raises(InitDataInvalido):
        validar_init_data(init_data(id_telegram=id_raro), TOKEN, AHORA)
