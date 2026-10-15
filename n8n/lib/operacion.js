// Piezas deterministas propias del workflow n8n: quién escribe, el hilo de la
// conversación, fallos visibles, filas a guardar y el reporte del mes.
//
// Igual que verificacion.js: funciones puras, sin globals de n8n. El build las
// pega en los Code nodes, después de verificacion.js.

"use strict";

// <solo-tests>
const { CAMPOS, aCentavos, montosEnTexto, repr, strip } = require("./verificacion.js");
// </solo-tests>

// Única tool del agente. El reporte no es tool a propósito: los totales de la
// familia no pasan por el LLM (el agente solo pide `accion: "reporte"`).
const HERRAMIENTAS = ["verificar_gasto"];

/** La tool con la que el Agent de n8n entrega la salida estructurada. No es nuestra. */
const TOOL_SALIDA = "format_final_json_response";

const TTL_HILO_MS = 30 * 60 * 1000;
const MAX_MENSAJES_HILO = 5;

// --- Quién escribe (config.py: TELEGRAM_USUARIOS) ----------------------------

/**
 * "id:rol,id:rol" → { id: rol }. Falla rápido, igual que la versión Python:
 * una allowlist mal escrita no puede dejar pasar a nadie por accidente.
 */
function parsearUsuarios(texto, taxonomia) {
  const usuarios = {};
  for (const parte of (texto || "").split(",")) {
    if (!strip(parte)) continue;
    const i = parte.indexOf(":");
    const idCrudo = strip(i === -1 ? parte : parte.slice(0, i));
    const rol = i === -1 ? "" : strip(parte.slice(i + 1)).toLowerCase();
    if (i === -1 || !rol) {
      throw new Error(`TELEGRAM_USUARIOS: ${repr(strip(parte))} no tiene la forma id:rol`);
    }
    if (!/^\d+$/.test(idCrudo)) {
      throw new Error(`TELEGRAM_USUARIOS: ${repr(idCrudo)} no es un ID numérico`);
    }
    const id = String(BigInt(idCrudo));
    if (id in usuarios) throw new Error(`TELEGRAM_USUARIOS: el ID ${id} aparece dos veces`);
    if (!taxonomia.personas.includes(rol)) {
      throw new Error(
        `TELEGRAM_USUARIOS: el rol ${repr(rol)} no es una persona de la taxonomía ` +
          `(${taxonomia.personas.join(", ")})`,
      );
    }
    usuarios[id] = rol;
  }
  return usuarios;
}

/** Rol de quien escribe, o null si no está autorizado. */
function remitenteDe(idTelegram, usuarios) {
  if (idTelegram === null || idTelegram === undefined) return null;
  return usuarios[String(idTelegram)] ?? null;
}

// --- Hilo de conversación ------------------------------------------------------
//
// El monto se verifica contra lo que la persona escribió en el hilo abierto, no
// solo contra el último mensaje: si el agente preguntó "¿cuánto fue?", la
// respuesta "40" tiene que respaldar el monto. El hilo se cierra al terminar
// una propuesta (guardada o no) o una respuesta, así que un monto ya confirmado
// nunca respalda un gasto nuevo.
//
// `hilos` es el estado persistente del workflow ($getWorkflowStaticData).

/**
 * Agrega el mensaje al hilo del chat. Si el hilo anterior quedó abierto (el
 * agente preguntó y nadie respondió a tiempo), lo reporta como abandonado:
 * también cuenta para el reporte de calidad.
 *
 * `hiloId` es además la sesión de la memoria del agente: un hilo nuevo empieza
 * con la memoria vacía, sin tener que borrarla a mano.
 */
