// Solo se llega acá si la fila quedó escrita.
const { gasto, fila_gasto } = $("Decidir").first().json;
return [
  {
    json: {
      resultado: "confirmada",
      gasto,
      mensaje: guardado(fila_gasto, TAXONOMIA.etiquetas),
    },
  },
];
