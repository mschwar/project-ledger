"""Deterministic explanation surface (`ledger explain <referent>`, P3.6).

Explains from compiled artifacts and durable decisions — without fresh model
improvisation:

- why observations merged or stayed separate;
- why canonical fields (`project_key`, `display_name`, `gbrain_source_slug`) were chosen;
- why a preferred working location won (and any git upstream drift warnings);
- why an identity review was opened or how a durable decision resolved it.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import COMPILER_VERSION
from .capsules import load_project_capsule
from .identity import (
    _decision_observation_ids,
    load_canonical_projects,
    load_identity_decisions,
    resolve_payload,
)


def _load_json_if_exists(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _active_and_superseded_decisions(decisions_payload: dict) -> tuple[list[dict], set[str], dict[str, str]]:
    decisions = list(decisions_payload.get("decisions", []))
    superseded_by: dict[str, str] = {}
    for decision in decisions:
        if str(decision.get("type", "")).strip() == "supersede":
            target = str(decision.get("supersedes_decision_id", "")).strip()
            if target:
                superseded_by[target] = str(decision.get("decision_id", "")).strip()
    superseded = set(superseded_by)
    active = [d for d in decisions if str(d.get("decision_id", "")).strip() not in superseded]
    return active, superseded, superseded_by


def explain_payload(
    state_dir: Path,
    referent: str,
    *,
    decisions_path: Path | None = None,
) -> dict:
    """Return a deterministic explanation payload for a project, observation, review, or decision."""
    query = str(referent or "").strip()
    if not query:
        raise ValueError("Explain referent must be non-empty.")

    state_dir = state_dir.resolve()
    canonical_payload = load_canonical_projects(state_dir)
    reviews_payload = _load_json_if_exists(state_dir / "review-queue.json") or {"items": []}
    evidence_payload = _load_json_if_exists(state_dir / "identity-evidence.json") or {
        "by_project": {},
        "cross_project_weak_overlaps": [],
    }
    observations_payload = _load_json_if_exists(state_dir / "observations.json") or {
        "observations": []
    }
    changes_payload = _load_json_if_exists(state_dir / "changes.json") or {"changes": []}
    if decisions_path is not None and decisions_path.is_file():
        decisions_payload = load_identity_decisions(decisions_path)
    else:
        default_dec = Path("registry/identity-decisions.json")
        decisions_payload = (
            load_identity_decisions(default_dec)
            if default_dec.is_file()
            else {"schema_version": "1.0.0", "decisions": []}
        )

    _, superseded_ids, superseded_by = _active_and_superseded_decisions(decisions_payload)
    projects_by_id = {p["canonical_project_id"]: p for p in canonical_payload.get("projects", [])}
    obs_to_project: dict[str, dict] = {}
    for project in canonical_payload.get("projects", []):
        for obs_id in project.get("observation_ids", []):
            obs_to_project[obs_id] = project

    # 1. Exact change_id match (`chg_...`)
    for change in changes_payload.get("changes", []):
        if str(change.get("change_id", "")).casefold() == query.casefold():
            return {
                "status": "resolved",
                "subject_kind": "change_event",
                "referent": query,
                "compiler_version": COMPILER_VERSION,
                "change": change,
                "explanation": (
                    f"Change {change['change_id']} ({change['change_type']}, "
                    f"entity={change['entity_kind']}:{change['entity_id']}, "
                    f"severity={change.get('severity')}, materiality={change.get('materiality')}) "
                    f"between {change.get('previous_run_id')} and {change.get('run_id')}: "
                    f"{change.get('summary')}"
                ),
            }

    # 2. Exact review_id match (`rev_...`)
    for review in reviews_payload.get("items", []):
        if str(review.get("review_id", "")).casefold() == query.casefold():
            affected_projects = sorted(
                {
                    obs_to_project[oid]["canonical_project_id"]
                    for oid in review.get("affected_observation_ids", [])
                    if oid in obs_to_project
                }
            )
            return {
                "status": "resolved",
                "subject_kind": "review_item",
                "referent": query,
                "compiler_version": COMPILER_VERSION,
                "review": review,
                "affected_canonical_project_ids": affected_projects,
                "explanation": (
                    f"Review {review['review_id']} ({review['code']}, severity={review.get('severity')}) "
                    f"is {review.get('state', 'open')}: {review.get('detail')} "
                    f"Reason automation stopped: {review.get('reason_automation_stopped')}"
                ),
            }

    # 2. Exact decision_id match (`dec_...`)
    for decision in decisions_payload.get("decisions", []):
        did = str(decision.get("decision_id", ""))
        if did.casefold() == query.casefold():
            obs_refs = _decision_observation_ids(decision) if decision.get("type") != "supersede" else []
            affected_projects = sorted(
                {
                    obs_to_project[oid]["canonical_project_id"]
                    for oid in obs_refs
                    if oid in obs_to_project
                }
            )
            is_superseded = did in superseded_ids
            return {
                "status": "resolved",
                "subject_kind": "identity_decision",
                "referent": query,
                "compiler_version": COMPILER_VERSION,
                "decision": decision,
                "active": not is_superseded,
                "superseded_by": superseded_by.get(did),
                "affected_observation_ids": obs_refs,
                "affected_canonical_project_ids": affected_projects,
                "explanation": (
                    f"Decision {did} (type={decision.get('type')}, "
                    f"state={'superseded' if is_superseded else 'active'}) "
                    f"affects observations {obs_refs} -> projects {affected_projects}."
                ),
            }

    # 3. Exact observation_id match (`obs_...`)
    observations_by_id = {
        str(o.get("observation_id", "")): o for o in observations_payload.get("observations", [])
    }
    if query in observations_by_id and query in obs_to_project:
        project = obs_to_project[query]
        pid = project["canonical_project_id"]
        capsule = load_project_capsule(state_dir, pid)
        ranked_entry = next(
            (item for item in capsule.get("observations", []) if item["observation_id"] == query),
            None,
        )
        return {
            "status": "resolved",
            "subject_kind": "observation",
            "referent": query,
            "compiler_version": COMPILER_VERSION,
            "observation_id": query,
            "canonical_project_id": pid,
            "project_key": project["project_key"],
            "display_name": project["display_name"],
            "gbrain_source_slug": project.get("gbrain_source_slug"),
            "location_ranking": ranked_entry,
            "preferred_location": capsule.get("preferred_location"),
            "identity_anchor": project.get("identity_anchor"),
            "identity_evidence_summary": project.get("identity_evidence_summary"),
            "explanation": (
                f"Observation {query} belongs to canonical project {pid} ({project['project_key']}) "
                f"anchored by {project['identity_anchor']['kind']}={project['identity_anchor']['value']}."
            ),
        }

    # 4. Canonical project referent via resolve_payload
    resolution = resolve_payload(state_dir, query)
    if resolution["status"] != "resolved":
        return resolution

    pid = resolution["canonical_project_id"]
    project = projects_by_id[pid]
    capsule = load_project_capsule(state_dir, pid)
    member_set = set(project.get("observation_ids", []))

    applied_decisions: list[dict] = []
    for decision in decisions_payload.get("decisions", []):
        did = str(decision.get("decision_id", ""))
        dtype = str(decision.get("type", ""))
        if dtype == "supersede":
            target = str(decision.get("supersedes_decision_id", ""))
            target_dec = next(
                (d for d in decisions_payload.get("decisions", []) if d.get("decision_id") == target),
                None,
            )
            if target_dec is not None:
                t_refs = set(_decision_observation_ids(target_dec))
                if t_refs & member_set:
                    applied_decisions.append(
                        {**decision, "active": did not in superseded_ids, "superseded_by": superseded_by.get(did)}
                    )
            continue
        refs = set(_decision_observation_ids(decision))
        if refs & member_set:
            applied_decisions.append(
                {**decision, "active": did not in superseded_ids, "superseded_by": superseded_by.get(did)}
            )

    weak_overlaps = [
        item
        for item in evidence_payload.get("cross_project_weak_overlaps", [])
        if item.get("left_observation_id") in member_set
        or item.get("right_observation_id") in member_set
    ]

    summary = project.get("identity_evidence_summary", {})
    pref = capsule["preferred_location"]
    current_state = capsule.get("current_state") if isinstance(capsule.get("current_state"), dict) else {}
    machina_cont = (
        current_state.get("machina_continuity")
        if isinstance(current_state.get("machina_continuity"), dict)
        else {}
    )
    historical_ids = [
        str(ref.get("value"))
        for ref in project.get("referents", [])
        if isinstance(ref, dict) and ref.get("kind") == "historical_canonical_id" and ref.get("value")
    ]
    continuity_note = ""
    if machina_cont.get("state") == "available":
        if machina_cont.get("active_unit_id"):
            continuity_note = f" Machina continuity active unit={machina_cont['active_unit_id']}."
        elif machina_cont.get("last_completed_unit_id"):
            continuity_note = f" Machina continuity last completed unit={machina_cont['last_completed_unit_id']}."
        else:
            continuity_note = " Machina continuity initialized."

    return {
        "status": "resolved",
        "subject_kind": "canonical_project",
        "referent": query,
        "matched_by": resolution["matched_by"],
        "compiler_version": COMPILER_VERSION,
        "run_id": capsule["run_id"],
        "compiled_at": capsule["compiled_at"],
        "as_of": capsule["as_of"],
        "canonical_project_id": pid,
        "project_key": project["project_key"],
        "display_name": project["display_name"],
        "gbrain_source_slug": project.get("gbrain_source_slug"),
        "gbrain": project.get("gbrain"),
        "identity": {
            "identity_anchor": project.get("identity_anchor"),
            "observation_ids": project.get("observation_ids"),
            "historical_canonical_ids": historical_ids,
            "unifying_authority": summary.get("unifying_authority"),
            "strong_reason_code": summary.get("strong_reason_code"),
            "automatic": summary.get("automatic"),
            "merge_decision_ids": project.get("merge_decision_ids", []),
            "canonical_key_decision_id": project.get("canonical_key_decision_id"),
            "evidence_summary": summary,
            "evidence": project.get("identity_evidence", []),
            "cross_project_weak_overlaps": weak_overlaps,
        },
        "resolved_fields": project.get("resolved_fields", {}),
        "preferred_location": pref,
        "git_facts": capsule.get("git_facts"),
        "current_state": current_state,
        "warnings": list(capsule.get("warnings") or []),
        "locations": capsule.get("observations", []),
        "decisions": applied_decisions,
        "review": capsule.get("review", {"open_review_count": 0, "items": []}),
        "explanation": (
            f"Project {pid} ({project['project_key']}) has {len(project['observation_ids'])} "
            f"observation(s) unified by {summary.get('unifying_authority')} "
            f"({summary.get('strong_reason_code')}); preferred location is "
            f"{pref.get('location')} [{pref.get('state')}/{pref.get('confidence')}] "
            f"({', '.join(pref.get('reason_codes', []))}); "
            f"gbrain source is {project.get('gbrain_source_slug')} ({project.get('gbrain', {}).get('page_key')})."
            f"{continuity_note}"
        ),
    }
