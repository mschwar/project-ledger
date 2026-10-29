"""Review queue reader and frontier zero-shot weak-overlap triage (`ledger review`).

Replaces Wave 5's originally proposed local-LLM deduplication pipeline with a
frontier zero-shot triage contract over ``state/identity-evidence.json``
(``cross_project_weak_overlaps``) + repository READMEs, emitting a batch of
candidate ``merge`` / ``reject_match`` / ``canonical_key`` decisions for
one-click human approval (`ledger review --approve-candidates`).
"""

from __future__ import annotations

import json
from pathlib import Path

from . import COMPILER_VERSION, IDENTITY_DECISION_SCHEMA_VERSION, REVIEW_QUEUE_SCHEMA_VERSION
from .identity import load_identity_decisions

README_EXCERPT_MAX_CHARS = 280


def _read_readme_excerpt(readme_path: str | None) -> str | None:
    text_path = str(readme_path or "").strip()
    if not text_path or not text_path.startswith("/"):
        return None
    path = Path(text_path)
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not raw:
        return None
    compact = " ".join(raw.split())
    return compact[:README_EXCERPT_MAX_CHARS]


def _observation_triage_brief(observation: dict) -> dict:
    compat = observation.get("compat_entry") if isinstance(observation.get("compat_entry"), dict) else {}
    readme_path = str(compat.get("readme_path") or "").strip() or None
    return {
        "observation_id": observation["observation_id"],
        "source_id": observation.get("source_id"),
        "location": observation.get("location"),
        "project_key": observation.get("project_key"),
        "name": compat.get("name"),
        "remote_url": str(compat.get("remote_url") or "").strip() or None,
        "description": str(compat.get("description") or "").strip()[:README_EXCERPT_MAX_CHARS] or None,
        "readme_path": readme_path,
        "readme_excerpt": _read_readme_excerpt(readme_path),
    }


