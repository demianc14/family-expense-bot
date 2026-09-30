// Embudo único: todo resultado pasa por acá y se registra una sola vez.
const { resultado, gasto, mensaje } = $json;
return [
  {
    json: {
      fila: filaExtraccion(resultado, gasto ?? null, new Date().toISOString()),
      mensaje: mensaje || "",
    },
  },
];
