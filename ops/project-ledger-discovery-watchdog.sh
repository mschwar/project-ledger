#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${PROJECT_LEDGER_REPO:-}" ]]; then
  if [[ -d /central/repos/active/project-ledger ]]; then
    PROJECT_LEDGER_REPO=/central/repos/active/project-ledger
  else
    PROJECT_LEDGER_REPO=/home/prime/project-ledger
  fi
fi
PROJECT_LEDGER_CONFIG=${PROJECT_LEDGER_CONFIG:-$PROJECT_LEDGER_REPO/ledger_config.json}
WATCHDOG_STATE_DIR=${PROJECT_LEDGER_WATCHDOG_STATE_DIR:-/central/registry/reports/project-ledger/watchdog}
PROJECT_LEDGER_PYTHON=${PROJECT_LEDGER_PYTHON:-python3}
PROJECT_LEDGER_FULL_SCAN=${PROJECT_LEDGER_FULL_SCAN:-0}
PROJECT_LEDGER_GITHUB_SYNC=${PROJECT_LEDGER_GITHUB_SYNC:-auto}
PROJECT_LEDGER_CLONE_ROOT=${PROJECT_LEDGER_CLONE_ROOT:-/central/repos/active}
PROJECT_LEDGER_GITHUB_INVENTORY_FILE=${PROJECT_LEDGER_GITHUB_INVENTORY_FILE:-}
PROJECT_LEDGER_GBRAIN_SYNC=${PROJECT_LEDGER_GBRAIN_SYNC:-auto}
PROJECT_LEDGER_BRAIN_DIR=${PROJECT_LEDGER_BRAIN_DIR:-/home/prime/brain}
PROJECT_LEDGER_GBRAIN_SYNC_HOOK=${PROJECT_LEDGER_GBRAIN_SYNC_HOOK:-}
PROJECT_LEDGER_NOTIFY_HOOK=${PROJECT_LEDGER_NOTIFY_HOOK:-}
PROJECT_LEDGER_SYNC_REPO_STATE=${PROJECT_LEDGER_SYNC_REPO_STATE:-0}
PROJECT_LEDGER_TRIGGER_ON_INITIAL=${PROJECT_LEDGER_TRIGGER_ON_INITIAL:-0}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --full-scan)
      PROJECT_LEDGER_FULL_SCAN=1
      shift
      ;;
    -h|--help)
      cat <<'EOF'
Usage: project-ledger-discovery-watchdog.sh [--full-scan]

Reconciles GitHub remote repositories against local checkouts over SSH
(ledger sync-github: auto-cloning missing non-archived repos into
PROJECT_LEDGER_CLONE_ROOT and fast-forwarding clean default-branch checkouts),
runs Project Ledger refresh (--no-markdown-mirror) with persistent P5.1 source
checkpoints, U3 lineage pinning, and P4.5 run-history delta state, republishes
the Reality Provider projection to PROJECT_LEDGER_WATCHDOG_STATE_DIR, and gates
downstream GBrain page/source syncs and operator alert receipts on semantic
changes (change_count > 0 or untriaged_weak_overlap_count > 0).
EOF
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

TMP=$(mktemp -d)
STAGE_DIRS=()
cleanup_watchdog() {
  local d
  for d in "${STAGE_DIRS[@]}"; do
    if [[ -n "$d" && -e "$d" ]]; then
      "$PROJECT_LEDGER_PYTHON" -c 'import pathlib, shutil, sys; p = pathlib.Path(sys.argv[1]); shutil.rmtree(p, ignore_errors=True) if p.is_dir() and not p.is_symlink() else p.unlink(missing_ok=True)' "$d" 2>/dev/null || true
    fi
  done
  if [[ -n "${TMP:-}" && -d "$TMP" ]]; then
    "$PROJECT_LEDGER_PYTHON" -c 'import shutil, sys; shutil.rmtree(sys.argv[1], ignore_errors=True)' "$TMP" 2>/dev/null || true
  fi
}
trap cleanup_watchdog EXIT

