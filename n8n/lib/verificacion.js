// Verificación determinista: port de extraction/verificacion.py, categorias.py
// y de las reglas de parser.py.
//
// Es la compuerta del workflow n8n y también la tool `verificar_gasto` del
// agente. El agente propone; este código decide. Nada de n8n acá: son funciones
// puras que se testean con `node --test` y contra la versión Python
// (tests/test_n8n_paridad.py usa Python como oráculo).
//
// El build pega este archivo dentro de los Code nodes; los bloques
// <solo-tests> se quitan al pegarlo.
//
// Diferencia conocida con Python, a propósito: acá los dígitos son solo ASCII.
// En Python `\d` acepta dígitos de otros alfabetos ("٢٠٠"). Solo puede hacer que
// un monto quede en confianza `baja`, nunca que se acepte uno que no debía.

"use strict";

const CAMPOS = ["monto", "categoria", "fecha", "atribuido_a"];
const CONFIANZAS = ["alta", "media", "baja"];
const ANTIGUEDAD_MAXIMA_DIAS = 365;

const COMO_SE_LLAMA = {
  monto: "el monto",
  categoria: "la categoría",
  fecha: "la fecha",
  atribuido_a: "de quién es el gasto",
};

class ErrorDeExtraccion extends Error {
  constructor(mensaje) {
    super(mensaje);
    this.name = "ErrorDeExtraccion";
  }
}

// --- Texto: equivalentes de str.strip(), repr() y \b de Python ---------------

// Lo que str.isspace() considera espacio. `trim()` de JS difiere (﻿, \x1c-\x1f).
const ESPACIO_PY = "\\t\\n\\x0b\\x0c\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const BORDES_PY = new RegExp(`^[${ESPACIO_PY}]+|[${ESPACIO_PY}]+$`, "gu");

function strip(texto) {
  return texto.replace(BORDES_PY, "");
}

// `\b` de Python es Unicode; el de JS es solo ASCII. Se arma a mano.
const PALABRA = "[\\p{L}\\p{N}_]";
const BORDE = `(?:(?<=${PALABRA})(?!${PALABRA})|(?<!${PALABRA})(?=${PALABRA}))`;

function escaparRegex(texto) {
  return texto.replace(/[\\^$.*+?()[\]{}|/]/g, "\\$&");
}

const NO_IMPRIMIBLE = /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}\p{Zs}]/u;

/** repr() de Python, para que las notas que ve la persona sean las mismas. */
function repr(valor) {
  if (valor === null || valor === undefined) return "None";
  const comilla = valor.includes("'") && !valor.includes('"') ? '"' : "'";
  let salida = "";
  for (const c of valor) {
    const cp = c.codePointAt(0);
    if (c === "\\") salida += "\\\\";
    else if (c === comilla) salida += "\\" + c;
    else if (c === "\n") salida += "\\n";
    else if (c === "\r") salida += "\\r";
    else if (c === "\t") salida += "\\t";
    else if (c !== " " && NO_IMPRIMIBLE.test(c)) {
      if (cp < 0x100) salida += "\\x" + cp.toString(16).padStart(2, "0");
      else if (cp < 0x10000) salida += "\\u" + cp.toString(16).padStart(4, "0");
      else salida += "\\U" + cp.toString(16).padStart(8, "0");
    } else salida += c;
  }
  return comilla + salida + comilla;
}

/** Minúsculas y sin tildes: "Común" y "comun" tienen que ser lo mismo. */
function normalizar(texto) {
  return texto.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
}

function contieneFrase(textoNormalizado, frase) {
  const patron = new RegExp(BORDE + escaparRegex(normalizar(frase)) + BORDE, "u");
  return patron.test(textoNormalizado);
}

// --- Montos (verificacion.py) -----------------------------------------------
//
// Un monto es un string decimal canónico igual a str(Decimal) de Python
// ("12.50", "1200", "0.00"). Para comparar se pasa a centavos con BigInt: nunca
// punto flotante, por la misma razón que storage/dinero.py.

const NUMERO = /\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?/g;

function sinSeparadorDeMiles(entero, sep) {
  if (!entero.includes(sep)) return entero;
  if (!new RegExp(`^\\d{1,3}(?:${escaparRegex(sep)}\\d{3})+$`).test(entero)) return null;
  return entero.split(sep).join("");
}