def load_candidate_decisions(path: Path | None) -> list[dict]:
    if path is None or not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid candidate decision batch {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
        raise ValueError(f"Candidate decision batch must contain a candidates array: {path}")
    return list(payload["candidates"])


def review_payload(
    state_dir: Path,
    *,
    candidates_path: Path | None = None,
    decisions_path: Path | None = None,
) -> dict:
    """Return open identity reviews, untriaged weak overlaps, and pending candidate decisions."""
    state_dir = state_dir.resolve()
    review_file = state_dir / "review-queue.json"
    try:
        reviews = json.loads(review_file.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(
            f"Review queue does not exist: {review_file}; run `python -m ledger compile` first."
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Review queue is invalid JSON: {review_file}: {exc}") from exc

    evidence_file = state_dir / "identity-evidence.json"
    evidence = (
        json.loads(evidence_file.read_text(encoding="utf-8"))
        if evidence_file.is_file()
        else {"cross_project_weak_overlaps": []}
    )
    observations_file = state_dir / "observations.json"
    observations_list = (
        json.loads(observations_file.read_text(encoding="utf-8")).get("observations", [])
        if observations_file.is_file()
        else []
    )
    observations_by_id = {str(o["observation_id"]): o for o in observations_list if o.get("observation_id")}

    overlaps = list(evidence.get("cross_project_weak_overlaps", []))
    untriaged_overlaps: list[dict] = []
    for item in overlaps:
        has_negative = any(
            rec.get("kind") == "negative_decision" for rec in item.get("evidence", [])
        )
        if has_negative:
            continue
        lid = item["left_observation_id"]
        rid = item["right_observation_id"]
        left_obs = observations_by_id.get(lid)
        right_obs = observations_by_id.get(rid)
        untriaged_overlaps.append(
            {
                "left_observation_id": lid,
                "right_observation_id": rid,
                "verdict": item.get("verdict"),
                "shared_weak": list(item.get("shared_weak", [])),
                "left": _observation_triage_brief(left_obs) if left_obs else {"observation_id": lid},
                "right": _observation_triage_brief(right_obs) if right_obs else {"observation_id": rid},
            }
        )

    eff_decisions_path = decisions_path or Path("registry/identity-decisions.json")
    existing_decisions = (
        load_identity_decisions(eff_decisions_path)
        if eff_decisions_path.is_file()
        else {"schema_version": IDENTITY_DECISION_SCHEMA_VERSION, "decisions": []}
    )
    existing_ids = {str(d["decision_id"]) for d in existing_decisions.get("decisions", [])}

    eff_candidates_path = candidates_path or Path("registry/weak-overlap-triage-candidates.json")
    candidates = load_candidate_decisions(eff_candidates_path)
    pending_candidates = [c for c in candidates if str(c.get("decision_id", "")) not in existing_ids]
    approved_candidates = [c for c in candidates if str(c.get("decision_id", "")) in existing_ids]

    return {
        "schema_version": REVIEW_QUEUE_SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "run_id": reviews.get("run_id"),
        "compiled_at": reviews.get("compiled_at"),
        "review_count": reviews.get("review_count", len(reviews.get("items", []))),
        "items": list(reviews.get("items", [])),
        "weak_overlap_count": len(overlaps),
        "untriaged_weak_overlap_count": len(untriaged_overlaps),
        "untriaged_weak_overlaps": untriaged_overlaps,
        "candidates_path": str(eff_candidates_path.resolve()) if eff_candidates_path.is_file() else None,
        "pending_candidate_count": len(pending_candidates),
        "approved_candidate_count": len(approved_candidates),
        "candidate_decisions": pending_candidates,
    }


def approve_candidate_decisions(
    *,
    candidates_path: Path,
    decisions_path: Path,
    decision_ids: list[str] | None = None,
) -> dict:
    """Promote frontier-triaged candidate decisions into `registry/identity-decisions.json`."""
    candidates_path = candidates_path.resolve()
    decisions_path = decisions_path.resolve()
    candidates = load_candidate_decisions(candidates_path)
    if not candidates:
        raise ValueError(f"No candidate decisions found in {candidates_path}")

    existing_payload = (
        load_identity_decisions(decisions_path)
        if decisions_path.is_file()
        else {"schema_version": IDENTITY_DECISION_SCHEMA_VERSION, "decisions": []}
    )
    existing_decisions = list(existing_payload.get("decisions", []))
    existing_ids = {str(d["decision_id"]) for d in existing_decisions}

    requested_ids = {str(did).strip() for did in (decision_ids or []) if str(did).strip()}
    if requested_ids:
        known_candidate_ids = {str(c.get("decision_id", "")) for c in candidates}
        unknown = sorted(requested_ids - known_candidate_ids)
        if unknown:
            raise ValueError(f"Unknown candidate decision_id(s): {unknown}")

    newly_approved: list[dict] = []
    already_present: list[str] = []
    for candidate in candidates:
        did = str(candidate.get("decision_id", "")).strip()
        if requested_ids and did not in requested_ids:
            continue
        if did in existing_ids:
            already_present.append(did)
            continue
        clean_decision = {
            k: v
            for k, v in candidate.items()
            if not k.startswith("_") and k != "triage_verdict"
        }
        newly_approved.append(clean_decision)
        existing_ids.add(did)

    merged_payload = {
        "schema_version": IDENTITY_DECISION_SCHEMA_VERSION,
        "decisions": existing_decisions + newly_approved,
    }
    # Validate merged payload before writing to disk.
    tmp_check = decisions_path.parent / f".{decisions_path.name}.tmp"
    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_check.write_text(json.dumps(merged_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        load_identity_decisions(tmp_check)
        tmp_check.replace(decisions_path)
    finally:
        if tmp_check.exists():
            tmp_check.unlink()

    return {
        "ok": True,
        "candidates_path": str(candidates_path),
        "decisions_path": str(decisions_path),
        "approved_count": len(newly_approved),
        "approved_decision_ids": [d["decision_id"] for d in newly_approved],
        "already_present_count": len(already_present),
        "total_decisions": len(merged_payload["decisions"]),
    }
