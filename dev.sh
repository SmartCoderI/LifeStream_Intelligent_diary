#!/usr/bin/env bash
# Start the LifeStream backend (API + processing) and the web dashboard together.
#
#   ./dev.sh          development: live-reloading web app on http://localhost:5173
#   ./dev.sh build    one server: builds the web app and serves everything on http://127.0.0.1:8000
#
# Ctrl+C stops everything.

set -euo pipefail
cd "$(dirname "$0")"

MODE="${1:-dev}"
API_PORT=8000
WEB_PORT=5173

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing '$1'. Install it with: $2"; exit 1; }; }
need uv "brew install uv"
need npm "brew install node"
need ffmpeg "brew install ffmpeg"

port_free() { ! lsof -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
if ! port_free "$API_PORT"; then
  echo "Port $API_PORT is already in use (is LifeStream already running?). Stop it first."
  exit 1
fi
[ -f .env ] || echo "Warning: no .env file. Copy .env.example to .env and add your keys."

uv sync --quiet
[ -d web/node_modules ] || (echo "Installing web packages..." && cd web && npm install --silent)

# search database (Stage 2). It keeps running after Ctrl+C; stop it with `docker compose down`.
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  docker compose up -d --wait db >/dev/null 2>&1 \
    && echo "Search database: running" \
    || echo "Warning: couldn't start the search database; Ask / Search won't work."
else
  echo "Warning: Docker isn't running, so Ask / Search won't work. Start Docker Desktop and re-run."
fi

pids=()
# stop a process and everything it started (uv -> python, npm -> vite -> esbuild)
kill_tree() {
  local child
  for child in $(pgrep -P "$1" 2>/dev/null); do kill_tree "$child"; done
  kill "$1" 2>/dev/null || true
}
cleanup() {
  trap - EXIT INT TERM
  echo
  echo "Stopping..."
  for pid in ${pids[@]+"${pids[@]}"}; do kill_tree "$pid"; done
  wait 2>/dev/null || true
  echo "Stopped."
}
trap cleanup EXIT INT TERM

if [ "$MODE" = "build" ]; then
  echo "Building the web app..."
  npm --prefix web run build --silent
  uv run lifestream serve --port "$API_PORT" &
  pids+=($!)
  echo "LifeStream: http://127.0.0.1:$API_PORT"
else
  if ! port_free "$WEB_PORT"; then
    echo "Port $WEB_PORT is already in use. Stop the other dev server first."
    exit 1
  fi
  uv run lifestream serve --port "$API_PORT" &
  pids+=($!)
  npm --prefix web run dev -- --port "$WEB_PORT" --strictPort &
  pids+=($!)
  echo "LifeStream (dev): http://localhost:$WEB_PORT   (API on :$API_PORT)"
fi

# keep running until Ctrl+C or until either process stops (macOS bash 3.2 has no `wait -n`)
while true; do
  for pid in "${pids[@]}"; do
    kill -0 "$pid" 2>/dev/null || { echo "A process stopped; shutting down."; exit 1; }
  done
  sleep 1
done
