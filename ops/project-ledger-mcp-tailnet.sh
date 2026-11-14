#!/usr/bin/env bash
# Tailnet-only Streamable HTTP MCP wrapper for Project Ledger (`ai-server`).
# Resolves the local Tailscale IPv4 address dynamically so the server binds ONLY
# to the tailnet interface (or 127.0.0.1 override), never 0.0.0.0.
set -euo pipefail

export PATH="/home/prime/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export PROJECT_LEDGER_HOME="${PROJECT_LEDGER_HOME:-/central/repos/active/project-ledger}"

BIND_HOST="${PROJECT_LEDGER_MCP_HOST:-}"
if [[ -z "$BIND_HOST" ]]; then
  for _ in {1..30}; do
    BIND_HOST="$(tailscale ip -4 2>/dev/null | head -n1 || true)"
    [[ -n "$BIND_HOST" ]] && break
    sleep 1
  done
fi

if [[ -z "$BIND_HOST" ]]; then
  echo "ERROR: tailscale ip -4 unavailable after 30s; refusing to bind 0.0.0.0." >&2
  exit 1
fi

PORT="${PROJECT_LEDGER_MCP_PORT:-3132}"
PYTHON_BIN="${PROJECT_LEDGER_PYTHON:-/usr/bin/python3}"

cd "$PROJECT_LEDGER_HOME"
exec "$PYTHON_BIN" -m ledger.mcp_server \
  --transport http \
  --host "$BIND_HOST" \
  --port "$PORT" \
  "$@"
