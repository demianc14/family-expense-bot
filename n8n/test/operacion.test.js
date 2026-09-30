// Tests de las piezas propias del workflow n8n. Sin dependencias: node --test n8n/test

"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const v = require("../lib/verificacion.js");
const op = require("../lib/operacion.js");

const taxonomia = v.crearTaxonomia(
  JSON.parse(fs.readFileSync(path.join(__dirname, "..", "taxonomia.json"), "utf8")),
);

const BASE = {
  monto: "200",
  categoria: "alimentacion",
  fecha: null,
  quien_paga: null,
  descripcion: "supermercado",
  confianza_monto: "alta",
  confianza_categoria: "alta",
  confianza_fecha: "alta",
  confianza_quien_paga: "alta",
};

function gasto(respuesta = {}, texto = "200 dólares supermercado", remitente = "mama") {
  return v.verificarGasto({
    respuesta: { ...BASE, ...respuesta },
    texto,
    remitente,
    hoy: "2026-03-15",
    taxonomia,
  });
}

// --- Quién escribe -------------------------------------------------------------

test("TELEGRAM_USUARIOS se lee igual que en Python", () => {
  // IDs sintéticos: nunca IDs reales en el repo.
  const usuarios = op.parsearUsuarios(" 111:Mama , 222:demian,", taxonomia);
  assert.deepEqual(usuarios, { 111: "mama", 222: "demian" });
  assert.equal(op.remitenteDe(111, usuarios), "mama");
  assert.equal(op.remitenteDe("222", usuarios), "demian");
  assert.equal(op.remitenteDe(333, usuarios), null);
  assert.equal(op.remitenteDe(undefined, usuarios), null);
  assert.deepEqual(op.parsearUsuarios("", taxonomia), {});
});

test("TELEGRAM_USUARIOS mal escrito falla rápido", () => {
  for (const malo of ["111", "111:", "abc:mama", "111:mama,111:papa", "111:casa", "111:tia"]) {
    assert.throws(() => op.parsearUsuarios(malo, taxonomia), /TELEGRAM_USUARIOS/, malo);
  }
});

// --- Hilo ----------------------------------------------------------------------

test("el hilo junta los mensajes para respaldar el monto de una respuesta", () => {
  const hilos = {};
  op.agregarAlHilo(hilos, 1, "fui al veterinario", 0);
  const { abandonado, textoRespaldo, hiloId } = op.agregarAlHilo(hilos, 1, "40", 60_000);
  assert.equal(abandonado, false);
  assert.equal(hiloId, "1:0");
  assert.equal(textoRespaldo, "fui al veterinario\n40");

  const g = gasto({ monto: "40", categoria: "mascotas" }, textoRespaldo, "papa");
  assert.equal(g.monto, "40");
  assert.equal(g.confianza_monto, "alta");
});

test("cerrar el hilo impide que un monto ya confirmado respalde otro gasto", () => {
  const hilos = {};
  op.agregarAlHilo(hilos, 1, "200 supermercado", 0);
  op.cerrarHilo(hilos, 1);
  const { textoRespaldo } = op.agregarAlHilo(hilos, 1, "y lo mismo de gasolina", 1000);
  assert.equal(textoRespaldo, "y lo mismo de gasolina");
  assert.equal(gasto({ monto: "200" }, textoRespaldo).confianza_monto, "baja");
});

test("un hilo vencido se reporta como abandonado y empieza de cero", () => {
  const hilos = {};
  op.agregarAlHilo(hilos, 1, "fui al veterinario", 0);
  const r = op.agregarAlHilo(hilos, 1, "200 supermercado", op.TTL_HILO_MS + 1);
  assert.equal(r.abandonado, true);
  assert.equal(r.textoRespaldo, "200 supermercado");
  assert.notEqual(r.hiloId, "1:0", "un hilo nuevo es una sesión de memoria nueva");
});

test("un hilo no crece sin límite", () => {
  const hilos = {};
  for (let i = 0; i < op.MAX_MENSAJES_HILO; i++) op.agregarAlHilo(hilos, 1, `m${i}`, i);
  const r = op.agregarAlHilo(hilos, 1, "otro", 99);
  assert.equal(r.abandonado, true);
  assert.equal(r.textoRespaldo, "otro");
});

