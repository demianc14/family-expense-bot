// Solo se llega acá si la fila quedó escrita. Si quien asume el gasto tiene
// presupuesto, se le dice cuánto queda en ese bolsillo del mes.
const { gasto, fila_gasto } = $("Decidir").first().json;
const preparar = $("Preparar").first().json;

let lineaPresupuesto = null;
const ingreso = preparar.ingresos[fila_gasto.atribuido_a];
if (ingreso) {
  try {
    const [anio, mes] = fila_gasto.fecha.split("-").map(Number);
    const filas = $("Gastos tras guardar")
      .all()
      .map((i) => i.json)
      .filter((f) => f.id !== undefined);
    const delMes = resumenPresupuesto(filas, TAXONOMIA, fila_gasto.atribuido_a, ingreso, anio, mes);
    const nombre = TAXONOMIA.bolsilloDe(fila_gasto.categoria, fila_gasto.texto_original);
    lineaPresupuesto = lineaDeBolsillo(
      nombre ? delMes.bolsillos.find((b) => b.nombre === nombre) : null,
      mes,
    );
  } catch (e) {
    // Un fallo acá no tapa el "Guardado": el gasto ya está escrito.
    console.log(`No se pudo calcular el presupuesto después de guardar: ${e.message}`);
  }
}

return [
  {
    json: {
      resultado: "confirmada",
      gasto,
      mensaje: guardado(fila_gasto, TAXONOMIA.etiquetas, lineaPresupuesto),
    },
  },
];
