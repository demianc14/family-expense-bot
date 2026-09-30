// La persona dijo que sí y la escritura falló: se le dice, y cuenta como intento.
const { gasto } = $("Decidir").first().json;
const fallo = clasificarFallo($json.error ?? $json);
console.log(`No se pudo guardar el gasto: ${fallo.detalle}`);
return [
  {
    json: {
      resultado: "error_al_guardar",
      gasto,
      mensaje: errorAlGuardar(fallo.detalle || "error desconocido"),
    },
  },
];
