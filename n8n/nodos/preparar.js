// Quién escribe, por dónde sigue el mensaje y el hilo de la conversación.
// Nunca se llama al LLM por un desconocido.
const update = $input.first().json;
const msg = update.message;
if (!msg || !msg.chat || !msg.from) return [];

const usuarios = parsearUsuarios($env.TELEGRAM_USUARIOS, TAXONOMIA);
const remitente = remitenteDe(msg.from.id, usuarios);
if (remitente === null) {
  console.log(`Mensaje ignorado: el ID ${msg.from.id} no está en TELEGRAM_USUARIOS`);
  return [];
}
if (typeof msg.text !== "string" || !strip(msg.text)) return [];

const ahora = $now.setZone(ZONA);
const base = {
  chat_id: String(msg.chat.id),
  usuario_id: String(msg.from.id),
  remitente,
  hoy: ahora.toISODate(),
  mes_actual: ahora.toFormat("yyyy-MM"),
};

const comando = strip(msg.text).split(/\s+/)[0].split("@")[0].toLowerCase();
if (comando === "/start") return [{ json: { ...base, ruta: "start" } }];
if (comando === "/reporte") return [{ json: { ...base, ruta: "reporte", mes: base.mes_actual } }];

// El estado del hilo solo se toca antes de esperar la confirmación: una
// ejecución que se reanuda no vuelve a guardarlo (ver README).
const estado = $getWorkflowStaticData("global");
estado.hilos = estado.hilos || {};
const hilo = agregarAlHilo(estado.hilos, msg.chat.id, msg.text, Date.now());

return [
  {
    json: {
      ...base,
      ruta: "agente",
      texto: msg.text,
      texto_respaldo: hilo.textoRespaldo,
      sesion: hilo.hiloId,
      hilo_abandonado: hilo.abandonado,
      categorias: TAXONOMIA.describirParaPrompt(),
    },
  },
];