if [[ ! -d "$PROJECT_LEDGER_REPO" ]]; then
  echo "ERROR: project-ledger repo missing at $PROJECT_LEDGER_REPO" >&2
  exit 1
fi
if [[ ! -f "$PROJECT_LEDGER_CONFIG" ]]; then
  echo "ERROR: project-ledger config missing at $PROJECT_LEDGER_CONFIG" >&2
  exit 1
fi
if [[ ! -f "$PROJECT_LEDGER_REPO/ledger/__main__.py" ]]; then
  echo "ERROR: Project Ledger Wave 1 ledger CLI is unavailable in $PROJECT_LEDGER_REPO" >&2
  exit 1
fi

mkdir -p "$WATCHDOG_STATE_DIR" "$TMP/state"

# Seed P5.1 extraction checkpoints, GitHub pushed_at cache, and P4.5 bounded run
# snapshots from the last published watchdog projection (or fallback to repo state
# on first seed) so warm incremental refreshes reuse unchanged observations and
# compute true semantic deltas across runs.
for seed_state_file in source-checkpoints.json github-sync.json canonical-projects.json; do
  if [[ -f "$WATCHDOG_STATE_DIR/$seed_state_file" ]]; then
    cp -p "$WATCHDOG_STATE_DIR/$seed_state_file" "$TMP/state/$seed_state_file"
  elif [[ -f "$PROJECT_LEDGER_REPO/state/$seed_state_file" ]]; then
    cp -p "$PROJECT_LEDGER_REPO/state/$seed_state_file" "$TMP/state/$seed_state_file"
  fi
done

if [[ -d "$WATCHDOG_STATE_DIR/runs" ]]; then
  cp -a "$WATCHDOG_STATE_DIR/runs" "$TMP/state/runs"
  if [[ -f "$WATCHDOG_STATE_DIR/system-manifest.json" ]]; then
    cp -p "$WATCHDOG_STATE_DIR/system-manifest.json" "$TMP/state/system-manifest.json"
  fi
  if [[ -f "$WATCHDOG_STATE_DIR/changes.json" ]]; then
    cp -p "$WATCHDOG_STATE_DIR/changes.json" "$TMP/state/changes.json"
  fi
fi

# Step 1: Reconcile GitHub remote repositories -> local SSH checkouts (Law 11 contained).
github_sync_status="skipped"
github_cloned_count=0
github_ff_count=0
run_github_sync=0
if [[ "$PROJECT_LEDGER_GITHUB_SYNC" == "1" || "$PROJECT_LEDGER_GITHUB_SYNC" == "always" ]]; then
  run_github_sync=1
elif [[ "$PROJECT_LEDGER_GITHUB_SYNC" == "auto" && -f "$PROJECT_LEDGER_REPO/ledger/github_sync.py" ]]; then
  run_github_sync=1
fi

if [[ "$run_github_sync" == "1" ]]; then
  sync_gh_args=(
    --config "$PROJECT_LEDGER_CONFIG"
    --state-dir "$TMP/state"
    --clone-root "$PROJECT_LEDGER_CLONE_ROOT"
    --json
  )
  if [[ "$PROJECT_LEDGER_FULL_SCAN" == "1" ]]; then
    sync_gh_args+=(--force-fetch)
  fi
  if [[ -n "$PROJECT_LEDGER_GITHUB_INVENTORY_FILE" ]]; then
    sync_gh_args+=(--inventory-file "$PROJECT_LEDGER_GITHUB_INVENTORY_FILE")
  fi
  if (
    cd "$PROJECT_LEDGER_REPO"
    "$PROJECT_LEDGER_PYTHON" -m ledger sync-github "${sync_gh_args[@]}"
  ) >"$TMP/github-sync-cmd.json" 2>"$TMP/github-sync.err"; then
    IFS='|' read -r github_sync_status github_cloned_count github_ff_count < <(
      "$PROJECT_LEDGER_PYTHON" - "$TMP/state/github-sync.json" <<'PY'
import json
import sys
from pathlib import Path

p = Path(sys.argv[1])
doc = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
counts = doc.get("counts") or {}
print(
    doc.get("status", "ok"),
    int(counts.get("cloned") or 0),
    int(counts.get("fast_forwarded") or 0),
    sep="|",
)
PY
    )
  else
    github_sync_status="failed"
  fi
