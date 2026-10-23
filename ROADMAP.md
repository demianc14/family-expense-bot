# Roadmap

Objetivo: que quien llegue al repo vea en dos minutos un sistema que funciona, está medido y se despliega solo. Tesis: *el LLM propone, el código verifica*.

> Los plazos son estimaciones, no compromisos.

## Estado al 2026-10-22 (para retomar)

- **Hecho:** Fase A y Fase B, y además un rediseño de la Mini App (ver abajo). El PR #1
  entró a `main`; el resto de `fase-b-ingenieria` (rediseño, Mini App personal, docs y el
  GIF en el README) va en el PR #2. La CI pasó en verde en la rama (3.12 y 3.14).
- **Mini App rediseñada** (`0732ae6`): estilo risografía con sobres por bolsillo, fichas de
  categoría del color de su bolsillo, "Hoy / Ayer / Otro día" y hojas que suben desde abajo,
  sin menús desplegables. Probada en Telegram real. Detalle en `docs/mini-app.md`.
- **La Mini App es solo de Demian** (`e5dc87f`): todo gasto es de quien la abre; ya no hay
  "¿de quién es?" y el servidor ignora `atribuido_a`. El bot de chat no cambió.
- **Base real migrada** (`--migrar`, agregó `gastos.origen`). Respaldo previo en
  `data/processed/gastos.respaldo-20261021-133136.db`.
- **Corriendo con Docker:** `docker compose up -d`; la Mini App sale por un túnel rápido de
  cloudflared, cuya URL cambia en cada arranque y además vence si la Mac se duerme
  (actualizar `APP_URL` y `docker compose up -d bot`; `restart` no relee el `.env`).
  Resolverlo es la Fase E.
- **Fase C en curso** (rama `fase-c-evaluacion`): banco de 84 casos, `--evaluar` y la tabla de flash-lite en `docs/evaluacion.md`. La evaluación encontró que un 429 dejaba al bot dormido horas; ya está arreglado.
- **Siguiente:** completar flash a medida que haya cuota (20 por día) y las mejoras candidatas de `docs/evaluacion.md`, medidas antes y después.

## Fase A. Base y presentación (días 1 a 3)

- [x] Probar la Mini App en Telegram real; `pytest`, `ruff` y `mypy` en verde; push.
- [x] README corto (pitch, GIF de la Mini App, cómo correrlo, enlaces); detalle en `docs/`; la sección n8n pasa a `n8n/README.md`.
- [x] Revisar el historial de git por datos reales; confirmar que `data/processed/` y `*.egg-info/` no estén versionados. Queda como test en `tests/test_privacy.py`.
- [x] Descripción del repo y topics (`telegram-bot`, `llm`, `fastapi`, `n8n`, `personal-finance`).

## Fase B. Ingeniería visible (semana 1)

- [x] GitHub Actions: `pytest` + `ruff` + `mypy` en cada push, con badge. Sin secrets (Gemini va mockeado en los tests).
- [x] `Dockerfile` y `docker-compose` para levantar bot y Mini App con un comando.
- [x] `.env.example` documentado y `pre-commit` con ruff.
- [x] Extra: rediseño de la Mini App y presupuesto solo personal (ver Estado).

## Fase C. Evaluación del modelo (semana 2)

- [x] Dataset etiquetado: 84 mensajes sintéticos en `evals/mensajes.yaml`. No hay reales anonimizados porque `extracciones` no guarda el texto.
- [x] Script `eval` que mide aciertos por campo (monto, categoría, fecha, atribución; la atribución solo aplica al bot de chat, la Mini App ya no la pide) y cuántas veces el código rechazó o pidió confirmación correctamente.
- [ ] Comparar modelos (`GEMINI_MODELO`) y versiones de prompt; resultados en `docs/evaluacion.md` y tabla en el README. Hecho flash-lite (84 casos en `evals/`); flash espera cuota: el plan gratuito da 20 requests por día.
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
- [ ] CI verde con badge (verde en la rama; falta que llegue a `main`).
- [x] Tabla de evaluación con números reales.
- [ ] Al menos una automatización que actúa sin que se pida.
- [ ] README de una pantalla y limitaciones honestas.
- [x] Historial sin datos personales.