function agregarAlHilo(hilos, chatId, texto, ahoraMs) {
  const clave = String(chatId);
  const previo = hilos[clave];
  let abandonado = false;
  if (
    previo &&
    (ahoraMs - previo.ultimoMs > TTL_HILO_MS || previo.mensajes.length >= MAX_MENSAJES_HILO)
  ) {
    abandonado = true;
    delete hilos[clave];
  }
  const hilo = hilos[clave] || { id: `${clave}:${ahoraMs}`, mensajes: [], ultimoMs: ahoraMs };
  hilo.mensajes.push(texto);
  hilo.ultimoMs = ahoraMs;
  hilos[clave] = hilo;
  return { abandonado, hiloId: hilo.id, textoRespaldo: hilo.mensajes.join("\n") };
}

function cerrarHilo(hilos, chatId) {
  delete hilos[String(chatId)];
}

// --- Fallos visibles -----------------------------------------------------------

const MENSAJES = {
  procesando: "Leyendo tu mensaje...",
  cuota:
    "Por hoy ya no puedo leer más mensajes automáticamente " +
    "(se acabó la cuota gratuita del servicio). Intenta mañana.",
  timeout: "El servicio está lento y no alcanzó a responder. ¿Lo intentas de nuevo?",
  contrato: "No pude entender ese mensaje. ¿Me lo escribes de otra forma?",
  desconocido: "Algo falló de mi lado y no pude procesar tu mensaje. ¿Lo intentas de nuevo?",
  sinRegistro: "No registré ningún gasto con este mensaje.",
};

/**
 * Qué falló, en palabras de la persona. `error` es lo que n8n deja en la salida
 * de error del nodo del agente ({ message, description, httpCode, ... }).
 */
function clasificarFallo(error) {
  const e = typeof error === "string" ? { message: error } : error || {};
  const texto = [e.message, e.description, e.name, e.httpCode, e.code]
    .filter((v) => v !== undefined && v !== null)
    .join(" ");
  let motivo = "desconocido";
  // n8n reescribe el 429 del proveedor como "too many requests".
  if (/\b429\b|RESOURCE_EXHAUSTED|quota|too many requests/i.test(texto)) motivo = "cuota";
  else if (/timed? ?out|timeout|ETIMEDOUT|ECONNABORTED|aborted/i.test(texto)) motivo = "timeout";
  else if (/output parser|parse|contrato|schema|JSON/i.test(texto)) motivo = "contrato";
  return { motivo, mensaje: MENSAJES[motivo], detalle: texto.slice(0, 300) };
}

/** La tool responde con los items de su último nodo: un arreglo de un elemento. */
function interpretarObservacion(observacion) {
  let valor = observacion;
  if (typeof valor === "string") {
    try {
      valor = JSON.parse(valor);
    } catch {
      return null;
    }
  }
  if (Array.isArray(valor)) valor = valor.length === 1 ? valor[0] : null;
  if (valor && typeof valor === "object" && valor.json && typeof valor.json === "object") {
    valor = valor.json;
  }
  return valor && typeof valor === "object" ? valor : null;
}

/**
 * Revisa las llamadas a tools que hizo el agente. No se confía en que el agente
 * cuente sus propios problemas: una tool inexistente o caída queda registrada
 * y se le avisa a la persona.
 *
 * `pasos` son los intermediateSteps del nodo AI Agent: [{ action: { tool }, observation }].
 * Nuestras tools siempre responden JSON con `ok`; cualquier otra cosa es un fallo.
 */
function auditarPasos(pasos) {
  const problemas = [];
  for (const paso of pasos || []) {
    const tool = paso && paso.action ? paso.action.tool : undefined;
    if (tool === TOOL_SALIDA) continue;
    if (!HERRAMIENTAS.includes(tool)) {
      problemas.push({ tool: tool ?? null, motivo: "tool_inexistente" });
      continue;
    }
    const obs = interpretarObservacion(paso.observation);
    if (!obs || obs.ok !== true) {
      const detalle = obs && obs.error ? String(obs.error) : String(paso.observation ?? "");
      problemas.push({ tool, motivo: "tool_fallo", detalle: detalle.slice(0, 200) });
    }
  }
  return problemas;
}