fi

# Seed merged project-lineage registry into $TMP/project-lineage.json so newly
# cloned repos receive durable canonical_project_id pins without dirtying git.
use_staged_lineage=0
if [[ -f "$PROJECT_LEDGER_REPO/ledger/lineage.py" && -f "$PROJECT_LEDGER_REPO/registry/project-lineage.json" ]]; then
  use_staged_lineage=1
  if [[ -f "$WATCHDOG_STATE_DIR/project-lineage.json" ]]; then
    (
      cd "$PROJECT_LEDGER_REPO"
      "$PROJECT_LEDGER_PYTHON" - "$PROJECT_LEDGER_REPO/registry/project-lineage.json" "$WATCHDOG_STATE_DIR/project-lineage.json" "$TMP/project-lineage.json" <<'PY'
import json
import sys
from pathlib import Path
from ledger.lineage import load_project_lineage, merge_project_lineage, write_project_lineage

base = load_project_lineage(Path(sys.argv[1]), required=False)
overlay = load_project_lineage(Path(sys.argv[2]), required=False)
write_project_lineage(Path(sys.argv[3]), merge_project_lineage(base, overlay))
PY
    )
  else
    cp -p "$PROJECT_LEDGER_REPO/registry/project-lineage.json" "$TMP/project-lineage.json"
  fi
fi

refresh_args=(
  --config "$PROJECT_LEDGER_CONFIG"
  --output-dir "$TMP/output"
  --state-dir "$TMP/state"
  --no-markdown-mirror
)
if [[ "$use_staged_lineage" == "1" ]]; then
  refresh_args+=(--project-lineage "$TMP/project-lineage.json")
fi
scan_mode="incremental"
if [[ "$PROJECT_LEDGER_FULL_SCAN" == "1" ]]; then
  refresh_args+=(--full-scan)
  scan_mode="full-scan"
fi

# Project Ledger owns source/schema semantics. Homelab only runs the compiler and
# republishes its bounded provider projection into the existing registry report seam.
# Provider refreshes explicitly suppress Project Ledger's committed Markdown mirror so
# this monitoring path cannot dirty the external checkout.
(
  cd "$PROJECT_LEDGER_REPO"
  "$PROJECT_LEDGER_PYTHON" -m ledger refresh \
    "${refresh_args[@]}" \
    >"$TMP/refresh.log"

  if [[ "$use_staged_lineage" == "1" ]]; then
    "$PROJECT_LEDGER_PYTHON" -m ledger lineage \
      --state-dir "$TMP/state" \
      --project-lineage "$TMP/project-lineage.json" \
      --sync >/dev/null
  fi

  "$PROJECT_LEDGER_PYTHON" -m ledger orient \
    --state-dir "$TMP/state" --json >"$TMP/orient.json"
  "$PROJECT_LEDGER_PYTHON" -m ledger sources \
    --state-dir "$TMP/state" --json >"$TMP/sources.json"
)

required=(
  "$TMP/output/projects.csv"
  "$TMP/output/projects.json"
  "$TMP/output/projects.md"
  "$TMP/state/system-manifest.json"
  "$TMP/state/observations.json"
  "$TMP/orient.json"
  "$TMP/sources.json"
)
for path in "${required[@]}"; do
  if [[ ! -s "$path" ]]; then
    echo "ERROR: Project Ledger refresh did not produce required artifact: $path" >&2
    exit 1
  fi
done

