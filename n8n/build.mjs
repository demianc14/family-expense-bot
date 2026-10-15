// Genera n8n/workflow.json. Es la única forma de editarlo: el JSON es un
// artefacto, lo que se lee y se revisa son los nodos definidos acá, la
// biblioteca (n8n/lib) y los cuerpos de los Code nodes (n8n/nodos).
//
//   node n8n/build.mjs           escribe n8n/workflow.json
//   node n8n/build.mjs --check   falla si workflow.json no coincide con las fuentes
//
// Todo es determinista (IDs derivados del nombre del nodo), así que el mismo
// código produce el mismo archivo y el diff de git muestra solo cambios reales.

import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const AQUI = dirname(fileURLToPath(import.meta.url));
const leer = (...partes) => readFileSync(join(AQUI, ...partes), "utf8");

// --- Configuración pública (nada personal: los IDs viven en el entorno) -------

export const WORKFLOW_ID = "FamilyExpenseBot"; // 16 caracteres, como exige n8n
const ZONA = "America/Guayaquil";
const MODELO = "models/gemini-3.5-flash-lite";
// Cuánto espera la confirmación antes de darla por vencida. MINUTOS_PARA_CONFIRMAR
// en el entorno lo cambia (el E2E usa 1 minuto).
const MINUTOS_PARA_CONFIRMAR = 12 * 60;
export const TABLA_GASTOS = "gastos";
export const TABLA_EXTRACCIONES = "extracciones";

// Referencias a credenciales: solo IDs y nombres. Los valores se cargan en n8n
// (ver n8n/credenciales.ejemplo.json), nunca en este repo.
const CRED_TELEGRAM = { telegramApi: { id: "TelegramBotN8n01", name: "Telegram (bot n8n)" } };
const CRED_GEMINI = { googlePalmApi: { id: "GeminiApiN8n0001", name: "Gemini (plan gratuito)" } };

// --- Biblioteca embebida en cada Code node -----------------------------------

function sinBloquesDeTests(codigo) {
  return codigo
    .replace(/^\/\/ <solo-tests>\n[\s\S]*?^\/\/ <\/solo-tests>\n?/gm, "")
    .replace(/^"use strict";\n/m, "");
}

const TAXONOMIA_JSON = JSON.stringify(JSON.parse(leer("taxonomia.json")));

