// Pruebas E2E del workflow en un n8n real (Docker), con Telegram y Gemini falsos.
//
//   node --test n8n/e2e/e2e.test.mjs
//
// Tarda un par de minutos (levanta n8n desde cero y un escenario espera a que
// venza una confirmación). E2E_MANTENER=1 deja el entorno arriba al terminar.
// Nada de lo que corre acá es real: IDs, token y API key son sintéticos.

import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { bajar, control, esperar, filas, levantar, logs } from "./entorno.mjs";

const MAMA = 111;
const DEMIAN = 222;
const PAPA = 333;
const DESCONOCIDO = 555;

let siguienteUpdate = 1;

before(async () => {
  await levantar();
});

after(() => {
  if (!process.env.E2E_MANTENER) bajar();
});

// --- Ayudas --------------------------------------------------------------------

async function escribir(de, texto) {
  const r = await control("update", {
    update_id: siguienteUpdate++,
    message: {
      message_id: siguienteUpdate,
      date: Math.floor(Date.now() / 1000),
      chat: { id: de, type: "private" },
      from: { id: de, is_bot: false, first_name: "Persona" },
      text: texto,
    },
  });
  assert.equal(r.status, 200, `el webhook respondió ${r.status}: ${r.cuerpo}`);
}

async function guion(...turnos) {
  await control("guion", { turnos });
}

const salida = (campos) => ({
  tipo: "funcion",
  nombre: "format_final_json_response",
  args: {
    output: {
      accion: "proponer",
      mensaje: "",
      monto: "",
      categoria: "",
      fecha: "",
      quien_paga: "",
      descripcion: "",
      confianza_monto: "alta",
      confianza_categoria: "alta",
      confianza_fecha: "alta",
      confianza_quien_paga: "alta",
      ...campos,
    },
  },
});

async function mensajes() {
  return (await control("estado")).telegram.filter((c) => c.metodo === "sendMessage");
}

async function esperarMensaje(predicado, que) {
  try {
    return await esperar(async () => (await mensajes()).find(predicado), { que, ms: 30000 });
  } catch (e) {
    const vistos = (await mensajes()).map((m) => m.cuerpo.text);
    throw new Error(`${e.message}\nMensajes vistos: ${JSON.stringify(vistos, null, 1)}\n${logs(40)}`);
  }
}

const esConfirmacion = (m) => Boolean(m.cuerpo.reply_markup);

async function tocar(etiqueta) {
  const confirmacion = await esperarMensaje(esConfirmacion, "el mensaje de confirmación");
  const boton = confirmacion.cuerpo.reply_markup.inline_keyboard.flat().find((b) => b.text === etiqueta);
  assert.ok(boton, `no hay botón "${etiqueta}"`);
  assert.ok(boton.url, "sin HTTPS público, n8n usa botones con enlace");
  await control("abrir", { url: boton.url });
}

/** Espera a que el número de filas de extracciones llegue a `n`. */
async function esperarExtracciones(n) {
  return esperar(() => {
    const todas = filas("extracciones");
    return todas.length >= n ? todas : null;
  }, { que: `${n} extracciones registradas` });
}

async function escenario() {
  await control("reiniciar", {});
}

// --- Escenarios ------------------------------------------------------------------

test("camino feliz: propone, la persona confirma y recién ahí se guarda", async () => {
  await escenario();
  await guion(salida({ monto: "200", categoria: "alimentacion", descripcion: "supermercado" }));
  await escribir(MAMA, "200 dólares supermercado");

  const confirmacion = await esperarMensaje(esConfirmacion, "la confirmación");
  const texto = confirmacion.cuerpo.text;
  assert.match(texto, /Monto: \$200\.00\n/);
  assert.match(texto, /De: Mamá ❓/, "sin quién paga en el mensaje, la atribución queda en duda");
  assert.equal(filas("gastos").length, 0, "antes de confirmar no hay nada guardado");

  await tocar("Sí, guardar");
  await esperarMensaje((m) => m.cuerpo.text.startsWith("Guardado ✅"), "el aviso de guardado");
  const [gasto] = filas("gastos");
  assert.equal(gasto.monto_centavos, 20000);
  assert.equal(gasto.atribuido_a, "mama");
  assert.equal(gasto.categoria, "alimentacion");
  assert.equal(gasto.registrado_por, String(MAMA));
  assert.equal(gasto.confianza_atribuido_a, "media");

  const [extraccion] = await esperarExtracciones(1);
  assert.equal(extraccion.resultado, "confirmada");
  assert.equal(extraccion.confirmada, 1);
  assert.equal("monto" in extraccion || "texto_original" in extraccion, false);
});

