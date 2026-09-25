# family-expense-bot

Bot de Telegram para que una familia registre gastos en texto libre
("200 dólares supermercado"). El sistema extrae monto, categoría, fecha, a
quién corresponde el gasto y una descripción con un LLM gratuito, pide
confirmación y guarda el gasto para un reporte mensual.

> **Estado: Fase 4.** Pipeline completo: captura → extracción verificada →
> confirmación humana → almacenamiento → reporte mensual con métrica de calidad.

## Pipeline (objetivo)

captura → extracción estructurada con confianza por campo → confirmación
humana → almacenamiento → reporte

## Cómo se decide si un gasto es confiable

El LLM propone; el sistema verifica. La confianza que un modelo se asigna a
sí mismo no es evidencia: dice "alta" con la misma soltura cuando acierta que
cuando inventa. Por eso cada campo pasa por una regla determinista:

| Campo | Verificación | Si falla |
|---|---|---|
| monto | El número debe aparecer **literalmente** en el mensaje | confianza `baja` |
| monto | Debe ser positivo | se descarta |
| categoría | Debe existir en `config/categories.yaml` | cae en `otros`, confianza `baja` |
| fecha | No puede estar en el futuro ni tener más de un año | confianza `baja` |
| fecha | Si el mensaje no la menciona, se asume hoy | confianza `media` |
| atribución | Si el mensaje no dice de quién es, se asume quien escribe | confianza `media` |
| atribución | Otra persona solo se acepta si el mensaje la nombra | confianza `baja` |
| atribución | `casa` solo se acepta si el mensaje lo dice explícitamente | confianza `baja` |

Todo lo que no quede en `alta` se le muestra a la persona antes de guardar,
siempre con una nota que explica por qué. `GastoExtraido` es inmutable.

## Dos dimensiones: en qué y de quién

Cada gasto tiene una **categoría** (en qué se gastó) y una **atribución** (a
quién le corresponde el costo). Son independientes, y las dos viven en
`config/categories.yaml`, no en el código.

**La atribución es quién asume el costo**, no quién pagó en la caja ni quién
se beneficia. "Pagué la pensión de mi hija" nombra a la beneficiaria pero no
dice de qué bolsillo salió la plata, así que queda en duda. `casa` es el gasto
compartido 50/50 entre los papás, y **nunca se deduce**: ni de quién escribe,
ni del tipo de gasto. Solo cuenta si el mensaje lo dice ("gasto de la casa").

**Los parentescos dependen de quién habla.** "Mi hermana" escrito por el hijo
es la hija de la familia; escrito por la mamá es una tía, que no está en la
lista. Por eso el yaml tiene una tabla de cómo cada remitente llama a cada
persona, y el mapa de ID de Telegram → rol vive en `.env` (`TELEGRAM_USUARIOS`),
porque los IDs son datos personales.

**El LLM cita; el código resuelve.** Al principio el LLM elegía el rol
directamente. Contra `gemini-3.5-flash-lite` eso fallaba siempre en un caso:
"mi hija pagó 45 de ropa con su plata", escrito por la mamá o por el papá,
nunca llegaba al rol `hermana`. El nombre del rol contradice el parentesco,
y ni una descripción en prosa, ni una tabla explícita de parentescos en el
prompt, ni reescribir el ejemplo del prompt lo corrigieron. En cambio, "mi
esposo" → `papa` funcionó en todos los intentos, porque ahí el nombre no
contradice nada.

Ahora el LLM solo copia la frase ("mi hija"), igual que copia el monto, y el
código la resuelve con la tabla del yaml. Con ese diseño, esta batería salió
10 de 10 contra el modelo real:

