// Puente para tests/test_n8n_paridad.py: recibe casos por stdin, responde por
// stdout. Un solo proceso de node para toda la batería.
//
// Entrada: [{ fn, args }]. Salida: [{ ok: valor } | { error: nombre }].

"use strict";

const fs = require("node:fs");
const path = require("node:path");
const v = require("../lib/verificacion.js");
const op = require("../lib/operacion.js");
const m = require("../lib/mensajes.js");

const taxonomia = v.crearTaxonomia(
  JSON.parse(fs.readFileSync(path.join(__dirname, "..", "taxonomia.json"), "utf8")),
);

const FUNCIONES = {
  parsear_monto: (texto) => v.parsearMonto(texto),
  // Centavos como texto: el Set de BigInt no cruza JSON.
  montos_en_texto: (texto) => [...v.montosEnTexto(texto)].map(String).sort(),
  normalizar: (texto) => v.normalizar(texto),
  strip: (texto) => v.strip(texto),
  repr: (texto) => v.repr(texto),
  fecha_desde_iso: (texto) => {
    const dia = v.fechaDesdeIso(texto);
    return dia === null ? null : v.aIso(dia);
  },
  personas_en: (cita, remitente) => taxonomia.personasEn(cita, remitente),
  menciona_gasto_compartido: (texto) => taxonomia.mencionaGastoCompartido(texto),
  es_compartida: (cita) => taxonomia.esCompartida(cita),
  contiene: (nombre) => taxonomia.contiene(nombre),
  describir_para_prompt: () => taxonomia.describirParaPrompt(),
  verificar_gasto: (respuesta, texto, remitente, hoy) =>
    v.verificarGasto({ respuesta, texto, remitente, hoy, taxonomia }),
  resumen: (respuesta, texto, remitente, hoy) =>
    m.resumen(v.verificarGasto({ respuesta, texto, remitente, hoy, taxonomia }), taxonomia.etiquetas),
  falta_monto: (respuesta, texto, remitente, hoy) =>
    m.faltaMonto(v.verificarGasto({ respuesta, texto, remitente, hoy, taxonomia }), taxonomia.etiquetas),
  guardado: (fila) => m.guardado(fila, taxonomia.etiquetas),
  reporte: (filas, extracciones, anio, mes) =>
    m.reporte(op.reporteMes(filas, anio, mes), op.calcularCalidad(extracciones)),
};

const casos = JSON.parse(fs.readFileSync(0, "utf8"));
const resultados = casos.map(({ fn, args }) => {
  try {
    return { ok: FUNCIONES[fn](...args) };
  } catch (e) {
    return { error: e.name };
  }
});
process.stdout.write(JSON.stringify(resultados));