test("rechazo: no se guarda nada, pero el intento queda registrado", async () => {
  await escenario();
  const antes = filas("gastos").length;
  const extraccionesAntes = filas("extracciones").length;
  await guion(salida({ monto: "15", categoria: "transporte", descripcion: "taxi" }));
  await escribir(DEMIAN, "15 de taxi");
  await tocar("No");
  await esperarMensaje((m) => m.cuerpo.text.includes("no guardé nada"), "el aviso de descarte");
  const extracciones = await esperarExtracciones(extraccionesAntes + 1);
  assert.equal(extracciones.at(-1).resultado, "rechazada");
  assert.equal(filas("gastos").length, antes);
});

test('"mi hija" escrito por papá se resuelve a hermana: el LLM cita, el código resuelve', async () => {
  await escenario();
  // El agente intenta además colar un rol: se ignora.
  await guion(
    salida({
      monto: "45",
      categoria: "ropa",
      quien_paga: "mi hija",
      descripcion: "ropa",
      rol: "casa",
    }),
  );
  await escribir(PAPA, "mi hija pagó 45 de ropa con su plata");
  const confirmacion = await esperarMensaje(esConfirmacion, "la confirmación");
  assert.match(confirmacion.cuerpo.text, /De: Hermana\n/, "resuelta con confianza, sin ❓");
  await tocar("Sí, guardar");
  await esperarMensaje((m) => m.cuerpo.text.startsWith("Guardado ✅"), "el aviso de guardado");
  assert.equal(filas("gastos").at(-1).atribuido_a, "hermana");
});

test("un monto que nadie escribió se muestra con duda y con su motivo", async () => {
  await escenario();
  await guion(salida({ monto: "50", categoria: "alimentacion", descripcion: "compras" }));
  await escribir(MAMA, "compré cosas en el super");
  const confirmacion = await esperarMensaje(esConfirmacion, "la confirmación");
  assert.match(confirmacion.cuerpo.text, /Monto: \$50\.00 ❓/);
  assert.match(confirmacion.cuerpo.text, /El monto 50 no aparece en el mensaje original/);
  await tocar("No");
  await esperarMensaje((m) => m.cuerpo.text.includes("no guardé nada"), "el descarte");
});

test("casa sin decirlo en el mensaje queda en duda", async () => {
  await escenario();
  await guion(
    salida({ monto: "80", categoria: "transporte", quien_paga: "gasto de la casa", descripcion: "gasolina" }),
  );
  await escribir(MAMA, "80 de gasolina");
  const confirmacion = await esperarMensaje(esConfirmacion, "la confirmación");
  assert.match(confirmacion.cuerpo.text, /De: Casa ❓/);
  assert.match(confirmacion.cuerpo.text, /no dice explícitamente que sea un gasto compartido/);
  await tocar("No");
  await esperarMensaje((m) => m.cuerpo.text.includes("no guardé nada"), "el descarte");
});

test("sin monto no se ofrece guardar, y el intento cuenta", async () => {
  await escenario();
  const antes = filas("extracciones").length;
  await guion(salida({ monto: "", categoria: "mascotas", descripcion: "veterinario", confianza_monto: "baja" }));
  await escribir(PAPA, "fui al veterinario");
  await esperarMensaje((m) => m.cuerpo.text.includes("No encontré el monto"), "el pedido del monto");
  assert.equal((await mensajes()).some(esConfirmacion), false);
  const extracciones = await esperarExtracciones(antes + 1);
  assert.equal(extracciones.at(-1).resultado, "no_ofrecible");
});

test("el agente pregunta y la respuesta respalda el monto (hilo + memoria)", async () => {
  await escenario();
  await guion(salida({ accion: "preguntar", mensaje: "¿Cuánto fue?" }));
  await escribir(PAPA, "fui al veterinario");
  await esperarMensaje((m) => m.cuerpo.text === "¿Cuánto fue?", "la pregunta");

  await guion(salida({ monto: "40", categoria: "mascotas", descripcion: "veterinario" }));
  await escribir(PAPA, "40");
  const confirmacion = await esperarMensaje(esConfirmacion, "la confirmación");
  assert.match(confirmacion.cuerpo.text, /Monto: \$40\.00\n/, "el 40 está en el hilo: sin duda");

  const { gemini } = await control("estado");
  const ultimo = gemini.at(-1).cuerpo.contents;
  assert.ok(ultimo.length >= 3, "la memoria del hilo llega al modelo");
  await tocar("Sí, guardar");
  await esperarMensaje((m) => m.cuerpo.text.startsWith("Guardado ✅"), "el guardado");
  assert.equal(filas("gastos").at(-1).texto_original, "fui al veterinario\n40");
});

