#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

REPO_DIR="${PROJECT_LEDGER_REPO:-$DEFAULT_REPO_DIR}"
EXPLICIT_REPO_DIR=0
if [[ -n "${PROJECT_LEDGER_REPO:-}" ]]; then
  EXPLICIT_REPO_DIR=1
fi
BIN_DIR="${PROJECT_LEDGER_BIN_DIR:-$HOME/.local/bin}"
SYSTEMD_USER_DIR="${PROJECT_LEDGER_SYSTEMD_USER_DIR:-$HOME/.config/systemd/user}"
CHECK_ONLY=0
NO_SYSTEMD_RELOAD=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)
      CHECK_ONLY=1
      shift
      ;;
    --no-systemd-reload)
      NO_SYSTEMD_RELOAD=1
      shift
      ;;
    --repo-dir)
      REPO_DIR="$2"
      EXPLICIT_REPO_DIR=1
      shift 2
      ;;
    --bin-dir)
      BIN_DIR="$2"
      shift 2
      ;;
    --systemd-user-dir)
      SYSTEMD_USER_DIR="$2"
      shift 2
      ;;
    -h|--help)
      cat <<'EOF'
Usage: install-systemd-units.sh [--check] [--no-systemd-reload] [--repo-dir DIR] [--bin-dir DIR] [--systemd-user-dir DIR]

Installs or verifies Project Ledger's autonomous discovery/sync watchdog symlinks
and user systemd timers:
  - project-ledger-watchdog.timer          (every 15m incremental discovery, clone, and pull)
  - project-ledger-watchdog-fullscan.timer (daily 03:30 UTC all-repo force-fetch and full scan)
  - project-ledger-mcp.service             (tailnet Streamable HTTP MCP server)
EOF
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

REPO_DIR="$(cd "$REPO_DIR" && pwd)"
if [[ "$EXPLICIT_REPO_DIR" == "0" && -f "$REPO_DIR/.git" ]]; then
  echo "ERROR: Refusing to install symlinks pointing into linked git worktree $REPO_DIR; run from the canonical checkout (/central/repos/active/project-ledger) or pass --repo-dir explicitly." >&2
  exit 2
fi
UNIT_SRC_DIR="$REPO_DIR/ops/systemd"

SCRIPTS=(
  "project-ledger-discovery-watchdog.sh"
  "project-ledger-mcp-tailnet.sh"
)
UNITS=(
  "project-ledger-watchdog.service"
  "project-ledger-watchdog.timer"
  "project-ledger-watchdog-fullscan.service"
  "project-ledger-watchdog-fullscan.timer"
  "project-ledger-mcp.service"
)
TIMERS=(
  "project-ledger-watchdog.timer"
  "project-ledger-watchdog-fullscan.timer"
)

if [[ "$CHECK_ONLY" == "1" ]]; then
  drift=0
  for script_name in "${SCRIPTS[@]}"; do
    src="$REPO_DIR/ops/$script_name"
    dst="$BIN_DIR/$script_name"
    if [[ ! -L "$dst" ]] || [[ "$(readlink -f "$dst")" != "$(readlink -f "$src")" ]]; then
      echo "DRIFT: $dst does not symlink to $src" >&2
      drift=1
    fi
  done
  for unit_name in "${UNITS[@]}"; do
    src="$UNIT_SRC_DIR/$unit_name"
    dst="$SYSTEMD_USER_DIR/$unit_name"
    if [[ ! -f "$dst" ]] || ! cmp -s "$src" "$dst"; then
      echo "DRIFT: $dst differs from $src" >&2
      drift=1
    fi
  done
  if [[ "$NO_SYSTEMD_RELOAD" == "0" ]] && command -v systemctl >/dev/null 2>&1; then
    for timer_name in "${TIMERS[@]}"; do
      if ! systemctl --user is-enabled --quiet "$timer_name" 2>/dev/null; then
        echo "DRIFT: timer not enabled: $timer_name" >&2
        drift=1
      fi
      if ! systemctl --user is-active --quiet "$timer_name" 2>/dev/null; then
        echo "DRIFT: timer not active: $timer_name" >&2
        drift=1
      fi
    done
  fi
  if [[ "$drift" != "0" ]]; then
    exit 1
  fi
  echo "OK: Project Ledger symlinks, systemd units, and timers are in sync."
  exit 0
fi

mkdir -p "$BIN_DIR" "$SYSTEMD_USER_DIR"

for script_name in "${SCRIPTS[@]}"; do
  chmod 0755 "$REPO_DIR/ops/$script_name"
  ln -sfn "$REPO_DIR/ops/$script_name" "$BIN_DIR/$script_name"
done

for unit_name in "${UNITS[@]}"; do
  install -m 0644 "$UNIT_SRC_DIR/$unit_name" "$SYSTEMD_USER_DIR/$unit_name"
done

if [[ "$NO_SYSTEMD_RELOAD" == "0" ]] && command -v systemctl >/dev/null 2>&1; then
  systemctl --user daemon-reload
  systemctl --user enable --now "${TIMERS[@]}"
fi

echo "Installed Project Ledger symlinks in $BIN_DIR and systemd units in $SYSTEMD_USER_DIR."
