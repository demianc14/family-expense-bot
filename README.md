# family-expense-bot

Bot de Telegram para que una familia registre gastos en texto libre
("200 dólares mercado"). El sistema extrae monto, categoría, fecha y
descripción con un LLM gratuito, pide confirmación y guarda el gasto para un
reporte mensual.

> **Estado: Fase 0.** El bot está conectado y solo responde con eco. Todavía
> no hay extracción ni almacenamiento.

## Pipeline (objetivo)

captura → extracción estructurada con confianza por campo → confirmación
humana → almacenamiento → reporte

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env   # pega aquí el token de @BotFather
family-expense-bot
```

## Calidad

```bash
pytest && ruff check . && ruff format --check . && mypy
```

## Privacidad

El repo es público. La base de datos, los exports y el `.env` están en
`.gitignore` desde el primer commit, y `tests/test_privacy.py` verifica que
sigan excluidos. Los ejemplos y capturas usan solo datos sintéticos.

## Decisiones y limitaciones

- **Plataforma: Telegram.** Tiene API gratuita y no pide aprobación. Hoy los
  usuarios no usan Telegram, así que instalarlo es una fricción de adopción
  real: justo lo que el proyecto busca minimizar. WhatsApp se descartó
  porque la Business API exige aprobación de Meta y no apunta a uso personal.
