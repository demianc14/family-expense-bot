"""Punto de entrada: `family-expense-bot` levanta el bot en modo polling; `--app`, la Mini App."""

import argparse
import asyncio
import logging
from pathlib import Path

import uvicorn

from family_expense_bot.app.api import ServiciosApp, crear_app
from family_expense_bot.bot.handlers import Servicios, build_application, obtener_identidad
from family_expense_bot.config import Settings
from family_expense_bot.dashboard.report import exportar_csv
from family_expense_bot.extraction.categorias import cargar_taxonomia
from family_expense_bot.extraction.gemini import GeminiCliente
from family_expense_bot.extraction.parser import extraer
from family_expense_bot.storage.repository import (
    EsquemaDesactualizado,
    RepositorioExtracciones,
    RepositorioGastos,
    crear_engine,
    migrar,
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
        "Prueba manual del parser; no guarda nada. Requiere --como.",
    )
    parser.add_argument(
        "--como",
        metavar="ROL",
        help="Con --extraer: el rol de quien escribe (papa, mama, hermana, demian).",
    )
    parser.add_argument(
        "--app",
        action="store_true",
        help="Levanta la Mini App en 127.0.0.1 (exponla con cloudflared y pon la URL en "
        "APP_URL). Corre aparte del bot; comparten la base.",
    )
    parser.add_argument("--puerto", type=int, default=8080, help="Con --app: puerto local (8080).")
    parser.add_argument(
        "--migrar",
        action="store_true",
        help="Agrega a una base existente las columnas nuevas del esquema. Respáldala antes.",
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

    if args.migrar:
        agregadas = migrar(settings.database_url)
        print(f"Agregadas: {', '.join(agregadas)}" if agregadas else "La base ya estaba al día")
        return

    if args.app:
        _levantar_app(settings, token, args.puerto)
        return

    if args.extraer:
        if not args.como:
            parser.error("--extraer necesita --como ROL: los parentescos dependen de quién escribe")
        _mostrar_extraccion(args.extraer, args.como, settings)
        return

    try:
        servicios = Servicios(
            repositorio=RepositorioGastos(engine := crear_engine(settings.database_url)),
            extracciones=RepositorioExtracciones(engine),
            cliente=_cliente_gemini(settings),
            taxonomia=cargar_taxonomia(),
            usuarios=settings.usuarios,
            ingresos=settings.ingresos,
        )
    except (ValueError, EsquemaDesactualizado) as e:
        # Errores de configuración: se explican en una línea, sin traceback.
        raise SystemExit(f"No arranco: {e}") from e

    if not servicios.usuarios:
        # Sin usuarios el bot no le responde a nadie: es la falla segura para
        # un bot que cualquiera en Telegram puede encontrar.
        logging.getLogger(__name__).warning(
            "TELEGRAM_USUARIOS está vacío: el bot va a ignorar todos los mensajes. "
            "Escríbele a @userinfobot para saber tu ID y agrégalo en .env como id:rol"
        )

    build_application(token, servicios, settings.app_url).run_polling()


def _levantar_app(settings: Settings, token: str, puerto: int) -> None:
    try:
        servicios = ServiciosApp(
            repositorio=RepositorioGastos(crear_engine(settings.database_url)),
            taxonomia=cargar_taxonomia(),
            bot_token=token,
            usuarios=settings.usuarios,
            ingresos=settings.ingresos,
        )
    except (ValueError, EsquemaDesactualizado) as e:
        raise SystemExit(f"No arranco: {e}") from e
    # Solo local: lo que llega de afuera entra por el túnel, que termina el https.
    uvicorn.run(crear_app(servicios), host="127.0.0.1", port=puerto)


def _exportar(ruta: Path, settings: Settings) -> None:
    repositorio = RepositorioGastos(crear_engine(settings.database_url))
    gastos = repositorio.listar()
    exportar_csv(gastos, ruta)
    print(f"{len(gastos)} gasto(s) exportados a {ruta}")


def _cliente_gemini(settings: Settings) -> GeminiCliente:
    if settings.gemini_api_key is None:
        raise SystemExit("Falta GEMINI_API_KEY en .env")
    return GeminiCliente(settings.gemini_api_key.get_secret_value(), modelo=settings.gemini_modelo)


def _mostrar_extraccion(mensaje: str, remitente: str, settings: Settings) -> None:
    cliente = _cliente_gemini(settings)
    gasto = extraer(mensaje, cliente, cargar_taxonomia(), remitente.strip().lower())

    print(f"  monto:       {gasto.monto} ({gasto.confianza_monto})")
    print(f"  categoría:   {gasto.categoria} ({gasto.confianza_categoria})")
    print(f"  fecha:       {gasto.fecha} ({gasto.confianza_fecha})")
    print(f"  de:          {gasto.atribuido_a} ({gasto.confianza_atribuido_a})")
    print(f"  descripción: {gasto.descripcion}")
    for nota in gasto.notas:
        print(f"  ⚠ [{nota.campo}] {nota.texto}")
    print(f"\n  ¿Sin dudas? {'sí' if gasto.listo_para_guardar else 'no'}")


if __name__ == "__main__":
    main()