test("los hilos de chats distintos no se mezclan", () => {
  const hilos = {};
  op.agregarAlHilo(hilos, 1, "200", 0);
  assert.equal(op.agregarAlHilo(hilos, 2, "hola", 0).textoRespaldo, "hola");
});

// --- Fallos visibles -----------------------------------------------------------

test("cada fallo del modelo se clasifica con un motivo que la persona entiende", () => {
  const casos = [
    [{ message: "[429 Too Many Requests] RESOURCE_EXHAUSTED" }, "cuota"],
    [{ httpCode: "429", message: "Request failed" }, "cuota"],
    [{ message: "You exceeded your current quota" }, "cuota"],
    // Así llega en n8n 2.40.7 un 429 de Gemini (visto en el E2E):
    [{ message: "The service is receiving too many requests from you" }, "cuota"],
    [{ message: "Request timed out" }, "timeout"],
    [{ message: "ETIMEDOUT" }, "timeout"],
    [{ message: "Failed to parse. Text: ..." , description: "output parser" }, "contrato"],
    [{ message: "algo inesperado" }, "desconocido"],
    [undefined, "desconocido"],
    ["The resource you are requesting could not be found (429)", "cuota"],
  ];
  for (const [error, motivo] of casos) {
    const f = op.clasificarFallo(error);
    assert.equal(f.motivo, motivo, JSON.stringify(error));
    assert.ok(f.mensaje.length > 0);
  }
  assert.match(op.clasificarFallo(casos[0][0]).mensaje, /cuota/);
});

test("la auditoría detecta tools inexistentes y caídas, no confía en el agente", () => {
  const ok = JSON.stringify({ ok: true, gasto: {} });
  const pasos = [
    { action: { tool: "verificar_gasto" }, observation: ok },
    { action: { tool: "verificar_gasto" }, observation: { ok: true } },
    // Así responde la tool en n8n 2.40.7 (visto en el E2E): los items del último nodo.
    { action: { tool: "verificar_gasto" }, observation: JSON.stringify([{ ok: true, monto: "45" }]) },
    { action: { tool: "guardar_gasto" }, observation: "guardar_gasto is not a valid tool" },
    { action: { tool: "verificar_gasto" }, observation: "There was an error: timeout" },
    { action: { tool: "verificar_gasto" }, observation: JSON.stringify({ ok: false, error: "x" }) },
    { action: {}, observation: "" },
    { action: { tool: "format_final_json_response" }, observation: "" },
  ];
  assert.deepEqual(
    op.auditarPasos(pasos).map((p) => [p.tool, p.motivo]),
    [
      ["guardar_gasto", "tool_inexistente"],
      ["verificar_gasto", "tool_fallo"],
      ["verificar_gasto", "tool_fallo"],
      [null, "tool_inexistente"],
    ],
  );
  assert.deepEqual(op.auditarPasos(undefined), []);
  assert.equal(op.avisoDeProblemas([]), null);
  assert.match(op.avisoDeProblemas(op.auditarPasos(pasos)), /no existe \(guardar_gasto\)/);
});

test("un mensaje con número sin propuesta se marca como posible gasto no registrado", () => {
  assert.equal(op.probableGastoNoRegistrado("responder", "20 de taxi"), true);
  assert.equal(op.probableGastoNoRegistrado("preguntar", "20 de taxi"), true);
  assert.equal(op.probableGastoNoRegistrado("responder", "¿cuánto va el mes?"), false);
  assert.equal(op.probableGastoNoRegistrado("proponer", "20 de taxi"), false);
});

// --- Filas -----------------------------------------------------------------------

test("la fila de gasto guarda centavos enteros", () => {
  const g = gasto({ monto: "12,50", categoria: "alimentacion" }, "12,50 en pan", "papa");
  const fila = op.filaGasto(g, 111, taxonomia, "2026-03-15T12:00:00.000Z");
  assert.equal(fila.monto_centavos, 1250);
  assert.equal(fila.atribuido_a, "papa");
  assert.equal(fila.fecha, "2026-03-15");
  assert.equal(fila.registrado_por, "111");
  assert.equal(fila.corregido_manualmente, false);
  assert.equal(fila.anulado_en, null);
});

