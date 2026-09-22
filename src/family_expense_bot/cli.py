"""Punto de entrada: `family-expense-bot` levanta el bot en modo polling."""

import argparse
import asyncio
import logging
from pathlib import Path

from family_expense_bot.bot.handlers import Servicios, build_application, obtener_identidad
from family_expense_bot.config import Settings
from family_expense_bot.dashboard.report import exportar_csv
from family_expense_bot.extraction.categorias import cargar_taxonomia
from family_expense_bot.extraction.gemini import GeminiCliente
from family_expense_bot.extraction.parser import extraer
from family_expense_bot.storage.repository import (
    RepositorioExtracciones,
    RepositorioGastos,
    crear_engine,
)


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
        "--exportar",
        metavar="RUTA",
        nargs="?",
        const="data/processed/gastos.csv",
        help="Exporta los gastos a CSV para Power BI. El archivo lleva montos reales "
        "y vive en data/processed/, que está fuera de git.",
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

    if args.exportar:
        _exportar(Path(args.exportar), settings)
        return

    if args.extraer:
        _mostrar_extraccion(args.extraer, settings)
        return

    engine = crear_engine(settings.database_url)
    servicios = Servicios(
        repositorio=RepositorioGastos(engine),
        extracciones=RepositorioExtracciones(engine),
        cliente=_cliente_gemini(settings),
        taxonomia=cargar_taxonomia(),
        ids_autorizados=settings.ids_autorizados,
    )
    if not servicios.ids_autorizados:
        # Sin allowlist el bot no le responde a nadie: es la falla segura para
        # un bot que cualquiera en Telegram puede encontrar.
        logging.getLogger(__name__).warning(
            "TELEGRAM_ALLOWED_IDS está vacío: el bot va a ignorar todos los mensajes. "
            "Escríbele a @userinfobot para saber tu ID y agrégalo en .env"
        )

    build_application(token, servicios).run_polling()


def _exportar(ruta: Path, settings: Settings) -> None:
    repositorio = RepositorioGastos(crear_engine(settings.database_url))
    gastos = repositorio.listar()
    exportar_csv(gastos, ruta)
    print(f"{len(gastos)} gasto(s) exportados a {ruta}")


def _cliente_gemini(settings: Settings) -> GeminiCliente:
    if settings.gemini_api_key is None:
        raise SystemExit("Falta GEMINI_API_KEY en .env")
    return GeminiCliente(settings.gemini_api_key.get_secret_value(), modelo=settings.gemini_modelo)


def _mostrar_extraccion(mensaje: str, settings: Settings) -> None:
    cliente = _cliente_gemini(settings)
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
