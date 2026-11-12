# family-expense-bot — Brief de Proyecto para Claude Code

> Guarda este archivo como `CLAUDE.md` en la raíz de este repo (distinto del
> repo de `internship-matcher`). Claude Code lo lee automáticamente al
> iniciar sesión ahí.

## Rol de Claude Code en este proyecto

Ingeniero de datos/backend construyendo esto junto a Demian. Este proyecto
maneja **dinero real de terceros** (sus papás) — eso cambia el estándar de
rigor respecto a un proyecto de datos abiertos: nada se persiste sin que el
dato tenga confianza suficiente o confirmación humana. Antes de escribir
código en cada fase, propone el diseño y espera confirmación si algo es
ambiguo.

## Objetivo

Bot de Telegram donde mis papás registran gastos igual que ya lo hacen hoy
(mensaje de texto libre, ej. "200 dólares mercado"), y el sistema extrae
{monto, categoría, fecha, descripción} con un LLM gratuito, pide
confirmación, y lo guarda para generar un reporte mensual — sin pedirles
que aprendan una herramienta nueva.

**No es un chatbot conversacional genérico ni una app de finanzas
completa.** Es una tubería: captura → extracción estructurada con
confianza explícita → confirmación humana → almacenamiento → reporte.
Mismo espíritu que `internship-matcher` y `MetalurgicaAndina_QC`: contratos
de datos explícitos, fail-fast, reporte de calidad cuantificado, límites
documentados en el README, no solo "cómo correrlo".

## Principios no negociables

1. **Nunca se persiste un gasto sin confianza suficiente o confirmación.**
   Es dinero real de mis papás, no un dataset académico. Si el LLM extrae
   el monto o la categoría con baja confianza, el bot lo muestra y pide
   confirmación explícita antes de guardar — no asume.
2. **La extracción es un contrato explícito, con score de confianza.**
   Igual que el score explicable del matching de pasantías: no "el LLM
   dice que son 200 dólares de mercado" sin más — el output declara qué
   campos extrajo con alta confianza y cuáles quedaron ambiguos.
3. **El bot solo hace I/O.** Recibir mensaje, mostrar confirmación,
   responder. Cero lógica de negocio (extracción, categorización) dentro
   del handler de Telegram — eso vive en módulos aparte y se testea sin
   levantar el bot.
4. **La taxonomía de categorías es config, no código.** Vive en
   `config/categories.yaml`, no hardcodeada en el parser.
5. **Tests desde la fase 1**, con fixtures de mensajes de ejemplo — nunca
   llamando al LLM real en cada test run.
6. **Privacidad real, no teórica — este repo va a ser público en GitHub.**
   Nunca se versionan datos financieros reales de la familia: `.gitignore`
   excluye la base de datos y cualquier export con montos reales desde el
   primer commit. El README y las capturas de pantalla usan datos
   sintéticos/anonimizados, nunca gastos reales de mis papás.

## Decisión de plataforma (resuelta 2026-09-21)

**Telegram.** Confirmado con Demian: sus papás no lo usan hoy, pero aceptan
instalarlo. Esa fricción de adopción es real y se documenta en el README.
WhatsApp se descartó porque la Business API exige aprobación de Meta y no
apunta a uso personal.

**Layout:** paquete instalable en `src/family_expense_bot/` (bot/,
extraction/, storage/, dashboard/, cli.py). Son los mismos módulos de abajo
bajo un solo paquete.

## Decisiones de atribución (resueltas 2026-09-25)

Cada gasto tiene `categoria` (en qué) y `atribuido_a` (de quién), independientes.
Detalle y mediciones en docs/decisiones.md; lo que no se debe deshacer:

- **`atribuido_a` = quién asume el costo**, no quién pagó en la caja ni el
  beneficiario. Por defecto es el remitente, con confianza `media`, nunca `alta`.
- **`casa` nunca se deduce**, ni del remitente ni del tipo de gasto. Solo se
  acepta con confianza si el texto trae un marcador explícito del yaml.
- **El LLM cita la frase ("mi hija"); el código resuelve el parentesco** con
  `como_lo_llaman` del yaml. No volver a pedirle al LLM que elija el rol:
  contra gemini-3.5-flash-lite fallaba en "mi hija" → `hermana` con cualquier
  prompt que se probó.
- **Categorías `candidata: true` están inactivas** (hoy: salud, servicios). No
  activarlas sin confirmación de Demian con su familia.
- El mapa ID de Telegram → rol va en `.env` (`TELEGRAM_USUARIOS`), nunca en el repo.

## Decisiones de presupuesto (resueltas 2026-10-15)