function avisoDeProblemas(problemas) {
  if (!problemas.length) return null;
  const partes = problemas.map((p) =>
    p.motivo === "tool_inexistente"
      ? `intenté usar una herramienta que no existe (${p.tool ?? "sin nombre"})`
      : `la herramienta ${p.tool} falló`,
  );
  return (
    `⚠️ Aviso: ${partes.join("; ")}. ` +
    "Lo que te muestro lo revisó igual el verificador, pero cuéntale a quien mantiene el bot."
  );
}

/**
 * El agente puede decidir no proponer ningún gasto. Si el mensaje traía un
 * número, eso se dice y se registra: el agente no puede "olvidar" un gasto en silencio.
 */
function probableGastoNoRegistrado(accion, texto) {
  return accion !== "proponer" && montosEnTexto(texto).size > 0;
}

// --- Filas a guardar -----------------------------------------------------------

/**
 * Fila de `gastos`. Solo se llama desde la rama "aprobado" de la confirmación.
 * Repite las restricciones de la tabla SQL de Python: nada inválido entra aunque
 * alguien reconecte mal el workflow.
 */
function filaGasto(gasto, registradoPor, taxonomia, ahoraIso) {
  if (gasto.monto === null) throw new Error("No se guarda un gasto sin monto");
  if (!taxonomia.contiene(gasto.categoria)) {
    throw new Error(`Categoría ${repr(gasto.categoria)} no está activa en la taxonomía`);
  }
  if (!taxonomia.nombresAtribucion.includes(gasto.atribuido_a)) {
    throw new Error(`Atribución ${repr(gasto.atribuido_a)} no existe en la taxonomía`);
  }
  if (!gasto.fecha) throw new Error("No se guarda un gasto sin fecha");
  return {
    monto_centavos: aCentavos(gasto.monto),
    categoria: gasto.categoria,
    atribuido_a: gasto.atribuido_a,
    fecha: gasto.fecha,
    descripcion: gasto.descripcion.slice(0, 500),
    texto_original: gasto.texto_original,
    registrado_por: String(registradoPor),
    creado_en: ahoraIso,
    confianza_monto: gasto.confianza_monto,
    confianza_categoria: gasto.confianza_categoria,
    confianza_fecha: gasto.confianza_fecha,
    confianza_atribuido_a: gasto.confianza_atribuido_a,
    corregido_manualmente: false,
    anulado_en: null,
    motivo_anulacion: null,
  };
}

const RESULTADOS = [
  "confirmada", // la persona tocó "Sí, guardar"
  "rechazada", // tocó "No"
  "expirada", // nadie respondió a la confirmación a tiempo
  "no_ofrecible", // la compuerta no encontró monto: no se puede ofrecer guardar
  "no_propuesto", // el agente no propuso nada y el mensaje traía un número
  "abandonada", // el agente preguntó algo y nadie respondió
  "error_al_guardar", // la persona dijo que sí, pero la escritura falló
];

/**
 * Fila de `extracciones`: una por intento, se haya guardado o no (evita el
 * sesgo del superviviente). A propósito sin monto ni texto, como en Python.
 * Sin gasto verificado (no_propuesto, abandonada) las confianzas quedan null.
 */
function filaExtraccion(resultado, gasto, ahoraIso) {
  if (!RESULTADOS.includes(resultado)) throw new Error(`Resultado desconocido: ${repr(resultado)}`);
  const conf = (campo) => (gasto ? gasto[`confianza_${campo}`] : null);
  return {
    creado_en: ahoraIso,
    resultado,
    confirmada: resultado === "confirmada",
    corregida: false, // sin botones de corrección: corregir es rechazar y reescribir
    tenia_monto: Boolean(gasto && gasto.monto !== null),
    confianza_monto: conf("monto"),
    confianza_categoria: conf("categoria"),
    confianza_fecha: conf("fecha"),
    confianza_atribuido_a: conf("atribuido_a"),
  };
}

// --- Reporte del mes (dashboard/report.py) -------------------------------------

