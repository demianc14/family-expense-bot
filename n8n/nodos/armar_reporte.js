// Reporte determinista: los totales de la familia no pasan por el LLM.
const preparar = $("Preparar").first().json;
const mesPedido = $("Revisar agente").isExecuted
  ? $("Revisar agente").first().json.mes || preparar.mes_actual
  : preparar.mes || preparar.mes_actual;
const [anio, mes] = mesPedido.split("-").map(Number);

const filas = $("Leer gastos")
  .all()
  .map((i) => i.json)
  .filter((f) => f.id !== undefined);
const extracciones = $("Leer extracciones")
  .all()
  .map((i) => i.json)
  .filter((e) => e.id !== undefined && e.creado_en && e.creado_en.startsWith(mesPedido));

return [{ json: { mensaje: reporte(reporteMes(filas, anio, mes), calcularCalidad(extracciones)) } }];
