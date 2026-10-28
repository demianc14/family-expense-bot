# Roadmap

Objetivo: que quien llegue al repo vea en dos minutos un sistema que funciona, está medido y se despliega solo. Tesis: *el LLM propone, el código verifica*.

> Los plazos son estimaciones, no compromisos.

## Estado al 2026-10-26 (para retomar)

- **En `main`:** Fase A, Fase B (PR #2: Mini App riso y solo personal) y la evaluación de la
  Fase C (PR #3). CI en verde. Las capturas de `docs/img/` están al día con el diseño riso
  (`scripts/capturas.py` las regenera idénticas).
- **Evaluación** (`docs/evaluacion.md`): banco de 84 casos en `evals/mensajes.yaml` y
  `family-expense-bot --evaluar`. Con flash-lite: 0 errores silenciosos en monto y fecha,
  2 de 75 en categoría. Al evaluar apareció un bug: ante un 429 el SDK dormía horas con el
  `Retry-After`. Ya está arreglado (`REINTENTABLES` en `gemini.py`) y desplegado en Docker.
- **Flash, a medias:** 15 de 84 casos en `evals/corridas/gemini-3.5-flash-64386658.jsonl`.
  El plan gratuito da 20 requests por día; `family-expense-bot --evaluar --modelo
  gemini-3.5-flash` retoma donde quedó. Cuando esté completa, la tabla va a
  `docs/evaluacion.md`.
- **Corriendo con Docker** (`docker compose up -d`), con la Mini App por Tailscale Funnel
  (`tailscale funnel --bg 8080`): la URL es fija y vuelve sola cuando la Mac despierta. Si
  `APP_URL` cambia, hay que correr `docker compose up -d bot` (`restart` no relee el `.env`).
- **Base real:** respaldo previo a la migración en `data/processed/gastos.respaldo-20261021-133136.db`.

### Siguiente sesión

1. Completar flash (unos 4 días de cuota) y su tabla.
2. Mejoras candidatas de `docs/evaluacion.md`, cada una medida antes y después contra el
   banco: devoluciones registradas como gasto, `otros` con confianza alta, `personal` contra
   `educacion` y el timeout de 30 s.

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
- [ ] Comparar modelos (`GEMINI_MODELO`) y versiones de prompt; resultados en `docs/evaluacion.md` y tabla en el README. Hecho flash-lite (84 casos en `evals/`); flash va en 15 de 84, porque el plan gratuito da 20 requests por día.
- [ ] Mejoras guiadas por esa tabla, medidas antes y después: varios gastos en un mensaje y estabilidad de la confianza.

## Fase D. Automatización (semana 3)

- [ ] Resumen semanal/mensual que llega solo al chat de Telegram.
- [ ] Gastos recurrentes (suscripciones, cuotas) propuestos cada mes y confirmados con un toque.
- [ ] Alerta cuando un bolsillo llega al 80%.
- [ ] Opcional: orquestar el resumen con el flujo n8n existente.

## Fase E. Despliegue y cierre (semana 4)

- [x] Hosting estable: Tailscale Funnel, URL fija y datos en la Mac (2026-10-26, ver `docs/decisiones.md`).
- [ ] Logs y una métrica simple de latencia y errores.
- [ ] Demo corta (GIF o video).

## Fuera de alcance

- OCR de recibos (la Fase 5 original era opcional).
- Fine-tuning: sobredimensionado para el volumen de datos.
- Más frameworks o dashboards sin una pregunta que respondan.

## Criterio de "terminado"

- [x] Corre con un comando (Docker).
- [x] CI verde con badge.
- [x] Tabla de evaluación con números reales.
- [ ] Al menos una automatización que actúa sin que se pida.
- [ ] README de una pantalla y limitaciones honestas.
- [x] Historial sin datos personales.