function rangoDelMes(anio, mes) {
  const ultimo = new Date(Date.UTC(anio, mes, 0)).getUTCDate();
  const dos = (n) => String(n).padStart(2, "0");
  return [`${anio}-${dos(mes)}-01`, `${anio}-${dos(mes)}-${dos(ultimo)}`];
}

/**
 * Totales del mes por categoría, de mayor a menor, sobre filas de `gastos`.
 * Excluye anulados. Empates: primero la categoría del gasto más reciente, igual
 * que Python (lista por fecha e id descendente y ordena de forma estable).
 */
function reporteMes(filas, anio, mes) {
  const [desde, hasta] = rangoDelMes(anio, mes);
  const delMes = (filas || [])
    .filter((f) => !f.anulado_en && f.fecha >= desde && f.fecha <= hasta)
    .sort((a, b) => (a.fecha === b.fecha ? Number(b.id) - Number(a.id) : a.fecha < b.fecha ? 1 : -1));

  const acumulado = new Map();
  for (const f of delMes) {
    const fila = acumulado.get(f.categoria) || { categoria: f.categoria, total_centavos: 0, cantidad: 0 };
    fila.total_centavos += Number(f.monto_centavos);
    fila.cantidad += 1;
    acumulado.set(f.categoria, fila);
  }
  const porCategoria = [...acumulado.values()].sort((a, b) => b.total_centavos - a.total_centavos);
  return {
    anio,
    mes,
    total_centavos: delMes.reduce((s, f) => s + Number(f.monto_centavos), 0),
    cantidad: delMes.length,
    por_categoria: porCategoria,
  };
}

/** calcular_calidad de report.py. Una confianza null nunca cuenta como alta. */
function calcularCalidad(extracciones) {
  const lista = extracciones || [];
  const sinDudas = lista.filter(
    (e) => e.tenia_monto && CAMPOS.every((c) => e[`confianza_${c}`] === "alta"),
  ).length;
  const confirmadas = lista.filter((e) => e.confirmada).length;
  const corregidas = lista.filter((e) => e.confirmada && e.corregida).length;
  return { intentos: lista.length, confirmadas, corregidas, sin_dudas: sinDudas };
}

// --- Presupuesto (config.py: ingresos, dashboard/presupuesto.py) ---------------

/**
 * "rol:monto,rol:monto" → { rol: "monto" }. Mismo formato estricto que Python:
 * dígitos y hasta dos decimales. Falla rápido, como la allowlist.
 */
function parsearIngresos(texto, taxonomia) {
  const ingresos = {};
  for (const parte of (texto || "").split(",")) {
    if (!strip(parte)) continue;
    const i = parte.indexOf(":");
    const rol = strip(i === -1 ? parte : parte.slice(0, i)).toLowerCase();
    const crudo = i === -1 ? "" : strip(parte.slice(i + 1));
    if (i === -1 || !rol) {
      throw new Error(`INGRESOS_MENSUALES: ${repr(strip(parte))} no tiene la forma rol:monto`);
    }
    if (!/^\d+(\.\d{1,2})?$/.test(crudo)) {
      throw new Error(
        `INGRESOS_MENSUALES: ${repr(crudo)} no es un monto (dígitos y hasta dos decimales, con punto)`,
      );
    }
    if (aCentavos0(crudo) <= 0) {
      throw new Error(`INGRESOS_MENSUALES: el ingreso de ${rol} debe ser positivo`);
    }
    if (rol in ingresos) throw new Error(`INGRESOS_MENSUALES: ${rol} aparece dos veces`);
    if (!taxonomia.personas.includes(rol)) {
      throw new Error(`INGRESOS_MENSUALES tiene roles que no son personas: ['${rol}']`);
    }
    ingresos[rol] = crudo;
  }
  if (Object.keys(ingresos).length && !taxonomia.bolsillos.length) {
    throw new Error("Hay INGRESOS_MENSUALES pero categories.yaml no define un presupuesto");
  }
  return ingresos;
}

