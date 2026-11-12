# Presupuesto 30/30/20/20 y “¿puedo gastar esto?”

[← README](../README.md) · [Decisiones](decisiones.md) · [Presupuesto](presupuesto.md) · [Calidad](calidad.md) · [Evaluación](evaluacion.md) · [Mini App](mini-app.md) · [n8n](../n8n/README.md)

El núcleo de finanzas personales: cómo se reparte el ingreso y cómo se decide si conviene un gasto.

## Cómo funciona

Con un ingreso mensual configurado, el bot reparte ese ingreso en cuatro
bolsillos y lleva la cuenta de cuánto se ha gastado en cada uno.

**Cómo se usa en el día a día.** El día que entra el sueldo, el 30% sale
directo a ahorro e inversión y deja de estar disponible: por eso ese bolsillo
no registra gastos, solo dice cuánto apartar. Con lo que queda se vive el mes,
y cada gasto que se confirma descuenta del bolsillo que le corresponde.

| Bolsillo | % | Qué cuenta contra él |
|---|---|---|
| Ahorro | 30 | Nada: se aparta, no se gasta. El bot dice cuánto apartar. |
| Necesarios | 30 | alimentación, transporte, educación, mascotas, hogar, deudas/cuotas |
| Fondo personal | 20 | la categoría `personal` (cursos, metas), o cualquier gasto que diga "del fondo" |
| Lujos | 20 | comida fuera, entretenimiento, extras, ropa |

```bash
# .env (nunca en el repo): rol:monto, separados por coma
INGRESOS_MENSUALES=demian:1200
```

- **Ingreso del mes = fijo + extras.** Los ingresos extra que se registran en
  la Mini App (trabajo extra, bono, regalo…) se suman al fijo en el mes de su
  fecha, y se reparte la suma con los mismos porcentajes. Sin extras, el
  presupuesto es exactamente el de siempre. `/presupuesto` muestra el desglose:
  "Ingreso: $1,350.00 ($1,200.00 fijo + $150.00 extra)". El fijo sigue siendo el
  sueldo: cambiarlo es cambiar `.env`.

- **El reparto es config.** Los bolsillos, sus porcentajes y qué categorías
  caen en cada uno viven en `config/categories.yaml`. Al cargar se valida que
  los porcentajes sumen 100, que ninguna categoría esté en dos bolsillos y que
  ningún marcador apunte a dos. El monto del ingreso no va ahí, porque el
  archivo es público.