test("verificar_gasto: la tool recibe remitente y texto del workflow, no del modelo", async () => {
  await escenario();
  await guion(
    {
      tipo: "funcion",
      nombre: "verificar_gasto",
      args: {
        monto: "45",
        categoria: "ropa",
        quien_paga: "mi hija",
        descripcion: "ropa",
        confianza_monto: "alta",
        confianza_categoria: "alta",
        confianza_fecha: "alta",
        confianza_quien_paga: "alta",
      },
    },
    salida({ monto: "45", categoria: "ropa", quien_paga: "mi hija", descripcion: "ropa" }),
  );
  await escribir(MAMA, "mi hija pagó 45 de ropa");
  await esperarMensaje(esConfirmacion, "la confirmación");

  const { gemini } = await control("estado");
  const declaracion = gemini[0].cuerpo.tools[0].functionDeclarations.find(
    (f) => f.name === "verificar_gasto",
  );
  const parametros = Object.keys(declaracion.parameters.properties);
  for (const inyectado of ["texto", "remitente", "hoy"]) {
    assert.ok(!parametros.includes(inyectado), `el modelo no puede elegir ${inyectado}`);
  }
  const parte = gemini[1].cuerpo.contents.flatMap((c) => c.parts).find((p) => p.functionResponse);
  const [resultado] = JSON.parse(parte.functionResponse.response.result);
  assert.equal(resultado.ok, true);
  assert.equal(resultado.atribuido_a, "hermana", "desde mamá, 'mi hija' es hermana");
  await tocar("No");
  await esperarMensaje((m) => m.cuerpo.text.includes("no guardé nada"), "el descarte");
});

// Caracterización de una limitación de n8n 2.40.7, no de este workflow: el Agent
// descarta en silencio la llamada a una tool que no existe (createEngineRequests
// la filtra) y vuelve a llamar al modelo como si no hubiera pasado. Ningún nodo
// del workflow puede verlo. No tiene efecto (guardar no es una tool), pero no
// es visible. Si una versión nueva de n8n lo cambia, este test lo va a marcar.
test("tool inexistente: n8n la descarta en silencio y no tiene efecto (limitación)", async () => {
  await escenario();
  await guion(
    { tipo: "funcion", nombre: "guardar_gasto", args: { monto: "20" } },
    salida({ monto: "20", categoria: "transporte", descripcion: "taxi" }),
  );
  await escribir(DEMIAN, "20 de taxi");
  const confirmacion = await esperarMensaje(esConfirmacion, "la confirmación");
  const { gemini } = await control("estado");
  assert.equal(gemini.length, 2, "la llamada descartada cuesta un request más de cuota");
  assert.doesNotMatch(JSON.stringify(gemini[1].cuerpo.contents), /guardar_gasto/);
  assert.doesNotMatch(confirmacion.cuerpo.text, /Aviso/, "comportamiento actual: no se ve");
  assert.equal(filas("gastos").some((g) => g.descripcion === "taxi"), false);
  await tocar("No");
  await esperarMensaje((m) => m.cuerpo.text.includes("no guardé nada"), "el descarte");
});

test("una tool que falla se avisa junto a la propuesta, sin confiar en el agente", async () => {
  await escenario();
  await guion(
    {
      tipo: "funcion",
      nombre: "verificar_gasto",
      args: { monto: "20", categoria: "transporte", confianza_monto: "altísima" },
    },
    salida({ monto: "20", categoria: "transporte", descripcion: "taxi" }),
  );
  await escribir(DEMIAN, "20 de taxi");
  const confirmacion = await esperarMensaje(esConfirmacion, "la confirmación");
  assert.match(confirmacion.cuerpo.text, /Aviso: la herramienta verificar\\_gasto falló/);
  await tocar("No");
  await esperarMensaje((m) => m.cuerpo.text.includes("no guardé nada"), "el descarte");
});

