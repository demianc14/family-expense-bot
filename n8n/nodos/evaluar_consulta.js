// ¿Conviene este gasto? Mismo camino que /puedo en Python: el monto pasa por la
// compuerta (tiene que estar escrito en el mensaje) y el veredicto lo calcula
// evaluarGasto() con el presupuesto. Nada se guarda ni cuenta como intento.
const preparar = $("Preparar").first().json;
const { respuesta, aviso } = $("Revisar agente").first().json;
const conAviso = (texto) => [texto, aviso].filter(Boolean).join("\n\n");

const ingreso = preparar.ingresos[preparar.remitente];
if (!ingreso) return [{ json: { mensaje: conAviso(sinPresupuesto()) } }];

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
  console.log(`Consulta fuera de contrato: ${e.message}`);
  return [{ json: { mensaje: conAviso(MENSAJES.contrato) } }];
}
if (gasto.monto === null || gasto.confianza_monto === "baja") {
  return [{ json: { mensaje: conAviso(consultaSinMonto()) } }];
}

const [anio, mes] = preparar.mes_actual.split("-").map(Number);
const filas = $("Leer gastos de la consulta")
  .all()
  .map((i) => i.json)
  .filter((f) => f.id !== undefined);
const delMes = resumenPresupuesto(filas, TAXONOMIA, preparar.remitente, ingreso, anio, mes);
const consulta = evaluarGasto(
  delMes,
  TAXONOMIA,
  TAXONOMIA.bolsilloDe(gasto.categoria, gasto.texto_original),
  Number(aCentavosExactos(gasto.monto)),
  preparar.hoy,
);
return [
  {
    json: {
      mensaje: conAviso(
        respuestaConsulta(consulta, gasto.categoria, gasto.confianza_categoria !== "alta"),
      ),
    },
  },
];
