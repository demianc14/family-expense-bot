#!/usr/bin/env bash
# Instala o actualiza el workflow en el n8n de n8n/docker-compose.yml.
#
# Antes: n8n/.env completo (ver .env.example) y, solo la primera vez,
# n8n/credenciales.json (copia de credenciales.ejemplo.json con los valores).
set -euo pipefail
cd "$(dirname "$0")"

[[ -f .env ]] || { echo "Falta n8n/.env (copia .env.example y complétalo)." >&2; exit 1; }
touch credenciales.json # el volumen lo exige; vacío si ya se importaron
mkdir -p .datos
docker compose up -d --wait

# El CLI arranca su propio broker de task runners: otro puerto para no chocar
# con el n8n que ya está corriendo.
cli() { docker compose exec -T -e N8N_RUNNERS_BROKER_PORT=5690 n8n n8n "$@"; }

if [[ -s credenciales.json ]]; then
  cli import:credentials --input=/importar/credenciales.json
  : > credenciales.json # el token ya quedó cifrado en n8n: no dejarlo en disco
  echo "Credenciales importadas; n8n/credenciales.json quedó vacío."
fi
cli import:workflow --input=/importar/workflow.json
cli publish:workflow --id=FamilyExpenseBot
docker compose restart n8n # al arrancar registra el webhook de Telegram
echo "Listo. Escríbele /start al bot."