Presupuesto 30/30/20/20 por bolsillos, en Python y en n8n con paridad. Detalle
en docs/presupuesto.md; lo que no se debe deshacer:

- **El ingreso va en `.env`** (`INGRESOS_MENSUALES=rol:monto`), nunca en el repo.
  Formato estricto (dígitos y hasta dos decimales), igual en las dos versiones.
- **Ahorro se aparta, no se gasta**; el fondo personal sí se gasta.
- **"Del fondo" nunca se deduce**: un marcador del yaml en el texto manda el
  gasto al fondo; si no, decide la categoría (`personal` va al fondo).
- **Solo cuentan los gastos atribuidos a la persona con ingreso.**
- **El bolsillo se calcula al leer** (categoría + texto guardados): sin cambios
  de esquema en la base real.
- **Los montos del presupuesto no pasan por el LLM** en n8n (rama determinista).
- **"¿Puedo gastar esto?" lo decide el código, no el LLM** (resuelto 2026-10-16):
  regla del ritmo del mes (NO si se pasa; DIFIERE si deja menos de lo que le toca
  a los días restantes; SÍ si no). Si no cabe pero sí en el fondo, se sugiere.
  El LLM solo extrae el gasto; el monto tiene que estar escrito. Una consulta no
  guarda nada ni cuenta como intento. `/puedo` en Python, lenguaje natural en n8n.

## Decisión de rumbo (resuelta 2026-10-20)

La etapa del bot familiar se cerró (tag `v1.0-bot-familiar`): los papás no usan
Telegram y llevan sus finanzas en Excel con plantillas propias. **El usuario
principal ahora es Demian**, y lo siguiente es una Telegram Mini App personal
encima del mismo núcleo (bolsillos, "¿puedo?", formulario de gastos). Es otra
capa de I/O, sin lógica de negocio nueva en las rutas. Lo que no cambia:

- Los principios no negociables siguen igual (confirmación antes de guardar,
  privacidad, el LLM cita y el código decide), aunque el dinero sea propio.
- La familia queda en la taxonomía y en el README como caso de uso documentado.
  No se importa Excel por ahora.
- El repo sigue siendo el mismo, sin archivar: el tag marca el cierre.
- El README es de una pantalla (pitch, captura, cómo correrlo, enlaces); el detalle
  vive en `docs/` y la versión n8n en `n8n/README.md`. Las capturas se regeneran
  con `scripts/capturas.py`, siempre con datos sintéticos.

## Decisiones de la Mini App (resueltas 2026-11-10)

Pedidas por Demian; detalle en docs/mini-app.md y docs/decisiones.md:

- **Montos con coma o punto** para los centavos; sin separador de miles ("1,200"
  se rechaza, no se adivina).
- **Subcategoría = etiqueta libre** en `gastos.subcategoria`, fuera de
  `GastoExtraido`. No decide el bolsillo; las categorías siguen siendo del yaml.
- **`otros` pide bolsillo en la app** y se guarda como marcador en el texto
  (`marcadores_otros` del yaml), que solo vale para `otros`. No darle `marcadores`
  a necesarios/lujos: "¿puedo?" los ofrecería como alternativa al fondo.
- **Editar = anular + crear** en una transacción (`reemplazar`); borrar = anular.
  Solo gastos que registró y son de quien abre la app.
- **Ingresos extra** (tabla `ingresos`, categorías en `ingresos:` del yaml): se
  **suman** al fijo de `.env` en su mes y se reparten 30/30/20/20. Sin categoría
  "sueldo" (duplicaría el fijo). Mismo flujo que los gastos: revisar, sí, editar,
  borrar. `resumen_presupuesto(..., extra=0)` da lo de siempre: n8n no los ve.

## Decisión de rumbo (resuelta 2026-11-10)

El repo es la herramienta real de finanzas personales de Demian y una pieza
central de su portafolio. Cuenta una sola historia de producto: **captura
verificada → conciliación en SQL → análisis en Power BI**. Lo que no se debe
deshacer:

- **El repo se renombra a `personal-finance-bot`** (elegido el 2026-11-11). El
  paquete `family_expense_bot` y el comando `family-expense-bot` no se
  renombran: ver `docs/decisiones.md`.
- **Entra la conciliación de estados de cuenta** (Fase G de `ROADMAP.md`), con
  SQL y un dashboard de Power BI. Es la prioridad del repo después de las
  tareas 1 a 3 del roadmap.
- **La Fase F (tool use, consultas con LLM) sale de este repo.** RAG y agentes
  van al proyecto del asistente académico, para que cada repo muestre algo
  distinto.

