"""Genera las capturas de la Mini App del README, con datos sintéticos.

Levanta la app real (`crear_app`) sobre una base temporal con gastos inventados
y la abre en Chrome headless con un `Telegram.WebApp` falso, que firma su
initData con un token de prueba. Ningún dato sale de `.env` ni de la base real.

    pip install pillow
    python scripts/capturas.py            # escribe docs/img/mini-app.png y .gif
    python scripts/capturas.py --oscuro   # el tema oscuro: docs/img/mini-app-oscuro.png

Requiere Google Chrome instalado (la ruta de macOS es el default; `CHROME=...` la cambia).
"""

import hashlib
import hmac
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode

import uvicorn
from fastapi.responses import HTMLResponse
from PIL import Image

from family_expense_bot.app.api import ServiciosApp, crear_app
from family_expense_bot.extraction.categorias import cargar_taxonomia
from family_expense_bot.extraction.schema import Confianza, GastoExtraido
from family_expense_bot.storage.repository import RepositorioGastos, crear_engine

RAIZ = Path(__file__).resolve().parents[1]
SALIDA = RAIZ / "docs" / "img"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
TOKEN = "123456:token-de-prueba"
ID_DEMO = 42
HOY = date(2026, 10, 20)
ESQUEMA = "dark" if "--oscuro" in sys.argv else "light"
ANCHO, ALTO = 390, 844

GASTOS_SINTETICOS = [
    ("85.40", "alimentacion", "supermercado", 3),
    ("22.00", "transporte", "gasolina", 6),
    ("45.00", "deudas_cuotas", "cuota de la tarjeta", 10),
    ("18.50", "comida_fuera", "almuerzo", 8),
    ("35.00", "entretenimiento", "cine", 12),
    ("110.00", "ropa", "zapatos", 15),
    ("40.00", "personal", "curso de inglés", 2),
]

# Escenas: qué hace la página antes de la captura. Corre cuando la app ya cargó.
ESCENAS = {
    "bolsillos": "",
    "puedo": """
        tab('puedo'); $('p-monto').value = '45'; $('p-categoria').value = 'entretenimiento';
        $('form-puedo').requestSubmit();""",
    "registrar": """
        tab('registrar'); $('g-monto').value = '12.50'; $('g-descripcion').value = 'almuerzo';
        $('g-categoria').value = 'comida_fuera'; elegir('Demian');""",
    "confirmar": """
        tab('registrar'); $('g-monto').value = '12.50'; $('g-descripcion').value = 'almuerzo';
        $('g-categoria').value = 'comida_fuera'; elegir('Demian');
        $('form-gasto').requestSubmit();""",
}


def _init_data() -> str:
    """Firmado como lo firmaría Telegram, con el token de prueba."""
    campos = {
        "auth_date": str(int(datetime.now(UTC).timestamp())),
        "user": json.dumps({"id": ID_DEMO, "first_name": "Demo"}),
    }
    data_check_string = "\n".join(f"{k}={campos[k]}" for k in sorted(campos))
    clave = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    firma = hmac.new(clave, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**campos, "hash": firma})


def _stub(escena: str) -> str:
    # Sin animaciones: Chrome headless podría sacar la foto a mitad de una.
    return f"""<style>*, *::before, *::after {{ animation: none !important; }}</style>
<script>
window.Telegram = {{ WebApp: {{
  initData: {json.dumps(_init_data())}, colorScheme: {json.dumps(ESQUEMA)},
  ready() {{}}, expand() {{}}, onEvent() {{}},
  MainButton: {{ setText() {{ return this; }}, show() {{ return this; }}, hide() {{ return this; }},
                 showProgress() {{}}, hideProgress() {{}}, onClick() {{}} }},
  HapticFeedback: {{ notificationOccurred() {{}} }},
}} }};
window.addEventListener("load", () => {{
  const tab = (n) => document.querySelector(`nav [data-tab=${{n}}]`).click();
  const $ = (id) => document.getElementById(id);
  const elegir = (t) =>
    [...document.querySelectorAll("#g-de button")].find((b) => b.textContent === t).click();
  const esperar = setInterval(() => {{
    if ($("app").hidden) return;
    clearInterval(esperar);
    {ESCENAS[escena]}
  }}, 50);
}});
</script>"""