function combinar(entero, decimales, milesSep) {
  if (!/^\d{1,2}$/.test(decimales)) return null; // el dólar no tiene tres decimales
  const enteroLimpio = sinSeparadorDeMiles(entero, milesSep);
  if (enteroLimpio === null || !/^\d+$/.test(enteroLimpio)) return null;
  return `${enteroLimpio}.${decimales}`;
}

function rpartition(texto, sep) {
  const i = texto.lastIndexOf(sep);
  return [texto.slice(0, i), texto.slice(i + sep.length)];
}

function canonico(normalizado) {
  const [entero, decimales] = normalizado.split(".");
  const sinCeros = entero.replace(/^0+/, "") || "0";
  return decimales === undefined ? sinCeros : `${sinCeros}.${decimales}`;
}

/** Convierte un número escrito por una persona a string decimal canónico, o null. */
function parsearMonto(texto) {
  const limpio = strip(texto).split(" ").join("").split("$").join("");
  if (!limpio || !/^[\d.,]+$/.test(limpio)) return null;

  const tienePunto = limpio.includes(".");
  const tieneComa = limpio.includes(",");
  let normalizado;
  if (tienePunto && tieneComa) {
    const decimalSep = limpio.lastIndexOf(".") > limpio.lastIndexOf(",") ? "." : ",";
    const [entero, decimales] = rpartition(limpio, decimalSep);
    normalizado = combinar(entero, decimales, decimalSep === "." ? "," : ".");
  } else if (tienePunto || tieneComa) {
    const sep = tienePunto ? "." : ",";
    const [entero, resto] = rpartition(limpio, sep);
    normalizado =
      resto.length === 3 // "1.200" son mil doscientos, no uno coma dos
        ? sinSeparadorDeMiles(limpio, sep)
        : combinar(entero, resto, sep);
  } else {
    normalizado = limpio;
  }
  return normalizado === null ? null : canonico(normalizado);
}

function aCentavosExactos(monto) {
  const [entero, decimales = ""] = monto.split(".");
  return BigInt(entero) * 100n + BigInt(decimales.padEnd(2, "0") || "0");
}

/** Todos los números que una persona escribió en el mensaje, en centavos. */
function montosEnTexto(texto) {
  const encontrados = new Set();
  for (const m of texto.matchAll(NUMERO)) {
    const monto = parsearMonto(m[0]);
    if (monto !== null) encontrados.add(aCentavosExactos(monto));
  }
  return encontrados;
}

/** ¿El monto que devolvió el LLM está realmente escrito en el mensaje? */
function montoRespaldadoPorTexto(monto, texto) {
  return montosEnTexto(texto).has(aCentavosExactos(monto));
}

/** Para guardar (storage/dinero.py). Falla rápido con montos no positivos. */
function aCentavos(monto) {
  const centavos = aCentavosExactos(monto);
  if (centavos <= 0n) throw new Error(`El monto debe ser positivo, llegó ${monto}`);
  if (centavos > BigInt(Number.MAX_SAFE_INTEGER)) {
    throw new Error(`El monto ${monto} es demasiado grande para guardarse`);
  }
  return Number(centavos);
}

// --- Fechas: date.fromisoformat de Python -----------------------------------
//
// Una fecha es un número de día (días desde 1970-01-01, UTC). Acepta los mismos
// formatos que Python 3.12: YYYY-MM-DD, YYYYMMDD, YYYY-Www[-D], YYYYWww[D].

const MS_POR_DIA = 86_400_000;

function diaDe(anio, mes, dia) {
  const d = new Date(0);
  d.setUTCFullYear(anio, mes - 1, dia);
  return Math.round(d.getTime() / MS_POR_DIA);
}

function aIso(numeroDeDia) {
  const d = new Date(numeroDeDia * MS_POR_DIA);
  const anio = String(d.getUTCFullYear()).padStart(4, "0");
  const mes = String(d.getUTCMonth() + 1).padStart(2, "0");
  const dia = String(d.getUTCDate()).padStart(2, "0");
  return `${anio}-${mes}-${dia}`;
}

function esBisiesto(anio) {
  return anio % 4 === 0 && (anio % 100 !== 0 || anio % 400 === 0);
}

function diasDelMes(anio, mes) {
  return [31, esBisiesto(anio) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mes - 1];
}

function diaDeLaSemana(numeroDeDia) {
  return (((numeroDeDia + 3) % 7) + 7) % 7; // lunes = 0; 1970-01-01 fue jueves
}