IFS='|' read -r has_change_feed has_review_queue has_gbrain_page_sync < <(
  "$PROJECT_LEDGER_PYTHON" - "$TMP/state/system-manifest.json" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
caps = manifest.get("capabilities") or {}
change_ok = "1" if (caps.get("change_feed") or {}).get("state") == "available" else "0"
review_ok = "1" if (caps.get("review_queue") or {}).get("state") == "available" else "0"
gbrain_pages_ok = "1" if (caps.get("gbrain_page_sync") or {}).get("state") == "available" else "0"
print(change_ok, review_ok, gbrain_pages_ok, sep="|")
PY
)

if [[ "$has_change_feed" == "1" ]]; then
  (
    cd "$PROJECT_LEDGER_REPO"
    "$PROJECT_LEDGER_PYTHON" -m ledger changes \
      --state-dir "$TMP/state" --json >"$TMP/changes.json"
  )
fi
if [[ "$has_review_queue" == "1" ]]; then
  (
    cd "$PROJECT_LEDGER_REPO"
    "$PROJECT_LEDGER_PYTHON" -m ledger review \
      --state-dir "$TMP/state" --json >"$TMP/review.json"
  )
fi

# Maintain Wave 1 normalized semantic-snapshot.json both for backward-compatible
# consumers and as fallback when running against a Wave 1-only fixture.
"$PROJECT_LEDGER_PYTHON" - \
  "$TMP/output/projects.json" \
  "$TMP/state/system-manifest.json" \
  "$TMP/semantic-snapshot.json" <<'PY'
import json
import sys
from pathlib import Path

projects_path, manifest_path, out_path = map(Path, sys.argv[1:4])
projects = json.loads(projects_path.read_text(encoding="utf-8"))
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))


def stable_source(source: dict) -> dict:
    return {
        key: source.get(key)
        for key in (
            "source_id",
            "label",
            "source_class",
            "discovery",
            "machine_name",
            "storage_scope",
            "path",
            "path_state",
            "status",
            "status_reason",
            "freshness_state",
            "probe_fingerprint",
        )
    } | {
        "artifacts": [
            {key: artifact.get(key) for key in ("role", "path", "state", "sha256")}
            for artifact in source.get("artifacts", [])
        ]
    }

