// Texto que ve la persona: port de bot/mensajes.py. Lo dudoso se marca, no se
// esconde. `resumen`, `faltaMonto`, `guardado`, `descartado`, `reporte`,
// `presupuesto` y `lineaDeBolsillo` dan el mismo texto que Python
// (tests/test_n8n_paridad.py); el resto es propio de n8n.

"use strict";

const DUDOSO = "❓";

/** `${x:,.2f}` de Python, desde un monto decimal canónico ("1200.5" → "1,200.50"). */
function formatoDinero(monto) {
  const [entero, decimales = ""] = monto.split(".");
  const conComas = entero.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${conComas}.${decimales.padEnd(2, "0")}`;
}

function formatoCentavos(centavos) {
  const texto = String(centavos).padStart(3, "0");
  return formatoDinero(`${texto.slice(0, -2)}.${texto.slice(-2)}`);
}

/** "YYYY-MM-DD" → "dd/mm/YYYY". */
function formatoFecha(iso) {
  const [anio, mes, dia] = iso.split("-");
  return `${dia}/${mes}/${anio}`;
}

function marca(gasto, campo) {
  return gasto[`confianza_${campo}`] === "alta" ? "" : ` ${DUDOSO}`;
}

/** `bolsillo`: etiqueta del bolsillo, solo si quien asume el gasto tiene presupuesto. */
function resumen(gasto, etiquetas = {}, bolsillo = null) {
  const monto = gasto.monto !== null ? `$${formatoDinero(gasto.monto)}` : "—";
  const fecha = gasto.fecha ? formatoFecha(gasto.fecha) : "—";
  const quien = etiquetas[gasto.atribuido_a] ?? gasto.atribuido_a;
  const lineas = [
    "Entendí esto:",
    "",
    `Monto: ${monto}${marca(gasto, "monto")}`,
    `Categoría: ${gasto.categoria}${marca(gasto, "categoria")}`,
    `Fecha: ${fecha}${marca(gasto, "fecha")}`,
    `De: ${quien}${marca(gasto, "atribuido_a")}`,
    ...(bolsillo !== null ? [`Bolsillo: ${bolsillo}`] : []),
    `Descripción: ${gasto.descripcion}`,
  ];
  if (gasto.notas.length) lineas.push("", ...gasto.notas.map((n) => `${DUDOSO} ${n.texto}`));
  return lineas.join("\n");
}

/** Sin botones para cambiar la atribución: corregir es decir que no y reescribir. */
function pedirConfirmacion(gasto, etiquetas = {}, bolsillo = null) {
  return (
    `${resumen(gasto, etiquetas, bolsillo)}\n\n` +
    "¿Lo guardo? Si algo está mal, toca No y escríbemelo de nuevo."
  );
}

function faltaMonto(gasto, etiquetas = {}) {
  return (
    `${resumen(gasto, etiquetas)}\n\n` +
    "No encontré el monto, así que no puedo guardarlo. " +
    "¿Me lo escribes de nuevo incluyendo el valor?"
  );
}

/** `fila` es la fila de `gastos` que se guardó; `presupuesto`, la línea de lineaDeBolsillo(). */
function guardado(fila, etiquetas = {}, presupuesto = null) {
  const quien = etiquetas[fila.atribuido_a] ?? fila.atribuido_a;
  const texto =
    "Guardado ✅\n\n" +
    `$${formatoCentavos(fila.monto_centavos)} en ${fila.categoria}, de ${quien}, ` +
    `el ${formatoFecha(fila.fecha)}.`;
  return presupuesto ? `${texto}\n\n${presupuesto}` : texto;
}

function descartado() {
  return "Listo, no guardé nada. ¿Me lo escribes de otra forma?";
}

function expirado() {
  return "Pasó mucho tiempo sin respuesta y no guardé ese gasto. Si era real, escríbemelo de nuevo.";
}

function errorAlGuardar(detalle) {
  return (
    "⚠️ Dijiste que sí, pero no pude guardar el gasto: no quedó registrado. " +
    `Avísale a quien mantiene el bot.\nMotivo: ${detalle}`
  );
}

const BIENVENIDA =
  "Hola 👋 Escríbeme tus gastos como los dirías normalmente, " +
  'por ejemplo "200 dólares supermercado" o "pagué 15 de taxi ayer".\n\n' +
  "Si no dices de quién es, asumo que es tuyo. Para gastos compartidos, " +
  'dilo: "gasolina de la casa".\n\n' +
  "Te muestro lo que entendí y solo guardo si me dices que sí.\n\n" +
  "Escribe /reporte para ver el total del mes y /presupuesto para ver " +
  "cuánto te queda en cada bolsillo.";

const MESES = [
  "enero", "febrero", "marzo", "abril", "mayo", "junio",
  "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
];

/** `{:.0%}` de Python: redondeo al par en los empates exactos, no hacia arriba. */
function porcentaje(fraccion) {
  const v = fraccion * 100;
  const empate = Number.isInteger(v * 2) && !Number.isInteger(v);
  const n = empate ? (Math.floor(v) % 2 === 0 ? Math.floor(v) : Math.ceil(v)) : Number(v.toFixed(0));
  return `${n}%`;
}

/** `resumen` de reporteMes() y `calidad` de calcularCalidad(). */
function reporte(resumenMes, calidad) {
  const titulo = `Gastos de ${MESES[resumenMes.mes - 1]} ${resumenMes.anio}`;
  if (resumenMes.cantidad === 0) return `${titulo}\n\nTodavía no hay gastos registrados este mes.`;

  const lineas = [
    titulo,
    "",
    `Total: $${formatoCentavos(resumenMes.total_centavos)} en ${resumenMes.cantidad} gasto(s)`,
    "",
  ];
  const ancho = Math.max(...resumenMes.por_categoria.map((c) => c.categoria.length));
  for (const c of resumenMes.por_categoria) {
    lineas.push(
      `  ${c.categoria.padEnd(ancho)}  $${formatoCentavos(c.total_centavos).padStart(9)}  (${c.cantidad})`,
    );
  }
  if (calidad.intentos) {
    const directas = calidad.confirmadas - calidad.corregidas;
    lineas.push(
      "",
      `De ${calidad.intentos} mensaje(s) que leí, ${directas} ` +
        `(${porcentaje(directas / calidad.intentos)}) los entendí bien a la primera.`,
    );
  }
  return lineas.join("\n");
}

// --- Presupuesto ---------------------------------------------------------------

const disponible = (b) => b.asignado_centavos - b.gastado_centavos;

/** Cómo quedó el bolsillo después de guardar. null = el gasto no cuenta en ninguno. */
function lineaDeBolsillo(estado, mes) {
  if (estado === null) return "Este gasto no cuenta en ningún bolsillo del presupuesto.";
  const nombre = `${estado.etiqueta} de ${MESES[mes - 1]}`;
  if (disponible(estado) < 0) {
    return (
      `⚠️ ${nombre}: te pasaste $${formatoCentavos(-disponible(estado))} ` +
      `(llevas $${formatoCentavos(estado.gastado_centavos)} de $${formatoCentavos(estado.asignado_centavos)}).`
    );
  }
  return `${nombre}: quedan $${formatoCentavos(disponible(estado))} de $${formatoCentavos(estado.asignado_centavos)}.`;
}

/** `r` es el resultado de resumenPresupuesto(). */
function presupuesto(r) {
  const lineas = [
    `Presupuesto de ${MESES[r.mes - 1]} ${r.anio}`,
    `Ingreso: $${formatoCentavos(r.ingreso_centavos)}`,
    "",
  ];
  for (const b of r.bolsillos) {
    const cabeza = `${b.etiqueta} (${b.porcentaje}%)`;
    const llevado = `$${formatoCentavos(b.gastado_centavos)} de $${formatoCentavos(b.asignado_centavos)}`;
    if (!b.se_gasta) lineas.push(`${cabeza}: aparta $${formatoCentavos(b.asignado_centavos)}`);
    else if (disponible(b) < 0) {
      lineas.push(`${cabeza}: ${llevado} · ⚠️ te pasaste $${formatoCentavos(-disponible(b))}`);
    } else lineas.push(`${cabeza}: ${llevado} · quedan $${formatoCentavos(disponible(b))}`);
  }
  if (r.sin_bolsillo_centavos) {
    lineas.push(
      "",
      `Sin bolsillo: $${formatoCentavos(r.sin_bolsillo_centavos)} (${r.categorias_sin_bolsillo.join(", ")})`,
    );
  }
  return lineas.join("\n");
}

function sinPresupuesto() {
  return (
    "No tengo tu ingreso mensual, así que no puedo armar tu presupuesto. " +
    "Quien mantiene el bot lo configura en INGRESOS_MENSUALES."
  );
}

// <solo-tests>
module.exports = {
  BIENVENIDA,
  descartado,
  errorAlGuardar,
  expirado,
  faltaMonto,
  formatoCentavos,
  formatoDinero,
  guardado,
  lineaDeBolsillo,
  pedirConfirmacion,
  presupuesto,
  porcentaje,
  reporte,
  resumen,
  sinPresupuesto,
};
// </solo-tests>
