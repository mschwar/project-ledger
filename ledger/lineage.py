"""Durable canonical_project_id lineage registry (`registry/project-lineage.json`, U3).

Provides true immutable ``canonical_project_id`` continuity across lifecycle events
that change a project's underlying identity anchor:

- a local-only project (``observation`` anchor) gaining its first git remote;
- a GitHub/GitLab repository renaming its remote URL while keeping local observations
  or decision history;
- a decision-merged project adding or superseding a secondary merge decision;
- full ``state/`` directory wipes where ephemeral ``state/runs/`` snapshots are absent.

When two previously separate pinned projects later merge into one connected component,
deterministic winner selection preserves the primary record's ``canonical_project_id``
and folds the secondary record's ``canonical_project_id`` (and its prior history) into
``historical_canonical_id`` referents so external foreign keys never break. When a
pinned project splits symmetrically into multiple components, lineage abstains cleanly
rather than assigning one ID to two distinct split projects.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import PROJECT_LINEAGE_SCHEMA_VERSION
from .ids import stable_id

DEFAULT_LINEAGE_FILENAME = "project-lineage.json"
_PRJ_ID_RE = re.compile(r"^prj_[0-9a-f]{24}$")
_OBS_ID_RE = re.compile(r"^obs_[0-9a-f]{24}$")


def empty_project_lineage() -> dict[str, Any]:
    return {
        "schema_version": PROJECT_LINEAGE_SCHEMA_VERSION,
        "projects": [],
    }


def resolve_lineage_path(
    *,
    decisions_path: Path | None = None,
    lineage_path: Path | None = None,
) -> tuple[Path | None, bool]:
    """Resolve the active lineage registry path and whether it was explicitly requested.

    When ``lineage_path`` is omitted and ``decisions_path`` is provided, looks for a
    sibling ``project-lineage.json`` in the same directory as ``decisions_path`` so
    production compiles using ``registry/identity-decisions.json`` automatically pick up
    ``registry/project-lineage.json`` while hermetic unit tests using temporary decision
    files remain isolated unless they create their own sibling ``project-lineage.json``.
    """
    if lineage_path is not None:
        return lineage_path.resolve(), True
    if decisions_path is not None:
        candidate = decisions_path.resolve().parent / DEFAULT_LINEAGE_FILENAME
        if candidate.is_file():
            return candidate, False
    return None, False


def _clean_sorted_unique(values: Any, *, field_name: str, pattern: re.Pattern[str] | None = None) -> list[str]:
    if not isinstance(values, list):
        raise ValueError(f"{field_name} must be an array of strings.")
    cleaned: set[str] = set()
    for idx, raw in enumerate(values):
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"{field_name}[{idx}] must be a non-empty string.")
        val = raw.strip()
        if pattern is not None and not pattern.match(val):
            raise ValueError(f"{field_name}[{idx}]={val!r} does not match {pattern.pattern!r}.")
        cleaned.add(val)
    return sorted(cleaned)


def load_project_lineage(path: Path | None, *, required: bool = False) -> dict[str, Any]:
    """Load and validate ``registry/project-lineage.json``."""
    if path is None:
        return empty_project_lineage()
    resolved = path.resolve()
    if not resolved.is_file():
        if required:
            raise ValueError(f"Project lineage registry does not exist: {resolved}")
        return empty_project_lineage()
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Project lineage registry is invalid JSON: {resolved}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("Project lineage registry must be a JSON object.")
    if payload.get("schema_version") != PROJECT_LINEAGE_SCHEMA_VERSION:
        raise ValueError(
            "Unsupported project lineage registry schema_version "
            f"{payload.get('schema_version')!r}; expected {PROJECT_LINEAGE_SCHEMA_VERSION!r}."
        )
    raw_projects = payload.get("projects")
    if not isinstance(raw_projects, list):
        raise ValueError("Project lineage registry must contain a projects array.")

    seen_ids: set[str] = set()
    normalized_records: list[dict[str, Any]] = []
    for index, rec in enumerate(raw_projects):
        where = f"projects[{index}]"
        if not isinstance(rec, dict):
            raise ValueError(f"{where} must be an object.")
        cid = str(rec.get("canonical_project_id", "")).strip()
        if not _PRJ_ID_RE.match(cid):
            raise ValueError(f"{where}.canonical_project_id={cid!r} must match ^prj_[0-9a-f]{{24}}$.")
        if cid in seen_ids:
            raise ValueError(f"Duplicate canonical_project_id in project lineage registry: {cid}")
        seen_ids.add(cid)

        raw_hint = rec.get("project_key_hint")
        if raw_hint is not None and not isinstance(raw_hint, str):
            raise ValueError(f"{where}.project_key_hint must be a string or null.")
        hint = raw_hint.strip() if isinstance(raw_hint, str) and raw_hint.strip() else None

        remotes = sorted(
            {
                r.casefold()
                for r in _clean_sorted_unique(
                    rec.get("normalized_remotes", []),
                    field_name=f"{where}.normalized_remotes",
                )
            }
        )
        obs_ids = _clean_sorted_unique(
            rec.get("observation_ids", []),
            field_name=f"{where}.observation_ids",
            pattern=_OBS_ID_RE,
        )
        merge_ids = _clean_sorted_unique(
            rec.get("merge_decision_ids", []),
            field_name=f"{where}.merge_decision_ids",
        )
        hist_ids = [
            hid
            for hid in _clean_sorted_unique(
                rec.get("historical_canonical_ids", []),
                field_name=f"{where}.historical_canonical_ids",
                pattern=_PRJ_ID_RE,
            )
            if hid != cid
        ]

        normalized_records.append(
            {
                "canonical_project_id": cid,
                "project_key_hint": hint,
                "normalized_remotes": remotes,
                "observation_ids": obs_ids,
                "merge_decision_ids": merge_ids,
                "historical_canonical_ids": hist_ids,
            }
        )

    for rec in normalized_records:
        for hid in rec["historical_canonical_ids"]:
            if hid in seen_ids:
                raise ValueError(
                    f"Project lineage historical_canonical_id {hid!r} on "
                    f"{rec['canonical_project_id']!r} collides with an active canonical_project_id."
                )

    normalized_records.sort(key=lambda item: item["canonical_project_id"])
    return {
        "schema_version": PROJECT_LINEAGE_SCHEMA_VERSION,
        "projects": normalized_records,
    }


def resolve_lineage_for_components(
    components: list[dict[str, Any]],
    lineage_payload: dict[str, Any] | None,
) -> list[tuple[str, list[dict[str, str]]]]:
    """Resolve ``(canonical_project_id, historical_referents)`` for each compiled component.

    Each element of ``components`` must carry:
    - ``computed_anchor_id``: ``str``
    - ``unique_remotes``: ``list[str]``
    - ``member_ids``: ``list[str]``
    - ``merge_decision_ids``: ``list[str]``
    """
    records = (
        list(lineage_payload.get("projects") or [])
        if isinstance(lineage_payload, dict)
        else []
    )
    if not records or not components:
        return [(str(comp["computed_anchor_id"]), []) for comp in components]

    comp_sets = [
        {
            "computed_anchor_id": str(comp["computed_anchor_id"]),
            "remotes": {str(r).casefold() for r in comp.get("unique_remotes", []) if str(r).strip()},
            "obs": set(comp.get("member_ids", [])),
            "merges": set(comp.get("merge_decision_ids", [])),
        }
        for comp in components
    ]

    # Step 1: Map each lineage record to at most one compiled component.
    matched_records_by_comp: dict[int, list[dict[str, Any]]] = {}
    for rec in records:
        rec_id = str(rec["canonical_project_id"])
        rec_hist = set(rec.get("historical_canonical_ids", []))
        rec_remotes = {str(r).casefold() for r in rec.get("normalized_remotes", [])}
        rec_obs = set(rec.get("observation_ids", []))
        rec_merges = set(rec.get("merge_decision_ids", []))

        overlapping: list[tuple[int, bool, int, int, int]] = []
        for idx, cset in enumerate(comp_sets):
            exact_id = cset["computed_anchor_id"] == rec_id or cset["computed_anchor_id"] in rec_hist
            remote_hits = len(cset["remotes"] & rec_remotes)
            merge_hits = len(cset["merges"] & rec_merges)
            obs_hits = len(cset["obs"] & rec_obs)
            if exact_id or remote_hits > 0 or merge_hits > 0 or obs_hits > 0:
                overlapping.append((idx, exact_id, remote_hits, merge_hits, obs_hits))

        if not overlapping:
            continue
        if len(overlapping) == 1:
            target_idx = overlapping[0][0]
            matched_records_by_comp.setdefault(target_idx, []).append(rec)
            continue

        # Split case: multiple compiled components overlap this lineage record.
        # Only assign if exactly one split component uniquely owns the primary ID or remote.
        primary_owners = [
            item[0] for item in overlapping if item[1] or item[2] > 0
        ]
        if len(primary_owners) == 1:
            matched_records_by_comp.setdefault(primary_owners[0], []).append(rec)

    # Step 2: For each component, pick the winning lineage record (if any) and collect
    # superseded historical_canonical_id referents.
    used_ids: set[str] = set()
    resolved: list[tuple[str, list[dict[str, str]]]] = []

    for idx, cset in enumerate(comp_sets):
        computed_id = cset["computed_anchor_id"]
        candidates = matched_records_by_comp.get(idx, [])
        if not candidates:
            resolved.append((computed_id, []))
            used_ids.add(computed_id)
            continue

        def _rank_record(rec: dict[str, Any]) -> tuple[int, int, int, int]:
            rec_id = str(rec["canonical_project_id"])
            rec_remotes = {str(r).casefold() for r in rec.get("normalized_remotes", [])}
            rec_obs = set(rec.get("observation_ids", []))
            rec_merges = set(rec.get("merge_decision_ids", []))
            return (
                1 if rec_id == computed_id else 0,
                len(cset["remotes"] & rec_remotes),
                len(cset["obs"] & rec_obs),
                len(cset["merges"] & rec_merges),
            )

        # Highest tuple score wins; ties broken by lexicographically smallest canonical_project_id.
        ordered = sorted(
            candidates,
            key=lambda rec: (
                -_rank_record(rec)[0],
                -_rank_record(rec)[1],
                -_rank_record(rec)[2],
                -_rank_record(rec)[3],
                str(rec["canonical_project_id"]),
            ),
        )
        winner = ordered[0]
        chosen_id = str(winner["canonical_project_id"])
        if chosen_id in used_ids:
            chosen_id = computed_id
        used_ids.add(chosen_id)

        hist_refs: list[dict[str, str]] = []
        if computed_id != chosen_id:
            hist_refs.append(
                {
                    "kind": "historical_canonical_id",
                    "value": computed_id,
                    "basis": "lineage_pinned_reanchor",
                }
            )

        for hid in winner.get("historical_canonical_ids", []):
            if hid != chosen_id:
                hist_refs.append(
                    {
                        "kind": "historical_canonical_id",
                        "value": str(hid),
                        "basis": "project_lineage_registry",
                    }
                )
        for old_remote in winner.get("normalized_remotes", []):
            remote_anchor_id = stable_id("prj", "normalized_remote", str(old_remote).casefold())
            if remote_anchor_id != chosen_id:
                hist_refs.append(
                    {
                        "kind": "historical_canonical_id",
                        "value": remote_anchor_id,
                        "basis": "project_lineage_registry",
                    }
                )

        for loser in ordered[1:]:
            loser_id = str(loser["canonical_project_id"])
            if loser_id != chosen_id:
                hist_refs.append(
                    {
                        "kind": "historical_canonical_id",
                        "value": loser_id,
                        "basis": "project_lineage_merge",
                    }
                )
            for hid in loser.get("historical_canonical_ids", []):
                if hid != chosen_id:
                    hist_refs.append(
                        {
                            "kind": "historical_canonical_id",
                            "value": str(hid),
                            "basis": "project_lineage_merge",
                        }
                    )
            for old_remote in loser.get("normalized_remotes", []):
                remote_anchor_id = stable_id("prj", "normalized_remote", str(old_remote).casefold())
                if remote_anchor_id != chosen_id:
                    hist_refs.append(
                        {
                            "kind": "historical_canonical_id",
                            "value": remote_anchor_id,
                            "basis": "project_lineage_merge",
                        }
                    )

        resolved.append((chosen_id, hist_refs))

    return resolved


def build_synced_lineage(
    *,
    canonical_payload: dict[str, Any],
    existing_lineage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an updated, ratcheted lineage registry from compiled canonical projects."""
    existing_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(existing_lineage, dict):
        for rec in existing_lineage.get("projects", []):
            if isinstance(rec, dict) and str(rec.get("canonical_project_id") or "").strip():
                existing_by_id[str(rec["canonical_project_id"])] = rec

    compiled_projects = list(canonical_payload.get("projects") or [])
    active_ids = {str(p["canonical_project_id"]) for p in compiled_projects}

    # Track which existing records were absorbed as historical_canonical_id into a merged winner.
    absorbed_old_ids: set[str] = set()
    new_records_by_id: dict[str, dict[str, Any]] = {}

    for proj in compiled_projects:
        cid = str(proj["canonical_project_id"])
        prior = existing_by_id.get(cid, {})

        remotes = set(str(r).casefold() for r in prior.get("normalized_remotes", []) if str(r).strip())
        remotes.update(
            str(r).casefold() for r in proj.get("normalized_remotes", []) if str(r).strip()
        )

        # Keep current observation_ids plus prior observation_ids not claimed by another active project.
        obs_ids = set(str(o) for o in proj.get("observation_ids", []) if str(o).strip())

        merge_ids = set(str(m) for m in prior.get("merge_decision_ids", []) if str(m).strip())
        merge_ids.update(str(m) for m in proj.get("merge_decision_ids", []) if str(m).strip())

        hist_ids = set(
            str(h)
            for h in prior.get("historical_canonical_ids", [])
            if _PRJ_ID_RE.match(str(h)) and str(h) != cid and str(h) not in active_ids
        )
        for ref in proj.get("referents", []):
            if isinstance(ref, dict) and ref.get("kind") == "historical_canonical_id":
                val = str(ref.get("value") or "").strip()
                if _PRJ_ID_RE.match(val) and val != cid and val not in active_ids:
                    hist_ids.add(val)
                    if val in existing_by_id:
                        absorbed_old_ids.add(val)
                        absorbed_rec = existing_by_id[val]
                        remotes.update(
                            str(r).casefold()
                            for r in absorbed_rec.get("normalized_remotes", [])
                            if str(r).strip()
                        )
                        hist_ids.update(
                            str(h)
                            for h in absorbed_rec.get("historical_canonical_ids", [])
                            if _PRJ_ID_RE.match(str(h)) and str(h) != cid and str(h) not in active_ids
                        )

        pkey = str(proj.get("project_key") or "").strip() or prior.get("project_key_hint")
        new_records_by_id[cid] = {
            "canonical_project_id": cid,
            "project_key_hint": pkey if isinstance(pkey, str) and pkey else None,
            "normalized_remotes": sorted(remotes),
            "observation_ids": sorted(obs_ids),
            "merge_decision_ids": sorted(merge_ids),
            "historical_canonical_ids": sorted(hist_ids),
        }

    # Preserve existing lineage records for temporarily unavailable sources (neither active nor absorbed),
    # stripping any historical_canonical_ids that became active.
    claimed_obs = {
        oid
        for rec in new_records_by_id.values()
        for oid in rec["observation_ids"]
    }
    for old_id, old_rec in existing_by_id.items():
        if old_id in new_records_by_id or old_id in absorbed_old_ids:
            continue
        preserved_hist = sorted(
            h
            for h in old_rec.get("historical_canonical_ids", [])
            if _PRJ_ID_RE.match(str(h)) and str(h) != old_id and str(h) not in active_ids
        )
        preserved_obs = sorted(
            o for o in old_rec.get("observation_ids", []) if o not in claimed_obs
        )
        new_records_by_id[old_id] = {
            "canonical_project_id": old_id,
            "project_key_hint": old_rec.get("project_key_hint"),
            "normalized_remotes": sorted(
                str(r).casefold() for r in old_rec.get("normalized_remotes", []) if str(r).strip()
            ),
            "observation_ids": preserved_obs,
            "merge_decision_ids": sorted(old_rec.get("merge_decision_ids", [])),
            "historical_canonical_ids": preserved_hist,
        }

    # Ensure no historical_canonical_id is claimed by more than one record (drop ambiguous ones).
    hist_owner_counts: dict[str, int] = {}
    for rec in new_records_by_id.values():
        for hid in rec["historical_canonical_ids"]:
            hist_owner_counts[hid] = hist_owner_counts.get(hid, 0) + 1
    for rec in new_records_by_id.values():
        rec["historical_canonical_ids"] = [
            hid for hid in rec["historical_canonical_ids"] if hist_owner_counts.get(hid, 0) == 1
        ]

    ordered = [new_records_by_id[cid] for cid in sorted(new_records_by_id)]
    return {
        "schema_version": PROJECT_LINEAGE_SCHEMA_VERSION,
        "projects": ordered,
    }


