// La compuerta. Vuelve a verificar desde cero lo que afirma el agente, sin mirar
// lo que dijeron las tools. Es la única fuente de las confianzas que ve la persona.
const preparar = $("Preparar").first().json;
const { respuesta, aviso } = $json;
const conAviso = (texto) => [texto, aviso].filter(Boolean).join("\n\n");

// El hilo cumplió su función: se cierra ANTES de esperar la confirmación.
const estado = $getWorkflowStaticData("global");
cerrarHilo(estado.hilos || {}, preparar.chat_id);

let gasto;
try {
  gasto = verificarGasto({
    respuesta,
    texto: preparar.texto_respaldo,
    remitente: preparar.remitente,
    hoy: preparar.hoy,
    taxonomia: TAXONOMIA,
  });
} catch (e) {
  if (e.name !== "ErrorDeExtraccion") throw e;
  console.log(`Respuesta fuera de contrato: ${e.message}`);
  return [{ json: { estado: "contrato_roto", mensaje: conAviso(MENSAJES.contrato) } }];
}

if (gasto.monto === null) {
  return [
    {
      json: {
        estado: "sin_monto",
        resultado: "no_ofrecible",
        gasto,
        mensaje: conAviso(faltaMonto(gasto, TAXONOMIA.etiquetas)),
      },
    },
  ];
}

return [
  {
    json: {
      estado: "ofrecer",
      gasto,
      chat_id: preparar.chat_id,
      usuario_id: preparar.usuario_id,
      mensaje: conAviso(pedirConfirmacion(gasto, TAXONOMIA.etiquetas)),
    },
  },
];
