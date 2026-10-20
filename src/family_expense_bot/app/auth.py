"""Quién abre la Mini App, según Telegram.

Telegram le entrega a la Mini App un `initData` firmado con el token del bot.
Validar esa firma es lo que reemplaza a un login: si cuadra, el `user.id` lo
puso Telegram y no quien hizo el request. Después, el ID pasa por la misma
allowlist que los mensajes del bot.

Algoritmo oficial (https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app):
clave = HMAC-SHA256("WebAppData", token); hash = HMAC-SHA256(clave, data_check_string),
donde data_check_string son todos los campos menos `hash`, ordenados y unidos por "\\n".
"""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl

EDAD_MAXIMA = timedelta(hours=24)
"""El initData no cambia mientras la Mini App siga abierta. Un día alcanza para una
sesión larga y deja inservible uno que se haya filtrado en un log."""


class InitDataInvalido(ValueError):
    """La firma no cuadra, está vencida o le falta algo. Nunca se confía en su contenido."""


def validar_init_data(
    init_data: str, bot_token: str, ahora: datetime, edad_maxima: timedelta = EDAD_MAXIMA
) -> int:
    """Devuelve el ID de Telegram de quien abrió la Mini App, o lanza InitDataInvalido."""
    try:
        pares = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError as e:
        raise InitDataInvalido("initData mal formado") from e
    campos = dict(pares)
    if len(campos) != len(pares):
        raise InitDataInvalido("initData con campos repetidos")

    recibido = campos.pop("hash", None)
    if not recibido:
        raise InitDataInvalido("initData sin hash")
    data_check_string = "\n".join(f"{k}={campos[k]}" for k in sorted(campos))
    clave = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    esperado = hmac.new(clave, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(esperado, recibido):
        raise InitDataInvalido("La firma del initData no corresponde a este bot")

    # Recién acá se lee el contenido: antes de la firma, cualquiera pudo escribirlo.
    try:
        firmado_en = datetime.fromtimestamp(int(campos["auth_date"]), UTC)
        id_telegram = json.loads(campos["user"])["id"]
    except (KeyError, ValueError, TypeError) as e:
        raise InitDataInvalido("initData sin auth_date o sin user") from e
    if ahora - firmado_en > edad_maxima:
        raise InitDataInvalido("initData vencido: vuelve a abrir la app desde el bot")
    if not isinstance(id_telegram, int) or isinstance(id_telegram, bool):
        raise InitDataInvalido("initData con un user.id que no es numérico")
    return id_telegram
