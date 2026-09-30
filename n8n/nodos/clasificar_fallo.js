// Salida de error del agente: cuota, timeout, respuesta fuera de contrato u otro.
// La persona siempre se entera del motivo.
const preparar = $("Preparar").first().json;
const estado = $getWorkflowStaticData("global");
cerrarHilo(estado.hilos || {}, preparar.chat_id);

const fallo = clasificarFallo($json.error ?? $json);
console.log(`Falló el agente (${fallo.motivo}): ${fallo.detalle}`);
return [{ json: { mensaje: fallo.mensaje, motivo: fallo.motivo } }];
