# family-expense-bot

Bot de Telegram para que una familia registre gastos en texto libre
("200 dólares mercado"). El sistema extrae monto, categoría, fecha y
descripción con un LLM gratuito, pide confirmación y guarda el gasto para un
reporte mensual.

> **Estado: Fase 3.** El flujo completo funciona: mensaje → extracción →
> confirmación humana → guardado. Falta el reporte mensual (Fase 4).

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

Todo lo que no quede en `alta` se le muestra a la persona antes de guardar.
`GastoExtraido` es inmutable y lleva `notas` explicando cada degradación.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env   # pega aquí el token de @BotFather
family-expense-bot --check   # valida el token contra la API de Telegram
family-expense-bot --extraer "200 dólares mercado"   # prueba el parser (usa Gemini)
family-expense-bot           # levanta el bot (modo polling)
```

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
2. El bot responde "Leyendo tu mensaje..." al instante, porque la extracción
   puede tardar más de un minuto.
3. Se extrae con un límite de 45 s impuesto por el bot. Si se pasa, o si el
   LLM falla, se avisa y se pide reintentar. El bot nunca muere por eso.
4. Se muestra lo entendido, con ❓ en cada campo dudoso y las notas que lo
   explican, más dos botones.
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
- **Los IDs de Telegram autorizados van en `.env`**, no en `config/`: son
  datos personales y este repo es público.

## Privacidad

El repo es público. La base de datos, los exports y el `.env` están en
`.gitignore` desde el primer commit, y `tests/test_privacy.py` verifica que
sigan excluidos. Los ejemplos y capturas usan solo datos sintéticos.

## Decisiones y limitaciones

- **Plataforma: Telegram.** Tiene API gratuita y no pide aprobación. Hoy los
  usuarios no usan Telegram, así que instalarlo es una fricción de adopción
  real: justo lo que el proyecto busca minimizar. WhatsApp se descartó
  porque la Business API exige aprobación de Meta y no apunta a uso personal.
- **LLM: Gemini** (`gemini-3.8-flash`), por su salida estructurada nativa.
  Google ya no publica las cuotas del free tier: son por cuenta y se ven en
  https://aistudio.google.com/rate-limit. Si el límite diario resulta bajo
  para el uso de la familia, hay que medirlo antes de prometer que el bot
  responde siempre. El parser depende de un protocolo `ClienteLLM`, así que
  cambiar de proveedor no toca la lógica de verificación.
- **Latencia alta y variable.** Medido contra Gemini: 14 s para un mensaje
  simple y 84 s para uno ambiguo. El timeout de 30 s que pide el SDK no cortó
  esa llamada de 84 s, así que en la Fase 3 el corte tiene que imponerlo el
  bot, y avisarle a la persona que está procesando.
- **Solo dólares.** No se guarda la moneda; se asume USD (Ecuador).
- **Un gasto por mensaje.** "20 de gasolina y 8 del almuerzo" se extrae como
  uno solo y queda marcado con confianza baja para que la persona corrija.
