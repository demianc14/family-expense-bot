# Roadmap

Objetivo: que quien llegue al repo vea en dos minutos un sistema que funciona, está medido y se despliega solo. Tesis: *el LLM propone, el código verifica*.

> Los plazos son estimaciones, no compromisos.

## Fase A. Base y presentación (días 1 a 3)

- [ ] Probar la Mini App en Telegram real. (`pytest`, `ruff` y `mypy` en verde y push: hecho.)
- [x] README corto (pitch, GIF de la Mini App, cómo correrlo, enlaces); detalle en `docs/`; la sección n8n pasa a `n8n/README.md`. Falta cambiar la captura por un GIF.
- [x] Revisar el historial de git por datos reales; confirmar que `data/processed/` y `*.egg-info/` no estén versionados. Queda como test en `tests/test_privacy.py`.
- [x] Descripción del repo y topics (`telegram-bot`, `llm`, `fastapi`, `n8n`, `personal-finance`).

## Fase B. Ingeniería visible (semana 1)

- [x] GitHub Actions: `pytest` + `ruff` + `mypy` en cada push, con badge. Sin secrets (Gemini va mockeado en los tests).
- [x] `Dockerfile` y `docker-compose` para levantar bot y Mini App con un comando.
- [x] `.env.example` documentado y `pre-commit` con ruff.

## Fase C. Evaluación del modelo (semana 2)

- [ ] Dataset etiquetado a partir de `tests/fixtures/mensajes.yaml`, ampliado con mensajes anonimizados (tamaño inicial sugerido: 60 a 100).
- [ ] Script `eval` que mide aciertos por campo (monto, categoría, fecha, atribución) y cuántas veces el código rechazó o pidió confirmación correctamente.
- [ ] Comparar modelos (`GEMINI_MODELO`) y versiones de prompt; resultados en `docs/evaluacion.md` y tabla en el README.
- [ ] Mejoras guiadas por esa tabla, medidas antes y después: varios gastos en un mensaje y estabilidad de la confianza.

## Fase D. Automatización (semana 3)

- [ ] Resumen semanal/mensual que llega solo al chat de Telegram.
- [ ] Gastos recurrentes (suscripciones, cuotas) propuestos cada mes y confirmados con un toque.
- [ ] Alerta cuando un bolsillo llega al 80%.
- [ ] Opcional: orquestar el resumen con el flujo n8n existente.

## Fase E. Despliegue y cierre (semana 4)

- [ ] Hosting estable (túnel con nombre o VPS/PaaS barato; costo por verificar).
- [ ] Logs y una métrica simple de latencia y errores.
- [ ] Demo corta (GIF o video).

## Fuera de alcance

- OCR de recibos (la Fase 5 original era opcional).
- Fine-tuning: sobredimensionado para el volumen de datos.
- Más frameworks o dashboards sin una pregunta que respondan.

## Criterio de "terminado"

- [x] Corre con un comando (Docker).
- [ ] CI verde con badge.
- [ ] Tabla de evaluación con números reales.
- [ ] Al menos una automatización que actúa sin que se pida.
- [ ] README de una pantalla y limitaciones honestas.
- [x] Historial sin datos personales.
