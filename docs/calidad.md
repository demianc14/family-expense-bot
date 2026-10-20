# Calidad

[← README](../README.md) · [Decisiones](decisiones.md) · [Presupuesto](presupuesto.md) · [Calidad](calidad.md) · [Mini App](mini-app.md) · [n8n](../n8n/README.md)

Qué tan bien lee el LLM lo que escribe una persona, y cómo se prueba el resto.

## Reporte de calidad (la métrica del proyecto)

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

## Tests y linters

```bash
pytest && ruff check . && ruff format --check . && mypy
```

`pytest` incluye la paridad con la versión n8n y sus tests de `node --test`
(hace falta Node 24; sin Node, esos tests se saltan con un motivo visible).
Las pruebas E2E de n8n van aparte porque levantan Docker:

```bash
node --test n8n/e2e/e2e.test.mjs   # ~2 min: n8n real + Telegram y Gemini falsos
```