### Principios de la conciliación (no negociables)

- **El estado de cuenta cuadra o no entra.** Saldo anterior + créditos −
  débitos = saldo final, al centavo y en enteros (como `storage/dinero.py`). Si
  no cuadra se rechaza **el estado completo**; nunca se importa a medias. El
  mismo archivo no se importa dos veces (`hash_archivo` único).
- **La conciliación la deciden reglas, no el LLM.** Reglas deterministas en
  SQL, en orden y con nombre: monto exacto y misma fecha, monto exacto a ±3
  días, y monto exacto con descripción parecida. Cada par guarda qué regla lo
  unió, y ningún movimiento queda conciliado dos veces.
- **Nada se guarda como gasto sin confirmación.** Un movimiento no registrado
  se **propone** (Telegram o Mini App) y pasa por el flujo de siempre.
- **Sin borrado:** anulación con fecha y motivo, igual que los gastos.
- **Los estados de cuenta reales nunca entran al repo.** Tests, capturas y el
  `.pbix` público usan datos sintéticos. `.gitignore` sigue ignorando `*.csv`,
  `*.xlsx`, `*.pbix` y `data/raw/`; lo sintético entra con excepciones
  explícitas y acotadas, y `tests/test_privacy.py` falla si algo se cuela.
- **No se manda un estado de cuenta real a un LLM** sin que Demian lo decida
  explícitamente. Tampoco se categorizan movimientos con LLM: primero reglas.
- **Un banco nuevo es una entrada en `config/bancos.yaml`**, no código nuevo
  (como `categories.yaml`).
- **CSV o Excel primero; el PDF solo si no hay export**, como subfase aparte y
  con la misma verificación (cada monto aparece literal en la página y el
  saldo cuadra).
- **No se commitea un `.pbix`** sin que Demian confirme que es sintético. Las
  cifras de cobertura con datos reales se calculan solo en local y van al
  README solo si Demian lo aprueba.

## Reglas de trabajo (vigentes desde 2026-11-10)

- **Una rama y una PR por tarea.** Antes de cada commit: `pytest`,
  `ruff check .`, `ruff format --check .` y `mypy`.
- **No llamar a Gemini sin avisar:** decir cuántas llamadas hará una corrida y
  esperar el OK.
- **Toda cifra del README o de `docs/` sale de un comando** o de una corrida
  guardada.
- **Si hay dos diseños razonables, proponer en 5 líneas y esperar.** Lo marcado
  **DECIDE DEMIAN** en `ROADMAP.md` no se hace sin su respuesta.
- **Sin dependencias nuevas** salvo que la tarea lo justifique por escrito en
  la PR.
- **Si una mejora medida no mejora, no se mergea** y se documenta.
- **Al terminar cada sesión, reportar:** qué quedó hecho (con PRs), las cifras
  con su comando y qué decide Demian.

## Arquitectura

```
src/family_expense_bot/
  bot/
    handlers.py          # I/O de Telegram: recibe, muestra confirmación, responde.
                         # Sin lógica de negocio.
    mensajes.py          # textos que manda el bot
  app/
    api.py               # Mini App (FastAPI): otra capa de I/O sobre el mismo núcleo
    auth.py              # valida el initData firmado de Telegram + allowlist
    static/index.html    # la Mini App
  extraction/
    schema.py            # contrato Pydantic: GastoExtraido, confianza por campo
    parser.py            # LLM → GastoExtraido; el código verifica y degrada confianza
    gemini.py            # cliente de Gemini (reintentos, timeouts, cuota)
    verificacion.py      # el monto tiene que estar escrito en el mensaje
    formulario.py        # gastos del formulario de la Mini App, sin LLM
    categorias.py        # carga de la taxonomía desde el yaml
  storage/
    models.py            # esquema SQL (SQLAlchemy) de gastos confirmados
    repository.py        # persistencia, sin lógica de negocio
    dinero.py            # montos en centavos
  dashboard/
    report.py            # agregaciones mensuales y reporte de calidad
    presupuesto.py       # bolsillos 30/30/20/20, "¿puedo?", alertas
  conciliacion/          # Fase G, por construir
    ingesta.py           # CSV/Excel del banco → contrato Pydantic
    verificacion.py      # invariante de saldo, fechas en el periodo, duplicados
    reglas.py            # reglas de cruce movimiento ↔ gasto, con nombre
    recurrentes.py       # suscripciones y cargos que se repiten
  evaluacion.py          # banco etiquetado contra el LLM real (--evaluar)
  config.py
  cli.py
config/categories.yaml   # taxonomía, fuente de verdad
config/bancos.yaml       # (Fase G) formato de cada banco: columnas, fecha, decimal, signo
sql/vistas.sql           # (Fase G) cobertura, no registrados, recurrentes, costo del banco
powerbi/                 # (Fase G) .pbix y CSV, solo con datos sintéticos
evals/                   # mensajes.yaml (sintético) y corridas/ guardadas
n8n/                     # segunda implementación, congelada
scripts/capturas.py      # capturas del README con datos sintéticos
scripts/estado_sintetico.py  # (Fase G) estados de cuenta sintéticos que cuadran, y que no
data/processed/          # NUNCA se sube al repo — solo local, .gitignore
tests/                   # fixtures de mensajes; el LLM siempre mockeado
```

