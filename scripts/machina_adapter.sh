#!/usr/bin/env bash
# machina_adapter.sh: thin Machina Consumer Adapter v1 for Project Ledger.
#
# Translates between Project Ledger's canonical domain surfaces
# (ledger_config.json, CURRENT.md, SYSTEM.md, SCHEMA.md,
# registry/identity-decisions.json, python3 -m ledger validate, and compiled
# state/system-manifest.json when present) and Machina's generic Work Unit /
# Orient contracts without creating a second control state machine or importing
# another consumer's lifecycle vocabulary.
#
# Usage:
#   ./scripts/machina_adapter.sh orient   # delegate to Machina Orient Packet v1
#   ./scripts/machina_adapter.sh map      # print fail-closed Project Ledger domain projection
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ROOT="${LEDGER_ROOT_OVERRIDE:-${CODE_ROOT}}"

run_machina() {
  if [[ -n "${MACHINA_BIN:-}" && -x "${MACHINA_BIN}" ]]; then
    exec "${MACHINA_BIN}" "$@"
  fi
  if command -v machina >/dev/null 2>&1; then
    exec machina "$@"
  fi
  if [[ -x "${HOME}/.local/bin/machina" ]]; then
    exec "${HOME}/.local/bin/machina" "$@"
  fi
  local machina_src="${MACHINA_ROOT:-/central/repos/active/machina}/src"
  if [[ -d "${machina_src}/machina" ]]; then
    PYTHONPATH="${machina_src}${PYTHONPATH:+:${PYTHONPATH}}" exec python3 -m machina "$@"
  fi
  echo "machina_adapter.sh: machina runtime executable not found" >&2
  exit 127
}

map_domain_state() {
  local config_doc="${ROOT}/ledger_config.json"
  local current_doc="${ROOT}/CURRENT.md"
  local system_doc="${ROOT}/SYSTEM.md"
  local schema_doc="${ROOT}/SCHEMA.md"
  local decisions_doc="${ROOT}/registry/identity-decisions.json"

  for required in "${config_doc}" "${current_doc}" "${system_doc}" "${schema_doc}" "${decisions_doc}"; do
    if [[ ! -f "${required}" ]]; then
      echo "machina_adapter.sh: missing canonical Project Ledger source: ${required}" >&2
      exit 1
    fi
  done

  if [[ "${LEDGER_ADAPTER_SKIP_GIT_CHECK:-0}" != "1" ]] && git -C "${ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    if git -C "${ROOT}" rev-parse --verify origin/main >/dev/null 2>&1; then
      local behind
      behind="$(git -C "${ROOT}" rev-list --count HEAD..origin/main 2>/dev/null || echo 0)"
      if [[ "${behind}" != "0" ]]; then
        echo "machina_adapter.sh: stale checkout: HEAD is ${behind} commit(s) behind origin/main" >&2
        exit 1
      fi
    fi
  fi

  local validate_json
  if ! validate_json="$(PYTHONPATH="${CODE_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" python3 -m ledger validate --config "${config_doc}" --json 2>&1)"; then
    echo "machina_adapter.sh: ledger validate failed:" >&2
    echo "${validate_json}" >&2
    exit 1
  fi

  VALIDATE_JSON="${validate_json}" PYTHONPATH="${CODE_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" python3 - "${ROOT}" <<'PY'
import json
import os
import sys
from pathlib import Path
from ledger.identity import load_identity_decisions

root = Path(sys.argv[1]).resolve()

try:
    validate_payload = json.loads(os.environ["VALIDATE_JSON"])
except json.JSONDecodeError as exc:
    print(f"machina_adapter.sh: invalid JSON from ledger validate: {exc}", file=sys.stderr)
    sys.exit(1)

if not isinstance(validate_payload, dict) or validate_payload.get("ok") is not True:
    print("machina_adapter.sh: ledger validate did not report ok=true", file=sys.stderr)
    sys.exit(1)

configured_sources = int(validate_payload.get("source_count", 0))

try:
    decisions_payload = load_identity_decisions(root / "registry" / "identity-decisions.json")
except ValueError as exc:
    print(f"machina_adapter.sh: invalid identity-decisions registry: {exc}", file=sys.stderr)
    sys.exit(1)

decisions = decisions_payload.get("decisions") or []
manifest_path = root / "state" / "system-manifest.json"

manifest_state = "absent"
manifest_health = "uncompiled"
source_count = str(configured_sources)
observation_count = "uncompiled"
canonical_project_count = "uncompiled"
review_item_count = "uncompiled"

if manifest_path.exists():
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"machina_adapter.sh: unreadable state/system-manifest.json: {exc}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(manifest, dict):
        print("machina_adapter.sh: state/system-manifest.json must be a JSON object", file=sys.stderr)
        sys.exit(1)
    raw_health = manifest.get("health")
    if isinstance(raw_health, dict):
        health_val = str(raw_health.get("state") or "").strip()
    elif isinstance(raw_health, str):
        health_val = raw_health.strip()
    else:
        health_val = ""
    counts = manifest.get("counts")
    if not isinstance(counts, dict):
        print("machina_adapter.sh: state/system-manifest.json missing counts mapping", file=sys.stderr)
        sys.exit(1)
    for req_count in ("sources", "observations", "canonical_projects", "review_items"):
        if not isinstance(counts.get(req_count), int) or isinstance(counts.get(req_count), bool):
            print(f"machina_adapter.sh: state/system-manifest.json missing integer count {req_count}", file=sys.stderr)
            sys.exit(1)
    if health_val not in ("ok", "degraded"):
        print(
            f"machina_adapter.sh: invalid system-manifest health: {health_val or 'missing'}",
            file=sys.stderr,
        )
        sys.exit(1)
    if health_val == "degraded":
        avail_src = (
            raw_health.get("available_source_count")
            if isinstance(raw_health, dict)
            else None
        )
        if not isinstance(avail_src, int) or isinstance(avail_src, bool) or avail_src < 1:
            print(
                "machina_adapter.sh: degraded system-manifest has zero available sources",
                file=sys.stderr,
            )
            sys.exit(1)
    manifest_state = "present"
    manifest_health = health_val
    source_count = str(counts["sources"])
    observation_count = str(counts["observations"])
    canonical_project_count = str(counts["canonical_projects"])
    review_item_count = str(counts["review_items"])

print("ledger_has_execution_state_field=no")
print("canonical_config=ledger_config.json")
print("validate_status=ok")
print(f"configured_source_count={configured_sources}")
print(f"identity_decision_count={len(decisions)}")
print(f"manifest_state={manifest_state}")
print(f"manifest_health={manifest_health}")
print(f"source_count={source_count}")
print(f"observation_count={observation_count}")
print(f"canonical_project_count={canonical_project_count}")
print(f"review_item_count={review_item_count}")
print("note=Project Ledger domain truth stays in ledger_config.json, registry/identity-decisions.json, and compiled state/; .machina owns generic Work Unit/Attempt/slice control.")
PY
}

case "${1:-orient}" in
  orient)
    run_machina --root "${ROOT}" orient
    ;;
  map)
    map_domain_state
    ;;
  *)
    echo "usage: $0 {orient|map}" >&2
    exit 2
    ;;
esac
