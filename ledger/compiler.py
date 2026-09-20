from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import COMPAT_FLAT_SCHEMA_VERSION, COMPILER_VERSION, MANIFEST_SCHEMA_VERSION, OBSERVATION_SCHEMA_VERSION
from .compat_contract import check_compat_schema
from .config import describe_sources, read_config, validate_config
from .contracts import SOURCE_RESULT_STATES, capability
from .ids import digest_json, observation_id_for, stable_id
from .identity import materialize_identity


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_compat_output(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Compatibility observation input does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Compatibility observation input is invalid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        raise ValueError(f"Compatibility observation input must contain an entries array: {path}")
    check_compat_schema(payload)
    return payload


def _resolve_source(source_label: str, sources: list[dict]) -> tuple[str, str]:
    candidates = sorted(sources, key=lambda item: len(item["label"]), reverse=True)
    for source in candidates:
        label = source["label"]
        if source_label == label or source_label.startswith(label + ":"):
            return source["source_id"], "resolved"
    return stable_id("src", "unresolved", source_label), "unresolved"


def _iso_max(*values: str) -> str | None:
    """Return the latest ISO-8601 timestamp among non-empty values, else None.

    Parses each value as a timezone-aware datetime rather than comparing strings
    lexically, so mixed offset representations (``+00:00`` vs ``Z``) compare by
    instant, not by text. Malformed/unparseable values are ignored (treated as
    absent) so a bad upstream timestamp cannot poison source freshness.
    """
    latest: datetime | None = None
    latest_original: str | None = None
    for value in values:
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            continue  # not a timezone-bearing timestamp; do not invent timezone
        if latest is None or parsed > latest:
            latest = parsed
            latest_original = value
    return latest_original


def _source_content_as_of(observations: list[dict]) -> tuple[str | None, str | None]:
    """Derive the strongest authoritative upstream-as-of evidence for a source.

    P1.5: only claim a content timestamp where an upstream source actually
    supplies it as evidence. We never invent freshness. Authoritative priority is
    the git remote-ref committer date, then the local HEAD commit date, then the
    filesystem last-touch time. If no observation supplies any of these, the source
    freshness stays unknown (both returned values None).
    """
    remote_ref_candidates: list[str] = []
    head_commit_candidates: list[str] = []
    last_touch_candidates: list[str] = []
    for observation in observations:
        compat = observation.get("compat_entry") or {}
        remote_ref = str(compat.get("last_remote_ref_at", "") or "").strip()
        head_commit = str(compat.get("head_commit_at", "") or "").strip()
        last_touch = str(compat.get("last_touch_at", "") or "").strip()
        if remote_ref:
            remote_ref_candidates.append(remote_ref)
        if head_commit:
            head_commit_candidates.append(head_commit)
        if last_touch:
            last_touch_candidates.append(last_touch)

    # Only claim an as_of + basis together, and only where authoritative evidence
    # produced a usable timezone-bearing instant. Each candidate class is evaluated
    # in priority order; the first that yields a usable as_of wins.
    for candidates, basis_claim in (
        (remote_ref_candidates, "compat.entry.last_remote_ref_at"),
        (head_commit_candidates, "compat.entry.head_commit_at"),
        (last_touch_candidates, "compat.entry.last_touch_at"),
    ):
        if not candidates:
            continue
        candidate_as_of = _iso_max(*candidates)
        if candidate_as_of is not None:
            return candidate_as_of, basis_claim
    return None, None


def _classify_source_result(
    *,
    status: str,
    observation_count: int,
) -> str:
    """Classify a source's materialized result into the P1.5 observed vocabulary.

    ``unavailable``   - the source root/artifacts could not be accessed (failed to
                        probe), so nothing could be observed from it;
    ``observed_empty`` - the source was probed successfully but contributed zero
                        observations (a real, distinguishable outcome - the source is
                        healthy but empty, not broken);
    ``observed``      - the source was probed successfully and contributed >= 1
                        observation.
    """
    if status != "available":
        return "unavailable"
    return "observed" if observation_count > 0 else "observed_empty"


def compile_state(
    *,
    config_path: Path,
    compat_output_path: Path,
    state_dir: Path,
    identity_decisions_path: Path | None = None,
    generated_at: str | None = None,
) -> dict:
    config_path = config_path.resolve()
    compat_output_path = compat_output_path.resolve()
    state_dir = state_dir.resolve()
    config = read_config(config_path)
    # Compilation must preserve orientation when a source is temporarily unavailable.
    # Structural config errors still fail; source-artifact availability is reported in the manifest.
    validate_config(config, config_path.parent, check_artifacts=False)
    compat = read_compat_output(compat_output_path)

    compiled_at = generated_at or now_utc()
    observed_at = str(compat.get("generated_at") or compiled_at)
    config_digest = digest_json(config)
    run_id = stable_id("run", compiled_at, config_digest, str(compat_output_path))

    sources = describe_sources(config, config_path.parent)
    snapshot_id_by_source: dict[str, str] = {}
    for source in sources:
        snapshot_id = stable_id("snap", source["source_id"], observed_at)
        snapshot_id_by_source[source["source_id"]] = snapshot_id
        # Compatibility scanner v0 only gives one overall generated_at. This is a bounded
        # compatibility snapshot identity, separate from the source's current health probe.
        source["compat_snapshot_id"] = snapshot_id
        source["compat_snapshot_as_of"] = observed_at
        source["compat_snapshot_basis"] = "compat_output.generated_at"

    observations: list[dict] = []
    unresolved_source_count = 0
    observations_by_source: dict[str, list[dict]] = {}
    for entry in compat["entries"]:
        if not isinstance(entry, dict):
            raise ValueError("Each compatibility entry must be a JSON object.")
        source_label = str(entry.get("source_label", "")).strip()
        source_id, resolution = _resolve_source(source_label, sources)
        if resolution == "unresolved":
            unresolved_source_count += 1
        snapshot_id = snapshot_id_by_source.get(source_id) or stable_id("snap", source_id, observed_at)
        observation = {
            "schema_version": OBSERVATION_SCHEMA_VERSION,
            "observation_id": observation_id_for(entry, source_id),
            "source_id": source_id,
            "snapshot_id": snapshot_id,
            "source_resolution": resolution,
            "observed_at": observed_at,
            "project_key": str(entry.get("project_key", "")).strip() or None,
            "location": str(entry.get("path", "")).strip()
            or str(entry.get("canonical_url", "")).strip()
            or None,
            "compat_entry": entry,
        }
        observations.append(observation)
        observations_by_source.setdefault(source_id, []).append(observation)

    observations.sort(key=lambda item: item["observation_id"])

    # P1.5: enrich each source record with its materialized result state and the
    # strongest authoritative upstream content-as-of evidence (never invented).
    observed_empty_count = 0
    for source in sources:
        source_observations = observations_by_source.get(source["source_id"], [])
        observation_count = len(source_observations)
        result_state = _classify_source_result(
            status=source["status"],
            observation_count=observation_count,
        )
        if result_state == "observed_empty":
            observed_empty_count += 1
        content_as_of, content_as_of_basis = _source_content_as_of(source_observations)
        source["result_state"] = result_state
        source["observation_count"] = observation_count
        source["content_as_of"] = content_as_of
        source["content_as_of_basis"] = content_as_of_basis
        # Freshness is only ever 'known' when an upstream source actually supplied
        # authoritative timestamp evidence (git remote-ref / HEAD commit). An empty
        # but healthy source is not 'stale' or 'known' — it was successfully observed
        # to contain nothing. An unavailable source simply has no known freshness.
        if result_state == "unavailable":
            source["freshness_state"] = "unavailable"
        elif content_as_of is not None:
            source["freshness_state"] = "known"
        else:
            source["freshness_state"] = "unknown"
        if result_state not in SOURCE_RESULT_STATES:
            raise ValueError(f"Invalid source result_state: {result_state}")

    source_unavailable_count = sum(1 for source in sources if source["status"] != "available")
    health_state = "degraded" if source_unavailable_count or unresolved_source_count else "ok"

    state_dir.mkdir(parents=True, exist_ok=True)
    observations_path = state_dir / "observations.json"
    manifest_path = state_dir / "system-manifest.json"

    observation_payload = {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "compat_schema_version": COMPAT_FLAT_SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "run_id": run_id,
        "compiled_at": compiled_at,
        "observed_at": observed_at,
        "observation_count": len(observations),
        "observations": observations,
    }
    observations_path.write_text(json.dumps(observation_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    canonical_payload, review_payload = materialize_identity(
        observations=observations,
        decisions_path=identity_decisions_path,
        state_dir=state_dir,
        run_id=run_id,
        compiled_at=compiled_at,
    )
    canonical_path = state_dir / "canonical-projects.json"
    review_path = state_dir / "review-queue.json"
    evidence_path = state_dir / "identity-evidence.json"
    evidence_payload = json.loads(evidence_path.read_text(encoding="utf-8"))

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "run_id": run_id,
        "compiled_at": compiled_at,
        "input_observed_at": observed_at,
        "config": {
            "path": str(config_path),
            "digest": config_digest,
        },
        "health": {
            "state": health_state,
            "source_unavailable_count": source_unavailable_count,
            "observed_empty_source_count": observed_empty_count,
            "unresolved_observation_source_count": unresolved_source_count,
            "identity_review_count": review_payload["review_count"],
        },
        "counts": {
            "sources": len(sources),
            "observations": len(observations),
            "canonical_projects": canonical_payload["canonical_project_count"],
            "identity_evidence": evidence_payload["identity_evidence_count"],
            "review_items": review_payload["review_count"],
        },
        "sources": sources,
        "capabilities": {
            "compat_observations": capability("available"),
            "typed_observations": capability("available"),
            "source_health": capability("available"),
            "canonical_projects": capability("available"),
            "identity_evidence": capability("available"),
            "project_capsules": capability(
                "unavailable",
                reason_code="WAVE3_NOT_IMPLEMENTED",
                detail="Project capsules are not implemented yet.",
            ),
            "review_queue": capability("available"),
            "change_feed": capability(
                "unavailable",
                reason_code="WAVE4_NOT_IMPLEMENTED",
                detail="Historical semantic change feed is not implemented yet.",
            ),
        },
        "artifacts": {
            "compat_observations": str(compat_output_path),
            "typed_observations": str(observations_path),
            "canonical_projects": str(canonical_path),
            "identity_evidence": str(evidence_path),
            "review_queue": str(review_path),
            "system_manifest": str(manifest_path),
        },
        "limitations": [
            "Canonical identity auto-merges only exact normalized repository remotes; names and compatibility project_key values are referents, not automatic identity authority.",
            "Non-remote duplicate manifestations require an explicit identity decision until stronger typed declaration contracts land.",
            "Source freshness is reported as known only where an upstream source supplies authoritative timestamp evidence (git remote-ref or HEAD commit); otherwise it is honestly unknown rather than invented.",
            "Compatibility snapshot IDs are scoped by source and the v0 output generated_at; v0 does not preserve native per-source snapshot metadata.",
            "Current-state/session fields inside compatibility entries remain legacy declarations until the later current-state resolver lands.",
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def load_manifest(state_dir: Path) -> dict:
    path = state_dir.resolve() / "system-manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"System manifest does not exist: {path}; run `python -m ledger compile` first.") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"System manifest must be a JSON object: {path}")
    return payload


def orient_payload(manifest: dict) -> dict:
    available = [
        name for name, value in manifest.get("capabilities", {}).items() if value.get("state") == "available"
    ]
    unavailable = [
        name for name, value in manifest.get("capabilities", {}).items() if value.get("state") != "available"
    ]
    return {
        "run_id": manifest.get("run_id"),
        "compiled_at": manifest.get("compiled_at"),
        "input_observed_at": manifest.get("input_observed_at"),
        "health": manifest.get("health"),
        "counts": manifest.get("counts"),
        "available_capabilities": sorted(available),
        "unavailable_capabilities": sorted(unavailable),
    }
