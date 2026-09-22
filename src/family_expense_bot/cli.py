"""Punto de entrada: `family-expense-bot` levanta el bot en modo polling."""

import argparse
import asyncio
import logging

from family_expense_bot.bot.handlers import build_application, obtener_identidad
from family_expense_bot.config import Settings
from family_expense_bot.extraction.categorias import cargar_taxonomia
from family_expense_bot.extraction.gemini import GeminiCliente
from family_expense_bot.extraction.parser import extraer


def _configurar_logging() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
    )
    # httpx loguea la URL completa de cada request, que incluye el token del bot.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    parser = argparse.ArgumentParser(prog="family-expense-bot")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verifica el token contra la API de Telegram y sale. No levanta el bot.",
    )
    parser.add_argument(
        "--extraer",
        metavar="MENSAJE",
        help="Extrae un gasto de un mensaje usando el LLM real y muestra el resultado. "
        "Prueba manual del parser; no guarda nada.",
    )
    args = parser.parse_args()

    _configurar_logging()
    settings = Settings()  # valores vienen del entorno
    token = settings.telegram_bot_token.get_secret_value()

    if args.check:
        username = asyncio.run(obtener_identidad(token))
        print(f"Token válido. Conectado como @{username}")
        return

    if args.extraer:
        _mostrar_extraccion(args.extraer, settings)
        return

    build_application(token).run_polling()


def _mostrar_extraccion(mensaje: str, settings: Settings) -> None:
    if settings.gemini_api_key is None:
        raise SystemExit("Falta GEMINI_API_KEY en .env")

    cliente = GeminiCliente(settings.gemini_api_key.get_secret_value())
    gasto = extraer(mensaje, cliente, cargar_taxonomia())

    print(f"  monto:       {gasto.monto} ({gasto.confianza_monto})")
    print(f"  categoría:   {gasto.categoria} ({gasto.confianza_categoria})")
    print(f"  fecha:       {gasto.fecha} ({gasto.confianza_fecha})")
    print(f"  descripción: {gasto.descripcion}")
    for nota in gasto.notas:
        print(f"  ⚠ {nota}")
    print(f"\n  ¿Sin dudas? {'sí' if gasto.listo_para_guardar else 'no'}")


if __name__ == "__main__":
    main()
