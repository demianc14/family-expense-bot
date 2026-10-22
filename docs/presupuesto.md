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

- **El reparto es config.** Los bolsillos, sus porcentajes y qué categorías
  caen en cada uno viven en `config/categories.yaml`. Al cargar se valida que
  los porcentajes sumen 100, que ninguna categoría esté en dos bolsillos y que
  ningún marcador apunte a dos. El monto del ingreso no va ahí, porque el
  archivo es público.
- **"Del fondo" se dice, no se deduce.** Un gasto cuenta contra el bolsillo de
  su categoría, salvo que el mensaje traiga un marcador de otro bolsillo ("30
  de un libro, del fondo"). Es la misma regla que para la casa: el código busca
  la frase en el texto, el LLM no decide.
- **Solo cuentan tus gastos:** los atribuidos a la persona con ingreso. Un gasto
  de la casa o de otra persona no toca tu presupuesto aunque lo registres tú.
- **Dónde se ve:** la confirmación dice contra qué bolsillo cuenta el gasto; el
  aviso de guardado dice cuánto queda en ese bolsillo del mes (o cuánto te
  pasaste); `/presupuesto` muestra los cuatro.
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

Limitaciones: el ingreso es fijo (si un mes cobras distinto, hay que cambiar
`.env`), el ahorro no se registra (el bot no sabe si de verdad lo apartaste),
y las categorías que no están en ningún bolsillo (`otros`, `mesada`) se
muestran aparte como "sin bolsillo" en vez de repartirse.
