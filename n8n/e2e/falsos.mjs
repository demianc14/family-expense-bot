// Telegram y Gemini falsos, para probar el workflow en un n8n real sin tokens
// reales ni cuota. Sin dependencias: node falsos.mjs
//
// - Telegram: /bot<token>/<método>. Registra cada llamada. Cuando n8n registra
//   su webhook, lo guarda para poder "mandar" mensajes como lo haría Telegram.
// - Gemini: /v1beta/models/<modelo>:generateContent. Responde con un guion que
//   el test carga antes de cada escenario (llamar una tool, contestar, fallar
//   con 429). Registra cada request, para revisar qué le llega al modelo.
// - /_control/*: lo usa el test.

import { createServer } from "node:http";

const PUERTO = Number(process.env.PUERTO || 8080);

let estado;
function reiniciar() {
  estado = { telegram: [], gemini: [], guion: [], webhook: null, siguienteMensaje: 1 };
}
reiniciar();

function leerCuerpo(req) {
  return new Promise((resolve) => {
    let datos = "";
    req.on("data", (c) => (datos += c));
    req.on("end", () => {
      if (!datos) return resolve({});
      const tipo = req.headers["content-type"] || "";
      try {
        if (tipo.includes("application/json")) return resolve(JSON.parse(datos));
        if (tipo.includes("x-www-form-urlencoded")) {
          return resolve(Object.fromEntries(new URLSearchParams(datos)));
        }
        return resolve(JSON.parse(datos));
      } catch {
        return resolve({ crudo: datos });
      }
    });
  });
}

function responder(res, status, cuerpo) {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(cuerpo));
}

// --- Telegram ------------------------------------------------------------------

/**
 * Valida el Markdown "legacy" de Telegram como lo hace la API real: `_`, `*` y
 * `` ` `` tienen que cerrarse, `[` tiene que ser un enlace, y `\` escapa a
 * los cuatro. Si no, Telegram responde 400 "can't parse entities".
 */
function markdownValido(texto) {
  const abiertos = new Set();
  for (let i = 0; i < texto.length; i++) {
    const c = texto[i];
    if (abiertos.has("`")) {
      if (c === "`") abiertos.delete("`");
      continue;
    }
    if (c === "\\" && "_*`[".includes(texto[i + 1] ?? "")) {
      i++;
      continue;
    }
    if (c === "_" || c === "*" || c === "`") {
      if (abiertos.has(c)) abiertos.delete(c);
      else abiertos.add(c);
    } else if (c === "[") {
      const enlace = /^\[[^\]]*\]\([^)]*\)/.exec(texto.slice(i));
      if (!enlace) return false;
      i += enlace[0].length - 1;
    }
  }
  return abiertos.size === 0;
}

function telegram(metodo, cuerpo) {
  estado.telegram.push({ metodo, cuerpo, en: Date.now() });
  switch (metodo) {
    case "getMe":
      return { id: 9000, is_bot: true, first_name: "Bot de prueba", username: "bot_de_prueba" };
    case "setWebhook":
      estado.webhook = {
        url: cuerpo.url,
        secreto: cuerpo.secret_token,
        allowed_updates: cuerpo.allowed_updates,
      };
      return true;
    case "deleteWebhook":
      estado.webhook = null;
      return true;
    case "getWebhookInfo":
      return {
        url: estado.webhook ? estado.webhook.url : "",
        allowed_updates: estado.webhook ? estado.webhook.allowed_updates || [] : [],
      };
    case "sendMessage":
      return {
        message_id: estado.siguienteMensaje++,
        date: Math.floor(Date.now() / 1000),
        chat: { id: Number(cuerpo.chat_id), type: "private" },
        text: cuerpo.text,
      };
    default:
      return true;
  }
}

// --- Gemini --------------------------------------------------------------------

function respuestaGemini(turno, modelo) {
  const parte =
    turno.tipo === "funcion"
      ? { functionCall: { name: turno.nombre, args: turno.args || {} } }
      : { text: turno.texto };
  return {
    candidates: [{ content: { role: "model", parts: [parte] }, finishReason: "STOP", index: 0 }],
    usageMetadata: { promptTokenCount: 10, candidatesTokenCount: 5, totalTokenCount: 15 },
    modelVersion: modelo,
  };
}

function gemini(res, modelo, operacion, cuerpo) {
  estado.gemini.push({ modelo, operacion, cuerpo, en: Date.now() });
  const turno = estado.guion.shift();
  if (!turno) {
    return responder(res, 500, { error: { code: 500, message: "falsos.mjs: el guion está vacío" } });
  }
  if (turno.tipo === "error") {
    return responder(res, turno.status, {
      error: { code: turno.status, message: turno.mensaje, status: turno.estado || "ERROR" },
    });
  }
  const datos = respuestaGemini(turno, modelo);
  if (operacion === "streamGenerateContent") {
    res.writeHead(200, { "content-type": "text/event-stream" });
    res.end(`data: ${JSON.stringify(datos)}\n\n`);
    return;
  }
  return responder(res, 200, datos);
}

// --- Control -------------------------------------------------------------------

async function mandarUpdate(update) {
  if (!estado.webhook) throw new Error("n8n todavía no registró su webhook");
  const r = await fetch(estado.webhook.url, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(estado.webhook.secreto
        ? { "x-telegram-bot-api-secret-token": estado.webhook.secreto }
        : {}),
    },
    body: JSON.stringify(update),
  });
  return { status: r.status, cuerpo: await r.text() };
}

createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost");
  const cuerpo = await leerCuerpo(req);
  try {
    let m = url.pathname.match(/^\/bot[^/]+\/(\w+)$/);
    if (m) {
      if (cuerpo.parse_mode === "Markdown" && typeof cuerpo.text === "string" && !markdownValido(cuerpo.text)) {
        estado.telegram.push({ metodo: m[1], cuerpo, en: Date.now(), rechazado: true });
        return responder(res, 400, {
          ok: false,
          error_code: 400,
          description: "Bad Request: can't parse entities",
        });
      }
      return responder(res, 200, { ok: true, result: telegram(m[1], cuerpo) });
    }

    m = url.pathname.match(/^\/v1(?:beta)?\/models\/([^:]+):(\w+)$/);
    if (m) return gemini(res, m[1], m[2], cuerpo);

    switch (url.pathname) {
      case "/_control/salud":
        return responder(res, 200, { ok: true });
      case "/_control/reiniciar": {
        const webhook = estado.webhook; // el webhook sobrevive entre escenarios
        reiniciar();
        estado.webhook = webhook;
        return responder(res, 200, { ok: true });
      }
      case "/_control/guion":
        estado.guion.push(...cuerpo.turnos);
        return responder(res, 200, { ok: true, pendientes: estado.guion.length });
      case "/_control/estado":
        return responder(res, 200, estado);
      case "/_control/update":
        return responder(res, 200, await mandarUpdate(cuerpo));
      case "/_control/abrir": {
        // Tocar un botón con enlace: lo abre el navegador de la persona. n8n
        // ignora en silencio los User-Agent que parecen bots (así una vista
        // previa del enlace no aprueba sola), así que acá se imita un navegador.
        const r = await fetch(cuerpo.url, {
          headers: {
            "user-agent":
              "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36",
          },
        });
        return responder(res, 200, { status: r.status });
      }
      default:
        return responder(res, 404, { ok: false, description: `falsos.mjs: no existe ${url.pathname}` });
    }
  } catch (e) {
    return responder(res, 500, { ok: false, description: e.message });
  }
}).listen(PUERTO, () => console.log(`falsos.mjs escuchando en ${PUERTO}`));
