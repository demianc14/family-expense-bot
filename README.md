# family-expense-bot

[![CI](https://github.com/demianc14/family-expense-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/demianc14/family-expense-bot/actions/workflows/ci.yml)

Finanzas personales desde Telegram. Escribes "pagué 18 del almuerzo", un LLM
gratuito extrae el gasto, el código lo verifica campo por campo y **nada se
guarda hasta que una persona lo confirma**. Encima hay un presupuesto 30/30/20/20
y una Mini App para verlo en el celular.

![Mini App: bolsillos del mes, "¿puedo gastar esto?", formulario y confirmación (datos sintéticos)](docs/img/mini-app.gif)

## Qué tiene de interesante

- **El LLM propone, el código verifica.** La confianza que el modelo se asigna no
  cuenta como evidencia: el monto tiene que estar escrito en el mensaje y la categoría
  tiene que existir en el yaml. Para el parentesco, el LLM cita "mi hija" y el código
  lo resuelve. → [decisiones](docs/decisiones.md)
- **Una métrica de calidad honesta.** Se mide sobre *todos* los intentos, no solo
  sobre los gastos guardados, porque sobre esos daría 100% siempre. → [calidad](docs/calidad.md)
- **Evaluado contra 84 mensajes etiquetados.** Lo que se mide son los errores
  *silenciosos* (equivocado y con confianza alta): con flash-lite, 0 en monto y en
  fecha, y 2 de 75 en categoría. → [evaluación](docs/evaluacion.md)
- **"¿Puedo gastar esto?" lo decide el código**, con el ritmo del mes. Los saldos
  nunca pasan por el LLM. → [presupuesto](docs/presupuesto.md)
- **La misma lógica dos veces:** un pipeline en Python y un agente en n8n, con
  paridad probada en ~10.000 casos. → [n8n](n8n/README.md)
- **Privacidad verificada:** un test revisa cada objeto del historial de git en
  busca de secretos, y las capturas usan datos sintéticos.

**Stack:** Python 3.12 · python-telegram-bot · Gemini (`flash-lite`) · Pydantic ·
SQLAlchemy/SQLite · FastAPI (Mini App) · n8n · pytest/ruff/mypy (más de 400 tests).

## Cómo correrlo

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env          # token de @BotFather, API key de Gemini, tu ID y tu ingreso
family-expense-bot            # el bot (polling)
family-expense-bot --app      # la Mini App en 127.0.0.1:8080; ver docs/mini-app.md
pytest && ruff check . && mypy
pre-commit install            # ruff antes de cada commit
```

O, con Docker, bot y Mini App con un comando (la base queda en `data/processed/`):

```bash
cp .env.example .env && docker compose up -d
```

En Telegram: escribe un gasto en texto libre, `/presupuesto`, `/puedo 30 concierto`
o `/reporte`.

## Estado

Empezó como un bot para que mis papás registraran gastos. Funcionó, pero ellos
siguieron con sus plantillas de Excel, así que esa etapa quedó cerrada en
[`v1.0-bot-familiar`](https://github.com/demianc14/family-expense-bot/releases/tag/v1.0-bot-familiar).
Hoy es mi herramienta personal. La historia completa está en
[Cómo cambió el proyecto](docs/decisiones.md#cómo-cambió-el-proyecto).

**Docs:** [Decisiones y limitaciones](docs/decisiones.md) ·
[Presupuesto y "¿puedo?"](docs/presupuesto.md) · [Calidad](docs/calidad.md) ·
[Evaluación](docs/evaluacion.md) · [Mini App](docs/mini-app.md) · [Versión n8n](n8n/README.md)
