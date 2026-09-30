// Qué respondió la persona. Tras el tiempo límite, Send and Wait devuelve su
// entrada sin `data.approved`: eso es "expirada", no "rechazada".
const { gasto } = $("Compuerta").first().json;
const preparar = $("Preparar").first().json;
const aprobado = $json.data ? $json.data.approved : undefined;

if (aprobado === true) {
  try {
    const fila = filaGasto(gasto, preparar.usuario_id, TAXONOMIA, new Date().toISOString());
    return [{ json: { estado: "guardar", gasto, fila_gasto: fila } }];
  } catch (e) {
    return [
      {
        json: {
          estado: "resultado",
          resultado: "error_al_guardar",
          gasto,
          mensaje: errorAlGuardar(e.message),
        },
      },
    ];
  }
}
if (aprobado === false) {
  return [{ json: { estado: "resultado", resultado: "rechazada", gasto, mensaje: descartado() } }];
}
return [{ json: { estado: "resultado", resultado: "expirada", gasto, mensaje: expirado() } }];
