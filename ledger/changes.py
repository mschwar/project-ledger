"""Semantic change feed and run-history delta compiler (Wave 4 — P4.5).

Materializes estate-level semantic deltas between trusted compiled runs at
``state/changes.json``, retains a bounded ring buffer of compact semantic run
snapshots at ``state/runs/<run_id>.json``, and powers ``ledger changes``
(including ``--since <run_id>`` multi-hop delta queries).

Design invariants:

- Re-compiling unchanged estate inputs produces zero semantic change events,
  even when run timestamps, probe timestamps, or compatibility snapshot IDs
  advance.
- Offline/unavailable sources never mark their observations or canonical
  projects as deleted; they surface as ``SOURCE_STATUS_CHANGED``,
  ``OBSERVATION_UNAVAILABLE``, and ``PROJECT_UNAVAILABLE``.
- Canonical project lineage is tracked across observation-set overlap and
  stable canonical project IDs so merges (``PROJECT_MERGED``), splits
  (``PROJECT_SPLIT``), remote re-anchoring (``PROJECT_REANCHORED``), and
  working-tree moves are distinguished from project creation and deletion.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import CHANGE_FEED_SCHEMA_VERSION, COMPILER_VERSION
from .identity import resolve_payload
from .ids import stable_id
from .models import portable_artifact_path

RUN_HISTORY_MAX_RUNS = 50

SUPPORTED_CHANGE_ENTITY_KINDS = frozenset(
    {
        "source",
        "canonical_project",
        "observation",
        "review_item",
        "identity_decision",
    }
)

_ENTITY_KIND_ORDER = {
    "source": 0,
    "canonical_project": 1,
    "observation": 2,
    "review_item": 3,
    "identity_decision": 4,
}


def _load_json_dict(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def build_run_snapshot(
    *,
    run_id: str,
    compiled_at: str,
    input_observed_at: str,
    health_state: str,
    sources: list[dict],
    observations: list[dict],
    canonical_projects: list[dict],
    review_items: list[dict],
    cross_project_weak_overlaps: list[dict] | None = None,
    capsules_by_id: dict[str, dict] | None = None,
    sequence: int | None = None,
) -> dict:
    """Build a compact deterministic semantic snapshot for one compiled run."""
    capsules_map = capsules_by_id or {}
    overlaps = list(cross_project_weak_overlaps or [])

    obs_to_project: dict[str, str] = {}
    for proj in canonical_projects:
        pid = str(proj.get("canonical_project_id") or "").strip()
        if not pid:
            continue
        for oid in proj.get("observation_ids", []):
            obs_to_project[str(oid)] = pid

    sources_snap: dict[str, dict] = {}
    for src in sorted(sources, key=lambda item: str(item.get("source_id") or "")):
        sid = str(src.get("source_id") or "").strip()
        if not sid:
            continue
        sources_snap[sid] = {
            "source_id": sid,
            "label": str(src.get("label") or "").strip() or None,
            "source_class": str(src.get("source_class") or "").strip() or None,
            "status": str(src.get("status") or "unavailable"),
            "status_reason": str(src.get("status_reason") or "").strip() or None,
            "result_state": str(src.get("result_state") or "unavailable"),
            "observation_count": int(src.get("observation_count") or 0),
        }

    observations_snap: dict[str, dict] = {}
    for obs in sorted(observations, key=lambda item: str(item.get("observation_id") or "")):
        oid = str(obs.get("observation_id") or "").strip()
        if not oid:
            continue
        observations_snap[oid] = {
            "observation_id": oid,
            "source_id": str(obs.get("source_id") or "").strip() or None,
            "source_resolution": str(obs.get("source_resolution") or "resolved"),
            "location": str(obs.get("location") or "").strip() or None,
            "project_key": str(obs.get("project_key") or "").strip() or None,
            "canonical_project_id": obs_to_project.get(oid),
        }

    projects_snap: dict[str, dict] = {}
    for proj in sorted(
        canonical_projects, key=lambda item: str(item.get("canonical_project_id") or "")
    ):
        pid = str(proj.get("canonical_project_id") or "").strip()
        if not pid:
            continue
        capsule = capsules_map.get(pid) or {}
        pref = capsule.get("preferred_location") if isinstance(capsule.get("preferred_location"), dict) else {}
        git_facts = capsule.get("git_facts") if isinstance(capsule.get("git_facts"), dict) else {}
        curr_state = capsule.get("current_state") if isinstance(capsule.get("current_state"), dict) else {}
        gbrain = proj.get("gbrain") if isinstance(proj.get("gbrain"), dict) else (
            capsule.get("gbrain") if isinstance(capsule.get("gbrain"), dict) else {}
        )
        alias_decision_ids = sorted(
            {
                str(ref.get("decision_id")).strip()
                for ref in proj.get("referents", [])
                if isinstance(ref, dict)
                and ref.get("kind") == "decision_alias"
                and str(ref.get("decision_id") or "").strip()
            }
        )
        projects_snap[pid] = {
            "canonical_project_id": pid,
            "project_key": str(proj.get("project_key") or pid),
            "display_name": str(proj.get("display_name") or proj.get("project_key") or pid),
            "gbrain_source_slug": str(
                proj.get("gbrain_source_slug") or gbrain.get("source_slug") or ""
            ).strip()
            or None,
            "gbrain_page_key": str(gbrain.get("page_key") or "").strip() or None,
            "observation_ids": sorted(str(oid) for oid in proj.get("observation_ids", [])),
            "identity_anchor": dict(proj.get("identity_anchor") or {}),
            "normalized_remotes": sorted(str(r) for r in proj.get("normalized_remotes", [])),
            "merge_decision_ids": sorted(str(d) for d in proj.get("merge_decision_ids", [])),
            "canonical_key_decision_id": proj.get("canonical_key_decision_id"),
            "alias_decision_ids": alias_decision_ids,
            "preferred_location": {
                "state": pref.get("state"),
                "observation_id": pref.get("observation_id"),
                "source_id": pref.get("source_id"),
                "source_class": pref.get("source_class"),
                "location": pref.get("location"),
                "confidence": pref.get("confidence"),
            },
            "git_facts": {
                "state": git_facts.get("state"),
                "observation_id": git_facts.get("observation_id"),
                "head_branch": git_facts.get("head_branch"),
                "head_commit": git_facts.get("head_commit"),
                "remote_url": git_facts.get("remote_url"),
                "drift_state": git_facts.get("drift_state"),
                "ahead_count": git_facts.get("ahead_count"),
                "behind_count": git_facts.get("behind_count"),
                "main_behind_count": git_facts.get("main_behind_count"),
                "dirty": git_facts.get("dirty"),
                "warning_codes": sorted(str(c) for c in (git_facts.get("warning_codes") or [])),
                "warnings": list(git_facts.get("warnings") or []),
            },
            "current_state": {
                "lifecycle_state": curr_state.get("lifecycle_state"),
                "last_session_at": curr_state.get("last_session_at"),
                "next_step": curr_state.get("next_step"),
            },
        }

    reviews_snap: dict[str, dict] = {}
    for rev in sorted(review_items, key=lambda item: str(item.get("review_id") or "")):
        rid = str(rev.get("review_id") or "").strip()
        if not rid:
            continue
        reviews_snap[rid] = {
            "review_id": rid,
            "code": str(rev.get("code") or ""),
            "state": str(rev.get("state") or "open"),
            "severity": str(rev.get("severity") or "warning"),
            "affected_observation_ids": sorted(
                str(oid) for oid in rev.get("affected_observation_ids", [])
            ),
            "affected_decision_ids": sorted(
                str(did) for did in rev.get("affected_decision_ids", [])
            ),
            "detail": str(rev.get("detail") or ""),
        }

    untriaged_weak_overlaps = 0
    negative_decisions_pairs: dict[str, set[tuple[str, str]]] = {}
    for item in overlaps:
        if not isinstance(item, dict):
            continue
        left = str(item.get("left_observation_id") or "").strip()
        right = str(item.get("right_observation_id") or "").strip()
        neg_records = [
            rec
            for rec in (item.get("evidence") or [])
            if isinstance(rec, dict) and rec.get("kind") == "negative_decision"
        ]
        if not neg_records:
            untriaged_weak_overlaps += 1
            continue
        if left and right:
            pair = (min(left, right), max(left, right))
            for rec in neg_records:
                raw_val = str(rec.get("value") or "").strip()
                for did in raw_val.split("|"):
                    clean_did = did.strip()
                    if clean_did:
                        negative_decisions_pairs.setdefault(clean_did, set()).add(pair)

    negative_decisions_snap = {
        did: {
            "decision_id": did,
            "pairs": [list(pair) for pair in sorted(pairs)],
        }
        for did, pairs in sorted(negative_decisions_pairs.items())
    }

    snapshot: dict = {
        "schema_version": CHANGE_FEED_SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "run_id": run_id,
        "compiled_at": compiled_at,
        "input_observed_at": input_observed_at,
        "health_state": health_state,
        "counts": {
            "sources": len(sources_snap),
            "observations": len(observations_snap),
            "canonical_projects": len(projects_snap),
            "review_items": len(reviews_snap),
            "untriaged_weak_overlaps": untriaged_weak_overlaps,
        },
        "sources": sources_snap,
        "observations": observations_snap,
        "projects": projects_snap,
        "reviews": reviews_snap,
        "negative_decisions": negative_decisions_snap,
    }
    if sequence is not None:
        snapshot["sequence"] = int(sequence)
    return snapshot


def snapshot_from_state_dir(state_dir: Path) -> dict | None:
    """Reconstruct a semantic run snapshot from compiled artifacts in ``state_dir``."""
    state_dir = state_dir.resolve()
    manifest = _load_json_dict(state_dir / "system-manifest.json")
    if manifest is None:
        return None
    run_id = str(manifest.get("run_id") or "").strip()
    if not run_id:
        return None

    observations_doc = _load_json_dict(state_dir / "observations.json") or {}
    canonical_doc = _load_json_dict(state_dir / "canonical-projects.json") or {}
    reviews_doc = _load_json_dict(state_dir / "review-queue.json") or {}
    evidence_doc = _load_json_dict(state_dir / "identity-evidence.json") or {}

    projects = list(canonical_doc.get("projects") or [])
    capsules_by_id: dict[str, dict] = {}
    capsules_dir = state_dir / "projects"
    if capsules_dir.is_dir():
        for proj in projects:
            pid = str(proj.get("canonical_project_id") or "").strip()
            if not pid:
                continue
            cap = _load_json_dict(capsules_dir / f"{pid}.json")
            if cap is not None:
                capsules_by_id[pid] = cap

    health = manifest.get("health") if isinstance(manifest.get("health"), dict) else {}
    return build_run_snapshot(
        run_id=run_id,
        compiled_at=str(manifest.get("compiled_at") or ""),
        input_observed_at=str(manifest.get("input_observed_at") or ""),
        health_state=str(health.get("state") or "unknown"),
        sources=list(manifest.get("sources") or []),
        observations=list(observations_doc.get("observations") or []),
        canonical_projects=projects,
        review_items=list(reviews_doc.get("items") or []),
        cross_project_weak_overlaps=list(evidence_doc.get("cross_project_weak_overlaps") or []),
        capsules_by_id=capsules_by_id,
    )


def _runs_dir(state_dir: Path) -> Path:
    return state_dir.resolve() / "runs"


def _load_retained_snapshots(state_dir: Path) -> list[dict]:
    runs_dir = _runs_dir(state_dir)
    if not runs_dir.is_dir():
        return []
    snapshots: list[dict] = []
    for path in runs_dir.glob("*.json"):
        doc = _load_json_dict(path)
        if doc is not None and str(doc.get("run_id") or "").strip():
            snapshots.append(doc)
    snapshots.sort(
        key=lambda item: (
            int(item.get("sequence")) if isinstance(item.get("sequence"), int) else -1,
            str(item.get("compiled_at") or ""),
            str(item.get("run_id") or ""),
        )
    )
    return snapshots


def _save_run_snapshot(
    state_dir: Path,
    snapshot: dict,
    *,
    max_runs: int = RUN_HISTORY_MAX_RUNS,
) -> list[str]:
    """Persist ``snapshot`` to ``state/runs/<run_id>.json`` and prune beyond ``max_runs``."""
    runs_dir = _runs_dir(state_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_id = str(snapshot["run_id"])
    target_path = runs_dir / f"{run_id}.json"

    existing = _load_retained_snapshots(state_dir)
    existing_by_id = {str(item["run_id"]): item for item in existing}
    if run_id in existing_by_id and isinstance(existing_by_id[run_id].get("sequence"), int):
        seq = int(existing_by_id[run_id]["sequence"])
    else:
        max_seq = max(
            (int(item["sequence"]) for item in existing if isinstance(item.get("sequence"), int)),
            default=0,
        )
        seq = max_seq + 1

    to_write = dict(snapshot)
    to_write["sequence"] = seq
    target_path.write_text(json.dumps(to_write, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    retained = _load_retained_snapshots(state_dir)
    if max_runs > 0 and len(retained) > max_runs:
        to_prune = retained[: len(retained) - max_runs]
        for old in to_prune:
            old_path = runs_dir / f"{old['run_id']}.json"
            if old_path.is_file():
                old_path.unlink()
        retained = retained[len(retained) - max_runs :]

    return [str(item["run_id"]) for item in retained]


def capture_pre_compile_baseline(
    state_dir: Path,
    *,
    current_run_id: str,
    max_runs: int = RUN_HISTORY_MAX_RUNS,
) -> dict | None:
    """Capture and backfill any pre-existing state in ``state_dir`` before compile overwrites it."""
    state_dir = state_dir.resolve()
    existing_manifest = _load_json_dict(state_dir / "system-manifest.json")
    if existing_manifest is None:
        return None
    existing_run_id = str(existing_manifest.get("run_id") or "").strip()
    if not existing_run_id:
        return None

    runs_dir = _runs_dir(state_dir)
    existing_run_path = runs_dir / f"{existing_run_id}.json"

    if existing_run_id != current_run_id:
        baseline = _load_json_dict(existing_run_path)
        if baseline is None:
            baseline = snapshot_from_state_dir(state_dir)
            if baseline is not None:
                _save_run_snapshot(state_dir, baseline, max_runs=max_runs)
        return baseline

    # Re-compiling the exact same run_id: preserve idempotence by comparing against
    # that run's original predecessor rather than against itself.
    existing_changes = _load_json_dict(state_dir / "changes.json")
    if existing_changes is not None and existing_changes.get("run_id") == current_run_id:
        prev_id = existing_changes.get("previous_run_id")
        if prev_id is None:
            return None
        prev_snap = _load_json_dict(runs_dir / f"{prev_id}.json")
        if prev_snap is not None:
            return prev_snap

    retained = [
        snap for snap in _load_retained_snapshots(state_dir) if snap.get("run_id") != current_run_id
    ]
    return retained[-1] if retained else None


def _make_change(
    *,
    previous_run_id: str,
    run_id: str,
    entity_kind: str,
    entity_id: str,
    change_type: str,
    materiality: str,
    severity: str,
    summary: str,
    before: dict | None,
    after: dict | None,
    provenance: dict | None = None,
    canonical_project_id: str | None = None,
    project_key: str | None = None,
) -> dict:
    return {
        "change_id": stable_id(
            "chg",
            previous_run_id,
            run_id,
            entity_kind,
            entity_id,
            change_type,
        ),
        "previous_run_id": previous_run_id,
        "run_id": run_id,
        "entity_kind": entity_kind,
        "entity_id": entity_id,
        "canonical_project_id": canonical_project_id,
        "project_key": project_key,
        "change_type": change_type,
        "materiality": materiality,
        "severity": severity,
        "summary": summary,
        "before": before,
        "after": after,
        "provenance": provenance or {},
    }


def _emit_project_field_changes(
    prev_proj: dict,
    curr_proj: dict,
    *,
    previous_run_id: str,
    run_id: str,
    events: list[dict],
) -> None:
    pid = curr_proj["canonical_project_id"]
    pkey = curr_proj["project_key"]

    if prev_proj.get("project_key") != curr_proj.get("project_key"):
        events.append(
            _make_change(
                previous_run_id=previous_run_id,
                run_id=run_id,
                entity_kind="canonical_project",
                entity_id=pid,
                canonical_project_id=pid,
                project_key=pkey,
                change_type="PROJECT_KEY_CHANGED",
                materiality="material",
                severity="info",
                summary=(
                    f"Project {pid} key changed from {prev_proj.get('project_key')!r} "
                    f"to {curr_proj.get('project_key')!r}."
                ),
                before={"project_key": prev_proj.get("project_key")},
                after={"project_key": curr_proj.get("project_key")},
                provenance={
                    "canonical_key_decision_id": curr_proj.get("canonical_key_decision_id"),
                },
            )
        )

    prev_pref = prev_proj.get("preferred_location") or {}
    curr_pref = curr_proj.get("preferred_location") or {}
    pref_sig_before = (
        prev_pref.get("state"),
        prev_pref.get("location"),
        prev_pref.get("observation_id"),
        prev_pref.get("source_class"),
    )
    pref_sig_after = (
        curr_pref.get("state"),
        curr_pref.get("location"),
        curr_pref.get("observation_id"),
        curr_pref.get("source_class"),
    )
    if pref_sig_before != pref_sig_after:
        severity = "warning" if curr_pref.get("state") not in {"resolved", None} else "info"
        events.append(
            _make_change(
                previous_run_id=previous_run_id,
                run_id=run_id,
                entity_kind="canonical_project",
                entity_id=pid,
                canonical_project_id=pid,
                project_key=pkey,
                change_type="PROJECT_PREFERRED_LOCATION_CHANGED",
                materiality="high",
                severity=severity,
                summary=(
                    f"Preferred location for {pkey} ({pid}) changed from "
                    f"{prev_pref.get('location')!r} [{prev_pref.get('state')}] to "
                    f"{curr_pref.get('location')!r} [{curr_pref.get('state')}]."
                ),
                before=prev_pref,
                after=curr_pref,
                provenance={
                    "before_observation_id": prev_pref.get("observation_id"),
                    "after_observation_id": curr_pref.get("observation_id"),
                },
            )
        )

    prev_git = prev_proj.get("git_facts") or {}
    curr_git = curr_proj.get("git_facts") or {}
    git_state_before = (
        prev_git.get("state"),
        prev_git.get("head_branch"),
        prev_git.get("head_commit"),
        prev_git.get("remote_url"),
    )
    git_state_after = (
        curr_git.get("state"),
        curr_git.get("head_branch"),
        curr_git.get("head_commit"),
        curr_git.get("remote_url"),
    )
    if git_state_before != git_state_after:
        events.append(
            _make_change(
                previous_run_id=previous_run_id,
                run_id=run_id,
                entity_kind="canonical_project",
                entity_id=pid,
                canonical_project_id=pid,
                project_key=pkey,
                change_type="PROJECT_GIT_STATE_CHANGED",
                materiality="material",
                severity="info",
                summary=(
                    f"Git state for {pkey} ({pid}) changed: "
                    f"{prev_git.get('head_branch')}@{prev_git.get('head_commit')} -> "
                    f"{curr_git.get('head_branch')}@{curr_git.get('head_commit')}."
                ),
                before={
                    "state": prev_git.get("state"),
                    "head_branch": prev_git.get("head_branch"),
                    "head_commit": prev_git.get("head_commit"),
                    "remote_url": prev_git.get("remote_url"),
                },
                after={
                    "state": curr_git.get("state"),
                    "head_branch": curr_git.get("head_branch"),
                    "head_commit": curr_git.get("head_commit"),
                    "remote_url": curr_git.get("remote_url"),
                },
                provenance={
                    "observation_id": curr_git.get("observation_id") or prev_git.get("observation_id"),
                },
            )
        )

    def _norm_drift_sig(git_info: dict) -> tuple:
        d_state = git_info.get("drift_state")
        known_worktree = d_state not in {None, "unknown", "not_applicable"}
        return (
            d_state,
            tuple(git_info.get("warning_codes") or []),
            int(git_info.get("ahead_count") or 0) if known_worktree else git_info.get("ahead_count"),
            int(git_info.get("behind_count") or 0) if known_worktree else git_info.get("behind_count"),
            int(git_info.get("main_behind_count") or 0)
            if known_worktree
            else git_info.get("main_behind_count"),
            bool(git_info.get("dirty")) if known_worktree else git_info.get("dirty"),
        )

    def _fmt_drift_label(git_info: dict) -> str:
        codes = list(git_info.get("warning_codes") or [])
        parts = list(codes) if codes else ["clean"]
        for key in ("behind_count", "main_behind_count", "ahead_count"):
            val = git_info.get(key)
            if isinstance(val, int) and val > 0:
                parts.append(f"{key}={val}")
        return f"{git_info.get('drift_state')} ({', '.join(parts)})"

    drift_before = _norm_drift_sig(prev_git)
    drift_after = _norm_drift_sig(curr_git)
    if drift_before != drift_after:
        has_warnings = bool(curr_git.get("warning_codes"))
        events.append(
            _make_change(
                previous_run_id=previous_run_id,
                run_id=run_id,
                entity_kind="canonical_project",
                entity_id=pid,
                canonical_project_id=pid,
                project_key=pkey,
                change_type="PROJECT_GIT_DRIFT_CHANGED",
                materiality="high" if has_warnings else "material",
                severity="warning" if has_warnings else "info",
                summary=(
                    f"Git drift for {pkey} ({pid}) changed: "
                    f"{_fmt_drift_label(prev_git)} -> {_fmt_drift_label(curr_git)}."
                ),
                before={
                    "drift_state": prev_git.get("drift_state"),
                    "ahead_count": prev_git.get("ahead_count"),
                    "behind_count": prev_git.get("behind_count"),
                    "main_behind_count": prev_git.get("main_behind_count"),
                    "dirty": prev_git.get("dirty"),
                    "warning_codes": list(prev_git.get("warning_codes") or []),
                    "warnings": list(prev_git.get("warnings") or []),
                },
                after={
                    "drift_state": curr_git.get("drift_state"),
                    "ahead_count": curr_git.get("ahead_count"),
                    "behind_count": curr_git.get("behind_count"),
                    "main_behind_count": curr_git.get("main_behind_count"),
                    "dirty": curr_git.get("dirty"),
                    "warning_codes": list(curr_git.get("warning_codes") or []),
                    "warnings": list(curr_git.get("warnings") or []),
                },
                provenance={
                    "observation_id": curr_git.get("observation_id") or prev_git.get("observation_id"),
                },
            )
        )

    gbrain_before = (prev_proj.get("gbrain_source_slug"), prev_proj.get("gbrain_page_key"))
    gbrain_after = (curr_proj.get("gbrain_source_slug"), curr_proj.get("gbrain_page_key"))
    if gbrain_before != gbrain_after:
        events.append(
            _make_change(
                previous_run_id=previous_run_id,
                run_id=run_id,
                entity_kind="canonical_project",
                entity_id=pid,
                canonical_project_id=pid,
                project_key=pkey,
                change_type="PROJECT_GBRAIN_CHANGED",
                materiality="low",
                severity="info",
                summary=(
                    f"GBrain cross-link for {pkey} ({pid}) changed: "
                    f"{prev_proj.get('gbrain_source_slug')}:{prev_proj.get('gbrain_page_key')} -> "
                    f"{curr_proj.get('gbrain_source_slug')}:{curr_proj.get('gbrain_page_key')}."
                ),
                before={
                    "gbrain_source_slug": prev_proj.get("gbrain_source_slug"),
                    "gbrain_page_key": prev_proj.get("gbrain_page_key"),
                },
                after={
                    "gbrain_source_slug": curr_proj.get("gbrain_source_slug"),
                    "gbrain_page_key": curr_proj.get("gbrain_page_key"),
                },
            )
        )

    prev_cs = prev_proj.get("current_state") or {}
    curr_cs = curr_proj.get("current_state") or {}
    cs_before = (
        prev_cs.get("lifecycle_state"),
        prev_cs.get("last_session_at"),
        prev_cs.get("next_step"),
    )
    cs_after = (
        curr_cs.get("lifecycle_state"),
        curr_cs.get("last_session_at"),
        curr_cs.get("next_step"),
    )
    if cs_before != cs_after:
        events.append(
            _make_change(
                previous_run_id=previous_run_id,
                run_id=run_id,
                entity_kind="canonical_project",
                entity_id=pid,
                canonical_project_id=pid,
                project_key=pkey,
                change_type="PROJECT_CONTINUITY_CHANGED",
                materiality="material",
                severity="info",
                summary=f"Continuity/current-state declaration updated for {pkey} ({pid}).",
                before=prev_cs,
                after=curr_cs,
            )
        )


def diff_run_snapshots(
    previous: dict | None,
    current: dict,
    *,
    state_dir: Path | None = None,
    available_run_ids: list[str] | None = None,
) -> dict:
    """Compute the deterministic semantic change feed between ``previous`` and ``current``."""
    run_id = str(current["run_id"])
    compiled_at = str(current.get("compiled_at") or "")
    input_observed_at = str(current.get("input_observed_at") or "")

    pointers = {}
    if state_dir is not None:
        resolved_dir = state_dir.resolve()
        pointers = {
            "changes_path": portable_artifact_path(resolved_dir / "changes.json", state_dir=resolved_dir),
            "runs_dir": portable_artifact_path(_runs_dir(resolved_dir), state_dir=resolved_dir),
            "system_manifest_path": portable_artifact_path(
                resolved_dir / "system-manifest.json", state_dir=resolved_dir
            ),
        }

    if previous is None:
        return {
            "schema_version": CHANGE_FEED_SCHEMA_VERSION,
            "compiler_version": COMPILER_VERSION,
            "status": "ok",
            "baseline_state": "initial_baseline",
            "run_id": run_id,
            "compiled_at": compiled_at,
            "input_observed_at": input_observed_at,
            "previous_run_id": None,
            "previous_compiled_at": None,
            "previous_input_observed_at": None,
            "change_count": 0,
            "review_count": int((current.get("counts") or {}).get("review_items", 0) or 0),
            "untriaged_weak_overlap_count": int(
                (current.get("counts") or {}).get("untriaged_weak_overlaps", 0) or 0
            ),
            "summary": {
                "by_entity_kind": {},
                "by_change_type": {},
                "by_severity": {},
                "by_materiality": {},
                "count_deltas": {
                    key: {"before": None, "after": val, "delta": None}
                    for key, val in sorted((current.get("counts") or {}).items())
                },
            },
            "available_run_ids": list(available_run_ids or [run_id]),
            "pointers": pointers,
            "changes": [],
        }

    previous_run_id = str(previous["run_id"])
    if previous_run_id == run_id:
        return {
            "schema_version": CHANGE_FEED_SCHEMA_VERSION,
            "compiler_version": COMPILER_VERSION,
            "status": "ok",
            "baseline_state": "same_run",
            "run_id": run_id,
            "compiled_at": compiled_at,
            "input_observed_at": input_observed_at,
            "previous_run_id": previous_run_id,
            "previous_compiled_at": previous.get("compiled_at"),
            "previous_input_observed_at": previous.get("input_observed_at"),
            "change_count": 0,
            "review_count": int((current.get("counts") or {}).get("review_items", 0) or 0),
            "untriaged_weak_overlap_count": int(
                (current.get("counts") or {}).get("untriaged_weak_overlaps", 0) or 0
            ),
            "summary": {
                "by_entity_kind": {},
                "by_change_type": {},
                "by_severity": {},
                "by_materiality": {},
                "count_deltas": {
                    key: {"before": val, "after": val, "delta": 0}
                    for key, val in sorted((current.get("counts") or {}).items())
                },
            },
            "available_run_ids": list(available_run_ids or [run_id]),
            "pointers": pointers,
            "changes": [],
        }

    events: list[dict] = []
    prev_sources: dict[str, dict] = previous.get("sources") or {}
    curr_sources: dict[str, dict] = current.get("sources") or {}
    prev_obs: dict[str, dict] = previous.get("observations") or {}
    curr_obs: dict[str, dict] = current.get("observations") or {}
    prev_projects: dict[str, dict] = previous.get("projects") or {}
    curr_projects: dict[str, dict] = current.get("projects") or {}
    prev_reviews: dict[str, dict] = previous.get("reviews") or {}
    curr_reviews: dict[str, dict] = current.get("reviews") or {}
    prev_neg: dict[str, dict] = previous.get("negative_decisions") or {}
    curr_neg: dict[str, dict] = current.get("negative_decisions") or {}

    def _source_currently_unavailable(source_id: str | None) -> bool:
        if not source_id:
            return False
        src = curr_sources.get(source_id)
        return src is not None and src.get("status") != "available"

    # 1. Source deltas
    for sid in sorted(set(curr_sources) - set(prev_sources)):
        src = curr_sources[sid]
        events.append(
            _make_change(
                previous_run_id=previous_run_id,
                run_id=run_id,
                entity_kind="source",
                entity_id=sid,
                change_type="SOURCE_ADDED",
                materiality="material",
                severity="info",
                summary=f"Registered source {sid} ({src.get('source_class')}, status={src.get('status')}).",
                before=None,
                after=src,
                provenance={"source_id": sid},
            )
        )
    for sid in sorted(set(prev_sources) - set(curr_sources)):
        src = prev_sources[sid]
        events.append(
            _make_change(
                previous_run_id=previous_run_id,
                run_id=run_id,
                entity_kind="source",
                entity_id=sid,
                change_type="SOURCE_REMOVED",
                materiality="material",
                severity="warning",
                summary=f"Source {sid} ({src.get('label')}) is no longer registered.",
                before=src,
                after=None,
                provenance={"source_id": sid},
            )
        )
    for sid in sorted(set(prev_sources) & set(curr_sources)):
        b_src = prev_sources[sid]
        a_src = curr_sources[sid]
        if b_src.get("status") != a_src.get("status"):
            degraded = a_src.get("status") != "available"
            events.append(
                _make_change(
                    previous_run_id=previous_run_id,
                    run_id=run_id,
                    entity_kind="source",
                    entity_id=sid,
                    change_type="SOURCE_STATUS_CHANGED",
                    materiality="high",
                    severity="warning" if degraded else "info",
                    summary=(
                        f"Source {sid} status changed from {b_src.get('status')} "
                        f"to {a_src.get('status')} ({a_src.get('status_reason')})."
                    ),
                    before={
                        "status": b_src.get("status"),
                        "status_reason": b_src.get("status_reason"),
                        "result_state": b_src.get("result_state"),
                    },
                    after={
                        "status": a_src.get("status"),
                        "status_reason": a_src.get("status_reason"),
                        "result_state": a_src.get("result_state"),
                    },
                    provenance={"source_id": sid},
                )
            )
        elif b_src.get("result_state") != a_src.get("result_state"):
            became_empty = a_src.get("result_state") == "observed_empty"
            events.append(
                _make_change(
                    previous_run_id=previous_run_id,
                    run_id=run_id,
                    entity_kind="source",
                    entity_id=sid,
                    change_type="SOURCE_RESULT_STATE_CHANGED",
                    materiality="material",
                    severity="warning" if became_empty else "info",
                    summary=(
                        f"Source {sid} result_state changed from {b_src.get('result_state')} "
                        f"to {a_src.get('result_state')}."
                    ),
                    before={
                        "result_state": b_src.get("result_state"),
                        "observation_count": b_src.get("observation_count"),
                    },
                    after={
                        "result_state": a_src.get("result_state"),
                        "observation_count": a_src.get("observation_count"),
                    },
                    provenance={"source_id": sid},
                )
            )

    # 2. Observation topology deltas
    for oid in sorted(set(curr_obs) - set(prev_obs)):
        obs = curr_obs[oid]
        pid = obs.get("canonical_project_id")
        pkey = curr_projects.get(pid, {}).get("project_key") if pid else None
        events.append(
            _make_change(
                previous_run_id=previous_run_id,
                run_id=run_id,
                entity_kind="observation",
                entity_id=oid,
                canonical_project_id=pid,
                project_key=pkey,
                change_type="OBSERVATION_ADDED",
                materiality="material",
                severity="info",
                summary=f"Observed new manifestation {oid} at {obs.get('location')!r} (source={obs.get('source_id')}).",
                before=None,
                after=obs,
                provenance={"source_id": obs.get("source_id"), "canonical_project_id": pid},
            )
        )
    for oid in sorted(set(prev_obs) - set(curr_obs)):
        obs = prev_obs[oid]
        pid = obs.get("canonical_project_id")
        pkey = prev_projects.get(pid, {}).get("project_key") if pid else None
        sid = obs.get("source_id")
        if _source_currently_unavailable(sid):
            events.append(
                _make_change(
                    previous_run_id=previous_run_id,
                    run_id=run_id,
                    entity_kind="observation",
                    entity_id=oid,
                    canonical_project_id=pid,
                    project_key=pkey,
                    change_type="OBSERVATION_UNAVAILABLE",
                    materiality="material",
                    severity="warning",
                    summary=(
                        f"Observation {oid} at {obs.get('location')!r} was not observed because "
                        f"source {sid} is currently unavailable."
                    ),
                    before=obs,
                    after={"state": "unavailable", "reason_code": "SOURCE_UNAVAILABLE", "source_id": sid},
                    provenance={"source_id": sid, "previous_canonical_project_id": pid},
                )
            )
        else:
            events.append(
                _make_change(
                    previous_run_id=previous_run_id,
                    run_id=run_id,
                    entity_kind="observation",
                    entity_id=oid,
                    canonical_project_id=pid,
                    project_key=pkey,
                    change_type="OBSERVATION_REMOVED",
                    materiality="material",
                    severity="warning",
                    summary=f"Observation {oid} at {obs.get('location')!r} is no longer present in source {sid}.",
                    before=obs,
                    after=None,
                    provenance={"source_id": sid, "previous_canonical_project_id": pid},
                )
            )

    # 3. Canonical project bipartite lineage & field deltas
    prev_obs_to_pid: dict[str, str] = {}
    for pid, proj in prev_projects.items():
        for oid in proj.get("observation_ids") or []:
            prev_obs_to_pid[oid] = pid

    preds_by_curr: dict[str, set[str]] = {pid: set() for pid in curr_projects}
    succs_by_prev: dict[str, set[str]] = {pid: set() for pid in prev_projects}

    for curr_pid, curr_proj in curr_projects.items():
        if curr_pid in prev_projects:
            preds_by_curr[curr_pid].add(curr_pid)
            succs_by_prev[curr_pid].add(curr_pid)
        for oid in curr_proj.get("observation_ids") or []:
            prev_pid = prev_obs_to_pid.get(oid)
            if prev_pid is not None:
                preds_by_curr[curr_pid].add(prev_pid)
                succs_by_prev[prev_pid].add(curr_pid)

    # 3a. Degree-0 previous projects: unavailable vs removed, and >=2 successors: split
    for prev_pid in sorted(prev_projects):
        prev_proj = prev_projects[prev_pid]
        succs = sorted(succs_by_prev[prev_pid])
        if not succs:
            member_oids = list(prev_proj.get("observation_ids") or [])
            member_sources = sorted(
                {
                    str(prev_obs[oid].get("source_id"))
                    for oid in member_oids
                    if oid in prev_obs and prev_obs[oid].get("source_id")
                }
            )
            all_unavailable = bool(member_oids) and all(
                _source_currently_unavailable(prev_obs.get(oid, {}).get("source_id"))
                for oid in member_oids
            )
            if all_unavailable:
                events.append(
                    _make_change(
                        previous_run_id=previous_run_id,
                        run_id=run_id,
                        entity_kind="canonical_project",
                        entity_id=prev_pid,
                        canonical_project_id=prev_pid,
                        project_key=prev_proj.get("project_key"),
                        change_type="PROJECT_UNAVAILABLE",
                        materiality="high",
                        severity="warning",
                        summary=(
                            f"Project {prev_proj.get('project_key')} ({prev_pid}) was not observed "
                            f"because its source(s) {member_sources} are currently unavailable."
                        ),
                        before={
                            "canonical_project_id": prev_pid,
                            "project_key": prev_proj.get("project_key"),
                            "observation_ids": member_oids,
                            "preferred_location": prev_proj.get("preferred_location"),
                        },
                        after={
                            "state": "unavailable",
                            "reason_code": "SOURCE_UNAVAILABLE",
                            "unavailable_source_ids": member_sources,
                        },
                        provenance={
                            "observation_ids": member_oids,
                            "unavailable_source_ids": member_sources,
                        },
                    )
                )
            else:
                events.append(
                    _make_change(
                        previous_run_id=previous_run_id,
                        run_id=run_id,
                        entity_kind="canonical_project",
                        entity_id=prev_pid,
                        canonical_project_id=prev_pid,
                        project_key=prev_proj.get("project_key"),
                        change_type="PROJECT_REMOVED",
                        materiality="high",
                        severity="warning",
                        summary=(
                            f"Project {prev_proj.get('project_key')} ({prev_pid}) is no longer "
                            f"present in available sources."
                        ),
                        before={
                            "canonical_project_id": prev_pid,
                            "project_key": prev_proj.get("project_key"),
                            "observation_ids": member_oids,
                            "preferred_location": prev_proj.get("preferred_location"),
                        },
                        after=None,
                        provenance={
                            "observation_ids": member_oids,
                            "source_ids": member_sources,
                        },
                    )
                )
        elif len(succs) >= 2:
            target_summaries = [
                {
                    "canonical_project_id": target_pid,
                    "project_key": curr_projects[target_pid].get("project_key"),
                    "observation_ids": curr_projects[target_pid].get("observation_ids"),
                }
                for target_pid in succs
            ]
            target_keys = ", ".join(
                f"{t['project_key']} ({t['canonical_project_id']})" for t in target_summaries
            )
            events.append(
                _make_change(
                    previous_run_id=previous_run_id,
                    run_id=run_id,
                    entity_kind="canonical_project",
                    entity_id=prev_pid,
                    canonical_project_id=prev_pid,
                    project_key=prev_proj.get("project_key"),
                    change_type="PROJECT_SPLIT",
                    materiality="high",
                    severity="warning",
                    summary=(
                        f"Project {prev_proj.get('project_key')} ({prev_pid}) split into "
                        f"{len(succs)} canonical projects: {target_keys}."
                    ),
                    before={
                        "canonical_project_id": prev_pid,
                        "project_key": prev_proj.get("project_key"),
                        "observation_ids": prev_proj.get("observation_ids"),
                    },
                    after={
                        "target_canonical_project_ids": succs,
                        "targets": target_summaries,
                    },
                    provenance={
                        "source_canonical_project_id": prev_pid,
                        "target_canonical_project_ids": succs,
                    },
                )
            )

    # 3b. Current projects: added, merged, or 1-to-1 continuing
    for curr_pid in sorted(curr_projects):
        curr_proj = curr_projects[curr_pid]
        preds = sorted(preds_by_curr[curr_pid])
        pkey = curr_proj.get("project_key")

        if not preds:
            events.append(
                _make_change(
                    previous_run_id=previous_run_id,
                    run_id=run_id,
                    entity_kind="canonical_project",
                    entity_id=curr_pid,
                    canonical_project_id=curr_pid,
                    project_key=pkey,
                    change_type="PROJECT_ADDED",
                    materiality="high",
                    severity="info",
                    summary=(
                        f"Discovered new canonical project {pkey} ({curr_pid}) "
                        f"at {(curr_proj.get('preferred_location') or {}).get('location')!r}."
                    ),
                    before=None,
                    after={
                        "canonical_project_id": curr_pid,
                        "project_key": pkey,
                        "display_name": curr_proj.get("display_name"),
                        "gbrain_source_slug": curr_proj.get("gbrain_source_slug"),
                        "observation_ids": curr_proj.get("observation_ids"),
                        "preferred_location": curr_proj.get("preferred_location"),
                    },
                    provenance={
                        "observation_ids": curr_proj.get("observation_ids"),
                        "identity_anchor": curr_proj.get("identity_anchor"),
                    },
                )
            )
            continue

        if len(preds) >= 2:
            source_summaries = [
                {
                    "canonical_project_id": src_pid,
                    "project_key": prev_projects[src_pid].get("project_key"),
                    "observation_ids": prev_projects[src_pid].get("observation_ids"),
                }
                for src_pid in preds
            ]
            src_keys = ", ".join(str(s["project_key"]) for s in source_summaries)
            events.append(
                _make_change(
                    previous_run_id=previous_run_id,
                    run_id=run_id,
                    entity_kind="canonical_project",
                    entity_id=curr_pid,
                    canonical_project_id=curr_pid,
                    project_key=pkey,
                    change_type="PROJECT_MERGED",
                    materiality="high",
                    severity="info",
                    summary=(
                        f"Merged {len(preds)} canonical projects ({src_keys}) into "
                        f"{pkey} ({curr_pid})."
                    ),
                    before={
                        "source_canonical_project_ids": preds,
                        "sources": source_summaries,
                    },
                    after={
                        "canonical_project_id": curr_pid,
                        "project_key": pkey,
                        "observation_ids": curr_proj.get("observation_ids"),
                        "identity_anchor": curr_proj.get("identity_anchor"),
                        "preferred_location": curr_proj.get("preferred_location"),
                    },
                    provenance={
                        "merge_decision_ids": curr_proj.get("merge_decision_ids"),
                        "canonical_key_decision_id": curr_proj.get("canonical_key_decision_id"),
                        "identity_anchor": curr_proj.get("identity_anchor"),
                    },
                )
            )
            pred_obs_union = {
                oid
                for src_pid in preds
                for oid in (prev_projects[src_pid].get("observation_ids") or [])
            }
            curr_member_set = set(curr_proj.get("observation_ids") or [])
            if pred_obs_union != curr_member_set:
                added_oids = sorted(curr_member_set - pred_obs_union)
                lost_oids = sorted(pred_obs_union - curr_member_set)
                unavail_oids = [
                    oid
                    for oid in lost_oids
                    if _source_currently_unavailable(prev_obs.get(oid, {}).get("source_id"))
                ]
                removed_oids = [oid for oid in lost_oids if oid not in set(unavail_oids)]
                events.append(
                    _make_change(
                        previous_run_id=previous_run_id,
                        run_id=run_id,
                        entity_kind="canonical_project",
                        entity_id=curr_pid,
                        canonical_project_id=curr_pid,
                        project_key=pkey,
                        change_type="PROJECT_MEMBERSHIP_CHANGED",
                        materiality="material",
                        severity="warning" if unavail_oids else "info",
                        summary=(
                            f"Observation membership changed for merged project {pkey} ({curr_pid}): "
                            f"+{len(added_oids)} / -{len(removed_oids)} / unavailable={len(unavail_oids)}."
                        ),
                        before={"observation_ids": sorted(pred_obs_union)},
                        after={"observation_ids": sorted(curr_member_set)},
                        provenance={
                            "added_observation_ids": added_oids,
                            "removed_observation_ids": removed_oids,
                            "unavailable_observation_ids": unavail_oids,
                        },
                    )
                )
            if curr_pid in prev_projects:
                _emit_project_field_changes(
                    prev_projects[curr_pid],
                    curr_proj,
                    previous_run_id=previous_run_id,
                    run_id=run_id,
                    events=events,
                )
            continue

        # Single predecessor: len(preds) == 1
        prev_pid = preds[0]
        prev_proj = prev_projects[prev_pid]
        if len(succs_by_prev[prev_pid]) >= 2:
            # Split child of prev_pid; PROJECT_SPLIT already emitted. Check field deltas
            # only if this child retained the primary canonical_project_id.
            if curr_pid == prev_pid:
                _emit_project_field_changes(
                    prev_proj,
                    curr_proj,
                    previous_run_id=previous_run_id,
                    run_id=run_id,
                    events=events,
                )
            continue

        # Pure 1-to-1 continuing project lineage
        if prev_pid != curr_pid:
            events.append(
                _make_change(
                    previous_run_id=previous_run_id,
                    run_id=run_id,
                    entity_kind="canonical_project",
                    entity_id=curr_pid,
                    canonical_project_id=curr_pid,
                    project_key=pkey,
                    change_type="PROJECT_REANCHORED",
                    materiality="high",
                    severity="info",
                    summary=(
                        f"Project {pkey} re-anchored canonical ID from {prev_pid} to {curr_pid} "
                        f"({(curr_proj.get('identity_anchor') or {}).get('kind')})."
                    ),
                    before={
                        "canonical_project_id": prev_pid,
                        "identity_anchor": prev_proj.get("identity_anchor"),
                    },
                    after={
                        "canonical_project_id": curr_pid,
                        "identity_anchor": curr_proj.get("identity_anchor"),
                    },
                    provenance={
                        "previous_canonical_project_id": prev_pid,
                        "observation_ids": curr_proj.get("observation_ids"),
                    },
                )
            )

        prev_member_set = set(prev_proj.get("observation_ids") or [])
        curr_member_set = set(curr_proj.get("observation_ids") or [])
        if prev_member_set != curr_member_set:
            added_oids = sorted(curr_member_set - prev_member_set)
            lost_oids = sorted(prev_member_set - curr_member_set)
            unavail_oids = [
                oid
                for oid in lost_oids
                if _source_currently_unavailable(prev_obs.get(oid, {}).get("source_id"))
            ]
            removed_oids = [oid for oid in lost_oids if oid not in set(unavail_oids)]
            events.append(
                _make_change(
                    previous_run_id=previous_run_id,
                    run_id=run_id,
                    entity_kind="canonical_project",
                    entity_id=curr_pid,
                    canonical_project_id=curr_pid,
                    project_key=pkey,
                    change_type="PROJECT_MEMBERSHIP_CHANGED",
                    materiality="material",
                    severity="warning" if unavail_oids else "info",
                    summary=(
                        f"Observation membership changed for {pkey} ({curr_pid}): "
                        f"+{len(added_oids)} / -{len(removed_oids)} / unavailable={len(unavail_oids)}."
                    ),
                    before={"observation_ids": sorted(prev_member_set)},
                    after={"observation_ids": sorted(curr_member_set)},
                    provenance={
                        "added_observation_ids": added_oids,
                        "removed_observation_ids": removed_oids,
                        "unavailable_observation_ids": unavail_oids,
                    },
                )
            )

        _emit_project_field_changes(
            prev_proj,
            curr_proj,
            previous_run_id=previous_run_id,
            run_id=run_id,
            events=events,
        )

    # 4. Review queue deltas
    for rid in sorted(set(curr_reviews) - set(prev_reviews)):
        rev = curr_reviews[rid]
        sev = "error" if rev.get("severity") == "error" else "warning"
        events.append(
            _make_change(
                previous_run_id=previous_run_id,
                run_id=run_id,
                entity_kind="review_item",
                entity_id=rid,
                change_type="REVIEW_OPENED",
                materiality="high",
                severity=sev,
                summary=f"Opened {rev.get('code')} review {rid}: {rev.get('detail')}",
                before=None,
                after=rev,
                provenance={
                    "review_id": rid,
                    "code": rev.get("code"),
                    "affected_observation_ids": rev.get("affected_observation_ids"),
                    "affected_decision_ids": rev.get("affected_decision_ids"),
                },
            )
        )
    for rid in sorted(set(prev_reviews) - set(curr_reviews)):
        rev = prev_reviews[rid]
        events.append(
            _make_change(
                previous_run_id=previous_run_id,
                run_id=run_id,
                entity_kind="review_item",
                entity_id=rid,
                change_type="REVIEW_RESOLVED",
                materiality="high",
                severity="info",
                summary=f"Cleared {rev.get('code')} review {rid}.",
                before=rev,
                after=None,
                provenance={
                    "review_id": rid,
                    "code": rev.get("code"),
                    "affected_observation_ids": rev.get("affected_observation_ids"),
                    "affected_decision_ids": rev.get("affected_decision_ids"),
                },
            )
        )

    # 5. Negative identity decision deltas (reject_match / split pairs)
    for did in sorted(set(curr_neg) - set(prev_neg)):
        neg = curr_neg[did]
        events.append(
            _make_change(
                previous_run_id=previous_run_id,
                run_id=run_id,
                entity_kind="identity_decision",
                entity_id=did,
                change_type="NEGATIVE_DECISION_APPLIED",
                materiality="material",
                severity="info",
                summary=f"Applied negative identity decision {did} across {len(neg.get('pairs') or [])} observation pair(s).",
                before=None,
                after=neg,
                provenance={"decision_id": did, "pairs": neg.get("pairs")},
            )
        )
    for did in sorted(set(prev_neg) - set(curr_neg)):
        neg = prev_neg[did]
        pair_oids = {oid for pair in (neg.get("pairs") or []) for oid in pair}
        any_obs_unavail = any(
            _source_currently_unavailable(prev_obs.get(oid, {}).get("source_id"))
            for oid in pair_oids
        )
        if not any_obs_unavail:
            events.append(
                _make_change(
                    previous_run_id=previous_run_id,
                    run_id=run_id,
                    entity_kind="identity_decision",
                    entity_id=did,
                    change_type="NEGATIVE_DECISION_REMOVED",
                    materiality="material",
                    severity="info",
                    summary=f"Negative identity decision {did} is no longer active.",
                    before=neg,
                    after=None,
                    provenance={"decision_id": did, "pairs": neg.get("pairs")},
                )
            )

    events.sort(
        key=lambda item: (
            _ENTITY_KIND_ORDER.get(str(item["entity_kind"]), 99),
            str(item["entity_id"]),
            str(item["change_type"]),
            str(item["change_id"]),
        )
    )

    by_entity_kind: dict[str, int] = {}
    by_change_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    by_materiality: dict[str, int] = {}
    for ev in events:
        by_entity_kind[ev["entity_kind"]] = by_entity_kind.get(ev["entity_kind"], 0) + 1
        by_change_type[ev["change_type"]] = by_change_type.get(ev["change_type"], 0) + 1
        by_severity[ev["severity"]] = by_severity.get(ev["severity"], 0) + 1
        by_materiality[ev["materiality"]] = by_materiality.get(ev["materiality"], 0) + 1

    prev_counts = previous.get("counts") or {}
    curr_counts = current.get("counts") or {}
    all_count_keys = sorted(set(prev_counts) | set(curr_counts))
    count_deltas: dict[str, dict] = {}
    for key in all_count_keys:
        b_val = prev_counts.get(key)
        a_val = curr_counts.get(key)
        delta = (int(a_val) - int(b_val)) if (b_val is not None and a_val is not None) else None
        count_deltas[key] = {"before": b_val, "after": a_val, "delta": delta}

    return {
        "schema_version": CHANGE_FEED_SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "status": "ok",
        "baseline_state": "compared",
        "run_id": run_id,
        "compiled_at": compiled_at,
        "input_observed_at": input_observed_at,
        "previous_run_id": previous_run_id,
        "previous_compiled_at": previous.get("compiled_at"),
        "previous_input_observed_at": previous.get("input_observed_at"),
        "change_count": len(events),
        "review_count": int((current.get("counts") or {}).get("review_items", 0) or 0),
        "untriaged_weak_overlap_count": int(
            (current.get("counts") or {}).get("untriaged_weak_overlaps", 0) or 0
        ),
        "summary": {
            "by_entity_kind": dict(sorted(by_entity_kind.items())),
            "by_change_type": dict(sorted(by_change_type.items())),
            "by_severity": dict(sorted(by_severity.items())),
            "by_materiality": dict(sorted(by_materiality.items())),
            "count_deltas": count_deltas,
        },
        "available_run_ids": list(available_run_ids or [previous_run_id, run_id]),
        "pointers": pointers,
        "changes": events,
    }


def materialize_changes(
    *,
    previous_snapshot: dict | None,
    current_snapshot: dict,
    state_dir: Path,
    max_runs: int = RUN_HISTORY_MAX_RUNS,
) -> dict:
    """Persist ``current_snapshot`` to ``state/runs/`` and materialize ``state/changes.json``."""
    state_dir = state_dir.resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    available_run_ids = _save_run_snapshot(state_dir, current_snapshot, max_runs=max_runs)
    payload = diff_run_snapshots(
        previous_snapshot,
        current_snapshot,
        state_dir=state_dir,
        available_run_ids=available_run_ids,
    )
    changes_path = state_dir / "changes.json"
    changes_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def load_changes(state_dir: Path) -> dict:
    path = state_dir.resolve() / "changes.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(
            f"Change feed does not exist: {path}; run `python -m ledger compile` first."
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Change feed is invalid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("changes"), list):
        raise ValueError(f"Change feed is malformed: {path}")
    return payload


def _event_touches_project(event: dict, target_pid: str, target_key: str | None = None) -> bool:
    if event.get("canonical_project_id") == target_pid or event.get("entity_id") == target_pid:
        return True
    if target_key and str(event.get("project_key") or "").casefold() == target_key.casefold():
        return True
    before = event.get("before") if isinstance(event.get("before"), dict) else {}
    after = event.get("after") if isinstance(event.get("after"), dict) else {}
    prov = event.get("provenance") if isinstance(event.get("provenance"), dict) else {}
    if target_pid in (before.get("source_canonical_project_ids") or []):
        return True
    if target_pid in (after.get("target_canonical_project_ids") or []):
        return True
    if prov.get("previous_canonical_project_id") == target_pid:
        return True
    if target_key:
        for src in before.get("sources") or []:
            if isinstance(src, dict) and str(src.get("project_key") or "").casefold() == target_key.casefold():
                return True
        for tgt in after.get("targets") or []:
            if isinstance(tgt, dict) and str(tgt.get("project_key") or "").casefold() == target_key.casefold():
                return True
    return False


def changes_payload(
    state_dir: Path,
    *,
    since_run_id: str | None = None,
    project_referent: str | None = None,
    entity_kind: str | None = None,
    change_type: str | None = None,
) -> dict:
    """Query the semantic change feed (optionally diffing ``--since <run_id>`` or filtering)."""
    state_dir = state_dir.resolve()
    latest = load_changes(state_dir)
    current_run_id = str(latest.get("run_id") or "")

    retained = _load_retained_snapshots(state_dir)
    retained_by_id = {str(item["run_id"]): item for item in retained}
    available_ids = [str(item["run_id"]) for item in retained] or list(
        latest.get("available_run_ids") or ([current_run_id] if current_run_id else [])
    )

    if entity_kind is not None and entity_kind not in SUPPORTED_CHANGE_ENTITY_KINDS:
        raise ValueError(
            f"Unsupported change entity_kind {entity_kind!r}; "
            f"expected one of {sorted(SUPPORTED_CHANGE_ENTITY_KINDS)}."
        )

    if since_run_id is not None:
        requested = str(since_run_id).strip()
        if not requested:
            raise ValueError("--since run_id must be non-empty.")
        current_snap = retained_by_id.get(current_run_id) or snapshot_from_state_dir(state_dir)
        if current_snap is None:
            raise ValueError(f"Current compiled state is unavailable in {state_dir}")
        if requested == current_run_id:
            base_payload = diff_run_snapshots(
                current_snap,
                current_snap,
                state_dir=state_dir,
                available_run_ids=available_ids,
            )
        elif requested in retained_by_id:
            base_payload = diff_run_snapshots(
                retained_by_id[requested],
                current_snap,
                state_dir=state_dir,
                available_run_ids=available_ids,
            )
        else:
            return {
                "schema_version": CHANGE_FEED_SCHEMA_VERSION,
                "compiler_version": COMPILER_VERSION,
                "status": "unresolved_baseline",
                "reason_code": "SINCE_RUN_NOT_FOUND",
                "requested_since_run_id": requested,
                "run_id": current_run_id,
                "available_run_ids": available_ids,
                "detail": (
                    f"Run {requested!r} is not present in retained run history "
                    f"({len(available_ids)} run(s) available)."
                ),
            }
    else:
        base_payload = dict(latest)
        base_payload["available_run_ids"] = available_ids

    filtered = list(base_payload.get("changes") or [])
    filters_applied: dict[str, str] = {}

    if project_referent is not None:
        query = str(project_referent).strip()
        if not query:
            raise ValueError("--project referent must be non-empty.")
        resolution = resolve_payload(state_dir, query)
        if resolution["status"] == "ambiguous":
            return resolution
        if resolution["status"] == "resolved":
            matched_by = resolution.get("matched_by") or []
            matched_only_historical_id = bool(matched_by) and all(
                isinstance(ref, dict) and ref.get("kind") == "historical_canonical_id"
                for ref in matched_by
            )
            historical_matches = (
                [ev for ev in filtered if _event_touches_project(ev, query, None)]
                if matched_only_historical_id
                else []
            )
            if historical_matches:
                filtered = historical_matches
                filters_applied["historical_project_referent"] = query
            else:
                target_pid = resolution["canonical_project_id"]
                target_key = resolution.get("project_key")
                filtered = [
                    ev for ev in filtered if _event_touches_project(ev, target_pid, target_key)
                ]
                filters_applied["canonical_project_id"] = target_pid
        else:
            # Allow querying a historical project ID or key that was just removed/merged/split.
            historical_matches = [
                ev
                for ev in filtered
                if _event_touches_project(ev, query, query)
            ]
            if not historical_matches:
                return resolution
            filtered = historical_matches
            filters_applied["historical_project_referent"] = query

    if entity_kind is not None:
        filtered = [ev for ev in filtered if ev.get("entity_kind") == entity_kind]
        filters_applied["entity_kind"] = entity_kind

    if change_type is not None:
        code = str(change_type).strip()
        filtered = [ev for ev in filtered if ev.get("change_type") == code]
        filters_applied["change_type"] = code

    if filters_applied:
        result = dict(base_payload)
        result["unfiltered_change_count"] = base_payload["change_count"]
        result["change_count"] = len(filtered)
        result["filters"] = filters_applied
        result["changes"] = filtered
        return result

    return base_payload