| Quien escribe | Mensaje | Resultado |
|---|---|---|
| mamá | mi hija pagó 45 de ropa con su plata | `hermana`, alta |
| papá | mi hija pagó 45 de ropa con su plata | `hermana`, alta |
| mamá | mi esposo pagó 60 de zapatos con su plata | `papa`, alta |
| hijo | mi ñaña pagó 30 del cine con su plata | `hermana`, alta |
| hijo | mi hermana pagó 45 de ropa con su plata | `hermana`, alta |
| mamá | mi hermana pagó 45 de ropa con su plata | `mama`, baja (es una tía) |
| papá | 300 de la pensión de mi hija | `papa`, baja (beneficiaria, no pagadora) |
| mamá | 80 de gasolina, gasto de la casa | `casa`, alta |
| papá | 300 de pensión | `papa`, baja |
| mamá | 200 dólares supermercado | `mama`, media (asumido) |

Son 10 mensajes corridos una vez: muestran que el caso que fallaba ya no
falla, no son una tasa de acierto. La tasa real sale del reporte de calidad
con uso real. Efecto secundario bueno: al LLM ya no le llega quién escribe
ni quiénes son la familia.

**Corregir la atribución cuesta un toque.** La confirmación trae botones para
cambiar a quién corresponde el gasto sin reescribir el mensaje. Eso importa
porque el caso más común (el súper, que suele ser de la casa) es justo el que
el valor por defecto atribuye mal.

