#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
API_URL="http://127.0.0.1:8787"
UI_URL="http://127.0.0.1:5173"
API_PID=""
UI_PID=""
USE_PROCESS_GROUPS=0

usage() {
  cat <<'EOF'
Usage: ./scripts/launch_dashboard.sh [--protected]

Starts the AdvisorAI dashboard API and Vite operator console together.

Options:
  --protected   Require configured password + TOTP MFA instead of local dev mode.
  --help        Show this help.

Environment:
  ADVISORAI_DASHBOARD_LEDGER_PATH  Optional SQLite WAL path for command receipts.
EOF
}

case "${1:-}" in
  --protected)
    export ADVISORAI_DASHBOARD_DEV_MODE=0
    ;;
  --help|-h)
    usage
    exit 0
    ;;
  "")
    export ADVISORAI_DASHBOARD_DEV_MODE="${ADVISORAI_DASHBOARD_DEV_MODE:-1}"
    ;;
  *)
    echo "Unknown option: $1" >&2
    usage >&2
    exit 2
    ;;
esac

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install it from https://docs.astral.sh/uv/" >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required to run the React operator console." >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required for the API health check." >&2
  exit 1
fi
if command -v setsid >/dev/null 2>&1; then
  USE_PROCESS_GROUPS=1
fi

cd "$ROOT_DIR"

if [[ ! -d "$ROOT_DIR/dashboard/node_modules" ]]; then
  echo "Installing dashboard dependencies…"
  npm ci --prefix "$ROOT_DIR/dashboard"
fi

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM
  if [[ -n "$UI_PID" ]] && kill -0 "$UI_PID" 2>/dev/null; then
    if [[ "$USE_PROCESS_GROUPS" -eq 1 ]]; then
      kill -- "-$UI_PID" 2>/dev/null || true
    else
      pkill -TERM -P "$UI_PID" 2>/dev/null || true
      kill "$UI_PID" 2>/dev/null || true
    fi
  fi
  if [[ -n "$API_PID" ]] && kill -0 "$API_PID" 2>/dev/null; then
    if [[ "$USE_PROCESS_GROUPS" -eq 1 ]]; then
      kill -- "-$API_PID" 2>/dev/null || true
    else
      pkill -TERM -P "$API_PID" 2>/dev/null || true
      kill "$API_PID" 2>/dev/null || true
    fi
  fi
  wait "$UI_PID" 2>/dev/null || true
  wait "$API_PID" 2>/dev/null || true
  exit "$exit_code"
}

trap cleanup EXIT INT TERM

echo "Starting AdvisorAI dashboard API on ${API_URL}…"
if [[ "$USE_PROCESS_GROUPS" -eq 1 ]]; then
  setsid uv run --extra dashboard python -m advisorai.api.dashboard_server &
else
  uv run --extra dashboard python -m advisorai.api.dashboard_server &
fi
API_PID=$!

api_ready=0
for _ in {1..30}; do
  if ! kill -0 "$API_PID" 2>/dev/null; then
    echo "Dashboard API exited before becoming healthy." >&2
    wait "$API_PID" || true
    exit 1
  fi
  if curl -fsS "$API_URL/api/v1/health" >/dev/null 2>&1; then
    api_ready=1
    break
  fi
  sleep 1
done

if [[ "$api_ready" -ne 1 ]]; then
  echo "Dashboard API did not become healthy within 30 seconds." >&2
  exit 1
fi

echo "Starting React operator console on ${UI_URL}…"
if [[ "$USE_PROCESS_GROUPS" -eq 1 ]]; then
  setsid npm --prefix "$ROOT_DIR/dashboard" run dev -- --host 127.0.0.1 --port 5173 &
else
  npm --prefix "$ROOT_DIR/dashboard" run dev -- --host 127.0.0.1 --port 5173 &
fi
UI_PID=$!

echo
echo "AdvisorAI V3 control room is ready: ${UI_URL}"
if [[ "$ADVISORAI_DASHBOARD_DEV_MODE" == "1" ]]; then
  echo "Mode: local development / synthetic paper snapshot"
else
  echo "Mode: protected authentication (password + TOTP MFA)"
fi
echo "Press Ctrl-C to stop both services."

while true; do
  if ! kill -0 "$API_PID" 2>/dev/null; then
    wait "$API_PID" || true
    echo "Dashboard API stopped; shutting down the console." >&2
    exit 1
  fi
  if ! kill -0 "$UI_PID" 2>/dev/null; then
    wait "$UI_PID" || true
    echo "React console stopped; shutting down the API." >&2
    exit 1
  fi
  sleep 1
done