/** Número de día o null si no es una fecha que Python aceptaría. */
function fechaDesdeIso(texto) {
  let m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(texto) || /^(\d{4})(\d{2})(\d{2})$/.exec(texto);
  if (m) {
    const [anio, mes, dia] = m.slice(1).map(Number);
    if (anio < 1 || mes < 1 || mes > 12 || dia < 1 || dia > diasDelMes(anio, mes)) return null;
    return diaDe(anio, mes, dia);
  }
  m = /^(\d{4})-W(\d{2})(?:-(\d))?$/.exec(texto) || /^(\d{4})W(\d{2})(\d)?$/.exec(texto);
  if (m) {
    const anio = Number(m[1]);
    const semana = Number(m[2]);
    const dia = m[3] === undefined ? 1 : Number(m[3]);
    if (anio < 1) return null;
    const primeroDeEnero = diaDeLaSemana(diaDe(anio, 1, 1));
    const tiene53 = primeroDeEnero === 3 || (primeroDeEnero === 2 && esBisiesto(anio));
    if (semana < 1 || semana > 53 || (semana === 53 && !tiene53)) return null;
    if (dia < 1 || dia > 7) return null;
    const cuatroDeEnero = diaDe(anio, 1, 4);
    const lunesSemana1 = cuatroDeEnero - diaDeLaSemana(cuatroDeEnero);
    return lunesSemana1 + (semana - 1) * 7 + (dia - 1);
  }
  return null;
}

// --- Taxonomía (categorias.py) ----------------------------------------------
//
// Recibe el JSON de n8n/taxonomia.json, que ya pasó las validaciones de Python.

function crearTaxonomia(datos) {
  const activas = datos.categorias.filter((c) => !c.candidata);
  const nombres = activas.map((c) => c.nombre);
  const compartida = datos.atribuciones.find((a) => a.compartida);
  const personas = datos.atribuciones.filter((a) => !a.compartida).map((a) => a.nombre);
  const etiquetas = Object.fromEntries(datos.atribuciones.map((a) => [a.nombre, a.etiqueta]));

  function expresiones(persona, remitente) {
    const atribucion = datos.atribuciones.find((a) => a.nombre === persona);
    return (atribucion && atribucion.como_lo_llaman[remitente]) || [];
  }

  function mencionaA(persona, remitente, texto) {
    const normalizado = normalizar(texto);
    return expresiones(persona, remitente).some((e) => contieneFrase(normalizado, e));
  }

  function mencionaAlguna(texto, frases) {
    const normalizado = normalizar(texto);
    return frases.some((f) => contieneFrase(normalizado, f));
  }

  function mencionaGastoCompartido(texto) {
    return mencionaAlguna(texto, compartida.marcadores);
  }

  const bolsillos = datos.presupuesto ? datos.presupuesto.bolsillos : [];

  return {
    fallback: datos.fallback,
    nombres,
    personas,
    etiquetas,
    compartida,
    nombresAtribucion: datos.atribuciones.map((a) => a.nombre),
    contiene: (nombre) =>
      typeof nombre === "string" && nombres.includes(strip(nombre).toLowerCase()),
    describirParaPrompt: () =>
      activas
        .map(
          (c) =>
            `- ${c.nombre}: ${c.descripcion}` +
            (c.ejemplos.length ? ` Ej.: ${c.ejemplos.join(", ")}.` : ""),
        )
        .join("\n"),
    expresiones,
    mencionaA,
    mencionaGastoCompartido,
    /** Resolución determinista de parentescos: "mi hija" según quién escribe. */
    personasEn: (cita, remitente) =>
      personas.filter((p) => p !== remitente && mencionaA(p, remitente, cita)),
    esCompartida: (cita) =>
      strip(normalizar(cita)) === compartida.nombre || mencionaGastoCompartido(cita),
    bolsillos,
    /**
     * Contra qué bolsillo cuenta un gasto, o null (categorias.py: bolsillo_de).
     * Un marcador ("del fondo") gana sobre la categoría si apunta a un solo bolsillo.
     */
    bolsilloDe: (categoria, texto) => {
      const porMarcador = bolsillos.filter((b) => mencionaAlguna(texto, b.marcadores));
      if (porMarcador.length === 1) return porMarcador[0].nombre;
      const deCategoria = bolsillos.find((b) => b.categorias.includes(categoria));
      return deCategoria ? deCategoria.nombre : null;
    },
  };
}

