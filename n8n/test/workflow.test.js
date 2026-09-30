// Invariantes de n8n/workflow.json que se revisan sin levantar n8n.
//
// Lo central: guardar exige confirmación humana por ESTRUCTURA. Estos tests
// recorren el grafo del workflow y fallan si alguien reconecta un nodo de modo
// que se pueda guardar sin pasar por "Confirmar", si el agente gana una tool
// nueva, o si un resultado deja de registrarse.

"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const path = require("node:path");
const vm = require("node:vm");

const RAIZ = path.join(__dirname, "..");
const workflow = require("../workflow.json");
const { n8n: VERSION_N8N, tipos: TIPOS } = require("./tipos-n8n-2.40.7.json");

const porNombre = Object.fromEntries(workflow.nodes.map((n) => [n.name, n]));
const TRIGGERS = workflow.nodes.filter((n) => /Trigger$|trigger$/i.test(n.type)).map((n) => n.name);

/** Nodos alcanzables por conexiones `main` desde `inicio`, sin pasar por `excluidos`. */
function alcanzables(inicios, excluidos = new Set()) {
  const vistos = new Set();
  const pendientes = [...inicios].filter((n) => !excluidos.has(n));
  while (pendientes.length) {
    const actual = pendientes.pop();
    if (vistos.has(actual)) continue;
    vistos.add(actual);
    for (const salida of workflow.connections[actual]?.main ?? []) {
      for (const destino of salida ?? []) {
        if (!excluidos.has(destino.node)) pendientes.push(destino.node);
      }
    }
  }
  return vistos;
}

function destinos(nodo, salida, tipo = "main") {
  return (workflow.connections[nodo]?.[tipo]?.[salida] ?? []).map((d) => d.node);
}

function conectadosA(nodo, tipo) {
  return Object.entries(workflow.connections)
    .filter(([, porTipo]) => (porTipo[tipo] ?? []).flat().some((d) => d.node === nodo))
    .map(([origen]) => origen);
}

const insertaEn = (tabla) =>
  workflow.nodes.filter(
    (n) =>
      n.type === "n8n-nodes-base.dataTable" &&
      n.parameters.operation === "insert" &&
      n.parameters.dataTableId.value === tabla,
  );

// --- Sincronía con las fuentes -----------------------------------------------------

test("workflow.json está generado desde las fuentes actuales", () => {
  execFileSync(process.execPath, [path.join(RAIZ, "build.mjs"), "--check"], { stdio: "pipe" });
});

test(`cada nodo existe en n8n ${VERSION_N8N} con esa versión`, () => {
  for (const n of workflow.nodes) {
    assert.ok(TIPOS[n.type], `${n.name}: el tipo ${n.type} no existe`);
    assert.ok(TIPOS[n.type].includes(n.typeVersion), `${n.name}: ${n.type} v${n.typeVersion}`);
  }
});

test("las conexiones apuntan a nodos que existen", () => {
  for (const [origen, porTipo] of Object.entries(workflow.connections)) {
    assert.ok(porNombre[origen], `origen inexistente: ${origen}`);
    for (const d of Object.values(porTipo).flat(2)) assert.ok(porNombre[d.node], `${origen} → ${d.node}`);
  }
});

// --- Guardar exige confirmación humana, por estructura -----------------------------

test("guardar un gasto solo es alcanzable pasando por Confirmar", () => {
  const guardar = insertaEn("gastos");
  assert.deepEqual(guardar.map((n) => n.name), ["Guardar gasto"], "un solo nodo escribe gastos");

  const sinConfirmar = alcanzables(TRIGGERS, new Set(["Confirmar"]));
  assert.ok(!sinConfirmar.has("Guardar gasto"), "hay un camino a Guardar gasto que evita Confirmar");
  assert.ok(alcanzables(TRIGGERS).has("Guardar gasto"), "Guardar gasto quedó desconectado");
});

test("después de Confirmar solo se guarda por la rama aprobada", () => {
  assert.deepEqual(destinos("Confirmar", 0), ["Decidir"]);
  assert.match(porNombre["Decidir"].parameters.jsCode, /aprobado === true/);
  assert.deepEqual(destinos("¿Guardar?", 0), ["Guardar gasto"]);
  assert.match(porNombre["¿Guardar?"].parameters.output, /\$json\.estado === "guardar"/);
});

test("el agente tiene una sola tool, y guardar no es una de ellas", () => {
  assert.deepEqual(conectadosA("Agente", "ai_tool"), ["verificar_gasto"]);
  const tool = alcanzables(["Llamada de la tool"]);
  for (const nombre of tool) {
    assert.notEqual(porNombre[nombre].type, "n8n-nodes-base.dataTable", `la tool llega a ${nombre}`);
  }
});

