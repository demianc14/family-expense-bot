// Levanta el entorno E2E desde cero: n8n real + falsos, importa credenciales
// sintéticas y el workflow, y lo publica. Las tablas las crea el workflow.
//
//   node n8n/e2e/entorno.mjs          lo levanta y lo deja corriendo
//   node n8n/e2e/entorno.mjs --bajar  lo baja y borra los datos

import { execFileSync } from "node:child_process";
import { mkdirSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { fileURLToPath } from "node:url";

const AQUI = dirname(fileURLToPath(import.meta.url));
const DATOS = join(AQUI, ".datos");
export const FALSOS = "http://localhost:18080";

function compose(...args) {
  return execFileSync("docker", ["compose", ...args], {
    cwd: AQUI,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });
}

/** Un comando del CLI de n8n dentro del contenedor, con su propio puerto de broker. */
export function n8n(...args) {
  return compose("exec", "-T", "-e", "N8N_RUNNERS_BROKER_PORT=5690", "n8n", "n8n", ...args);
}

export async function control(ruta, cuerpo) {
  const r = await fetch(`${FALSOS}/_control/${ruta}`, {
    method: cuerpo === undefined ? "GET" : "POST",
    headers: { "content-type": "application/json" },
    body: cuerpo === undefined ? undefined : JSON.stringify(cuerpo),
  });
  return r.json();
}

export async function esperar(condicion, { ms = 20000, cada = 250, que = "la condición" } = {}) {
  const limite = Date.now() + ms;
  for (;;) {
    const valor = await condicion();
    if (valor) return valor;
    if (Date.now() > limite) throw new Error(`Se agotó el tiempo esperando ${que}`);
    await new Promise((r) => setTimeout(r, cada));
  }
}

export function bajar() {
  try {
    compose("down", "-v", "--remove-orphans");
  } catch {}
  rmSync(DATOS, { recursive: true, force: true });
}

export async function levantar() {
  bajar();
  mkdirSync(DATOS, { recursive: true });
  compose("up", "-d", "--wait");
  n8n("import:credentials", "--input=/importar/credenciales.json");
  n8n("import:workflow", "--input=/importar/workflow.json");
  n8n("publish:workflow", "--id=FamilyExpenseBot");
  compose("restart", "n8n");
  compose("up", "-d", "--wait");
  await esperar(async () => (await control("estado")).webhook, {
    ms: 60000,
    que: "que n8n registre el webhook de Telegram",
  });
}

/** Reimporta y republica el workflow sin bajar el entorno (para iterar rápido). */
export async function recargar() {
  n8n("import:workflow", "--input=/importar/workflow.json");
  n8n("publish:workflow", "--id=FamilyExpenseBot");
  compose("restart", "n8n");
  compose("up", "-d", "--wait");
}

export function logs(lineas = 200) {
  return compose("logs", "n8n", "--tail", String(lineas));
}

/** Filas de una Data Table, leídas directo de la base SQLite de n8n. */
export function filas(tabla) {
  const db = new DatabaseSync(join(DATOS, "database.sqlite"), { readOnly: true });
  try {
    const meta = db.prepare("SELECT id FROM data_table WHERE name = ?").get(tabla);
    if (!meta) throw new Error(`No existe la tabla ${tabla}`);
    return db.prepare(`SELECT * FROM "data_table_user_${meta.id}" ORDER BY id`).all();
  } finally {
    db.close();
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  if (process.argv.includes("--recargar")) {
    await recargar();
    console.log("Workflow recargado.");
  } else if (process.argv.includes("--bajar")) {
    bajar();
    console.log("Entorno E2E abajo.");
  } else {
    await levantar();
    console.log("Entorno E2E listo: n8n en http://localhost:15678, falsos en", FALSOS);
  }
}
