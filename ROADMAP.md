# Roadmap

Objetivo: que quien llegue al repo vea en dos minutos un sistema que funciona, está medido y hace algo útil sin que se lo pidan. Tesis: *el LLM propone, el código verifica*.

> Los plazos son estimaciones, no compromisos. El tiempo es poco (Movmo, universidad, cursos): una fase a la vez, y cada fase termina con una PR y una evidencia (tabla, test o captura).

## Estado al 2026-11-05 (para retomar)

- **En `main` (de446e9, 27-oct):** Fases A y B completas; Fase C con la evaluación hecha; hosting con Tailscale Funnel; Mini App con validación firmada de `initData` y allowlist.
- **Verificado el 5-nov en un clon limpio:** `pytest` 443 pasan, `ruff` y `mypy` sin errores. La CI corre en Python 3.12 y 3.14 con historial completo y Node.
- **Evaluación** (`docs/evaluacion.md`): 84 casos con flash-lite → 0 errores silenciosos en monto y fecha, 2 de 75 en categoría; 4 de 26 casos que debían dudar no dudaron (devolución como gasto, `otros` con confianza alta).
- **Flash:** 15 de 84 casos (`evals/corridas/gemini-3.5-flash-64386658.jsonl`); el plan gratuito da 20 requests/día y ya se descartó para producción por latencia y cuota. **Decisión: no se completa.** Se deja la corrida parcial documentada.
- **Sin mergear:** la rama `fase-d-alertas` tiene D1 hecho (alertas al 80 % y 100 %, también desde la Mini App; ver `docs/presupuesto.md#alertas`). Espera a que se cierre la Fase C.
- **Lo que falta para el perfil objetivo:** una automatización que actúe sola (Fase D) y, opcionalmente, un paso de agente con herramientas (Fase F). Hoy todo ocurre cuando alguien escribe.

### Operación

- Corre con `docker compose up -d`; la Mini App sale por `tailscale funnel --bg 8080` (URL fija, vuelve sola cuando la Mac despierta). Si cambia `APP_URL`: `docker compose up -d bot` (`restart` no relee el `.env`).
- **Aplicado de verdad recién el 2026-11-09.** Hasta ese día `APP_URL` seguía en cloudflared y la app no abría. La puesta en marcha, con una comprobación por paso, está en `docs/mini-app.md#puesta-en-marcha-una-sola-vez-por-mac`. Desde la PR #11, el bot verifica `APP_URL` al arrancar y avisa por Telegram si no carga.
- Respaldo de la base previo a la migración: `data/processed/gastos.respaldo-20261021-133136.db` (local, nunca en el repo).

## Hecho (Fases A y B)

- README de una pantalla, detalle en `docs/`, n8n en `n8n/README.md`; historial sin datos reales (`tests/test_privacy.py`); topics del repo.
- CI con badge (`pytest` + `ruff` + `mypy`, Gemini mockeado), `Dockerfile` + `docker-compose`, `.env.example`, `pre-commit`.
- Rediseño riso de la Mini App y presupuesto solo personal (PR #2).

## Fase C. Cerrar la evaluación (semana 1)

- [x] Banco de 84 casos y `--evaluar` con errores silenciosos separados.
- [x] flash-lite medido.
- [ ] Cerrar flash como "descartado por cuota y latencia" en `docs/evaluacion.md` y en este roadmap.
- [ ] Mejora 1: `otros` nunca con confianza alta (igual que `casa`). Medida antes y después contra el banco.
- [ ] Mejora 2: devoluciones e ingresos ("me devolvieron 20") se rechazan como "no es gasto". Medida antes y después.
- [ ] Mejora 3: separar `personal` de `educacion` en el yaml.
- [ ] Mejora 4: timeout más corto con un reintento.
- [ ] Tabla "antes → después" en `docs/evaluacion.md` y una línea en el README. Esa tabla es la evidencia de portafolio de esta fase.

Regla: cada mejora es una PR chica; si el número empeora, se descarta y se documenta.

## Fase D. Automatización que actúa sola (semanas 2 y 3)

Se hace en este orden, de lo más determinista a lo más complejo. Ninguna usa el LLM.

- [ ] D1. Alerta cuando un bolsillo llega al 80 %: mensaje de Telegram una sola vez por bolsillo y mes. *(Hecha en `fase-d-alertas`, sin mergear.)*
- [ ] D2. Resumen semanal automático al chat (JobQueue de python-telegram-bot; sin n8n).
- [ ] D3. Gastos recurrentes (suscripciones, cuotas): el bot propone el gasto cada mes y se confirma con un toque; nunca se guarda solo.
- [ ] Requisitos de las tres: tests con reloj falso, **idempotencia** (un reinicio del bot no reenvía lo ya enviado) y envío solo a IDs de la allowlist. D1 la saca del cruce antes/después del mismo gasto, sin estado en la base; D2 y D3 no tienen un gasto que las dispare, así que guardan el último envío en la base.
- [ ] README: sección corta "Qué hace sin que se lo pidas".

## Fase E. Operación mínima (semana 3 o 4)

- [x] Hosting estable: Tailscale Funnel (2026-10-26, ver `docs/decisiones.md`).
- [x] Demo corta (GIF de la Mini App en el README).
- [ ] Logs estructurados (una línea por extracción: modelo, latencia, resultado, error).
- [ ] Comando `/estado`: latencia p50/p90 y tasa de error de los últimos 7 días, desde la tabla `extracciones`. Nada de Grafana ni Prometheus.

## Fase F. Consultas con herramientas (opcional, solo si C y D están cerradas)

Un paso de agente pequeño y medible, para mostrar tool use sin abrir otro repo.

- [ ] El LLM recibe una pregunta ("¿cuánto gasté en comida este mes?") y **elige una función de solo lectura** con parámetros tipados (Pydantic): `gasto_por_categoria`, `saldo_bolsillo`, `puedo_gastar`, `ultimos_gastos`.
- [ ] El código ejecuta la función y **arma la respuesta con los números que devolvió**; el LLM no escribe cifras. Mismo principio, un nivel más arriba.
- [ ] Verificación: si el LLM pide una función que no existe o parámetros inválidos, se rechaza. Sin herramientas de escritura.
- [ ] Evaluación aparte: 30 preguntas etiquetadas; se mide función correcta, parámetros correctos y cifras que no vienen de la herramienta (meta: 0).
- [ ] Límite honesto en el README: un solo usuario, datos chicos, cuota gratuita. OJO: la cuota diaria de flash-lite no está documentada aquí; verificarla antes de planificar la evaluación.

Si la Fase D no sale primero, no se empieza esta.

## Fuera de alcance

- OCR de recibos, fine-tuning, importar Excel, multi-moneda, multiusuario, WhatsApp.
- Más trabajo en la versión n8n (queda congelada como segunda implementación).
- Dashboards o frameworks sin una pregunta que respondan.
- Hosting en la nube: Funnel basta para una persona.

## Criterio de "terminado"

- [x] Corre con un comando (Docker).
- [x] CI verde con badge.
- [x] Tabla de evaluación con números reales.
- [x] Historial sin datos personales.
- [ ] Tabla "antes → después" de las mejoras de la Fase C.
- [ ] Al menos una automatización que actúa sin que se pida (Fase D).
- [ ] `/estado` con latencia y errores (Fase E).
- [ ] README de una pantalla y limitaciones honestas, al día con lo anterior.
