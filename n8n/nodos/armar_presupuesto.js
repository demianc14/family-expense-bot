// Presupuesto de quien escribe, del mes en curso. Determinista: no pasa por el LLM.
const preparar = $("Preparar").first().json;
const ingreso = preparar.ingresos[preparar.remitente];
if (!ingreso) return [{ json: { mensaje: sinPresupuesto() } }];

const [anio, mes] = preparar.mes_actual.split("-").map(Number);
const filas = $("Leer gastos del presupuesto")
  .all()
  .map((i) => i.json)
  .filter((f) => f.id !== undefined);
const delMes = resumenPresupuesto(filas, TAXONOMIA, preparar.remitente, ingreso, anio, mes);
return [{ json: { mensaje: presupuesto(delMes) } }];