// --- Contrato de la respuesta del LLM (schema.py: RespuestaLLM) --------------

const CAMPOS_OPCIONALES = ["monto", "categoria", "fecha", "quien_paga"];
const CAMPOS_CONFIANZA = [
  "confianza_monto",
  "confianza_categoria",
  "confianza_fecha",
  "confianza_quien_paga",
];

/** Valida la afirmación cruda. Lanza ErrorDeExtraccion si no respeta el contrato. */
function validarRespuesta(respuesta) {
  if (respuesta === null || typeof respuesta !== "object" || Array.isArray(respuesta)) {
    throw new ErrorDeExtraccion("El LLM no respetó el contrato: no es un objeto");
  }
  const problemas = [];
  for (const campo of CAMPOS_OPCIONALES) {
    const valor = respuesta[campo];
    if (valor !== undefined && valor !== null && typeof valor !== "string") {
      problemas.push(`${campo} debe ser texto o null`);
    }
  }
  if (typeof respuesta.descripcion !== "string") problemas.push("falta descripcion");
  for (const campo of CAMPOS_CONFIANZA) {
    if (!CONFIANZAS.includes(respuesta[campo])) problemas.push(`${campo} inválida`);
  }
  if (problemas.length) {
    throw new ErrorDeExtraccion(`El LLM no respetó el contrato: ${problemas.join("; ")}`);
  }
  const limpia = {};
  for (const campo of CAMPOS_OPCIONALES) limpia[campo] = respuesta[campo] ?? null;
  limpia.descripcion = respuesta.descripcion;
  for (const campo of CAMPOS_CONFIANZA) limpia[campo] = respuesta[campo];
  return limpia;
}

// --- Reglas por campo (parser.py) --------------------------------------------

function verificarMontoDe(respuesta, texto, notas) {
  const nota = (t) => notas.push({ campo: "monto", texto: t });
  if (respuesta.monto === null) {
    nota("El mensaje no trae un monto.");
    return [null, "baja"];
  }
  const monto = parsearMonto(respuesta.monto);
  if (monto === null) {
    nota(`No pude leer ${repr(respuesta.monto)} como un número.`);
    return [null, "baja"];
  }
  if (aCentavosExactos(monto) <= 0n) {
    nota(`El monto ${monto} no es positivo.`);
    return [null, "baja"];
  }
  if (!montoRespaldadoPorTexto(monto, texto)) {
    // Un número que nadie escribió no se acepta, por seguro que se declare.
    nota(`El monto ${monto} no aparece en el mensaje original.`);
    return [monto, "baja"];
  }
  return [monto, respuesta.confianza_monto];
}

function verificarCategoriaDe(respuesta, taxonomia, notas) {
  if (!taxonomia.contiene(respuesta.categoria)) {
    notas.push({
      campo: "categoria",
      texto:
        `La categoría ${repr(respuesta.categoria)} no está en la lista; ` +
        `queda como ${repr(taxonomia.fallback)}.`,
    });
    return [taxonomia.fallback, "baja"];
  }
  return [strip(respuesta.categoria).toLowerCase(), respuesta.confianza_categoria];
}

function verificarFechaDe(respuesta, hoy, notas) {
  const nota = (t) => notas.push({ campo: "fecha", texto: t });
  if (respuesta.fecha === null) {
    nota("El mensaje no menciona fecha; se asume hoy.");
    return [hoy, "media"];
  }
  const fecha = fechaDesdeIso(respuesta.fecha);
  if (fecha === null) {
    nota(`No pude leer ${repr(respuesta.fecha)} como fecha; se asume hoy.`);
    return [hoy, "baja"];
  }
  if (fecha > hoy) {
    nota(`La fecha ${aIso(fecha)} está en el futuro.`);
    return [fecha, "baja"];
  }
  if (hoy - fecha > ANTIGUEDAD_MAXIMA_DIAS) {
    nota(`La fecha ${aIso(fecha)} tiene más de un año.`);
    return [fecha, "baja"];
  }
  return [fecha, respuesta.confianza_fecha];
}

/**
 * Quién asume el costo. El LLM cita la frase; acá se resuelve con la tabla del
 * yaml según quién escribe. Por defecto, el remitente, nunca con confianza alta.
 */