function aCentavos0(monto) {
  const [entero, decimales = ""] = monto.split(".");
  return Number(entero) * 100 + Number(decimales.padEnd(2, "0") || "0");
}

/** Centavos exactos por bolsillo: truncados, y lo que sobra, de a uno a los primeros. */
function repartirCentavos(total, bolsillos) {
  const partes = bolsillos.map((b) => Math.floor((total * b.porcentaje) / 100));
  const sobra = total - partes.reduce((s, p) => s + p, 0);
  for (let i = 0; i < sobra; i++) partes[i % partes.length] += 1;
  return partes;
}

/**
 * Cuánto lleva gastado `persona` en cada bolsillo durante el mes, sobre filas de
 * `gastos`. Solo cuentan sus gastos (los atribuidos a ella) y no los anulados.
 */
function resumenPresupuesto(filas, taxonomia, persona, ingreso, anio, mes) {
  const [desde, hasta] = rangoDelMes(anio, mes);
  const suyas = (filas || [])
    .filter((f) => !f.anulado_en && f.atribuido_a === persona && f.fecha >= desde && f.fecha <= hasta)
    // Mismo orden que Python (fecha e id descendente): define el de "sin bolsillo".
    .sort((a, b) => (a.fecha === b.fecha ? Number(b.id) - Number(a.id) : a.fecha < b.fecha ? 1 : -1));

  const gastado = new Map();
  const sueltas = [];
  for (const f of suyas) {
    const bolsillo = taxonomia.bolsilloDe(f.categoria, f.texto_original);
    const acumulado = gastado.get(bolsillo) || { centavos: 0, cantidad: 0 };
    acumulado.centavos += Number(f.monto_centavos);
    acumulado.cantidad += 1;
    gastado.set(bolsillo, acumulado);
    if (bolsillo === null && !sueltas.includes(f.categoria)) sueltas.push(f.categoria);
  }

  const ingresoCentavos = aCentavos0(ingreso);
  const asignados = repartirCentavos(ingresoCentavos, taxonomia.bolsillos);
  return {
    persona,
    anio,
    mes,
    ingreso_centavos: ingresoCentavos,
    bolsillos: taxonomia.bolsillos.map((b, i) => ({
      nombre: b.nombre,
      etiqueta: b.etiqueta,
      porcentaje: b.porcentaje,
      se_gasta: b.se_gasta,
      asignado_centavos: asignados[i],
      gastado_centavos: (gastado.get(b.nombre) || { centavos: 0 }).centavos,
      cantidad: (gastado.get(b.nombre) || { cantidad: 0 }).cantidad,
    })),
    sin_bolsillo_centavos: (gastado.get(null) || { centavos: 0 }).centavos,
    categorias_sin_bolsillo: sueltas,
  };
}

/** Etiqueta del bolsillo de un gasto, si quien lo asume tiene presupuesto (handlers._bolsillo). */
function bolsilloVisible(taxonomia, ingresos, gasto) {
  if (!(gasto.atribuido_a in ingresos)) return null;
  const nombre = taxonomia.bolsilloDe(gasto.categoria, gasto.texto_original);
  const bolsillo = taxonomia.bolsillos.find((b) => b.nombre === nombre);
  return bolsillo ? bolsillo.etiqueta : "ninguno";
}

// <solo-tests>
module.exports = {
  HERRAMIENTAS,
  MAX_MENSAJES_HILO,
  MENSAJES,
  RESULTADOS,
  TOOL_SALIDA,
  TTL_HILO_MS,
  agregarAlHilo,
  auditarPasos,
  bolsilloVisible,
  avisoDeProblemas,
  calcularCalidad,
  cerrarHilo,
  clasificarFallo,
  filaExtraccion,
  filaGasto,
  parsearIngresos,
  parsearUsuarios,
  probableGastoNoRegistrado,
  rangoDelMes,
  remitenteDe,
  reporteMes,
  repartirCentavos,
  resumenPresupuesto,
};
// </solo-tests>