stable = {
    "compat_entries": projects.get("entries", []),
    "health": manifest.get("health", {}),
    "counts": {
        key: manifest.get("counts", {}).get(key)
        for key in ("sources", "observations", "canonical_projects", "review_items")
    },
    "sources": [stable_source(source) for source in manifest.get("sources", [])],
    "capabilities": manifest.get("capabilities", {}),
    "limitations": manifest.get("limitations", []),
}
out_path.write_text(
    json.dumps(stable, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
PY

change_count=0
untriaged_weak_overlaps=0
review_count=0
baseline_state="legacy"
triggered=no

if [[ "$has_change_feed" == "1" && -s "$TMP/changes.json" ]]; then
  IFS='|' read -r baseline_state change_count untriaged_weak_overlaps review_count < <(
    "$PROJECT_LEDGER_PYTHON" - "$TMP/changes.json" "${TMP}/review.json" <<'PY'
import json
import sys
from pathlib import Path

changes = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
review_path = Path(sys.argv[2])
review = json.loads(review_path.read_text(encoding="utf-8")) if review_path.is_file() else {}

baseline = str(changes.get("baseline_state") or "compared")
c_count = int(changes.get("change_count") or 0)
u_count = int(
    changes["untriaged_weak_overlap_count"]
    if "untriaged_weak_overlap_count" in changes
    else (review.get("untriaged_weak_overlap_count") or 0)
)
r_count = int(
    changes["review_count"]
    if "review_count" in changes
    else (review.get("review_count") or 0)
)
print(baseline, c_count, u_count, r_count, sep="|")
PY
  )
  if [[ "$baseline_state" == "initial" || "$baseline_state" == "initial_baseline" || "$change_count" -gt 0 ]]; then
    changed=yes
  else
    changed=no
  fi
  if [[ "$change_count" -gt 0 || "$untriaged_weak_overlaps" -gt 0 ]]; then
    triggered=yes
  elif [[ ( "$baseline_state" == "initial" || "$baseline_state" == "initial_baseline" ) && "$PROJECT_LEDGER_TRIGGER_ON_INITIAL" == "1" ]]; then
    triggered=yes
  fi
else
  changed=yes
  if [[ -f "$WATCHDOG_STATE_DIR/semantic-snapshot.json" ]] \
    && cmp -s "$TMP/semantic-snapshot.json" "$WATCHDOG_STATE_DIR/semantic-snapshot.json"; then
    changed=no
  fi
fi

replace_dir_atomic() {
  local src="$1"
  local dst="$2"
  local stage="${dst}.stage.$$"
  local old="${dst}.old.$$"
  STAGE_DIRS+=("$stage" "$old")
  "$PROJECT_LEDGER_PYTHON" - "$src" "$dst" "$stage" "$old" <<'PY'
import os
import shutil
import sys
from pathlib import Path

src, dst, stage, old = (Path(arg) for arg in sys.argv[1:5])
for p in (stage, old):
    if p.exists() or p.is_symlink():
        shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()
shutil.copytree(src, stage, symlinks=True)
moved_old = False
if dst.exists() or dst.is_symlink():
    os.replace(dst, old)
    moved_old = True
try:
    os.replace(stage, dst)
except BaseException:
    if moved_old and not dst.exists():
        os.replace(old, dst)
    raise
if moved_old:
    shutil.rmtree(old, ignore_errors=True)
PY
}

# Publish only after every compiler/query step and artifact check succeeded.
install -m 0644 "$TMP/output/projects.csv" "$WATCHDOG_STATE_DIR/projects.csv"
install -m 0644 "$TMP/output/projects.json" "$WATCHDOG_STATE_DIR/projects.json"
install -m 0644 "$TMP/output/projects.md" "$WATCHDOG_STATE_DIR/projects.md"
install -m 0644 "$TMP/state/system-manifest.json" "$WATCHDOG_STATE_DIR/system-manifest.json"
install -m 0644 "$TMP/state/observations.json" "$WATCHDOG_STATE_DIR/observations.json"
install -m 0644 "$TMP/orient.json" "$WATCHDOG_STATE_DIR/orient.json"
install -m 0644 "$TMP/sources.json" "$WATCHDOG_STATE_DIR/sources.json"
install -m 0644 "$TMP/semantic-snapshot.json" "$WATCHDOG_STATE_DIR/semantic-snapshot.json"

for optional_state_file in \
  source-checkpoints.json \
  github-sync.json \
  canonical-projects.json \
  canonical-projects.md \
  identity-evidence.json \
  review-queue.json; do
  if [[ -f "$TMP/state/$optional_state_file" ]]; then
    install -m 0644 "$TMP/state/$optional_state_file" "$WATCHDOG_STATE_DIR/$optional_state_file"
  fi
done

if [[ "$use_staged_lineage" == "1" && -f "$TMP/project-lineage.json" ]]; then
  install -m 0644 "$TMP/project-lineage.json" "$WATCHDOG_STATE_DIR/project-lineage.json"
fi

if [[ -s "$TMP/changes.json" ]]; then
  install -m 0644 "$TMP/changes.json" "$WATCHDOG_STATE_DIR/changes.json"
elif [[ -f "$TMP/state/changes.json" ]]; then
  install -m 0644 "$TMP/state/changes.json" "$WATCHDOG_STATE_DIR/changes.json"
fi
if [[ -s "$TMP/review.json" ]]; then
  install -m 0644 "$TMP/review.json" "$WATCHDOG_STATE_DIR/review.json"
fi

for optional_state_dir in runs projects; do
  if [[ -d "$TMP/state/$optional_state_dir" ]]; then
    replace_dir_atomic "$TMP/state/$optional_state_dir" "$WATCHDOG_STATE_DIR/$optional_state_dir"
  fi
done

# Optional live-estate mirror into gitignored $PROJECT_LEDGER_REPO/state so direct
# `python3 -m ledger resolve` consumers inside the repo see the same 15m fresh state.
if [[ "$PROJECT_LEDGER_SYNC_REPO_STATE" == "1" ]]; then
  mkdir -p "$PROJECT_LEDGER_REPO/state"
  for state_file in \
    system-manifest.json \
    observations.json \
    source-checkpoints.json \
    github-sync.json \
    canonical-projects.json \
    canonical-projects.md \
    identity-evidence.json \
    review-queue.json \
    changes.json; do
    if [[ -f "$TMP/state/$state_file" ]]; then
      install -m 0644 "$TMP/state/$state_file" "$PROJECT_LEDGER_REPO/state/$state_file"
    fi
  done
  for state_subdir in runs projects; do
    if [[ -d "$TMP/state/$state_subdir" ]]; then
      replace_dir_atomic "$TMP/state/$state_subdir" "$PROJECT_LEDGER_REPO/state/$state_subdir"
    fi
  done
fi

IFS='|' read -r run_id health observations canonical_projects sources < <(
  "$PROJECT_LEDGER_PYTHON" - "$TMP/state/system-manifest.json" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
counts = manifest.get("counts") or {}
cp = counts.get("canonical_projects")
print(
    manifest.get("run_id", "unknown"),
    manifest.get("health", {}).get("state", "unknown"),
    counts.get("observations", "unknown"),
    "none" if cp is None else cp,
    counts.get("sources", "unknown"),
    sep="|",
)
PY
)

# Downstream semantic change trigger: fire ONLY when change_count > 0 or
# untriaged_weak_overlap_count > 0 (or initial run with explicit override).
if [[ "$triggered" == "yes" ]]; then
  mkdir -p "$WATCHDOG_STATE_DIR/alerts"
  "$PROJECT_LEDGER_PYTHON" - \
    "$TMP/state/system-manifest.json" \
    "$TMP/changes.json" \
    "$TMP/state/canonical-projects.json" \
    "$TMP/alert-base.json" <<'PY'
import json
import sys
from pathlib import Path

manifest_path, changes_path, projects_path, out_path = map(Path, sys.argv[1:5])
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
changes = json.loads(changes_path.read_text(encoding="utf-8")) if changes_path.is_file() else {}
projects_payload = (
    json.loads(projects_path.read_text(encoding="utf-8"))
    if projects_path.is_file()
    else {"projects": []}
)
projects_by_id = {
    str(p.get("canonical_project_id")): p
    for p in (projects_payload.get("projects") or [])
    if p.get("canonical_project_id")
}

affected_projects = []
seen_pids = set()
slugs = set()
for event in changes.get("changes") or []:
    pid = event.get("canonical_project_id")
    if pid and pid not in seen_pids:
        seen_pids.add(pid)
        proj = projects_by_id.get(str(pid), {})
        gbrain = proj.get("gbrain") or {}
        slug = proj.get("gbrain_source_slug") or gbrain.get("source_slug")
        if slug and slug != "default":
            slugs.add(str(slug))
        affected_projects.append(
            {
                "canonical_project_id": str(pid),
                "project_key": proj.get("project_key") or event.get("project_key"),
                "gbrain_page_key": gbrain.get("page_key"),
                "gbrain_source_slug": slug,
            }
        )

reasons = []
if int(changes.get("change_count") or 0) > 0:
    reasons.append("SEMANTIC_CHANGES_DETECTED")
if int(changes.get("untriaged_weak_overlap_count") or 0) > 0:
    reasons.append("UNTRIAGED_WEAK_OVERLAPS_PRESENT")
if not reasons:
    reasons.append("INITIAL_TRIGGER_OVERRIDE")

alert = {
    "run_id": manifest.get("run_id"),
    "compiled_at": manifest.get("compiled_at"),
    "trigger_reasons": reasons,
    "change_count": int(changes.get("change_count") or 0),
    "untriaged_weak_overlap_count": int(changes.get("untriaged_weak_overlap_count") or 0),
    "review_count": int(changes.get("review_count") or 0),
    "github_sync_status": None,
    "affected_gbrain_slugs": sorted(slugs),
    "affected_projects": affected_projects,
    "change_summaries": [
        {
            "change_type": ev.get("change_type"),
            "entity_kind": ev.get("entity_kind"),
            "canonical_project_id": ev.get("canonical_project_id"),
            "summary": ev.get("summary"),
        }
        for ev in (changes.get("changes") or [])[:50]
    ],
}
out_path.write_text(json.dumps(alert, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

  # Use a non-whitespace delimiter ('|') because Bash whitespace-IFS ($'\t')
  # strips leading empty fields when gbrain_slugs is empty (e.g. 'default' slug).
  IFS='|' read -r gbrain_slugs affected_project_ids < <(
    "$PROJECT_LEDGER_PYTHON" - "$TMP/alert-base.json" <<'PY'
import json
import sys
from pathlib import Path

alert = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
slugs = " ".join(alert.get("affected_gbrain_slugs") or [])
pids = " ".join(
    str(item.get("canonical_project_id"))
    for item in (alert.get("affected_projects") or [])
    if item.get("canonical_project_id")
)
print(slugs, pids, sep="|")
PY
  )

  gbrain_page_sync_status="skipped"
  if [[ "$has_gbrain_page_sync" == "1" && "$PROJECT_LEDGER_GBRAIN_SYNC" != "off" && -d "$PROJECT_LEDGER_BRAIN_DIR" ]]; then
    if [[ "$scan_mode" == "full-scan" || -n "$affected_project_ids" ]]; then
      sync_gbrain_args=(
        --state-dir "$TMP/state"
        --brain-dir "$PROJECT_LEDGER_BRAIN_DIR"
        --ingest
        --json
      )
      if [[ "$scan_mode" != "full-scan" && -n "$affected_project_ids" ]]; then
        for pid in $affected_project_ids; do
          sync_gbrain_args+=(--project "$pid")
        done
      fi
      if (
        cd "$PROJECT_LEDGER_REPO"
        "$PROJECT_LEDGER_PYTHON" -m ledger sync-gbrain "${sync_gbrain_args[@]}"
      ) >"$TMP/gbrain-page-sync.log" 2>&1; then
        gbrain_page_sync_status="synced"
      else
        gbrain_page_sync_status="failed"
      fi
    fi
  fi

  gbrain_sync_status="skipped"
  if [[ -n "$PROJECT_LEDGER_GBRAIN_SYNC_HOOK" ]]; then
    if WATCHDOG_STATE_DIR="$WATCHDOG_STATE_DIR" \
       WATCHDOG_RUN_ID="$run_id" \
       WATCHDOG_CHANGE_COUNT="$change_count" \
       WATCHDOG_UNTRIAGED_WEAK_OVERLAPS="$untriaged_weak_overlaps" \
       WATCHDOG_GBRAIN_SLUGS="$gbrain_slugs" \
       WATCHDOG_AFFECTED_PROJECTS="$affected_project_ids" \
       WATCHDOG_ALERT_BASE_JSON="$TMP/alert-base.json" \
       bash -c "$PROJECT_LEDGER_GBRAIN_SYNC_HOOK" >"$TMP/gbrain-sync.log" 2>&1; then
      gbrain_sync_status="hook_ok"
    else
      gbrain_sync_status="hook_failed"
    fi
  elif [[ "$PROJECT_LEDGER_GBRAIN_SYNC" != "off" && -n "$gbrain_slugs" ]] && command -v gbrain >/dev/null 2>&1; then
    gbrain_sync_status="synced"
    if ! gbrain sources list --json >"$TMP/gbrain-sources.json" 2>>"$TMP/gbrain-sync.log"; then
      printf '{"sources":[]}\n' >"$TMP/gbrain-sources.json"
    fi
    for slug in $gbrain_slugs; do
      slug_local_path="$("$PROJECT_LEDGER_PYTHON" - "$TMP/gbrain-sources.json" "$slug" <<'PY'
import json
import sys
from pathlib import Path

try:
    doc = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except Exception:
    doc = {}
sources = doc.get("sources") if isinstance(doc, dict) else doc
target = sys.argv[2]
local_path = ""
if isinstance(sources, list):
    for item in sources:
        if isinstance(item, dict) and str(item.get("id") or item.get("slug") or "") == target:
            local_path = str(item.get("local_path") or item.get("repo") or "").strip()
            break
print(local_path)
PY
)"
      if [[ -n "$slug_local_path" && -d "$slug_local_path" ]]; then
        if git -C "$slug_local_path" rev-parse --git-dir >/dev/null 2>&1; then
          sync_extra=()
          remote_url="$(git -C "$slug_local_path" remote get-url origin 2>/dev/null || true)"
          case "$remote_url" in
            /recovery-backup/*|file:///recovery-backup/*|"")
              sync_extra+=(--no-pull)
              ;;
          esac
          if ! gbrain sync --source "$slug" --skip-failed --no-embed "${sync_extra[@]}" >>"$TMP/gbrain-sync.log" 2>&1; then
            gbrain_sync_status="partial_failure"
          fi
        else
          if ! gbrain import "$slug_local_path" --source-id "$slug" --no-embed >>"$TMP/gbrain-sync.log" 2>&1; then
            gbrain_sync_status="partial_failure"
          fi
        fi
      else
        if ! gbrain sync --source "$slug" --skip-failed --no-embed >>"$TMP/gbrain-sync.log" 2>&1; then
          gbrain_sync_status="partial_failure"
        fi
      fi
    done
  fi

  "$PROJECT_LEDGER_PYTHON" - "$TMP/alert-base.json" "$gbrain_sync_status" "$gbrain_page_sync_status" "$github_sync_status" "$TMP/gbrain-sync.log" "$TMP/alert-final.json" <<'PY'
import json
import sys
from pathlib import Path

base = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
base["gbrain_sync_status"] = sys.argv[2]
base["gbrain_page_sync_status"] = sys.argv[3]
base["github_sync_status"] = sys.argv[4]
sync_log = Path(sys.argv[5])
if sys.argv[2] in {"partial_failure", "hook_failed"} and sync_log.is_file():
    tail = [line for line in sync_log.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()][-20:]
    if tail:
        base["gbrain_sync_error_tail"] = tail
Path(sys.argv[6]).write_text(json.dumps(base, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

  install -m 0644 "$TMP/alert-final.json" "$WATCHDOG_STATE_DIR/alerts/${run_id}.json"
  install -m 0644 "$TMP/alert-final.json" "$WATCHDOG_STATE_DIR/alerts/latest.json"

  if [[ -n "$PROJECT_LEDGER_NOTIFY_HOOK" ]]; then
    WATCHDOG_STATE_DIR="$WATCHDOG_STATE_DIR" \
    WATCHDOG_ALERT_JSON="$WATCHDOG_STATE_DIR/alerts/latest.json" \
    WATCHDOG_CHANGE_COUNT="$change_count" \
    WATCHDOG_UNTRIAGED_WEAK_OVERLAPS="$untriaged_weak_overlaps" \
    bash -c "$PROJECT_LEDGER_NOTIFY_HOOK" >>"$TMP/notify.log" 2>&1 || true
  fi
fi

printf 'project-ledger watchdog refreshed observations=%s projects=%s sources=%s health=%s mode=%s changed=%s change_count=%s untriaged_weak_overlaps=%s triggered=%s github_sync=%s cloned=%s ff=%s\n' \
  "$observations" "$canonical_projects" "$sources" "$health" "$scan_mode" "$changed" "$change_count" "$untriaged_weak_overlaps" "$triggered" "$github_sync_status" "$github_cloned_count" "$github_ff_count"