test("cuota agotada: la persona recibe el motivo", async () => {
  await escenario();
  const error = { tipo: "error", status: 429, mensaje: "Resource has been exhausted", estado: "RESOURCE_EXHAUSTED" };
  await guion(...Array.from({ length: 12 }, () => error));
  const inicio = Date.now();
  await escribir(DEMIAN, "20 de taxi");
  await esperar(async () => (await mensajes()).find((m) => /cuota/.test(m.cuerpo.text)), {
    ms: 180000,
    que: "el aviso de cuota",
  });
  const { gemini } = await control("estado");
  console.log(`cuota agotada → ${gemini.length} intentos a Gemini, ${Math.round((Date.now() - inicio) / 1000)} s`);
  await control("reiniciar", {});
});

test("no autorizado: no se responde ni se llama al modelo", async () => {
  await escenario();
  await escribir(DESCONOCIDO, "200 supermercado");
  await new Promise((r) => setTimeout(r, 3000));
  const estado = await control("estado");
  assert.equal(estado.gemini.length, 0);
  assert.equal(estado.telegram.filter((c) => c.metodo === "sendMessage").length, 0);
});

test("responder sin proponer, con un número en el mensaje: se dice y se registra", async () => {
  await escenario();
  const antes = filas("extracciones").length;
  await guion(salida({ accion: "responder", mensaje: "¡Hola!" }));
  await escribir(DEMIAN, "hola, 20");
  await esperarMensaje((m) => m.cuerpo.text.includes("No registré ningún gasto"), "el aviso");
  const extracciones = await esperarExtracciones(antes + 1);
  assert.equal(extracciones.at(-1).resultado, "no_propuesto");
});

test("reporte: determinista, y los totales nunca llegan al modelo", async () => {
  await escenario();
  await guion(salida({ accion: "reporte", mes: "" }));
  await escribir(MAMA, "¿cuánto llevamos gastado este mes?");
  const reporte = await esperarMensaje((m) => m.cuerpo.text.startsWith("Gastos de"), "el reporte");
  assert.match(reporte.cuerpo.text, /Total: \$285\.00 en 3 gasto\(s\)/);
  const { gemini } = await control("estado");
  assert.equal(gemini.length, 1);
  assert.doesNotMatch(JSON.stringify(gemini), /285/);

  await escenario();
  await escribir(MAMA, "/reporte");
  const directo = await esperarMensaje((m) => m.cuerpo.text.startsWith("Gastos de"), "el /reporte");
  assert.equal(directo.cuerpo.text, reporte.cuerpo.text);
  assert.equal((await control("estado")).gemini.length, 0, "/reporte no usa el LLM");
});

test("Markdown: una categoría con guion bajo no rompe la confirmación", async () => {
  await escenario();
  await guion(salida({ monto: "12", categoria: "comida_fuera", descripcion: "*almuerzo* [promo]" }));
  await escribir(DEMIAN, "12 almuerzo");
  const confirmacion = await esperarMensaje(esConfirmacion, "la confirmación");
  assert.match(confirmacion.cuerpo.text, /comida\\_fuera/);
  const rechazados = (await control("estado")).telegram.filter((c) => c.rechazado);
  assert.deepEqual(rechazados, []);
  await tocar("No");
  await esperarMensaje((m) => m.cuerpo.text.includes("no guardé nada"), "el descarte");
});

test("un monto imposible de guardar: la persona lo sabe y el intento cuenta", async () => {
  await escenario();
  const antes = filas("extracciones").length;
  await guion(salida({ monto: "99999999999999999999", categoria: "otros", descripcion: "x" }));
  await escribir(DEMIAN, "99999999999999999999 de algo");
  await tocar("Sí, guardar");
  await esperarMensaje((m) => m.cuerpo.text.includes("no pude guardar"), "el aviso");
  const extracciones = await esperarExtracciones(antes + 1);
  assert.equal(extracciones.at(-1).resultado, "error_al_guardar");
});

test("una confirmación sin respuesta vence y queda registrada como expirada", async () => {
  await escenario();
  const antes = filas("extracciones").length;
  await guion(salida({ monto: "9", categoria: "transporte", descripcion: "bus" }));
  await escribir(DEMIAN, "9 de bus");
  await esperarMensaje(esConfirmacion, "la confirmación");
  await esperar(async () => (await mensajes()).find((m) => m.cuerpo.text.includes("sin respuesta")), {
    ms: 150000,
    cada: 2000,
    que: "que venza la confirmación (MINUTOS_PARA_CONFIRMAR=1)",
  });
  const extracciones = await esperarExtracciones(antes + 1);
  assert.equal(extracciones.at(-1).resultado, "expirada");
});
