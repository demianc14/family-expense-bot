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

## Arquitectura propuesta

```
src/
  bot/
    handlers.py          # I/O de Telegram: recibe mensaje/foto, muestra
                          # confirmación, responde. Sin lógica de negocio.
  extraction/
    schema.py            # contrato Pydantic: GastoExtraido (monto,
                          # categoria, fecha, descripcion, confianza,
                          # texto_original)
    parser.py            # llama al LLM gratuito (Gemini/Groq), devuelve
                          # GastoExtraido con score de confianza por campo
    ocr.py                # Tesseract para fotos de recibos (fase posterior)
  storage/
    models.py             # esquema SQL (SQLAlchemy) para gastos confirmados
    repository.py         # persistencia, sin lógica de negocio
  dashboard/
    report.py             # agregaciones mensuales, export a Power BI/Streamlit
  cli.py
config/
  categories.yaml          # taxonomía de categorías, fuente de verdad
data/
  processed/                # NUNCA se sube al repo — solo local, .gitignore
tests/
  fixtures/                 # mensajes de ejemplo (texto libre variado)
README.md
```

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

**Fase 5 — OCR de recibos (opcional).** Solo si se decide extender más
allá de texto libre. Tesseract extrae texto de la foto, y ese texto
entra al mismo `parser.py` de la Fase 1 — no un pipeline paralelo.

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
