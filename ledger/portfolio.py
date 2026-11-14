"""Estate-wide portfolio triage index, cross-repo blast radius, and fleet overlay.

Materializes ``state/portfolio-index.json`` during ``compile_state()`` so agents
can answer estate-wide triage, dependency/blast-radius, structural cycle, and
``INV-FLEET-GAME-01`` dispatchability queries in a single ``O(1)`` read instead
of loading ``N`` individual ``state/projects/<id>.json`` capsules.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from . import COMPILER_VERSION, PORTFOLIO_INDEX_SCHEMA_VERSION
from .ecosystem import build_ecosystem_graph
from .fleet import DEFAULT_FLEET_PULSE_PATH, read_fleet_pulse_overlay
from .identity import resolve_payload
from .models import atomic_write_text
from .rules import infer_capsule_posture, infer_capsule_posture_reasons

PORTFOLIO_INDEX_FILENAME = "portfolio-index.json"


def _parse_reference_dt(raw: str | None) -> dt.datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        parsed = dt.datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except ValueError:
        return None


def build_portfolio_index(
    *,
    capsules: list[dict[str, Any]],
    run_id: str,
    compiled_at: str,
    input_observed_at: str,
    fleet_pulse_path: Path | None = None,
) -> dict[str, Any]:
    """Build deterministic estate-wide portfolio triage & ecosystem payload."""
    ref_dt = _parse_reference_dt(compiled_at)
    ecosystem_graph = build_ecosystem_graph(capsules)
    eco_by_pid = ecosystem_graph["by_project"]
    fleet_overlay = read_fleet_pulse_overlay(
        pulse_path=fleet_pulse_path,
        reference_iso=compiled_at,
    )

    by_lifecycle: dict[str, int] = {}
    by_urgency: dict[str, int] = {"0": 0, "1": 0, "2": 0, "3": 0}

    resolved_count = 0
    urgent_count = 0
    dirty_count = 0
    ahead_count = 0
    behind_count = 0
    remote_ahead_count = 0
    cycle_project_count = 0
    machina_active_count = 0

    projects: list[dict[str, Any]] = []
    for capsule in capsules:
        pid = str(capsule["canonical_project_id"])
        pkey = str(capsule["project_key"])
        dname = str(capsule["display_name"])

        pref = capsule.get("preferred_location") if isinstance(capsule.get("preferred_location"), dict) else {}
        pref_warn_codes = [str(c) for c in (pref.get("warning_codes") or []) if str(c).strip()]
        if pref.get("state") == "resolved":
            resolved_count += 1

        git_facts = capsule.get("git_facts") if isinstance(capsule.get("git_facts"), dict) else {}
        git_warn_codes = [str(c) for c in (git_facts.get("warning_codes") or []) if str(c).strip()]
        all_warn_codes = sorted(set(pref_warn_codes) | set(git_warn_codes))

        is_dirty = bool(git_facts.get("dirty") is True or "GIT_DIRTY_WORKTREE" in all_warn_codes)
        git_ahead = int(git_facts.get("ahead_count") or 0)
        git_behind = max(
            int(git_facts.get("behind_count") or 0),
            int(git_facts.get("main_behind_count") or 0),
        )
        remote_ahead = "GIT_REMOTE_AHEAD" in all_warn_codes

        if is_dirty:
            dirty_count += 1
        if git_ahead >= 1:
            ahead_count += 1
        if git_behind >= 1:
            behind_count += 1
        if remote_ahead:
            remote_ahead_count += 1

        posture_base = infer_capsule_posture(capsule, reference_now=ref_dt)
        posture_reasons = infer_capsule_posture_reasons(capsule, reference_now=ref_dt)
        cur_state = capsule.get("current_state") if isinstance(capsule.get("current_state"), dict) else {}
        declared_lc = str(cur_state.get("lifecycle_state") or "unknown")
        lc_posture = str(posture_base["lifecycle_posture"])
        urgency = int(posture_base["attention_urgency"])

        by_lifecycle[lc_posture] = by_lifecycle.get(lc_posture, 0) + 1
        urg_key = str(urgency)
        by_urgency[urg_key] = by_urgency.get(urg_key, 0) + 1
        if urgency >= 1:
            urgent_count += 1

        mc = (
            cur_state.get("machina_continuity")
            if isinstance(cur_state.get("machina_continuity"), dict)
            else {}
        )
        mc_active_id = mc.get("active_unit_id")
        if mc_active_id:
            machina_active_count += 1

        ctx_blk = capsule.get("ctx") if isinstance(capsule.get("ctx"), dict) else {}
        ctx_state = str(ctx_blk.get("state") or "not_present")
        raw_tf = ctx_blk.get("total_files")
        total_files = (
            int(raw_tf)
            if ctx_state == "available" and isinstance(raw_tf, int) and not isinstance(raw_tf, bool)
            else None
        )
        dep_sum = (
            ctx_blk.get("dependency_summary")
            if isinstance(ctx_blk.get("dependency_summary"), dict)
            else {}
        )
        cycle_cnt = int(dep_sum.get("cycle_count") or 0)
        if cycle_cnt >= 1:
            cycle_project_count += 1

        eco_entry = eco_by_pid.get(
            pid,
            {
                "depends_on_count": 0,
                "depended_on_by_count": 0,
                "depends_on": [],
                "depended_on_by": [],
            },
        )
        gbrain_blk = capsule.get("gbrain") if isinstance(capsule.get("gbrain"), dict) else {}

        projects.append(
            {
                "canonical_project_id": pid,
                "project_key": pkey,
                "display_name": dname,
                "canonical_url": capsule.get("canonical_url"),
                "gbrain_page_key": gbrain_blk.get("page_key"),
                "observation_count": int(capsule.get("observation_count") or 0),
                "preferred_location": {
                    "state": str(pref.get("state") or "unavailable"),
                    "location": pref.get("location"),
                    "source_id": pref.get("source_id"),
                    "source_class": pref.get("source_class"),
                    "machine_name": pref.get("machine_name"),
                    "confidence": str(pref.get("confidence") or "none"),
                    "warning_codes": all_warn_codes,
                },
                "git_state": {
                    "state": str(git_facts.get("state") or "not_applicable"),
                    "head_branch": git_facts.get("head_branch"),
                    "head_commit": git_facts.get("head_commit"),
                    "drift_state": str(git_facts.get("drift_state") or "not_applicable"),
                    "dirty": is_dirty if git_facts.get("state") == "known" else None,
                    "ahead_count": git_ahead if git_facts.get("state") == "known" else None,
                    "behind_count": git_behind if git_facts.get("state") == "known" else None,
                    "remote_ahead": remote_ahead,
                },
                "posture": {
                    "declared_lifecycle_state": declared_lc,
                    "lifecycle_posture": lc_posture,
                    "lifecycle_confidence": float(posture_base["lifecycle_confidence"]),
                    "lifecycle_reasons": posture_reasons["lifecycle_reasons"],
                    "attention_urgency": urgency,
                    "urgency_confidence": float(posture_base["urgency_confidence"]),
                    "urgency_reasons": posture_reasons["urgency_reasons"],
                },
                "machina": {
                    "state": str(mc.get("state") or "not_present"),
                    "active_unit_id": mc_active_id,
                    "last_completed_unit_id": mc.get("last_completed_unit_id"),
                    "has_checkpoint": bool(mc.get("has_checkpoint")),
                    "has_frontier": bool(mc.get("has_frontier")),
                },
                "structural_health": {
                    "ctx_state": ctx_state,
                    "total_files": total_files,
                    "subsystem_count": int(ctx_blk.get("subsystem_count") or 0),
                    "operational_surface_count": int(ctx_blk.get("operational_surface_count") or 0),
                    "internal_edge_count": int(dep_sum.get("internal_edge_count") or 0),
                    "cycle_count": cycle_cnt,
                },
                "ecosystem": eco_entry,
            }
        )

    projects.sort(
        key=lambda item: (
            -int(item["posture"]["attention_urgency"]),
            -int(item["ecosystem"]["depended_on_by_count"]),
            -int(item["structural_health"]["cycle_count"]),
            str(item["project_key"]).casefold(),
            str(item["canonical_project_id"]),
        )
    )

    return {
        "schema_version": PORTFOLIO_INDEX_SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "run_id": run_id,
        "compiled_at": compiled_at,
        "as_of": input_observed_at,
        "summary": {
            "project_count": len(projects),
            "resolved_count": resolved_count,
            "urgent_count": urgent_count,
            "dirty_count": dirty_count,
            "ahead_count": ahead_count,
            "behind_count": behind_count,
            "remote_ahead_count": remote_ahead_count,
            "cycle_project_count": cycle_project_count,
            "machina_active_count": machina_active_count,
            "ecosystem_edge_count": ecosystem_graph["edge_count"],
            "by_lifecycle_posture": dict(sorted(by_lifecycle.items())),
            "by_urgency": by_urgency,
        },
        "fleet": fleet_overlay,
        "ecosystem_edges": ecosystem_graph["edges"],
        "projects": projects,
    }


def materialize_portfolio_index(
    *,
    capsules: list[dict[str, Any]],
    state_dir: Path,
    run_id: str,
    compiled_at: str,
    input_observed_at: str,
    fleet_pulse_path: Path | None = None,
) -> dict[str, Any]:
    """Compile and atomically write ``state/portfolio-index.json``."""
    state_dir = state_dir.resolve()
    payload = build_portfolio_index(
        capsules=capsules,
        run_id=run_id,
        compiled_at=compiled_at,
        input_observed_at=input_observed_at,
        fleet_pulse_path=fleet_pulse_path,
    )
    out_path = state_dir / PORTFOLIO_INDEX_FILENAME
    atomic_write_text(out_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def load_portfolio_index(state_dir: Path) -> dict[str, Any]:
    """Load ``state/portfolio-index.json`` from disk."""
    path = state_dir.resolve() / PORTFOLIO_INDEX_FILENAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(
            f"Portfolio index does not exist: {path}; run `python -m ledger compile` first."
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Portfolio index is invalid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("projects"), list):
        raise ValueError(f"Portfolio index is malformed: {path}")
    return payload


def load_project_ecosystem_slice(state_dir: Path, canonical_project_id: str) -> dict[str, Any] | None:
    """Return the compiled ``ecosystem`` block for ``canonical_project_id`` when available."""
    path = state_dir.resolve() / PORTFOLIO_INDEX_FILENAME
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("projects"), list):
        return None
    for proj in doc["projects"]:
        if isinstance(proj, dict) and proj.get("canonical_project_id") == canonical_project_id:
            eco = proj.get("ecosystem")
            return dict(eco) if isinstance(eco, dict) else None
    return None


def portfolio_payload(
    state_dir: Path,
    *,
    urgent_only: bool = False,
    dirty_only: bool = False,
    behind_only: bool = False,
    remote_ahead_only: bool = False,
    cycles_only: bool = False,
    lifecycle: str | None = None,
    depends_on: str | None = None,
    depended_on_by: str | None = None,
    limit: int | None = None,
    fleet_pulse_path: Path | None = None,
) -> dict[str, Any]:
    """Query and filter the materialized estate portfolio index in ``O(N)`` memory."""
    state_dir = state_dir.resolve()
    doc = load_portfolio_index(state_dir)

    depends_on_pid: str | None = None
    if depends_on and depends_on.strip():
        res = resolve_payload(state_dir, depends_on.strip())
        if res.get("status") != "resolved":
            return res
        depends_on_pid = str(res["canonical_project_id"])

    depended_on_by_pid: str | None = None
    if depended_on_by and depended_on_by.strip():
        res = resolve_payload(state_dir, depended_on_by.strip())
        if res.get("status") != "resolved":
            return res
        depended_on_by_pid = str(res["canonical_project_id"])

    lifecycle_norm = lifecycle.strip().lower() if lifecycle and lifecycle.strip() else None

    matched: list[dict[str, Any]] = []
    for item in doc.get("projects", []):
        if urgent_only and int(item["posture"]["attention_urgency"]) < 1:
            continue
        if dirty_only and item["git_state"].get("dirty") is not True:
            continue
        if behind_only and int(item["git_state"].get("behind_count") or 0) < 1:
            continue
        if remote_ahead_only and item["git_state"].get("remote_ahead") is not True:
            continue
        if cycles_only and int(item["structural_health"].get("cycle_count") or 0) < 1:
            continue
        if lifecycle_norm is not None:
            lc_posture = str(item["posture"].get("lifecycle_posture") or "").lower()
            decl_lc = str(item["posture"].get("declared_lifecycle_state") or "").lower()
            if lifecycle_norm not in {lc_posture, decl_lc}:
                continue
        if depends_on_pid is not None:
            deps = item.get("ecosystem", {}).get("depends_on") or []
            if not any(d.get("canonical_project_id") == depends_on_pid for d in deps):
                continue
        if depended_on_by_pid is not None:
            consumers = item.get("ecosystem", {}).get("depended_on_by") or []
            if not any(c.get("canonical_project_id") == depended_on_by_pid for c in consumers):
                continue
        matched.append(item)

    total_matched = len(matched)
    if limit is not None and limit >= 0:
        matched = matched[:limit]

    fleet_block = doc.get("fleet")
    env_pulse = os.environ.get("PROJECT_LEDGER_FLEET_PULSE", "").strip()
    if fleet_pulse_path is not None or env_pulse or DEFAULT_FLEET_PULSE_PATH.is_file():
        now_iso = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        fleet_block = read_fleet_pulse_overlay(pulse_path=fleet_pulse_path, reference_iso=now_iso)

    matched_pids = {item["canonical_project_id"] for item in matched}
    has_project_filter = any(
        (
            urgent_only,
            dirty_only,
            behind_only,
            remote_ahead_only,
            cycles_only,
            lifecycle_norm is not None,
            depends_on_pid is not None,
            depended_on_by_pid is not None,
            limit is not None,
        )
    )
    if has_project_filter:
        edges = [
            edge
            for edge in doc.get("ecosystem_edges", [])
            if edge.get("source_project_id") in matched_pids
            or edge.get("target_project_id") in matched_pids
        ]
    else:
        edges = list(doc.get("ecosystem_edges", []))

    return {
        "status": "ok",
        "schema_version": doc["schema_version"],
        "compiler_version": doc["compiler_version"],
        "run_id": doc["run_id"],
        "compiled_at": doc["compiled_at"],
        "as_of": doc["as_of"],
        "summary": doc["summary"],
        "fleet": fleet_block,
        "filters": {
            "urgent_only": urgent_only,
            "dirty_only": dirty_only,
            "behind_only": behind_only,
            "remote_ahead_only": remote_ahead_only,
            "cycles_only": cycles_only,
            "lifecycle": lifecycle_norm,
            "depends_on": depends_on_pid,
            "depended_on_by": depended_on_by_pid,
            "limit": limit,
        },
        "matched_project_count": total_matched,
        "returned_project_count": len(matched),
        "ecosystem_edge_count": len(edges),
        "ecosystem_edges": edges,
        "projects": matched,
    }


__all__ = [
    "PORTFOLIO_INDEX_FILENAME",
    "build_portfolio_index",
    "load_portfolio_index",
    "load_project_ecosystem_slice",
    "materialize_portfolio_index",
    "portfolio_payload",
]