def write_project_lineage(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = resolved.parent / f".{resolved.name}.tmp"
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(resolved)


def lineage_status_payload(
    *,
    state_dir: Path,
    lineage_path: Path,
    sync: bool = False,
) -> dict[str, Any]:
    """Inspect, check, or sync ``registry/project-lineage.json`` against compiled state."""
    from .identity import load_canonical_projects

    canonical_payload = load_canonical_projects(state_dir)
    lineage_path = lineage_path.resolve()
    existing = load_project_lineage(lineage_path, required=False)
    synced = build_synced_lineage(
        canonical_payload=canonical_payload,
        existing_lineage=existing,
    )

    existing_by_id = {r["canonical_project_id"]: r for r in existing["projects"]}
    synced_by_id = {r["canonical_project_id"]: r for r in synced["projects"]}
    added_ids = sorted(set(synced_by_id) - set(existing_by_id))
    absorbed_ids = sorted(set(existing_by_id) - set(synced_by_id))
    updated_ids = sorted(
        cid
        for cid in (set(synced_by_id) & set(existing_by_id))
        if synced_by_id[cid] != existing_by_id[cid]
    )
    in_sync = lineage_path.is_file() and existing == synced

    if sync and not in_sync:
        write_project_lineage(lineage_path, synced)
        in_sync = True

    return {
        "schema_version": PROJECT_LINEAGE_SCHEMA_VERSION,
        "lineage_path": str(lineage_path),
        "mode": "sync" if sync else "check",
        "in_sync": in_sync,
        "compiled_project_count": len(canonical_payload.get("projects", [])),
        "lineage_project_count": len(synced["projects"]),
        "added_count": len(added_ids),
        "updated_count": len(updated_ids),
        "absorbed_count": len(absorbed_ids),
        "added_canonical_project_ids": added_ids,
        "updated_canonical_project_ids": updated_ids,
        "absorbed_canonical_project_ids": absorbed_ids,
    }
