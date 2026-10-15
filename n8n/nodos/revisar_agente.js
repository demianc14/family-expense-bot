// Qué decidió el agente, revisado por código. No se confía en que el agente
// cuente sus propios problemas: las tools que llamó se auditan acá.
const preparar = $("Preparar").first().json;
const ACCIONES = ["proponer", "preguntar", "reporte", "presupuesto", "responder"];

let salida = $json.output;
if (typeof salida === "string") {
  try {
    salida = JSON.parse(salida);
  } catch {
    salida = null;
  }
}
if (salida && typeof salida === "object" && salida.output && !salida.accion) salida = salida.output;

const problemas = auditarPasos($json.intermediateSteps);
const aviso = avisoDeProblemas(problemas);
const conAviso = (texto) => [texto, aviso].filter(Boolean).join("\n\n");
if (problemas.length) console.log(`Problemas con tools: ${JSON.stringify(problemas)}`);

const estado = $getWorkflowStaticData("global");
const cerrar = () => cerrarHilo(estado.hilos || {}, preparar.chat_id);

if (!salida || typeof salida !== "object" || !ACCIONES.includes(salida.accion)) {
  cerrar();
  return [{ json: { ruta: "fallo", mensaje: conAviso(MENSAJES.contrato), problemas } }];
}

const texto = typeof salida.mensaje === "string" ? strip(salida.mensaje) : "";
const vacioANull = (v) => (typeof v === "string" && strip(v) ? v : null);

if (salida.accion === "proponer") {
  // Solo lo que el agente afirma. Quién escribe, el texto y la fecha de hoy
  // los pone la compuerta desde el workflow, nunca desde el LLM.
  const respuesta = {
    monto: vacioANull(salida.monto),
    categoria: vacioANull(salida.categoria),
    fecha: vacioANull(salida.fecha),
    quien_paga: vacioANull(salida.quien_paga),
    descripcion: typeof salida.descripcion === "string" ? salida.descripcion : "",
    confianza_monto: salida.confianza_monto,
    confianza_categoria: salida.confianza_categoria,
    confianza_fecha: salida.confianza_fecha,
    confianza_quien_paga: salida.confianza_quien_paga,
  };
  return [{ json: { ruta: "proponer", respuesta, aviso, problemas } }];
}

if (salida.accion === "preguntar" && texto) {
  // El hilo sigue abierto: la respuesta de la persona tiene que poder respaldar el monto.
  return [{ json: { ruta: "enviar", mensaje: conAviso(texto), problemas } }];
}

cerrar();
if (salida.accion === "presupuesto") {
  return [{ json: { ruta: "presupuesto", aviso, problemas } }];
}
if (salida.accion === "reporte") {
  const pedido = typeof salida.mes === "string" ? strip(salida.mes) : "";
  const valido = /^\d{4}-(0[1-9]|1[0-2])$/.test(pedido) && pedido <= preparar.mes_actual;
  return [{ json: { ruta: "reporte", mes: valido ? pedido : preparar.mes_actual, aviso, problemas } }];
}

const respuestaFinal = texto || MENSAJES.contrato;
if (probableGastoNoRegistrado(salida.accion, preparar.texto_respaldo)) {
  return [
    {
      json: {
        ruta: "no_propuesto",
        resultado: "no_propuesto",
        gasto: null,
        mensaje: conAviso(`${respuestaFinal}\n\n${MENSAJES.sinRegistro}`),
        problemas,
      },
    },
  ];
}
return [{ json: { ruta: "enviar", mensaje: conAviso(respuestaFinal), problemas } }];