**Categorías candidatas.** `salud` y `servicios` están en el yaml marcadas
`candidata: true`: el LLM no las ve y el parser no las acepta hasta que la
familia las confirme. Así no entra ningún dato real bajo una categoría que
después podría desaparecer.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env   # pega aquí el token de @BotFather
family-expense-bot --check   # valida el token contra la API de Telegram
family-expense-bot --extraer "200 dólares mercado"   # prueba el parser (usa Gemini)
family-expense-bot --exportar   # CSV para Power BI (queda fuera de git)
family-expense-bot           # levanta el bot (modo polling)
```

En Telegram, `/reporte` devuelve el total del mes por categoría.

El token vive solo en `.env`. Si alguna vez se filtra (por ejemplo, pegado en
un chat o en una captura), revócalo con `/revoke` en @BotFather: quien lo
tenga puede leer los mensajes del bot y responder como si fuera él.

## Calidad

```bash
pytest && ruff check . && ruff format --check . && mypy
```

## Flujo de confirmación

1. Llega el mensaje. Si el ID de Telegram no está autorizado, se ignora en
   silencio y se loguea (nunca se llama al LLM por un desconocido).
2. El bot responde "Leyendo tu mensaje..." al instante, para que nadie se
   quede mirando un chat mudo.
3. Se extrae con un límite de 45 s impuesto por el bot (una extracción normal
   toma ~3 s). Si se pasa, si se agota la cuota del LLM o si falla cualquier
   otra cosa, se avisa con el motivo. El bot nunca muere por eso.
4. Se muestra lo entendido, con ❓ en cada campo dudoso y las notas que lo
   explican. Debajo, "Sí, guardar" / "No", y una fila para cambiar a quién
   corresponde el gasto. Cambiarlo no guarda: vuelve a mostrar el resumen,
   con ✏️ en lo corregido, y espera el sí.
5. **Solo al tocar "Sí, guardar" se escribe en la base.** Si dice que no, se
   descarta y se le pide reescribirlo.

Las extracciones sin confirmar viven en memoria, nunca en disco. Si el bot se
reinicia, un botón viejo ya no guarda nada: responde como descartado.

## Almacenamiento

Solo se guardan gastos **confirmados por una persona**; las extracciones sin
confirmar viven en memoria y se descartan si se rechazan. Decisiones del
esquema (`src/family_expense_bot/storage/`):

- **Montos en centavos, como enteros.** En punto flotante, `0.1 + 0.2` no da
  `0.3`; sumar gastos de una familia durante un año arrastraría ese error.
- **Nada se borra.** `anular()` marca el gasto con fecha y motivo. El historial
  queda auditable, que es lo mínimo cuando es dinero de otras personas.
- **Se guarda cómo se extrajo cada gasto** (la confianza por campo y si hubo
  corrección manual). Es el insumo del reporte de calidad de la Fase 4.
- **Quien registra y a quien se atribuye son columnas distintas.** La mamá
  puede registrar un gasto de la hija.
- **El esquema se verifica al arrancar.** `create_all` crea tablas nuevas pero
  no agrega columnas a las existentes, así que una base vieja arrancaría normal
  y se caería en el primer insert, en medio de una conversación. Ahora el bot
  no arranca y dice qué columnas faltan.

## Reporte de calidad

La pregunta que el proyecto se hace a sí mismo es *¿qué tan bien lee el LLM lo
que escribe la familia?*, y responderla honestamente exige una decisión de
diseño que no es obvia: **se registra cada extracción, no solo las que se
guardaron.**

Si la medición saliera de la tabla `gastos`, daría 100% de acierto siempre,
porque ahí solo llegan las confirmadas — sesgo del superviviente. Por eso
existe la tabla `extracciones`, que guarda el veredicto (confirmada o no) y
las confianzas por campo. A propósito **no** guarda montos ni el texto del
mensaje: son gastos que nadie confirmó, y para medir calidad no hacen falta.

Tres métricas salen de ahí:

- **Acierto directo** (la métrica del proyecto): de todos los mensajes leídos,
  cuántos se guardaron tal cual, sin rechazo y sin tocar ningún botón de
  corrección. Las confianzas registradas son las del LLM, no las que quedan
  después de que una persona corrige.
- **Tasa de confirmación:** cuántos se guardaron, con o sin corrección.
- **Tasa sin dudas:** cuántas extracciones salieron con los cuatro campos en
  confianza alta.

## Privacidad

El repo es público. La base de datos, los exports y el `.env` están en
`.gitignore` desde el primer commit, y `tests/test_privacy.py` verifica que
sigan excluidos. Los ejemplos y capturas usan solo datos sintéticos.

## Decisiones y limitaciones

- **Plataforma: Telegram.** Tiene API gratuita y no pide aprobación. Hoy los
  usuarios no usan Telegram, así que instalarlo es una fricción de adopción
  real: justo lo que el proyecto busca minimizar. WhatsApp se descartó
  porque la Business API exige aprobación de Meta y no apunta a uso personal.
- **LLM: Gemini** (`gemini-3.5-flash-lite`), por su salida estructurada
  nativa. El modelo importa más de lo esperado. Medido con el prompt real de
  este proyecto, mismo mensaje:

  | Modelo | Tiempo | Resultado |
  |---|---|---|
  | `gemini-3.8-flash` | 109 s | HTTP 429: 20 requests/día en el plan gratuito |
  | `gemini-3.5-flash` | 15.3 s | ok |
  | `gemini-3.5-flash-lite` | **2.8 s** | ok |

  El límite de 20 requests diarios de los modelos grandes hace inviable un bot
  familiar: tres personas registrando gastos lo agotan en un día. `flash-lite`
  es además cinco veces más rápido. El parser depende de un protocolo
  `ClienteLLM`, así que cambiar de modelo o de proveedor (`GEMINI_MODELO` en
  `.env`) no toca la lógica de verificación.
- **La cuota diaria es el límite real, no la velocidad.** Con `flash-lite`
  una extracción toma ~3 s, pero el plan gratuito tiene un tope de requests
  por día que se ve en https://aistudio.google.com/rate-limit. Al agotarse,
  el bot lo dice explícitamente ("vuelve mañana") en vez de fingir que no
  entendió. Cuántos gastos diarios aguanta la familia es algo que hay que
  medir con uso real.
- **Solo dólares.** No se guarda la moneda; se asume USD (Ecuador).
- **La confianza que el modelo declara no es estable.** El mismo mensaje sin
  dueño ("200 dólares supermercado") salió una vez con confianza `alta` y otra
  con `baja` en la atribución. Los dos resultados son seguros (el gasto queda
  del remitente, con ❓), pero es ruido que la tasa sin dudas va a registrar.
- **"X se compró Y" cuenta como que X asume el costo.** Si la hija se compró
  ropa con la tarjeta de los papás, el bot la atribuye a la hija con confianza
  alta. Se ve en la confirmación ("De: Hermana") y se corrige con un toque,
  pero no lleva ❓.
- **Un gasto por mensaje.** "20 de gasolina y 8 del almuerzo" se extrae como
  uno solo y queda marcado con confianza baja para que la persona corrija.