function resolverAtribucion(respuesta, texto, remitente, taxonomia, notas) {
  const nota = (t) => notas.push({ campo: "atribuido_a", texto: t });
  const cita = respuesta.quien_paga;
  const confianza = respuesta.confianza_quien_paga;

  if (cita === null || !strip(cita)) {
    if (confianza === "baja") {
      nota("No queda claro quién asume este gasto; por ahora quedó como tuyo.");
      return [remitente, "baja"];
    }
    nota("El mensaje no dice de quién es el gasto; asumí que es tuyo porque lo escribiste tú.");
    return [remitente, "media"];
  }

  const compartida = taxonomia.compartida.nombre;
  if (taxonomia.esCompartida(cita)) {
    if (!taxonomia.mencionaGastoCompartido(texto)) {
      nota("El mensaje no dice explícitamente que sea un gasto compartido.");
      return [compartida, "baja"];
    }
    return [compartida, confianza];
  }

  const personas = taxonomia.personasEn(cita, remitente);
  if (personas.length > 1) {
    nota(`${repr(cita)} nombra a más de una persona; por ahora quedó como tuyo.`);
    return [remitente, "baja"];
  }
  if (personas.length === 0) {
    nota(`${repr(cita)} no es nadie de la lista; por ahora quedó como tuyo.`);
    return [remitente, "baja"];
  }
  const persona = personas[0];
  if (!taxonomia.mencionaA(persona, remitente, texto)) {
    // La cita no está en el mensaje: el LLM la inventó.
    nota(`El mensaje no menciona a ${taxonomia.etiquetas[persona]}.`);
    return [persona, "baja"];
  }
  return [persona, confianza];
}

function explicarDudasSinNota(confianzas, notas) {
  for (const campo of CAMPOS) {
    if (confianzas[campo] !== "alta" && !notas.some((n) => n.campo === campo)) {
      notas.push({ campo, texto: `No estoy seguro de ${COMO_SE_LLAMA[campo]}.` });
    }
  }
}

/**
 * La compuerta: equivalente a parser.extraer() después de la llamada al LLM.
 *
 * `respuesta` es lo que el agente afirma; `texto`, `remitente` y `hoy` los
 * pone el workflow, nunca el LLM. `hoy` es "YYYY-MM-DD".
 */
function verificarGasto({ respuesta, texto, remitente, hoy, taxonomia }) {
  if (!taxonomia.personas.includes(remitente)) {
    // Error de configuración, no del LLM.
    throw new Error(
      `Remitente ${repr(remitente)} no es una persona de la taxonomía (${taxonomia.personas.join(", ")})`,
    );
  }
  const diaDeHoy = fechaDesdeIso(hoy);
  if (diaDeHoy === null) throw new Error(`hoy inválido: ${repr(hoy)}`);

  const r = validarRespuesta(respuesta);
  const notas = [];
  const [monto, confianzaMonto] = verificarMontoDe(r, texto, notas);
  const [categoria, confianzaCategoria] = verificarCategoriaDe(r, taxonomia, notas);
  const [fecha, confianzaFecha] = verificarFechaDe(r, diaDeHoy, notas);
  const [atribuidoA, confianzaAtribucion] = resolverAtribucion(
    r,
    texto,
    remitente,
    taxonomia,
    notas,
  );
  const confianzas = {
    monto: confianzaMonto,
    categoria: confianzaCategoria,
    fecha: confianzaFecha,
    atribuido_a: confianzaAtribucion,
  };
  explicarDudasSinNota(confianzas, notas);

  const camposDudosos = CAMPOS.filter((c) => confianzas[c] !== "alta");
  if (monto === null && !camposDudosos.includes("monto")) camposDudosos.unshift("monto");

  return {
    monto,
    categoria,
    fecha: fecha === null ? null : aIso(fecha),
    atribuido_a: atribuidoA,
    descripcion: strip(r.descripcion),
    texto_original: texto,
    remitente,
    confianza_monto: confianzaMonto,
    confianza_categoria: confianzaCategoria,
    confianza_fecha: confianzaFecha,
    confianza_atribuido_a: confianzaAtribucion,
    notas,
    campos_dudosos: camposDudosos,
    listo_para_guardar: monto !== null && camposDudosos.length === 0,
  };
}

// <solo-tests>
module.exports = {
  CAMPOS,
  ErrorDeExtraccion,
  aCentavos,
  aIso,
  crearTaxonomia,
  fechaDesdeIso,
  montosEnTexto,
  normalizar,
  parsearMonto,
  repr,
  strip,
  validarRespuesta,
  verificarGasto,
};
// </solo-tests>