def _sembrar(url: str) -> RepositorioGastos:
    repo = RepositorioGastos(crear_engine(url))
    for monto, categoria, descripcion, dia in GASTOS_SINTETICOS:
        repo.guardar(
            GastoExtraido(
                monto=Decimal(monto),
                categoria=categoria,
                fecha=HOY.replace(day=dia),
                atribuido_a="demian",
                descripcion=descripcion,
                texto_original=descripcion,
                remitente="demian",
                confianza_monto=Confianza.ALTA,
                confianza_categoria=Confianza.ALTA,
                confianza_fecha=Confianza.ALTA,
                confianza_atribuido_a=Confianza.ALTA,
            ),
            registrado_por=ID_DEMO,
        )
    return repo


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = _sembrar(f"sqlite:///{Path(tmp) / 'demo.db'}")
        app = crear_app(
            ServiciosApp(
                repositorio=repo,
                taxonomia=cargar_taxonomia(),
                bot_token=TOKEN,
                usuarios={ID_DEMO: "demian"},
                ingresos={"demian": Decimal("1200")},
                hoy=lambda: HOY,
            )
        )
        html = (RAIZ / "src/family_expense_bot/app/static/index.html").read_text("utf-8")
        sdk = '<script src="https://telegram.org/js/telegram-web-app.js"></script>'

        @app.get("/demo/{escena}", response_class=HTMLResponse)
        def demo(escena: str) -> str:
            return html.replace(sdk, _stub(escena))

        # Chrome headless no achica la ventana a menos de ~500 px: la app va en un
        # iframe del ancho de un celular y después se recorta.
        @app.get("/marco/{escena}", response_class=HTMLResponse)
        def marco(escena: str) -> str:
            return (
                '<body style="margin:0"><iframe src="/demo/' + escena + '" '
                f'style="border:0;width:{ANCHO}px;height:{ALTO}px"></iframe></body>'
            )

        puerto = _puerto_libre()
        servidor = uvicorn.Server(uvicorn.Config(app, port=puerto, log_level="warning"))
        threading.Thread(target=servidor.run, daemon=True).start()
        while not servidor.started:
            time.sleep(0.05)

        SALIDA.mkdir(parents=True, exist_ok=True)
        cuadros = []
        for escena in ESCENAS:
            ruta = Path(tmp) / f"{escena}.png"
            subprocess.run(
                [
                    CHROME,
                    "--headless=new",
                    "--hide-scrollbars",
                    f"--window-size={ANCHO + 400},{ALTO}",
                    "--force-device-scale-factor=2",
                    "--virtual-time-budget=4000",
                    f"--screenshot={ruta}",
                    f"http://127.0.0.1:{puerto}/marco/{escena}",
                ],
                check=True,
                capture_output=True,
            )
            cuadros.append(Image.open(ruta).convert("RGB").crop((0, 0, 2 * ANCHO, 2 * ALTO)))
        servidor.should_exit = True

    # PNG: las cuatro pantallas lado a lado. GIF: las mismas, una tras otra.
    margen = 24
    mosaico = Image.new(
        "RGB",
        (len(cuadros) * (cuadros[0].width + margen) + margen, cuadros[0].height + 2 * margen),
        "#2a2722" if ESQUEMA == "dark" else "#e4e0d6",
    )
    for i, c in enumerate(cuadros):
        mosaico.paste(c, (margen + i * (c.width + margen), margen))
    if ESQUEMA == "dark":
        mosaico.save(SALIDA / "mini-app-oscuro.png", optimize=True)
        print(f"Listo: {SALIDA / 'mini-app-oscuro.png'}")
        return
    mosaico.save(SALIDA / "mini-app.png", optimize=True)

    chicos = [c.resize((ANCHO, ALTO), Image.Resampling.LANCZOS) for c in cuadros]
    chicos[0].save(
        SALIDA / "mini-app.gif",
        save_all=True,
        append_images=chicos[1:],
        duration=2200,
        loop=0,
        optimize=True,
    )
    print(f"Listo: {SALIDA / 'mini-app.png'} y {SALIDA / 'mini-app.gif'}")


if __name__ == "__main__":
    main()
