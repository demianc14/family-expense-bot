// Último recurso: algo falló fuera de las rutas previstas. Se avisa a quien
// mantiene el bot, sin datos del gasto (solo nodo, motivo y ejecución).
const chatAdmin = $env.TELEGRAM_ADMIN_CHAT_ID;
const { execution = {}, workflow = {} } = $json;
const error = execution.error || {};
const texto =
  `⚠️ Falló el workflow ${workflow.name || ""}\n` +
  `Nodo: ${execution.lastNodeExecuted || "?"}\n` +
  `Motivo: ${String(error.message || "desconocido").slice(0, 300)}\n` +
  `Ejecución: ${execution.id || "?"}`;
console.error(texto);
if (!chatAdmin) {
  console.error("TELEGRAM_ADMIN_CHAT_ID no está configurado: el aviso quedó solo en el log");
  return [];
}
return [{ json: { chat_id: String(chatAdmin), mensaje: texto } }];