## Estado

El plan vigente y el estado para retomar están en `ROADMAP.md` (fases A a F).
Las fases de abajo son las originales del bot familiar, ya cerradas en
`v1.0-bot-familiar`; se dejan como historia.

## Fases de desarrollo

**Fase 0 — Setup + decisión de plataforma.** Confirmar Telegram vs.
WhatsApp (ver arriba). Estructura de carpetas, `pyproject.toml`, entorno
virtual, `pytest`/`ruff`/`mypy` corriendo. Bot conectado respondiendo
"echo" simple — cero lógica de negocio todavía. `.gitignore` con
`data/processed/` desde el primer commit, antes de que exista un solo
dato real.

**Fase 1 — Contrato de extracción.** Definir `GastoExtraido` en
`schema.py`: monto, categoría, fecha, descripción, y un campo de
confianza por dato extraído (no un solo score global — el monto puede
estar claro y la categoría ambigua). `parser.py` llama al LLM sobre texto
libre y devuelve ese contrato. Tests con fixtures de mensajes típicos
("200 mercado", "pagué 15 dólares de taxi ayer", mensajes ambiguos o
incompletos) sin llamar al LLM real en cada run — mockeado.

**Fase 2 — Storage.** Esquema SQL para gastos ya confirmados (no
extracciones sin confirmar — esas no se persisten). SQLite para el MVP.
Capa de repositorio separada del modelo.

**Fase 3 — Flujo completo con confirmación humana.** Conectar
`handlers.py`: mensaje entra → `parser.py` extrae → el bot responde
mostrando lo que entendió y pide confirmación (botones sí/no o reacción)
→ solo si se confirma, se llama a `repository.py` para guardar. Si se
rechaza, se descarta o se pide corrección manual — documentar cuál de
las dos.

**Fase 4 — Categorización + reporte.** Taxonomía en `categories.yaml`.
Agregaciones mensuales (`report.py`): total por categoría, gastos que
necesitaron corrección manual vs. los que el LLM acertó a la primera
(esta métrica es el reporte de calidad de este proyecto, equivalente al
de MetalurgicaAndina_QC). Dashboard en Streamlit o export a Power BI.

**Fase 5 — OCR de recibos: descartada.** Quedó fuera de alcance (ver
`ROADMAP.md`). Si algún día vuelve, el texto de la foto entra al mismo
`parser.py`, no a un pipeline paralelo.

**Fase 6 — Tests de integración + README + hardening de privacidad.**
Verificar que ningún dato real quede versionado en ningún punto del
historial de git (no solo en el último commit). README con arquitectura,
reporte de calidad (% de extracciones aceptadas sin corrección) y
limitaciones honestas — incluida la limitación de que el sistema depende
de que alguien de la familia efectivamente use el bot, no es 100%
automático como la meta original de "cero revisión manual".

Al cerrar cada fase, resumir qué quedó funcionando, qué decisión quedó
pendiente, y no avanzar sin tests pasando — mismo criterio que
`internship-matcher`.

## Qué NO hacer

- No persistir un gasto sin confianza suficiente o confirmación humana.
- No subir datos financieros reales de la familia al repo público, en
  ningún commit.
- No hardcodear categorías dentro del parser.
- No mezclar el I/O del bot con la lógica de extracción/categorización.
- No asumir WhatsApp ni Telegram sin haberlo confirmado con Demian primero.
- No avanzar de fase sin que la fase anterior tenga tests pasando.
- No importar a medias un estado de cuenta que no cuadra.
- No dejar que el LLM decida una conciliación ni mandarle un estado de cuenta real.
- No versionar estados de cuenta reales ni un `.pbix` sin confirmar que es sintético.
- No hacer en este repo tool use, RAG ni agentes: van al asistente académico.
- No conectarse al banco (APIs, open banking, scraping con credenciales).
