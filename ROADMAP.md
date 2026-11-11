# Roadmap

Objetivo: que quien llegue al repo vea en dos minutos un sistema que funciona, está medido y hace algo útil sin que se lo pidan. Tesis: *el LLM propone, el código verifica*.

El repo cuenta una sola historia de producto: **captura verificada → conciliación en SQL → análisis en Power BI**.

> Los plazos son estimaciones, no compromisos. El tiempo es poco (Movmo, universidad, cursos): una tarea a la vez, y cada una termina con una PR y una evidencia (tabla, test o captura).

## Estado al 2026-11-11 (para retomar)

- **En `main` (9a583c1, 11-nov):** Fases A y B completas; Fase C con la evaluación hecha, flash descartado, `--desde-corrida` y la mejora 1; Mini App con coma decimal, subcategoría, bolsillo para `otros`, editar y borrar, e ingresos extra (PRs #13 a #15); docs de la caída del 10-nov (PRs #16 y #17).
- **Verificado el 11-nov en `main`:** `pytest` 519 pasan; `ruff check .`, `ruff format --check .` y `mypy` sin errores.
- **D1 (PR #7, rama `fase-d-alertas`):** rebaseada sobre `main` el 11-nov, con la alerta calculada sobre el presupuesto que incluye ingresos extra, sin repetir avisos al editar ni al reiniciar. `pytest` 545 pasan en la rama. Falta subirla y mergear.
- **Evaluación** (`docs/evaluacion.md`): 84 casos con flash-lite → 0 errores silenciosos en monto y fecha, 2 de 75 en categoría; dudó cuando debía en 24 de 26 (falta la devolución contada como gasto).
- **Decisión de rumbo del 10-nov:** el repo se renombra, entra la conciliación de estados de cuenta (Fase G) y la Fase F sale de este repo.

### Operación

- Corre con `docker compose up -d`; la Mini App sale por `tailscale funnel --bg 8080` (URL fija, vuelve sola cuando la Mac despierta). Si cambia `APP_URL`: `docker compose up -d bot` (`restart` no relee el `.env`).
- **Aplicado de verdad recién el 2026-11-09.** Hasta ese día `APP_URL` seguía en cloudflared y la app no abría. La puesta en marcha, con una comprobación por paso, está en `docs/mini-app.md#puesta-en-marcha-una-sola-vez-por-mac`. Desde la PR #11, el bot verifica `APP_URL` al arrancar y avisa por Telegram si no carga.
- Respaldo de la base previo a la migración: `data/processed/gastos.respaldo-20261021-133136.db` (local, nunca en el repo).

## Hecho (Fases A y B)

- README de una pantalla, detalle en `docs/`, n8n en `n8n/README.md`; historial sin datos reales (`tests/test_privacy.py`); topics del repo.
- CI con badge (`pytest` + `ruff` + `mypy`, Gemini mockeado), `Dockerfile` + `docker-compose`, `.env.example`, `pre-commit`.
- Rediseño riso de la Mini App y presupuesto solo personal (PR #2).

## Pedidos de uso real (10-nov, fuera de fase)

- [x] Montos con coma decimal en la Mini App.
- [x] Subcategoría libre, con sugerencias de las ya usadas.
- [x] "Otros" pide de qué bolsillo sale (`marcadores_otros`, con paridad en n8n).
- [x] Pestaña Gastos: editar (anular + crear) y borrar con confirmación.
- [x] Ingresos extra: se suman al fijo y se reparten 30/30/20/20; mismo flujo que los gastos.
- [ ] En la Mac: respaldo, `--migrar` y `docker compose up -d --build` (ver `docs/mini-app.md`).

## Tareas, en orden

Una rama y una PR por tarea. Lo marcado **DECIDE DEMIAN** no se hace sin su respuesta.

### 1. Renombrar el repo (DECIDE DEMIAN el nombre)

- [ ] Demian elige el nombre y renombra en GitHub (Settings → General).
- [ ] Enlaces al día dentro del repo: título y primera línea del README, badge de CI, enlaces a releases y tags, `CLAUDE.md` (Objetivo y Rol), `description` de `pyproject.toml` y menciones en `docs/`.
- [ ] El paquete `family_expense_bot` y el comando `family-expense-bot` **no** se renombran: cambio grande sin valor visible. Queda como decisión en `docs/decisiones.md`.
- [ ] Lista para Demian de los enlaces de fuera del repo (CV, LinkedIn, README del perfil, brief del portafolio).

### 2. Mergear D1 (alertas al 80 % y 100 %)

- [x] Rebase sobre `main`.
- [x] Test: la alerta y la Mini App usan el mismo presupuesto con ingresos extra.
- [x] Test: un reinicio no reenvía una alerta ya enviada.
- [x] Editar un gasto no repite el aviso (el "antes" incluye el gasto reemplazado).
- [ ] PR #7 verde y mergeada.

### 3. Mejora 2 de la Fase C: devoluciones e ingresos no son gasto

- [ ] Decidir dónde se resuelve (prompt, verificación o los dos).
- [ ] Medir antes y después contra el banco de 84 casos.
- [ ] Fila en la tabla "antes → después" de `docs/evaluacion.md`. Si no mejora, no se mergea y se documenta.

### 4. Conciliación de estados de cuenta (Fase G, fases 0 a 2)

Es la **prioridad del repo** después de las tareas 1 a 3. Ver [Fase G](#fase-g-conciliación-de-estados-de-cuenta).

### 5. Después de la conciliación (si hay tiempo, en este orden)

- [ ] D2: resumen semanal automático (JobQueue, sin LLM, idempotente, tests con reloj falso). Puede incluir "movimientos del banco sin registrar".
- [ ] `/estado`: latencia p50/p90 y tasa de error de 7 días, desde la tabla `extracciones`.
- [ ] README: sección corta "Qué hace sin que se lo pidas".

## Fase G. Conciliación de estados de cuenta

El bot sabe lo que Demian le escribe; el banco y la tarjeta saben lo que de verdad salió. La conciliación cruza las dos fuentes y responde: qué gasté y no registré, qué pago cada mes sin pensarlo y cuánto me cuesta el banco. Métrica nueva: **cobertura**, el porcentaje del gasto real que capturó el bot.

Principios, además de los de `CLAUDE.md`:

- **El estado de cuenta cuadra o no entra.** Saldo anterior + créditos − débitos = saldo final, al centavo y en enteros. Si no cuadra, se rechaza el estado completo.
- **La conciliación la deciden reglas en SQL, no el LLM.** En orden y con nombre; cada par guarda qué regla lo unió.
- **Nada se guarda como gasto sin confirmación**, y nada se borra: anulación con fecha y motivo.
- **Los estados de cuenta reales nunca entran al repo ni van a un LLM** sin que Demian lo decida. Tests, capturas y el `.pbix` público usan datos sintéticos.

### G0. Fuentes (DECIDE DEMIAN)

- [ ] Banco y tarjeta; si exportan CSV o Excel; si el PDF trae texto.
- [ ] Desde qué mes hay estados de cuenta y cuánto se paga en efectivo.
- [ ] Dónde corre Power BI (Desktop no corre en Mac).
- [ ] Respuestas en `docs/conciliacion.md`.

### G1. Ingesta y modelo

- [ ] Esquema con migración: `estados_cuenta` (con `hash_archivo` único), `movimientos` (centavos con signo, anulación) y `conciliaciones` (qué regla unió cada par).
- [ ] `conciliacion/`: `ingesta.py` (CSV/Excel → contrato Pydantic), `verificacion.py` (invariante de saldo, fechas en el periodo, duplicados), `reglas.py` y `recurrentes.py`.
- [ ] `config/bancos.yaml`: un banco nuevo es una entrada en el yaml, no código.
- [ ] CLI: `--importar-estado`, `--conciliar <AAAA-MM>` y `--exportar-bi`.
- [ ] `scripts/estado_sintetico.py`: estados sintéticos que cuadran y variantes que no.
- [ ] `.gitignore` con excepciones acotadas para lo sintético, y `tests/test_privacy.py` ampliado a CSV, xlsx y pbix.
- [ ] **Cierre:** importar un estado sintético funciona, uno que no cuadra se rechaza con un mensaje exacto y el mismo archivo dos veces no duplica nada.

### G1b. PDF (solo si G0 dice "solo PDF")

- [ ] Texto con una librería de PDF y reglas; el LLM solo si las reglas no alcanzan y con la misma verificación (cada monto aparece literal en la página y el saldo cuadra).

### G2. Conciliación, SQL y Power BI

- [ ] Vistas en `sql/vistas.sql`: `v_cobertura_mensual`, `v_no_registrados`, `v_recurrentes`, `v_costo_banco` y `v_gasto_real_bolsillo`.
- [ ] Tests de SQL: conteos, totales contra el estado de cuenta y ningún movimiento conciliado dos veces.
- [ ] Mini App: lista "Sin registrar" con Registrar o Ignorar, por el flujo de confirmación de siempre.
- [ ] `--exportar-bi` escribe las vistas a CSV; dashboard de 3 páginas (Cobertura, Gasto real, Costo del banco y suscripciones) con datos sintéticos.
- [ ] Medición: precisión y recall por regla sobre pares etiquetados a mano, con casos difíciles (mismo monto dos veces en la semana, pagos partidos, devoluciones).
- [ ] **Cierre:** vistas con tests, "Sin registrar" funcionando, dashboard en PDF y capturas, tabla de precisión y recall en `docs/conciliacion.md` y una línea en el README.

## Fase C. Evaluación (cerrada salvo la mejora 2)

- [x] Banco de 84 casos y `--evaluar` con errores silenciosos separados.
- [x] flash-lite medido; flash descartado por cuota y latencia.
- [x] Mejora 1: `otros` nunca con confianza alta. Medida antes y después.
- [ ] Mejora 2: devoluciones e ingresos (tarea 3).
- Mejoras 3 (separar `personal` de `educacion`) y 4 (timeout más corto con un reintento): si hay tiempo.

Regla: cada mejora es una PR chica; si el número empeora, se descarta y se documenta.

## Fase D. Automatización que actúa sola

- [ ] D1. Alertas al 80 % y 100 % (tarea 2).
- [ ] D2. Resumen semanal (tarea 5).
- D3. Gastos recurrentes: si hay tiempo. La conciliación detecta suscripciones (`v_recurrentes`) y puede reemplazarla; se evalúa al llegar a G2.
- Requisitos: tests con reloj falso, idempotencia y envío solo a IDs de la allowlist.

## Fase E. Operación mínima

- [x] Hosting estable: Tailscale Funnel (2026-10-26, ver `docs/decisiones.md`).
- [x] Demo corta (GIF de la Mini App en el README).
- [ ] `/estado` (tarea 5).

## Fase F. Consultas con herramientas: fuera de este repo

Decidido el 10-nov: tool use, RAG y agentes van al proyecto del asistente académico, para que cada repo muestre algo distinto. Aquí no se hace.

## Fuera de alcance

- Fase F (ver arriba).
- Conexión directa con el banco (APIs, open banking) y scraping de la banca web.
- Categorización de movimientos con LLM: primero reglas; el LLM solo si la medición muestra que hace falta y con OK de Demian.
- OCR de fotos, fine-tuning, importar Excel de gastos, multi-moneda, multiusuario, WhatsApp.
- Más trabajo en la versión n8n (queda congelada como segunda implementación).
- Hosting en la nube: Funnel basta para una persona.

## Criterio de "terminado"

- [x] Corre con un comando (Docker).
- [x] CI verde con badge.
- [x] Tabla de evaluación con números reales.
- [x] Historial sin datos personales.
- [ ] Repo renombrado y enlaces al día.
- [ ] Tabla "antes → después" de las mejoras de la Fase C, con la mejora 2.
- [ ] Al menos una automatización que actúa sin que se pida (D1).
- [ ] Conciliación con invariante de saldo probado y precisión/recall medidos.
- [ ] Dashboard Power BI con datos sintéticos en el repo (PDF + capturas).
- [ ] `/estado` con latencia y errores.
- [ ] README de una pantalla y limitaciones honestas, al día con lo anterior.
