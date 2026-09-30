// Tool `verificar_gasto`: la misma compuerta, para que el agente pueda revisar
// antes de proponer. Lo que devuelve pasa por el LLM, así que no decide nada:
// la compuerta del flujo principal vuelve a verificar desde cero.
const e = $json;
const vacioANull = (v) => (typeof v === "string" && strip(v) ? v : null);
if (e.herramienta !== "verificar_gasto") {
  return [{ json: { ok: false, error: `Herramienta desconocida: ${e.herramienta}` } }];
}
try {
  const gasto = verificarGasto({
    respuesta: {
      monto: vacioANull(e.monto),
      categoria: vacioANull(e.categoria),
      fecha: vacioANull(e.fecha),
      quien_paga: vacioANull(e.quien_paga),
      descripcion: typeof e.descripcion === "string" ? e.descripcion : "",
      confianza_monto: e.confianza_monto,
      confianza_categoria: e.confianza_categoria,
      confianza_fecha: e.confianza_fecha,
      confianza_quien_paga: e.confianza_quien_paga,
    },
    texto: e.texto,
    remitente: e.remitente,
    hoy: e.hoy,
    taxonomia: TAXONOMIA,
  });
  return [
    {
      json: {
        ok: true,
        monto: gasto.monto,
        categoria: gasto.categoria,
        fecha: gasto.fecha,
        atribuido_a: gasto.atribuido_a,
        confianzas: {
          monto: gasto.confianza_monto,
          categoria: gasto.confianza_categoria,
          fecha: gasto.confianza_fecha,
          atribuido_a: gasto.confianza_atribuido_a,
        },
        notas: gasto.notas.map((n) => n.texto),
      },
    },
  ];
} catch (error) {
  return [{ json: { ok: false, error: error.message } }];
}