- **"Del fondo" se dice, no se deduce.** Un gasto cuenta contra el bolsillo de
  su categoría, salvo que el mensaje traiga un marcador de otro bolsillo ("30
  de un libro, del fondo"). Es la misma regla que para la casa: el código busca
  la frase en el texto, el LLM no decide.
- **`otros` toma el bolsillo que se le diga.** No tiene uno propio, así que la
  Mini App pide elegirlo y lo guarda como frase en el texto (`marcadores_otros`
  del yaml: "de necesarios", "de lujos"). Esas frases solo valen para `otros`:
  "pan, de lujos" sigue contando en Necesarios. Son raras a propósito, para que
  "un reloj de lujo" no mueva nada. En el chat también funcionan ("15 de una
  cosa, de lujos"), y n8n aplica la misma regla.
- **Solo cuentan tus gastos:** los atribuidos a la persona con ingreso. Un gasto
  de la casa o de otra persona no toca tu presupuesto aunque lo registres tú.
- **Dónde se ve:** la confirmación dice contra qué bolsillo cuenta el gasto; el
  aviso de guardado dice cuánto queda en ese bolsillo del mes (o cuánto te
  pasaste); `/presupuesto` muestra los cuatro. Al cruzar el 80% o el 100%,
  llega además una [alerta](#alertas).
- **Sin cambios en la base.** El bolsillo se calcula al leer, a partir de la
  categoría y del texto ya guardados. Una base existente funciona sin migrar;
  la contracara es que si cambias el reparto en el yaml, los meses pasados se
  recalculan con el reparto nuevo.
- **Centavos exactos.** El reparto se hace en centavos enteros y los centavos
  que sobran del redondeo van a los primeros bolsillos: 1000.01 repartido en
  30/30/20/20 suma exactamente 1000.01.

## ¿Puedo gastar esto?

Antes de gastar se le puede preguntar al bot: `/puedo 30 concierto` en Python,
o "¿puedo gastar 30 en un concierto?" en lenguaje natural en n8n. El LLM solo
extrae el gasto, con el monto tal como está escrito (si no está, no opina), y
la respuesta la calcula el código con el presupuesto del mes, por el **ritmo**
del bolsillo:

| Veredicto | Cuándo |
|---|---|
| ❌ No | El bolsillo se pasaría. Si en el fondo personal sí cabe, lo sugiere ("di 'del fondo' al registrarlo"). |
| ⏸️ Difiérelo | Cabe, pero deja el bolsillo con menos de lo que le toca a los días que faltan del mes (asignado × días restantes ÷ días del mes). |
| ✅ Sí | Cabe y deja al menos eso. |

> *"⏸️ Mejor difiérelo. Te alcanza, pero en Lujos quedarías con $15.00, y a
> los 14 días que faltan del mes les tocan $37.33."* (datos sintéticos)

Una consulta no guarda nada, no pide confirmación ni cuenta como intento en
el reporte de calidad. Es aritmética sobre el presupuesto que uno mismo
definió, no asesoría financiera. La regla del ritmo es estricta a principio de
mes: el día 2 casi cualquier gasto grande sale "difiérelo", porque todavía
faltan casi todos los días. Es a propósito (gastar parejo), pero conviene
saberlo.

## Alertas

Al guardar un gasto que hace cruzar el 80% de su bolsillo, el bot manda un
mensaje aparte al chat, y otro al llegar al 100%. Se guarde por el chat o por la Mini App,
el aviso llega igual, como notificación de Telegram:

> *"🔔 Lujos de octubre llegó al 80%: llevas $204.00 de $240.00. Quedan $36.00
> para los 15 días que faltan."* (datos sintéticos)

- **Los umbrales son config**: `alertas: [80, 100]` en `config/categories.yaml`.
  Una lista vacía apaga los avisos.
- **Avisa al cruzar, no al estar arriba.** El cruce sale del antes y el después
  del mismo gasto (`alerta_tras_gasto`), sin guardar estado: un reinicio no
  repite avisos, y quien ya iba en 85% no vuelve a recibir el del 80%. Si un
  gasto cruza los dos umbrales de una vez, llega solo el del 100%.
- **El porcentaje es sobre el presupuesto con ingresos extra.** La alerta usa
  el mismo `resumen_presupuesto` que la pestaña Bolsillos: con $150 extra,
  Lujos pasa de $240 a $270 y el 80% de $192 a $216.
- **Editar no repite un aviso.** Al editar, el "antes" es el bolsillo con el
  gasto viejo (`ya_contaba`): corregir la descripción no avisa; subir el monto
  hasta cruzar otro umbral, sí.
- **Solo el mes en curso.** Un gasto atrasado del mes anterior no avisa:
  ese mes ya cerró y el aviso no serviría de nada.
- **Desde la Mini App, el aviso sale después de responder.** Si Telegram falla,
  queda en el log y el gasto igual está guardado.

Limitación: anular un gasto no "desavisa". Si el gasto que cruzó el 80% se
anula, la alerta ya llegó y no se corrige.

## De dónde sale el reparto

La idea viene del video de Mark Tilbury
[*How To Manage Your Money Like The 1%*](https://www.youtube.com/watch?v=QThz1B8SHmc),
que propone repartir el ingreso en 25/15/50/10. El de este bot es una
adaptación personal:

| Tilbury | % | Acá | % |
|---|---|---|---|
| Crecimiento (inversión) | 25 | Ahorro e inversión | 30 |
| Estabilidad (fondo de emergencia) | 15 | Fondo personal | 20 |
| Esenciales | 50 | Necesarios | 30 |
| Disfrute | 10 | Lujos | 20 |

La diferencia más grande no es de números: en Tilbury el 15% es un colchón para
imprevistos que normalmente no se toca, y acá el fondo personal sí se gasta, en
cosas para uno mismo (cursos, metas). Volver al reparto original es cambiar
cuatro porcentajes en `config/categories.yaml`; la validación exige que sumen 100.

Limitaciones: el sueldo es fijo (si un mes cobras distinto, hay que cambiar
`.env`; lo que cobres de más se puede registrar como ingreso extra), n8n no ve
los ingresos extra, el ahorro no se registra (el bot no sabe si de verdad lo apartaste),
y las categorías que no están en ningún bolsillo (`mesada`, y `otros` cuando
no trae un `marcadores_otros`) se muestran aparte como "sin bolsillo" en vez de
repartirse. En el chat, un gasto de `otros` sin frase sigue quedando ahí: el
bot no pregunta el bolsillo.