test("quién escribe, el texto y la fecha los pone el workflow, no el modelo", () => {
  const entradas = porNombre["verificar_gasto"].parameters.workflowInputs.value;
  for (const campo of ["texto", "remitente", "hoy"]) {
    assert.doesNotMatch(entradas[campo], /\$fromAI/, campo);
    assert.match(entradas[campo], /\$\('Preparar'\)/, campo);
  }
  assert.match(entradas.quien_paga, /\$fromAI\('quien_paga'/);
});

test("la salida del agente no tiene campo de rol: el LLM no puede elegir el parentesco", () => {
  const esquema = JSON.parse(porNombre["Formato de salida"].parameters.inputSchema);
  const campos = Object.keys(esquema.properties);
  assert.ok(campos.includes("quien_paga"));
  for (const prohibido of ["atribuido_a", "rol", "remitente"]) assert.ok(!campos.includes(prohibido));
  assert.deepEqual(conectadosA("Agente", "ai_outputParser"), ["Formato de salida"]);
});

// --- Fallos visibles ------------------------------------------------------------------

test("un fallo del agente sale por su salida de error y se avisa", () => {
  assert.equal(porNombre["Agente"].onError, "continueErrorOutput");
  assert.deepEqual(destinos("Agente", 1), ["Clasificar fallo"]);
  assert.deepEqual(destinos("Clasificar fallo", 0), ["Avisar fallo"]);
  assert.equal(porNombre["Agente"].parameters.options.returnIntermediateSteps, true);
});

test("un fallo al guardar se avisa y cuenta como intento", () => {
  assert.equal(porNombre["Guardar gasto"].onError, "continueErrorOutput");
  assert.deepEqual(destinos("Guardar gasto", 1), ["Guardar falló"]);
});

test("el workflow es su propio Error Workflow y avisa a quien lo mantiene", () => {
  assert.equal(workflow.settings.errorWorkflow, workflow.id);
  assert.ok(alcanzables(["Error en el workflow"]).has("Avisar a quien mantiene"));
});

// --- Cada intento se registra (sin sesgo del superviviente) -------------------------

test("todo resultado termina en Registrar extracción", () => {
  const resultados = [
    ["Guardado", "confirmada"],
    ["Guardar falló", "error_al_guardar"],
    ["Decidir", "rechazada / expirada"],
    ["Compuerta", "no_ofrecible"],
    ["Revisar agente", "no_propuesto"],
    ["Hilo abandonado", "abandonada"],
  ];
  for (const [nodo, resultado] of resultados) {
    assert.ok(alcanzables([nodo]).has("Registrar extracción"), `${resultado} (${nodo}) no se registra`);
  }
  assert.deepEqual(insertaEn("extracciones").map((n) => n.name), ["Registrar extracción"]);
});

test("la fila de extracción no lleva montos ni texto", () => {
  const columnas = Object.keys(porNombre["Registrar extracción"].parameters.columns.value);
  for (const c of columnas) assert.doesNotMatch(c, /^monto|texto|descripcion/, c);
});

// --- Estado y privacidad ----------------------------------------------------------------

test("después de esperar la confirmación nadie toca el estado del workflow", () => {
  // Una ejecución reanudada guarda su copia vieja del estado: si lo tocara,
  // pisaría los hilos abiertos mientras tanto.
  for (const nombre of alcanzables(["Decidir"])) {
    const cuerpo = (porNombre[nombre].parameters.jsCode ?? "").split("// ==== Fin de la biblioteca ====").at(-1);
    assert.doesNotMatch(cuerpo, /\$getWorkflowStaticData/, nombre);
  }
});

test("las ejecuciones exitosas (con montos y mensajes) no se guardan", () => {
  assert.equal(workflow.settings.saveDataSuccessExecution, "none");
  assert.equal(workflow.settings.saveManualExecutions, false);
});

test("todo texto que va a Telegram escapa el Markdown", () => {
  for (const n of workflow.nodes.filter((n) => n.type === "n8n-nodes-base.telegram")) {
    const texto = n.parameters.text ?? n.parameters.message;
    if (texto.startsWith("=")) assert.match(texto, /\.replace\(\/\[_\*`\[\]\/g/, n.name);
    else assert.doesNotMatch(texto.replace(/\\[_*`[]/g, ""), /[_*`[]/, n.name);
  }
});

test("las credenciales son referencias, sin valores", () => {
  for (const n of workflow.nodes.filter((n) => n.credentials)) {
    for (const ref of Object.values(n.credentials)) {
      assert.deepEqual(Object.keys(ref).sort(), ["id", "name"], n.name);
    }
  }
  assert.doesNotMatch(JSON.stringify(workflow), /accessToken|apiKey|\d{8,10}:AA/);
});

// --- Code nodes -------------------------------------------------------------------------

test("cada Code node compila y su biblioteca carga completa", () => {
  for (const n of workflow.nodes.filter((n) => n.type === "n8n-nodes-base.code")) {
    const codigo = n.parameters.jsCode;
    assert.doesNotThrow(() => new vm.Script(`(async () => {${codigo}\n})`), n.name);
    if (!codigo.includes("Fin de la biblioteca")) continue;
    const contexto = vm.createContext({});
    vm.runInContext(
      `${codigo.split("// ==== Fin de la biblioteca ====")[0]}
       globalThis.resultado = [TAXONOMIA.personas.length, typeof verificarGasto, typeof filaExtraccion];`,
      contexto,
    );
    // JSON: el arreglo viene de otro realm (vm) y deepEqual compara prototipos.
    assert.deepEqual(JSON.parse(JSON.stringify(contexto.resultado)), [4, "function", "function"], n.name);
  }
});
