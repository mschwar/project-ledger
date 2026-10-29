"""Project capsules and preferred-location resolver (Wave 3 — P3.2, P3.4, P3.5).

Materializes one bounded semantic cache per canonical project at
``state/projects/<canonical_project_id>.json`` and exposes the agent read-plane
query helpers ``show_payload`` (``ledger show <project>``) and ``locate_payload``
(``ledger locate <project>``).

Design invariants:

- Every location ranking is deterministic and explainable with explicit reason
  codes and confidence (no hidden heuristics).
- Live accessible working trees outrank mirrors, which outrank backups, which
  outrank off-host inventory-only entries; unavailable sources never outrank
  accessible ones.
- Each capsule is bounded by an explicit byte/context budget
  (``CAPSULE_MAX_BYTES = 16384``) and links to deeper provenance artifacts via
  pointers rather than duplicating full observation or evidence payloads.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import COMPILER_VERSION, PROJECT_CAPSULE_SCHEMA_VERSION
from .gbrain import resolve_gbrain_link
from .human_view import write_canonical_markdown
from .identity import observation_facts, resolve_payload
from .sources.git import resolve_observation_git_drift

CAPSULE_MAX_BYTES = 16384
CAPSULE_MAX_OBSERVATION_SUMMARIES = 25
CAPSULE_MAX_PROVENANCE_REFS = 5
CAPSULE_PURPOSE_MAX_CHARS = 240

SOURCE_CLASS_TIER = {
    "live": 4,
    "mirror": 3,
    "backup": 2,
    "inventory": 1,
    "unknown": 0,
}

SOURCE_CLASS_REASON = {
    "live": "SOURCE_CLASS_LIVE",
    "mirror": "SOURCE_CLASS_MIRROR",
    "backup": "SOURCE_CLASS_BACKUP",
    "inventory": "SOURCE_CLASS_INVENTORY",
    "unknown": "SOURCE_CLASS_UNKNOWN",
}


def _parse_iso_instant(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _iso_epoch(value: str | None) -> float:
    parsed = _parse_iso_instant(value)
    return parsed.timestamp() if parsed is not None else -1.0


def _infer_source_class(observation: dict, compat: dict) -> str:
    label = str(compat.get("source_label") or observation.get("source_id") or "").lower()
    loc = str(observation.get("location") or compat.get("path") or "").lower()
    if "inventory" in label or (":" in loc and not loc.startswith("/")):
        return "inventory"
    if "backup" in label or "/backup" in loc or "/archive" in loc:
        return "backup"
    if "mirror" in label or "/mirrors/" in loc:
        return "mirror"
    if loc.startswith("/"):
        return "live"
    return "unknown"


def _observation_content_as_of(compat: dict) -> tuple[str | None, str | None, str | None, float]:
    """Return (content_as_of, basis, reason_code, epoch) for one observation."""
    for field, basis, reason in (
        ("last_remote_ref_at", "compat.entry.last_remote_ref_at", "FRESH_REMOTE_REF"),
        ("head_commit_at", "compat.entry.head_commit_at", "FRESH_GIT_COMMIT"),
        ("last_touch_at", "compat.entry.last_touch_at", "FRESH_FILESYSTEM_TOUCH"),
    ):
        raw = str(compat.get(field) or "").strip()
        epoch = _iso_epoch(raw)
        if epoch >= 0.0:
            return raw, basis, reason, epoch
    return None, None, None, -1.0


def rank_project_observations(
    project: dict,
    observations_by_id: dict[str, dict],
    sources_by_id: dict[str, dict] | None = None,
) -> tuple[list[dict], dict]:
    """Rank a canonical project's observations and resolve its preferred location.

    Returns ``(ranked_observations, preferred_location)``.
    """
    sources_map = sources_by_id or {}
    project_remotes = {str(r).casefold() for r in project.get("normalized_remotes", []) if str(r).strip()}
    member_ids = sorted(set(project.get("observation_ids", [])))

    scored_items: list[tuple[tuple, dict]] = []
    for obs_id in member_ids:
        obs = observations_by_id.get(obs_id)
        if obs is None:
            continue
        compat = obs.get("compat_entry") if isinstance(obs.get("compat_entry"), dict) else {}
        facts = observation_facts(obs)
        source_id = str(obs.get("source_id") or "").strip()
        source = sources_map.get(source_id) or {}

        source_class = str(source.get("source_class") or "").strip() or _infer_source_class(obs, compat)
        if source_class not in SOURCE_CLASS_TIER:
            source_class = "unknown"
        source_tier = SOURCE_CLASS_TIER[source_class]

        if "status" in source:
            source_status = str(source["status"])
        else:
            source_status = "available" if obs.get("source_resolution") == "resolved" else "unavailable"

        location = (
            str(obs.get("location") or "").strip()
            or str(compat.get("path") or "").strip()
            or str(compat.get("canonical_url") or "").strip()
            or None
        )
        accessible_worktree = bool(
            source_status == "available"
            and source_class in {"live", "mirror", "backup"}
            and location
        )

        obs_remotes = {str(r).casefold() for r in facts.get("remotes", set())}
        canonical_remote_match = bool(obs_remotes and (obs_remotes & project_remotes))

        git_enabled = bool(compat.get("git"))
        head_commit = str(compat.get("head_commit") or "").strip() or None
        head_branch = str(compat.get("head_branch") or "").strip() or None
        has_git_commit = bool(git_enabled and head_commit)
        primary_branch = bool(head_branch and head_branch.lower() in {"main", "master"})

        primary_active_root = bool(
            source_id == "central-active"
            or (location and str(location).startswith("/central/repos/active/"))
        )

        session_at = str(compat.get("last_session_at") or "").strip() or None
        session_summary = str(compat.get("last_session_summary") or "").strip() or None
        next_step = str(compat.get("next_step") or "").strip() or None
        has_session = bool(session_at or session_summary or next_step)
        session_epoch = _iso_epoch(session_at)

        content_as_of, content_basis, freshness_reason, content_epoch = _observation_content_as_of(compat)
        git_drift = resolve_observation_git_drift(obs, compat)

        reasons: list[str] = []
        if source_status != "available":
            reasons.append("SOURCE_UNAVAILABLE")
        elif accessible_worktree:
            reasons.append("ACCESSIBLE_SOURCE")
        elif source_class == "inventory":
            reasons.append("INVENTORY_ONLY_LOCATOR")

        reasons.append(SOURCE_CLASS_REASON[source_class])

        if canonical_remote_match:
            reasons.append("CANONICAL_REMOTE_MATCH")
        if primary_active_root:
            reasons.append("PRIMARY_ACTIVE_ROOT")
        if has_session:
            reasons.append("RECENT_SESSION_EVIDENCE")
        if has_git_commit:
            reasons.append("GIT_WORKTREE")
        if primary_branch:
            reasons.append("PRIMARY_BRANCH")
        if freshness_reason:
            reasons.append(freshness_reason)

        if not accessible_worktree:
            confidence = "low"
        elif source_class == "live" and (canonical_remote_match or len(member_ids) == 1):
            confidence = "high"
        else:
            confidence = "medium"

        sort_key = (
            -int(accessible_worktree),
            -int(source_status == "available"),
            -source_tier,
            -int(canonical_remote_match),
            -int(has_session),
            -session_epoch,
            -int(has_git_commit),
            -int(primary_branch),
            -int(primary_active_root),
            -content_epoch,
            obs_id,
        )

        summary = {
            "observation_id": obs_id,
            "source_id": source_id,
            "source_label": str(compat.get("source_label") or source.get("label") or "").strip() or None,
            "source_class": source_class,
            "source_status": source_status,
            "machine_name": str(compat.get("machine_name") or source.get("machine_name") or "").strip() or None,
            "location": location,
            "project_type": str(compat.get("project_type") or "").strip() or None,
            "git": git_enabled,
            "head_branch": head_branch,
            "head_commit": head_commit,
            "git_drift": git_drift,
            "warning_codes": list(git_drift.get("warning_codes", [])),
            "warnings": list(git_drift.get("warnings", [])),
            "content_as_of": content_as_of,
            "content_as_of_basis": content_basis,
            "canonical_remote_match": canonical_remote_match,
            "confidence": confidence,
            "reason_codes": reasons,
            "score_components": {
                "accessible_worktree": accessible_worktree,
                "source_available": source_status == "available",
                "source_class_tier": source_tier,
                "canonical_remote_match": canonical_remote_match,
                "has_session_evidence": has_session,
                "has_git_commit": has_git_commit,
                "primary_branch": primary_branch,
                "primary_active_root": primary_active_root,
                "content_as_of": content_as_of,
            },
        }
        scored_items.append((sort_key, summary))

    scored_items.sort(key=lambda pair: pair[0])
    ranked: list[dict] = []
    for idx, (_key, item) in enumerate(scored_items, start=1):
        entry = dict(item)
        entry["rank"] = idx
        ranked.append(entry)

    if not ranked:
        preferred = {
            "state": "unavailable",
            "observation_id": None,
            "source_id": None,
            "source_class": None,
            "machine_name": None,
            "location": None,
            "confidence": "none",
            "reason_codes": ["SOURCE_UNAVAILABLE"],
            "git_drift": {
                "state": "not_applicable",
                "head_branch": None,
                "head_commit": None,
                "upstream_ref": None,
                "ahead_count": None,
                "behind_count": None,
                "main_behind_count": None,
                "dirty": None,
                "warning_codes": [],
                "warnings": [],
            },
            "warning_codes": [],
            "warnings": [],
            "explanation": "No observations are available for this canonical project.",
        }
        return ranked, preferred

    top = ranked[0]
    if top["score_components"]["accessible_worktree"]:
        pref_state = "resolved"
        pref_confidence = top["confidence"]
        pref_explanation = (
            f"Selected {top['source_class']} observation {top['observation_id']} at "
            f"{top['location']} ({', '.join(top['reason_codes'])})."
        )
    elif top["source_status"] == "available" and top["source_class"] == "inventory":
        pref_state = "inventory_only"
        pref_confidence = "low"
        pref_explanation = (
            f"Only inventory-backed observations exist (top: {top['observation_id']} at "
            f"{top['location']}); no local working tree is mounted."
        )
    else:
        pref_state = "unavailable"
        pref_confidence = "none"
        pref_explanation = (
            f"All configured sources for this project are currently unavailable "
            f"(top candidate: {top['observation_id']})."
        )

    preferred = {
        "state": pref_state,
        "observation_id": top["observation_id"],
        "source_id": top["source_id"],
        "source_class": top["source_class"],
        "machine_name": top["machine_name"],
        "location": top["location"],
        "confidence": pref_confidence,
        "reason_codes": list(top["reason_codes"]),
        "git_drift": dict(top["git_drift"]),
        "warning_codes": list(top["warning_codes"]),
        "warnings": list(top["warnings"]),
        "explanation": pref_explanation,
    }
    return ranked, preferred


def _project_freshness(
    ranked_observations: list[dict],
    observations_by_id: dict[str, dict],
    input_observed_at: str,
) -> dict:
    available_count = sum(1 for item in ranked_observations if item["source_status"] == "available")
    unavailable_count = len(ranked_observations) - available_count

    best_as_of: str | None = None
    best_basis: str | None = None
    for field, basis in (
        ("last_remote_ref_at", "compat.entry.last_remote_ref_at"),
        ("head_commit_at", "compat.entry.head_commit_at"),
        ("last_touch_at", "compat.entry.last_touch_at"),
    ):
        best_epoch = -1.0
        candidate_val: str | None = None
        for item in ranked_observations:
            obs = observations_by_id.get(item["observation_id"]) or {}
            compat = obs.get("compat_entry") if isinstance(obs.get("compat_entry"), dict) else {}
            raw = str(compat.get(field) or "").strip()
            epoch = _iso_epoch(raw)
            if epoch > best_epoch and epoch >= 0.0:
                best_epoch = epoch
                candidate_val = raw
        if candidate_val is not None:
            best_as_of = candidate_val
            best_basis = basis
            break

    if available_count == 0 and ranked_observations:
        state = "unavailable"
    elif best_as_of is not None:
        state = "known"
    else:
        state = "unknown"

    return {
        "state": state,
        "input_observed_at": input_observed_at,
        "content_as_of": best_as_of,
        "content_as_of_basis": best_basis,
        "available_observation_count": available_count,
        "unavailable_observation_count": unavailable_count,
    }


def _project_git_facts(ranked_observations: list[dict], observations_by_id: dict[str, dict]) -> dict:
    for item in ranked_observations:
        if not item.get("git"):
            continue
        obs = observations_by_id.get(item["observation_id"]) or {}
        compat = obs.get("compat_entry") if isinstance(obs.get("compat_entry"), dict) else {}
        head_commit = str(compat.get("head_commit") or "").strip() or None
        head_branch = str(compat.get("head_branch") or "").strip() or None
        head_commit_at = str(compat.get("head_commit_at") or "").strip() or None
        last_remote_ref_at = str(compat.get("last_remote_ref_at") or "").strip() or None
        remote_url = str(compat.get("remote_url") or compat.get("canonical_url") or "").strip() or None
        git_drift = item.get("git_drift") or {}
        state = "known" if (head_commit or head_branch or remote_url) else "unknown"
        return {
            "state": state,
            "observation_id": item["observation_id"],
            "head_branch": head_branch,
            "head_commit": head_commit,
            "head_commit_at": head_commit_at,
            "last_remote_ref_at": last_remote_ref_at,
            "remote_url": remote_url,
            "upstream_ref": git_drift.get("upstream_ref"),
            "ahead_count": git_drift.get("ahead_count"),
            "behind_count": git_drift.get("behind_count"),
            "main_behind_count": git_drift.get("main_behind_count"),
            "dirty": git_drift.get("dirty"),
            "drift_state": git_drift.get("state", "unknown"),
            "warning_codes": list(git_drift.get("warning_codes", [])),
            "warnings": list(git_drift.get("warnings", [])),
        }
    return {
        "state": "not_applicable",
        "observation_id": None,
        "head_branch": None,
        "head_commit": None,
        "head_commit_at": None,
        "last_remote_ref_at": None,
        "remote_url": None,
        "upstream_ref": None,
        "ahead_count": None,
        "behind_count": None,
        "main_behind_count": None,
        "dirty": None,
        "drift_state": "not_applicable",
        "warning_codes": [],
        "warnings": [],
    }


def _project_current_state(ranked_observations: list[dict], observations_by_id: dict[str, dict]) -> dict:
    lifecycle_state = "unknown"
    lifecycle_basis = "unknown"
    last_session_at: str | None = None
    last_session_summary: str | None = None
    next_step: str | None = None
    tags: set[str] = set()

    for item in ranked_observations:
        obs = observations_by_id.get(item["observation_id"]) or {}
        compat = obs.get("compat_entry") if isinstance(obs.get("compat_entry"), dict) else {}
        status = str(compat.get("status") or "").strip()
        if lifecycle_state == "unknown" and status:
            lifecycle_state = status
            lifecycle_basis = "declared_sidecar"
        if last_session_at is None and str(compat.get("last_session_at") or "").strip():
            last_session_at = str(compat.get("last_session_at")).strip()
        if last_session_summary is None and str(compat.get("last_session_summary") or "").strip():
            last_session_summary = str(compat.get("last_session_summary")).strip()[:CAPSULE_PURPOSE_MAX_CHARS]
        if next_step is None and str(compat.get("next_step") or "").strip():
            next_step = str(compat.get("next_step")).strip()[:CAPSULE_PURPOSE_MAX_CHARS]
        raw_tags = compat.get("tags")
        if isinstance(raw_tags, list):
            for tag in raw_tags:
                if str(tag or "").strip():
                    tags.add(str(tag).strip())

    return {
        "lifecycle_state": lifecycle_state,
        "lifecycle_basis": lifecycle_basis,
        "session_receipts_state": "unavailable",
        "session_receipts_reason_code": "WAVE4_NOT_IMPLEMENTED",
        "last_session_at": last_session_at,
        "last_session_summary": last_session_summary,
        "next_step": next_step,
        "tags": sorted(tags),
    }


def build_project_capsule(
    *,
    project: dict,
    observations_by_id: dict[str, dict],
    sources_by_id: dict[str, dict] | None = None,
    reviews: list[dict] | None = None,
    state_dir: Path,
    run_id: str,
    compiled_at: str,
    input_observed_at: str,
    max_bytes: int = CAPSULE_MAX_BYTES,
    max_observation_summaries: int = CAPSULE_MAX_OBSERVATION_SUMMARIES,
) -> dict:
    """Build one bounded, deterministic project capsule for ``project``."""
    state_dir = state_dir.resolve()
    canonical_id = project["canonical_project_id"]
    capsule_path = state_dir / "projects" / f"{canonical_id}.json"

    ranked_observations, preferred_location = rank_project_observations(
        project,
        observations_by_id,
        sources_by_id=sources_by_id,
    )

    # Resolve bounded purpose and canonical_url from ranked observations.
    purpose: str | None = None
    canonical_url: str | None = None
    if project.get("normalized_remotes"):
        canonical_url = f"https://{project['normalized_remotes'][0]}"
    for item in ranked_observations:
        obs = observations_by_id.get(item["observation_id"]) or {}
        compat = obs.get("compat_entry") if isinstance(obs.get("compat_entry"), dict) else {}
        desc = str(compat.get("description") or "").strip()
        if purpose is None and desc:
            purpose = desc[:CAPSULE_PURPOSE_MAX_CHARS]
        raw_url = str(compat.get("canonical_url") or compat.get("remote_url") or "").strip()
        if canonical_url is None and raw_url:
            canonical_url = raw_url

    aliases: set[str] = set()
    for hint in project.get("project_key_hints", []):
        if str(hint or "").strip():
            aliases.add(str(hint).strip())
    for remote in project.get("normalized_remotes", []):
        if str(remote or "").strip():
            aliases.add(str(remote).strip())
    for ref in project.get("referents", []):
        if ref.get("kind") in {"decision_alias", "project_key_hint", "display_name"}:
            val = str(ref.get("value") or "").strip()
            if val:
                aliases.add(val)

    member_set = set(project.get("observation_ids", []))
    project_reviews = [
        {
            "review_id": item["review_id"],
            "code": item["code"],
            "severity": item.get("severity", "warning"),
            "state": item.get("state", "open"),
            "detail": item["detail"],
            "resolution_actions": list(item.get("resolution_actions", [])),
        }
        for item in (reviews or [])
        if set(item.get("affected_observation_ids", [])) & member_set
    ]
    project_reviews.sort(key=lambda item: item["review_id"])

    freshness = _project_freshness(ranked_observations, observations_by_id, input_observed_at)
    git_facts = _project_git_facts(ranked_observations, observations_by_id)
    current_state = _project_current_state(ranked_observations, observations_by_id)
    gbrain_link = (
        dict(project["gbrain"])
        if isinstance(project.get("gbrain"), dict)
        else resolve_gbrain_link(project, observations_by_id)
    )
    gbrain_source_slug = str(project.get("gbrain_source_slug") or gbrain_link["source_slug"])
    capsule_warnings = list(preferred_location.get("warnings") or [])

    shown_count = min(len(ranked_observations), max_observation_summaries)
    observations_truncated = shown_count < len(ranked_observations)

    bounded_resolved_fields: dict[str, dict] = {}
    for field_name, field_info in (project.get("resolved_fields") or {}).items():
        if not isinstance(field_info, dict):
            continue
        prov = list(field_info.get("provenance") or [])
        entry = {
            "value": field_info.get("value"),
            "provenance": prov[:CAPSULE_MAX_PROVENANCE_REFS],
        }
        if len(prov) > CAPSULE_MAX_PROVENANCE_REFS:
            entry["provenance_count"] = len(prov)
            entry["provenance_truncated"] = True
        bounded_resolved_fields[field_name] = entry

    def _assemble(limit: int, truncated: bool) -> dict:
        return {
            "schema_version": PROJECT_CAPSULE_SCHEMA_VERSION,
            "compiler_version": COMPILER_VERSION,
            "run_id": run_id,
            "compiled_at": compiled_at,
            "as_of": input_observed_at,
            "canonical_project_id": canonical_id,
            "project_key": project["project_key"],
            "display_name": project["display_name"],
            "aliases": sorted(aliases, key=str.casefold),
            "purpose": purpose,
            "canonical_url": canonical_url,
            "normalized_remotes": list(project.get("normalized_remotes", [])),
            "identity_anchor": dict(project.get("identity_anchor", {})),
            "resolved_fields": bounded_resolved_fields,
            "gbrain_source_slug": gbrain_source_slug,
            "gbrain": gbrain_link,
            "freshness": freshness,
            "preferred_location": preferred_location,
            "warnings": capsule_warnings,
            "git_facts": git_facts,
            "current_state": current_state,
            "observation_count": len(ranked_observations),
            "observations": ranked_observations[:limit],
            "review": {
                "open_review_count": len(project_reviews),
                "items": project_reviews,
            },
            "pointers": {
                "capsule_path": str(capsule_path),
                "canonical_projects_path": str(state_dir / "canonical-projects.json"),
                "observations_path": str(state_dir / "observations.json"),
                "identity_evidence_path": str(state_dir / "identity-evidence.json"),
                "review_queue_path": str(state_dir / "review-queue.json"),
                "observation_ids": list(project.get("observation_ids", [])),
                "merge_decision_ids": list(project.get("merge_decision_ids", [])),
                "canonical_key_decision_id": project.get("canonical_key_decision_id"),
                "task_system": {
                    "state": "external",
                    "provider": "machina",
                    "canonical_project_id": canonical_id,
                    "project_key": project["project_key"],
                },
                "knowledge_system": {
                    "state": "external",
                    "provider": "gbrain",
                    "canonical_project_id": canonical_id,
                    "project_key": project["project_key"],
                    "gbrain_source_slug": gbrain_source_slug,
                    "gbrain_project_slug": gbrain_link["project_slug"],
                    "gbrain_page_key": gbrain_link["page_key"],
                },
            },
            "budget": {
                "max_bytes": max_bytes,
                "max_observation_summaries": max_observation_summaries,
                "observations_truncated": truncated,
                "within_budget": True,
            },
        }

    capsule = _assemble(shown_count, observations_truncated)
    while shown_count > 1 and len((json.dumps(capsule, indent=2, sort_keys=True) + "\n").encode("utf-8")) > max_bytes:
        shown_count -= 1
        observations_truncated = True
        capsule = _assemble(shown_count, observations_truncated)

    encoded_size = len((json.dumps(capsule, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    capsule["budget"]["within_budget"] = encoded_size <= max_bytes
    return capsule


def materialize_capsules(
    *,
    canonical_payload: dict,
    observations: list[dict],
    sources: list[dict] | None = None,
    review_payload: dict | None = None,
    state_dir: Path,
    run_id: str,
    compiled_at: str,
    input_observed_at: str,
) -> dict:
    """Materialize ``state/projects/<canonical_project_id>.json`` for every project."""
    state_dir = state_dir.resolve()
    capsules_dir = state_dir / "projects"
    capsules_dir.mkdir(parents=True, exist_ok=True)

    observations_by_id = {str(item["observation_id"]): item for item in observations}
    sources_by_id = {str(src["source_id"]): src for src in (sources or []) if src.get("source_id")}
    review_items = (review_payload or {}).get("items", [])

    active_filenames: set[str] = set()
    capsules: list[dict] = []
    for project in canonical_payload.get("projects", []):
        capsule = build_project_capsule(
            project=project,
            observations_by_id=observations_by_id,
            sources_by_id=sources_by_id,
            reviews=review_items,
            state_dir=state_dir,
            run_id=run_id,
            compiled_at=compiled_at,
            input_observed_at=input_observed_at,
        )
        filename = f"{project['canonical_project_id']}.json"
        active_filenames.add(filename)
        (capsules_dir / filename).write_text(
            json.dumps(capsule, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        capsules.append(capsule)

    # Remove stale capsule files from prior compiles in the same state_dir.
    for existing in capsules_dir.glob("*.json"):
        if existing.name not in active_filenames:
            existing.unlink()

    canonical_md_path = write_canonical_markdown(
        output_path=state_dir / "canonical-projects.md",
        capsules=capsules,
        source_count=len(sources or []),
        observation_count=len(observations),
        review_count=len(review_items),
        run_id=run_id,
        compiled_at=compiled_at,
        input_observed_at=input_observed_at,
    )

    return {
        "schema_version": PROJECT_CAPSULE_SCHEMA_VERSION,
        "capsule_count": len(capsules),
        "capsules_dir": str(capsules_dir),
        "canonical_markdown_path": str(canonical_md_path),
        "capsules": capsules,
    }


def load_project_capsule(state_dir: Path, canonical_project_id: str) -> dict:
    path = state_dir.resolve() / "projects" / f"{canonical_project_id}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(
            f"Project capsule does not exist: {path}; run `python -m ledger compile` first."
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Project capsule is invalid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("canonical_project_id") != canonical_project_id:
        raise ValueError(f"Project capsule is malformed: {path}")
    return payload


def show_payload(state_dir: Path, referent: str) -> dict:
    """Resolve ``referent`` and return the project capsule (or ambiguity/unresolved)."""
    resolution = resolve_payload(state_dir, referent)
    if resolution["status"] != "resolved":
        return resolution
    capsule = load_project_capsule(state_dir, resolution["canonical_project_id"])
    result = dict(capsule)
    result["status"] = "resolved"
    result["referent"] = resolution["referent"]
    result["matched_by"] = resolution["matched_by"]
    return result


def locate_payload(state_dir: Path, referent: str) -> dict:
    """Resolve ``referent`` and return the preferred location and ranked observations."""
    resolution = resolve_payload(state_dir, referent)
    if resolution["status"] != "resolved":
        return resolution
    capsule = load_project_capsule(state_dir, resolution["canonical_project_id"])
    return {
        "schema_version": capsule["schema_version"],
        "compiler_version": capsule["compiler_version"],
        "run_id": capsule["run_id"],
        "compiled_at": capsule["compiled_at"],
        "as_of": capsule["as_of"],
        "status": "resolved",
        "referent": resolution["referent"],
        "matched_by": resolution["matched_by"],
        "canonical_project_id": capsule["canonical_project_id"],
        "project_key": capsule["project_key"],
        "display_name": capsule["display_name"],
        "gbrain_source_slug": capsule.get("gbrain_source_slug"),
        "gbrain": capsule.get("gbrain"),
        "preferred_location": capsule["preferred_location"],
        "warnings": list(capsule.get("warnings") or []),
        "candidate_count": capsule["observation_count"],
        "locations": capsule["observations"],
    }
