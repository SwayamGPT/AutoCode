#!/usr/bin/env bash
# One command, zero configuration: Ollama + models, Postgres/pgvector memory, AutoCode UI.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running. Start Docker Desktop (or 'colima start') and run this again." >&2
  exit 1
fi

docker compose up -d --build
URL="http://127.0.0.1:${UI_PORT:-8000}"
echo "Downloading models on first start can take a few minutes..."
until curl -fsS "$URL/api/config" >/dev/null 2>&1; do sleep 3; done
echo "AutoCode is ready at $URL"
command -v open >/dev/null && open "$URL" || true