function preludio(libs) {
  if (!libs.length) return "";
  const partes = libs.map((lib) => sinBloquesDeTests(leer("lib", `${lib}.js`)).trim());
  if (partes.some((p) => /<\/?solo-tests>\n|require\(|module\.exports/.test(p))) {
    throw new Error("Quedó código de tests en la biblioteca embebida");
  }
  return [
    "// ==== Generado por n8n/build.mjs desde n8n/lib. No editar acá: se sobrescribe. ====",
    ...partes,
    `const TAXONOMIA = crearTaxonomia(${TAXONOMIA_JSON});`,
    `const ZONA = ${JSON.stringify(ZONA)};`,
    "// ==== Fin de la biblioteca ====",
    "",
  ].join("\n\n");
}

const TODAS = ["verificacion", "operacion", "mensajes"];

function codigo(archivo, libs = TODAS) {
  return preludio(libs) + leer("nodos", `${archivo}.js`);
}

// --- Nodos -------------------------------------------------------------------

function idEstable(texto) {
  const h = createHash("sha256").update(texto).digest("hex");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-4${h.slice(13, 16)}-a${h.slice(17, 20)}-${h.slice(20, 32)}`;
}

const nodos = [];
const conexiones = {};

function nodo(name, type, typeVersion, position, parameters, extra = {}) {
  const n = { parameters, id: idEstable(`nodo:${name}`), name, type, typeVersion, position, ...extra };
  nodos.push(n);
  return name;
}

function conectar(desde, hacia, { salida = 0, tipo = "main" } = {}) {
  const porTipo = (conexiones[desde] ??= {});
  const salidas = (porTipo[tipo] ??= []);
  while (salidas.length <= salida) salidas.push([]);
  salidas[salida].push({ node: hacia, type: tipo, index: 0 });
}

// Telegram interpreta el texto como Markdown (el Send and Wait no deja
// cambiarlo), así que "comida_fuera" o un "*" del LLM romperían el mensaje. Todo
// texto se escapa al salir, nunca antes: la lógica trabaja con el texto real.
const ESCAPAR_MD = 'replace(/[_*`[]/g, (c) => "\\\\" + c)';
const textoDe = (expresion) => `={{ String(${expresion}).${ESCAPAR_MD} }}`;
const textoFijo = (texto) => texto.replace(/[_*`[]/g, (c) => `\\${c}`);

const telegramTexto = (name, pos, texto, chatId = "={{ $('Preparar').first().json.chat_id }}") =>
  nodo(
    name,
    "n8n-nodes-base.telegram",
    1.2,
    pos,
    {
      resource: "message",
      operation: "sendMessage",
      chatId,
      text: texto,
      additionalFields: { appendAttribution: false },
    },
    { credentials: CRED_TELEGRAM, webhookId: idEstable(`webhook:${name}`) },
  );

const code = (name, pos, archivo, libs) =>
  nodo(name, "n8n-nodes-base.code", 2, pos, { jsCode: codigo(archivo, libs) });

/** Switch en modo expresión: `indice` es una expresión que devuelve la salida. */
const ruta = (name, pos, salidas, indice) =>
  nodo(name, "n8n-nodes-base.switch", 3.4, pos, {
    mode: "expression",
    numberOutputs: salidas,
    output: indice,
  });

function columnas(tabla) {
  const tipos = {
    [TABLA_GASTOS]: {
      monto_centavos: "number",
      categoria: "string",
      atribuido_a: "string",
      fecha: "string",
      descripcion: "string",
      texto_original: "string",
      registrado_por: "string",
      creado_en: "string",
      confianza_monto: "string",
      confianza_categoria: "string",
      confianza_fecha: "string",
      confianza_atribuido_a: "string",
      corregido_manualmente: "boolean",
      anulado_en: "string",
      motivo_anulacion: "string",
    },
    [TABLA_EXTRACCIONES]: {
      creado_en: "string",
      resultado: "string",
      confirmada: "boolean",
      corregida: "boolean",
      tenia_monto: "boolean",
      confianza_monto: "string",
      confianza_categoria: "string",
      confianza_fecha: "string",
      confianza_atribuido_a: "string",
    },
  };
  return tipos[tabla];
}

const tablaPorNombre = (tabla) => ({ __rl: true, mode: "name", value: tabla });

function insertar(name, pos, tabla, origen, extra = {}) {
  const cols = columnas(tabla);
  return nodo(
    name,
    "n8n-nodes-base.dataTable",
    1.1,
    pos,
    {
      resource: "row",
      operation: "insert",
      dataTableId: tablaPorNombre(tabla),
      columns: {
        mappingMode: "defineBelow",
        value: Object.fromEntries(Object.keys(cols).map((c) => [c, `={{ ${origen}.${c} }}`])),
        matchingColumns: [],
        schema: Object.entries(cols).map(([id, type]) => ({
          id,
          displayName: id,
          required: false,
          defaultMatch: false,
          display: true,
          type,
          readOnly: false,
          removed: false,
        })),
        attemptToConvertTypes: false,
        convertFieldsToString: false,
      },
      options: {},
    },
    extra,
  );
}

const leerTabla = (name, pos, tabla) =>
  nodo(
    name,
    "n8n-nodes-base.dataTable",
    1.1,
    pos,
    { resource: "row", operation: "get", dataTableId: tablaPorNombre(tabla), returnAll: true },
    { alwaysOutputData: true, executeOnce: true },
  );

const crearTabla = (name, pos, tabla) =>
  nodo(
    name,
    "n8n-nodes-base.dataTable",
    1.1,
    pos,
    {
      resource: "table",
      operation: "create",
      tableName: tabla,
      columns: {
        column: Object.entries(columnas(tabla)).map(([name, type]) => ({ name, type })),
      },
      options: { createIfNotExists: true },
    },
    { executeOnce: true },
  );

// --- Prompt y contrato de salida del agente ----------------------------------

const PROMPT_SISTEMA = `=Eres el asistente de gastos de una familia ecuatoriana en Telegram. Los montos siempre están en dólares estadounidenses.

Hoy es {{ $('Preparar').first().json.hoy }}.

Decide qué hacer con cada mensaje y entrega tu decisión con format_final_json_response:
- "proponer": el mensaje registra UN gasto. Llena los campos del gasto. Tú no guardas nada: la persona confirma después, y un verificador revisa cada campo contra lo que escribió.
- "preguntar": falta el monto, no se entiende, o trae varios gastos. Pregunta en \`mensaje\`, corto y amable; si son varios gastos, pide que los mande uno por uno.
- "reporte": la persona quiere ver cuánto se ha gastado. Si nombra un mes, ponlo en \`mes\` como YYYY-MM.
- "presupuesto": la persona pregunta cuánto le queda o cómo va su presupuesto o algún bolsillo (necesarios, fondo, lujos, ahorro).
- "responder": saludos o cosas que no son gastos. Responde corto en \`mensaje\`.

Si dudas del monto o de quién paga, puedes llamar a verificar_gasto con los campos que propondrías: te dice qué entendería el verificador. Si el monto no aparece en lo que escribió la persona, no lo inventes: pregunta.

Categorías permitidas (usa exactamente uno de estos nombres):
{{ $('Preparar').first().json.categorias }}

Reglas de los campos:
- \`monto\`: copia el número tal como aparece en el mensaje. No lo calcules ni lo redondees.
- \`fecha\`: resuelve expresiones como "ayer" o "el lunes" contra la fecha de hoy, en formato YYYY-MM-DD. Si el mensaje no menciona ninguna fecha, déjala vacía.
- \`quien_paga\`: si el mensaje dice que OTRA persona asumió el costo, copia tal cual cómo la nombra ("mi hija", "mi esposo", "mi ñaña"). Si dice que el gasto es compartido o de la casa, copia esa frase ("gasto de la casa", "compartido"). No resuelvas parentescos: solo copia las palabras.
  - Si quien paga es quien escribe, o el mensaje no lo dice, déjalo vacío.
  - Si alguien aparece solo como beneficiario ("la pensión del colegio de ...", "el regalo para ...") sin decir de qué bolsillo salió, déjalo vacío con confianza "baja".
  - Nunca deduzcas que es de la casa por el tipo de gasto.
- Deja vacío ("") todo campo que el mensaje no diga.
- Declara confianza "baja" cuando el mensaje sea ambiguo o incompleto. Preferimos preguntarle a la persona antes que adivinar: es dinero real.`;

const CONFIANZA = { type: "string", enum: ["alta", "media", "baja"] };
const ESQUEMA_SALIDA = {
  type: "object",
  properties: {
    accion: {
      type: "string",
      enum: ["proponer", "preguntar", "reporte", "presupuesto", "responder"],
    },
    mensaje: { type: "string", description: "Texto para la persona. Vacío en proponer y reporte." },
    monto: { type: "string", description: "Solo el número, tal como aparece en el mensaje." },
    categoria: { type: "string", description: "Una de las categorías permitidas." },
    fecha: { type: "string", description: "YYYY-MM-DD, o vacío si el mensaje no la menciona." },
    quien_paga: { type: "string", description: "Cómo nombra el mensaje a quien asume el costo, copiado tal cual." },
    descripcion: { type: "string", description: "Qué se compró, en pocas palabras." },
    mes: { type: "string", description: "Solo para reporte: YYYY-MM." },
    confianza_monto: CONFIANZA,
    confianza_categoria: CONFIANZA,
    confianza_fecha: CONFIANZA,
    confianza_quien_paga: CONFIANZA,
  },
  required: [
    "accion",
    "mensaje",
    "confianza_monto",
    "confianza_categoria",
    "confianza_fecha",
    "confianza_quien_paga",
  ],
};

const deIA = (clave, descripcion) => `={{ $fromAI('${clave}', \`${descripcion}\`, 'string', '') }}`;
const ENTRADAS_TOOL = {
  herramienta: "verificar_gasto",
  monto: deIA("monto", "Solo el número del monto, tal como aparece en el mensaje. Vacío si no hay."),
  categoria: deIA("categoria", "Una de las categorías permitidas."),
  fecha: deIA("fecha", "YYYY-MM-DD, o vacío si el mensaje no la menciona."),
  quien_paga: deIA("quien_paga", "Cómo nombra el mensaje a quien asume el costo, copiado tal cual. Vacío si no lo dice."),
  descripcion: deIA("descripcion", "Qué se compró, en pocas palabras."),
  confianza_monto: deIA("confianza_monto", "alta, media o baja"),
  confianza_categoria: deIA("confianza_categoria", "alta, media o baja"),
  confianza_fecha: deIA("confianza_fecha", "alta, media o baja"),
  confianza_quien_paga: deIA("confianza_quien_paga", "alta, media o baja"),
  // Estos tres los pone el workflow. Si los pusiera el LLM, podría elegir el
  // parentesco por la puerta de atrás.
  texto: "={{ $('Preparar').first().json.texto_respaldo }}",
  remitente: "={{ $('Preparar').first().json.remitente }}",
  hoy: "={{ $('Preparar').first().json.hoy }}",
};

// --- Flujo principal ---------------------------------------------------------

const X = (col) => col * 240;
const Y = (fila) => fila * 180;

nodo(
  "Telegram",
  "n8n-nodes-base.telegramTrigger",
  1.5,
  [X(0), Y(2)],
  { updates: ["message"], additionalFields: {} },
  { credentials: CRED_TELEGRAM, webhookId: idEstable("webhook:Telegram") },
);
code("Preparar", [X(1), Y(2)], "preparar");
conectar("Telegram", "Preparar");

// Las tablas se aseguran en cada mensaje autorizado (createIfNotExists): así
// instalar es solo importar y publicar. El CLI de n8n no puede crearlas: \`n8n
// execute\` no carga el módulo de Data Tables.
crearTabla("Tabla de gastos", [X(2), Y(2)], TABLA_GASTOS);
crearTabla("Tabla de extracciones", [X(3), Y(2)], TABLA_EXTRACCIONES);
conectar("Preparar", "Tabla de gastos");
conectar("Tabla de gastos", "Tabla de extracciones");

ruta("Ruta", [X(4), Y(2)], 4,
  `={{ ["start", "reporte", "agente", "presupuesto"].indexOf($('Preparar').first().json.ruta) }}`);
conectar("Tabla de extracciones", "Ruta");

telegramTexto(
  "Bienvenida",
  [X(5), Y(0)],
  textoFijo(
  "Hola 👋 Escríbeme tus gastos como los dirías normalmente, " +
    'por ejemplo "200 dólares supermercado" o "pagué 15 de taxi ayer".\n\n' +
    "Si no dices de quién es, asumo que es tuyo. Para gastos compartidos, " +
    'dilo: "gasolina de la casa".\n\n' +
    "Te muestro lo que entendí y solo guardo si me dices que sí.\n\n" +
    "Escribe /reporte para ver el total del mes y /presupuesto para ver " +
    "cuánto te queda en cada bolsillo.",
  ),
);
conectar("Ruta", "Bienvenida", { salida: 0 });

// Reporte: determinista, por /reporte o porque el agente lo pidió.
leerTabla("Leer gastos", [X(10), Y(0)], TABLA_GASTOS);
leerTabla("Leer extracciones", [X(11), Y(0)], TABLA_EXTRACCIONES);
code("Armar reporte", [X(12), Y(0)], "armar_reporte");
telegramTexto("Enviar reporte", [X(13), Y(0)], textoDe("$json.mensaje"));
conectar("Ruta", "Leer gastos", { salida: 1 });
conectar("Leer gastos", "Leer extracciones");
conectar("Leer extracciones", "Armar reporte");
conectar("Armar reporte", "Enviar reporte");

// Presupuesto: determinista, por /presupuesto o porque el agente lo pidió. Como el
// reporte, los montos no pasan por el LLM.
leerTabla("Leer gastos del presupuesto", [X(10), Y(-1)], TABLA_GASTOS);
code("Armar presupuesto", [X(11), Y(-1)], "armar_presupuesto");
telegramTexto("Enviar presupuesto", [X(12), Y(-1)], textoDe("$json.mensaje"));
conectar("Ruta", "Leer gastos del presupuesto", { salida: 3 });
conectar("Leer gastos del presupuesto", "Armar presupuesto");
conectar("Armar presupuesto", "Enviar presupuesto");

// Hilo abandonado: también es un intento que cuenta.
ruta("¿Hilo abandonado?", [X(7), Y(5)], 2, "={{ $('Preparar').first().json.hilo_abandonado ? 0 : 1 }}");
code("Hilo abandonado", [X(6), Y(5)], "hilo_abandonado", []);
conectar("Ruta", "¿Hilo abandonado?", { salida: 2 });
conectar("¿Hilo abandonado?", "Hilo abandonado");

telegramTexto("Leyendo", [X(5), Y(2)], textoFijo("Leyendo tu mensaje..."));
conectar("Ruta", "Leyendo", { salida: 2 });

nodo(
  "Agente",
  "@n8n/n8n-nodes-langchain.agent",
  3.1,
  [X(6), Y(2)],
  {
    promptType: "define",
    text: "={{ $('Preparar').first().json.texto }}",
    hasOutputParser: true,
    options: {
      systemMessage: PROMPT_SISTEMA,
      maxIterations: 4,
      returnIntermediateSteps: true,
      enableStreaming: false,
      passthroughBinaryImages: false,
    },
  },
  { onError: "continueErrorOutput" },
);
conectar("Leyendo", "Agente");

nodo("Gemini", "@n8n/n8n-nodes-langchain.lmChatGoogleGemini", 1.1, [X(5), Y(4)], {
  modelName: MODELO,
  options: {},
}, { credentials: CRED_GEMINI });
nodo("Memoria del hilo", "@n8n/n8n-nodes-langchain.memoryBufferWindow", 1.4, [X(6), Y(4)], {
  sessionIdType: "customKey",
  sessionKey: "={{ $('Preparar').first().json.sesion }}",
  contextWindowLength: 6,
});
nodo("verificar_gasto", "@n8n/n8n-nodes-langchain.toolWorkflow", 2.2, [X(7), Y(4)], {
  description:
    "Revisa un gasto como lo haría el verificador: si el monto aparece en el mensaje, " +
    "si la categoría existe, si la fecha es razonable y a quién corresponde la frase de " +
    "quien_paga. Úsala solo si dudas; la decisión final la toma igual el verificador.",
  workflowId: { __rl: true, mode: "id", value: "={{ $workflow.id }}" },
  workflowInputs: {
    mappingMode: "defineBelow",
    value: ENTRADAS_TOOL,
    matchingColumns: [],
    schema: Object.keys(ENTRADAS_TOOL).map((id) => ({
      id,
      displayName: id,
      required: false,
      defaultMatch: false,
      display: true,
      canBeUsedToMatch: true,
      type: "string",
      removed: false,
    })),
    attemptToConvertTypes: false,
    convertFieldsToString: false,
  },
});
nodo("Formato de salida", "@n8n/n8n-nodes-langchain.outputParserStructured", 1.3, [X(8), Y(4)], {
  schemaType: "manual",
  inputSchema: JSON.stringify(ESQUEMA_SALIDA, null, 2),
});
conectar("Gemini", "Agente", { tipo: "ai_languageModel" });
conectar("Memoria del hilo", "Agente", { tipo: "ai_memory" });
conectar("verificar_gasto", "Agente", { tipo: "ai_tool" });
conectar("Formato de salida", "Agente", { tipo: "ai_outputParser" });

// Fallos del agente: cuota, timeout, contrato. Siempre con motivo.
code("Clasificar fallo", [X(7), Y(3)], "clasificar_fallo");
telegramTexto("Avisar fallo", [X(10), Y(3)], textoDe("$json.mensaje"));
conectar("Agente", "Clasificar fallo", { salida: 1 });
conectar("Clasificar fallo", "Avisar fallo");

code("Revisar agente", [X(7), Y(2)], "revisar_agente");
conectar("Agente", "Revisar agente", { salida: 0 });

const RUTAS_AGENTE = ["proponer", "enviar", "reporte", "no_propuesto", "fallo", "presupuesto"];
ruta("Según la acción", [X(8), Y(2)], RUTAS_AGENTE.length,
  `={{ ${JSON.stringify(RUTAS_AGENTE)}.indexOf($json.ruta) }}`);
conectar("Revisar agente", "Según la acción");

code("Compuerta", [X(9), Y(2)], "compuerta");
telegramTexto("Enviar respuesta", [X(9), Y(1)], textoDe("$json.mensaje"));
conectar("Según la acción", "Compuerta", { salida: 0 });
conectar("Según la acción", "Enviar respuesta", { salida: 1 });
conectar("Según la acción", "Leer gastos", { salida: 2 });
// salida 3 (no_propuesto) va al embudo, más abajo
conectar("Según la acción", "Avisar fallo", { salida: 4 });
conectar("Según la acción", "Leer gastos del presupuesto", { salida: 5 });

const ESTADOS_COMPUERTA = ["ofrecer", "sin_monto", "contrato_roto"];
ruta("¿Se puede ofrecer?", [X(10), Y(2)], ESTADOS_COMPUERTA.length,
  `={{ ${JSON.stringify(ESTADOS_COMPUERTA)}.indexOf($json.estado) }}`);
conectar("Compuerta", "¿Se puede ofrecer?");
conectar("¿Se puede ofrecer?", "Avisar fallo", { salida: 2 });

// La confirmación humana. Guardar solo es alcanzable desde su salida.
nodo(
  "Confirmar",
  "n8n-nodes-base.telegram",
  1.2,
  [X(11), Y(2)],
  {
    resource: "message",
    operation: "sendAndWait",
    chatId: "={{ $json.chat_id }}",
    message: textoDe("$json.mensaje"),
    responseType: "approval",
    approvalOptions: {
      values: { approvalType: "double", approveLabel: "Sí, guardar", disapproveLabel: "No" },
    },
    chatApproval: true,
    approverIds: "={{ $json.usuario_id }}",
    unauthorizedReplyText: "Solo quien registró el gasto puede confirmarlo.",
    postDecisionBehavior: "removeButtons",
    options: {
      limitWaitTime: {
        values: {
          limitType: "afterTimeInterval",
          resumeAmount: `={{ Number($env.MINUTOS_PARA_CONFIRMAR) || ${MINUTOS_PARA_CONFIRMAR} }}`,
          resumeUnit: "minutes",
        },
      },
      appendAttribution: false,
    },
  },
  { credentials: CRED_TELEGRAM, webhookId: idEstable("webhook:Confirmar") },
);
conectar("¿Se puede ofrecer?", "Confirmar", { salida: 0 });

code("Decidir", [X(12), Y(2)], "decidir");
conectar("Confirmar", "Decidir");
ruta("¿Guardar?", [X(13), Y(2)], 2, `={{ $json.estado === "guardar" ? 0 : 1 }}`);
conectar("Decidir", "¿Guardar?");

insertar("Guardar gasto", [X(14), Y(1)], TABLA_GASTOS, "$json.fila_gasto", {
  onError: "continueErrorOutput",
});
// Ya guardado, se leen los gastos para decir cuánto queda en el bolsillo.
leerTabla("Gastos tras guardar", [X(15), Y(0)], TABLA_GASTOS);
code("Guardado", [X(16), Y(1)], "guardado");
code("Guardar falló", [X(15), Y(2)], "guardar_fallo");
conectar("¿Guardar?", "Guardar gasto", { salida: 0 });
conectar("Guardar gasto", "Gastos tras guardar", { salida: 0 });
conectar("Gastos tras guardar", "Guardado");
conectar("Guardar gasto", "Guardar falló", { salida: 1 });

// Embudo: todo resultado se registra una sola vez, se haya guardado o no.
code("Fila de extracción", [X(16), Y(3)], "fila_extraccion");
for (const [desde, salida] of [
  ["Guardado", 0],
  ["Guardar falló", 0],
  ["¿Guardar?", 1], // rechazada, expirada o error antes de guardar
  ["¿Se puede ofrecer?", 1], // sin monto
  ["Según la acción", 3], // el agente no propuso y había un número
  ["Hilo abandonado", 0],
]) {
  conectar(desde, "Fila de extracción", { salida });
}
ruta("¿Hay mensaje?", [X(17), Y(3)], 2, "={{ $json.mensaje ? 0 : 1 }}");
telegramTexto("Avisar resultado", [X(18), Y(2)], textoDe("$('Fila de extracción').first().json.mensaje"));
insertar("Registrar extracción", [X(19), Y(3)], TABLA_EXTRACCIONES, "$('Fila de extracción').first().json.fila");
conectar("Fila de extracción", "¿Hay mensaje?");
conectar("¿Hay mensaje?", "Avisar resultado", { salida: 0 });
conectar("¿Hay mensaje?", "Registrar extracción", { salida: 1 });
conectar("Avisar resultado", "Registrar extracción");

// --- La tool verificar_gasto: este mismo workflow, llamado por el agente ------

nodo("Llamada de la tool", "n8n-nodes-base.executeWorkflowTrigger", 1.2, [X(0), Y(7)], {
  inputSource: "workflowInputs",
  workflowInputs: {
    values: Object.keys(ENTRADAS_TOOL).map((name) => ({ name, type: "string" })),
  },
});
code("Tool verificar_gasto", [X(1), Y(7)], "tool_verificar_gasto", ["verificacion", "operacion"]);
conectar("Llamada de la tool", "Tool verificar_gasto");

// --- Último recurso: fallos no previstos, avisados a quien mantiene el bot -----

nodo("Error en el workflow", "n8n-nodes-base.errorTrigger", 1, [X(0), Y(11)], {});
code("Aviso de error", [X(1), Y(11)], "aviso_error", []);
telegramTexto("Avisar a quien mantiene", [X(2), Y(11)], textoDe("$json.mensaje"), "={{ $json.chat_id }}");
conectar("Error en el workflow", "Aviso de error");
conectar("Aviso de error", "Avisar a quien mantiene");

// --- Salida ------------------------------------------------------------------

const AJUSTES = {
  executionOrder: "v1",
  timezone: ZONA,
  callerPolicy: "workflowsFromSameOwner",
  // Una ejecución exitosa lleva montos y el texto del mensaje: no se guarda.
  // Las fallidas sí, para poder depurarlas (quedan en la base local de n8n).
  saveDataSuccessExecution: "none",
  saveDataErrorExecution: "all",
  saveManualExecutions: false,
  saveExecutionProgress: false,
};

export const workflow = {
  id: WORKFLOW_ID,
  name: "family-expense-bot (n8n)",
  nodes: nodos,
  connections: conexiones,
  settings: { ...AJUSTES, errorWorkflow: WORKFLOW_ID },
  pinData: {},
  active: false,
  tags: [],
};

const SALIDAS = {
  [join(AQUI, "workflow.json")]: workflow,
};

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  let desactualizados = 0;
  for (const [destino, contenido] of Object.entries(SALIDAS)) {
    const texto = `${JSON.stringify(contenido, null, 2)}\n`;
    if (process.argv.includes("--check")) {
      let actual = "";
      try {
        actual = readFileSync(destino, "utf8");
      } catch {}
      if (actual !== texto) {
        console.error(`${destino} no coincide con sus fuentes: corre \`node n8n/build.mjs\`.`);
        desactualizados++;
      }
    } else {
      writeFileSync(destino, texto);
      console.log(`Escrito ${destino} (${contenido.nodes.length} nodos)`);
    }
  }
  if (desactualizados) process.exit(1);
}