test("guardar rechaza lo que la tabla SQL de Python rechazaría", () => {
  const g = gasto();
  const ahora = "2026-03-15T12:00:00.000Z";
  assert.throws(() => op.filaGasto({ ...g, monto: null }, 1, taxonomia, ahora), /sin monto/);
  assert.throws(() => op.filaGasto({ ...g, monto: "0" }, 1, taxonomia, ahora), /positivo/);
  assert.throws(() => op.filaGasto({ ...g, categoria: "salud" }, 1, taxonomia, ahora), /activa/);
  assert.throws(() => op.filaGasto({ ...g, atribuido_a: "tia" }, 1, taxonomia, ahora), /Atribución/);
  assert.throws(
    () => op.filaGasto({ ...g, monto: "99999999999999999999" }, 1, taxonomia, ahora),
    /demasiado grande/,
  );
});

test("la fila de extracción no lleva montos ni texto", () => {
  const g = gasto();
  for (const resultado of op.RESULTADOS) {
    const fila = op.filaExtraccion(resultado, resultado === "abandonada" ? null : g, "t");
    assert.deepEqual(Object.keys(fila).sort(), [
      "confianza_atribuido_a",
      "confianza_categoria",
      "confianza_fecha",
      "confianza_monto",
      "confirmada",
      "corregida",
      "creado_en",
      "resultado",
      "tenia_monto",
    ]);
    assert.equal(fila.confirmada, resultado === "confirmada");
    assert.equal(JSON.stringify(fila).includes("200"), false);
    assert.equal(JSON.stringify(fila).includes("supermercado"), false);
  }
  assert.throws(() => op.filaExtraccion("guardada", g, "t"), /Resultado desconocido/);
});

test("sin gasto verificado, las confianzas quedan null y no cuentan como sin dudas", () => {
  const fila = op.filaExtraccion("no_propuesto", null, "t");
  assert.equal(fila.tenia_monto, false);
  assert.equal(fila.confianza_monto, null);
  assert.deepEqual(op.calcularCalidad([fila]), {
    intentos: 1,
    confirmadas: 0,
    corregidas: 0,
    sin_dudas: 0,
  });
});

// --- Reporte ---------------------------------------------------------------------

test("reporte del mes: por categoría de mayor a menor, sin anulados ni otros meses", () => {
  const filas = [
    { id: 1, fecha: "2026-03-01", categoria: "transporte", monto_centavos: 1500 },
    { id: 2, fecha: "2026-03-10", categoria: "alimentacion", monto_centavos: 20000 },
    { id: 3, fecha: "2026-03-31", categoria: "transporte", monto_centavos: 850 },
    { id: 4, fecha: "2026-04-01", categoria: "ropa", monto_centavos: 99999 },
    { id: 5, fecha: "2026-02-28", categoria: "ropa", monto_centavos: 99999 },
    { id: 6, fecha: "2026-03-05", categoria: "ropa", monto_centavos: 5000, anulado_en: "x" },
  ];
  assert.deepEqual(op.reporteMes(filas, 2026, 3), {
    anio: 2026,
    mes: 3,
    total_centavos: 22350,
    cantidad: 3,
    por_categoria: [
      { categoria: "alimentacion", total_centavos: 20000, cantidad: 1 },
      { categoria: "transporte", total_centavos: 2350, cantidad: 2 },
    ],
  });
  assert.deepEqual(op.reporteMes([], 2026, 3).por_categoria, []);
});

test("empates en el reporte: primero la categoría del gasto más reciente, como Python", () => {
  const filas = [
    { id: 1, fecha: "2026-03-01", categoria: "a", monto_centavos: 100 },
    { id: 2, fecha: "2026-03-02", categoria: "b", monto_centavos: 100 },
  ];
  assert.deepEqual(
    op.reporteMes(filas, 2026, 3).por_categoria.map((c) => c.categoria),
    ["b", "a"],
  );
});

test("rango del mes, incluido febrero bisiesto", () => {
  assert.deepEqual(op.rangoDelMes(2024, 2), ["2024-02-01", "2024-02-29"]);
  assert.deepEqual(op.rangoDelMes(2026, 12), ["2026-12-01", "2026-12-31"]);
});
