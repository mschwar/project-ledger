from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import COMPAT_FLAT_SCHEMA_VERSION, COMPILER_VERSION, MANIFEST_SCHEMA_VERSION, OBSERVATION_SCHEMA_VERSION
from .changes import build_run_snapshot, capture_pre_compile_baseline, materialize_changes
from .checkpoints import CHECKPOINT_FILENAME, load_source_checkpoints, write_source_checkpoints
from .compat_contract import check_compat_schema
from .config import describe_sources, read_config, validate_config
from .contracts import SOURCE_RESULT_STATES, capability
from .ids import digest_json, observation_id_for, path_digest, stable_id
from .identity import materialize_identity
from .models import portable_artifact_path
from .sidecar import inspect_entry_sidecar
from .sources.git import resolve_observation_git_drift
from .structural_ctx import extract_structural_ctx


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


def _lookup_checkpoint_candidate(
    cp_sources: dict,
    source_id: str,
    location: str | None,
) -> dict | None:
    if not location or not isinstance(cp_sources, dict):
        return None
    src_cp = cp_sources.get(source_id)
    if not isinstance(src_cp, dict) or not isinstance(src_cp.get("candidates"), dict):
        return None
    cands = src_cp["candidates"]
    loc_text = str(location).strip()
    if not loc_text:
        return None
    if loc_text.startswith("/"):
        try:
            resolved_key = str(Path(loc_text).resolve()).lower()
        except OSError:
            resolved_key = loc_text.lower()
        cand = cands.get(resolved_key) or cands.get(loc_text.lower())
        return cand if isinstance(cand, dict) else None
    lower_loc = loc_text.lower()
    cand = cands.get(lower_loc)
    if cand is None and ":" in lower_loc:
        remote_part, inv_part = lower_loc.split(":", 1)
        cand = cands.get(f"inventory::{remote_part}::{inv_part}") or cands.get(inv_part)
    return cand if isinstance(cand, dict) else None


def compile_state(
    *,
    config_path: Path,
    compat_output_path: Path,
    state_dir: Path,
    identity_decisions_path: Path | None = None,
    lineage_path: Path | None = None,
    generated_at: str | None = None,
    use_checkpoint_compile_cache: bool = False,
) -> dict:
    from .lineage import resolve_lineage_path

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
    compat_digest = path_digest(compat_output_path)
    decisions_digest = (
        path_digest(identity_decisions_path.resolve())
        if identity_decisions_path is not None and identity_decisions_path.is_file()
        else "none"
    )
    effective_lineage_path, _ = resolve_lineage_path(
        decisions_path=identity_decisions_path,
        lineage_path=lineage_path,
    )
    run_id_parts: list[object] = [
        compiled_at,
        config_digest,
        str(compat_output_path),
        compat_digest,
        decisions_digest,
    ]
    if effective_lineage_path is not None and effective_lineage_path.is_file():
        run_id_parts.append(path_digest(effective_lineage_path))
    run_id = stable_id("run", *run_id_parts)
    previous_snapshot = capture_pre_compile_baseline(state_dir, current_run_id=run_id)

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

    checkpoint_file = state_dir / CHECKPOINT_FILENAME
    checkpoint_payload = load_source_checkpoints(checkpoint_file)
    checkpoint_matched = (
        isinstance(checkpoint_payload, dict)
        and checkpoint_payload.get("config_digest") == config_digest
    )
    cp_sources = (
        checkpoint_payload.get("sources", {})
        if checkpoint_matched and isinstance(checkpoint_payload, dict)
        else {}
    )
    allow_compile_cache_read = bool(
        use_checkpoint_compile_cache
        and checkpoint_matched
        and isinstance(checkpoint_payload, dict)
        and checkpoint_payload.get("mode") != "bypassed"
    )
    allow_compile_cache_write = bool(
        use_checkpoint_compile_cache
        and checkpoint_matched
        and isinstance(checkpoint_payload, dict)
    )
    obs_cand_by_id: dict[str, dict] = {}
    pending_git_drift_by_obs: dict[str, dict] = {}
    pending_ctx_by_obs: dict[str, dict | None] = {}

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
        entry_location = (
            str(entry.get("path", "")).strip()
            or str(entry.get("canonical_url", "")).strip()
            or None
        )
        cand = _lookup_checkpoint_candidate(cp_sources, source_id, entry_location)
        cand_fp = cand.get("fingerprint") if isinstance(cand, dict) else None
        if (
            allow_compile_cache_read
            and cand_fp
            and cand.get("compile_cache_fingerprint") == cand_fp
            and isinstance(cand.get("sidecar_envelope"), dict)
            and "_sidecar_envelope" not in entry
        ):
            sidecar_envelope = dict(cand["sidecar_envelope"])
        else:
            sidecar_envelope = inspect_entry_sidecar(entry)
        raw_project_key = str(entry.get("project_key", "")).strip() or None
        if (
            sidecar_envelope.get("present")
            and sidecar_envelope.get("status") == "invalid"
            and "project_key" not in (sidecar_envelope.get("declarations") or {})
            and any(
                issue.get("field") in {None, "schema_version", "project_key"}
                for issue in (sidecar_envelope.get("issues") or [])
            )
            and not str(entry.get("remote_url") or "").strip()
        ):
            raw_project_key = None
        obs_id = observation_id_for(entry, source_id)
        observation = {
            "schema_version": OBSERVATION_SCHEMA_VERSION,
            "observation_id": obs_id,
            "source_id": source_id,
            "snapshot_id": snapshot_id,
            "source_resolution": resolution,
            "observed_at": observed_at,
            "project_key": raw_project_key,
            "location": entry_location,
            "sidecar": sidecar_envelope,
            "compat_entry": entry,
        }
        if isinstance(cand, dict) and cand_fp:
            obs_cand_by_id[obs_id] = cand
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

    for source in sources:
        src_cp = cp_sources.get(source["source_id"]) if isinstance(cp_sources, dict) else None
        if isinstance(src_cp, dict):
            source["checkpoint_status"] = str(src_cp.get("checkpoint_status") or "not_applicable")
            source["checkpoint_fingerprint"] = src_cp.get("checkpoint_fingerprint")
            source["last_extracted_at"] = src_cp.get("last_extracted_at")
            source["reused_candidate_count"] = int(src_cp.get("reused_candidate_count", 0))
            source["rebuilt_candidate_count"] = int(src_cp.get("rebuilt_candidate_count", 0))
        else:
            source["checkpoint_status"] = "not_applicable"
            source["checkpoint_fingerprint"] = None
            source["last_extracted_at"] = None
            source["reused_candidate_count"] = 0
            source["rebuilt_candidate_count"] = 0

    if checkpoint_matched and isinstance(checkpoint_payload, dict):
        cp_summary = dict(checkpoint_payload.get("summary") or {})
        checkpoints_block = {
            "state": "bypassed" if checkpoint_payload.get("mode") == "bypassed" else "active",
            "updated_at": checkpoint_payload.get("updated_at"),
            "source_count": int(cp_summary.get("source_count", len(sources))),
            "hit_source_count": int(cp_summary.get("hit_source_count", 0)),
            "partial_source_count": int(cp_summary.get("partial_source_count", 0)),
            "miss_source_count": int(cp_summary.get("miss_source_count", 0)),
            "unavailable_source_count": int(
                cp_summary.get("unavailable_source_count", source_unavailable_count)
            ),
            "bypassed_source_count": int(cp_summary.get("bypassed_source_count", 0)),
            "candidate_count": int(cp_summary.get("candidate_count", 0)),
            "reused_candidate_count": int(cp_summary.get("reused_candidate_count", 0)),
            "rebuilt_candidate_count": int(cp_summary.get("rebuilt_candidate_count", 0)),
        }
    else:
        checkpoints_block = {
            "state": "not_applicable",
            "updated_at": None,
            "source_count": len(sources),
            "hit_source_count": 0,
            "partial_source_count": 0,
            "miss_source_count": 0,
            "unavailable_source_count": source_unavailable_count,
            "bypassed_source_count": 0,
            "candidate_count": 0,
            "reused_candidate_count": 0,
            "rebuilt_candidate_count": 0,
        }

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

    def _resolve_git_drift_with_cache(obs: dict, compat_entry: dict) -> dict:
        oid = str(obs.get("observation_id") or "")
        cand = obs_cand_by_id.get(oid)
        cand_fp = cand.get("fingerprint") if isinstance(cand, dict) else None
        has_explicit_drift = any(
            key in compat_entry
            for key in ("behind_count", "ahead_count", "upstream_ref", "dirty", "main_behind_count")
        )
        if (
            allow_compile_cache_read
            and cand_fp
            and cand.get("compile_cache_fingerprint") == cand_fp
            and isinstance(cand.get("git_drift"), dict)
            and not has_explicit_drift
        ):
            return dict(cand["git_drift"])
        drift = resolve_observation_git_drift(obs, compat_entry)
        if isinstance(cand, dict) and cand_fp and not has_explicit_drift:
            pending_git_drift_by_obs[oid] = drift
        return drift

    def _resolve_ctx_with_cache(obs: dict, repo_dir: Path) -> dict | None:
        oid = str(obs.get("observation_id") or "")
        cand = obs_cand_by_id.get(oid)
        cand_fp = cand.get("fingerprint") if isinstance(cand, dict) else None
        if (
            allow_compile_cache_read
            and cand_fp
            and cand.get("compile_cache_fingerprint") == cand_fp
            and "ctx_raw_skeleton" in cand
        ):
            cached_ctx = cand.get("ctx_raw_skeleton")
            if cached_ctx is None:
                return None
            if (
                isinstance(cached_ctx, dict)
                and "operational_surfaces" in cached_ctx
                and "dependency_summary" in cached_ctx
            ):
                return dict(cached_ctx)
        scanned_ctx = extract_structural_ctx(repo_dir)
        if isinstance(cand, dict) and cand_fp:
            pending_ctx_by_obs[oid] = scanned_ctx
        return scanned_ctx

    in_mem_artifacts: dict = {}
    canonical_payload, review_payload = materialize_identity(
        observations=observations,
        decisions_path=identity_decisions_path,
        lineage_path=lineage_path,
        state_dir=state_dir,
        run_id=run_id,
        compiled_at=compiled_at,
        sources=sources,
        input_observed_at=observed_at,
        git_drift_resolver=_resolve_git_drift_with_cache if allow_compile_cache_write else None,
        ctx_resolver=_resolve_ctx_with_cache if allow_compile_cache_write else None,
        out_artifacts=in_mem_artifacts,
    )
    if allow_compile_cache_write and isinstance(checkpoint_payload, dict):
        compile_cache_dirty = False
        for obs in observations:
            oid = obs["observation_id"]
            cand = obs_cand_by_id.get(oid)
            if not isinstance(cand, dict) or not cand.get("fingerprint"):
                continue
            cand_fp = cand["fingerprint"]
            was_matched = cand.get("compile_cache_fingerprint") == cand_fp
            if (
                not was_matched
                or not isinstance(cand.get("sidecar_envelope"), dict)
                or oid in pending_git_drift_by_obs
                or oid in pending_ctx_by_obs
            ):
                compile_cache_dirty = True
            prior_drift = (
                cand.get("git_drift")
                if was_matched and isinstance(cand.get("git_drift"), dict)
                else None
            )
            has_prior_ctx = was_matched and "ctx_raw_skeleton" in cand
            prior_ctx = cand.get("ctx_raw_skeleton") if has_prior_ctx else None
            cand["compile_cache_fingerprint"] = cand_fp
            cand["sidecar_envelope"] = obs["sidecar"]
            drift_val = pending_git_drift_by_obs.get(oid) or prior_drift
            if isinstance(drift_val, dict):
                cand["git_drift"] = drift_val
            if oid in pending_ctx_by_obs:
                cand["ctx_raw_skeleton"] = pending_ctx_by_obs[oid]
            elif has_prior_ctx:
                cand["ctx_raw_skeleton"] = prior_ctx
        if compile_cache_dirty:
            write_source_checkpoints(checkpoint_file, checkpoint_payload)
    canonical_path = state_dir / "canonical-projects.json"
    capsules_dir = state_dir / "projects"
    review_path = state_dir / "review-queue.json"
    evidence_path = state_dir / "identity-evidence.json"
    changes_path = state_dir / "changes.json"
    evidence_payload = in_mem_artifacts.get("identity_evidence") or json.loads(
        evidence_path.read_text(encoding="utf-8")
    )

    in_mem_capsules = in_mem_artifacts.get("capsules")
    if isinstance(in_mem_capsules, list):
        capsules_by_id = {
            str(cap["canonical_project_id"]): cap
            for cap in in_mem_capsules
            if isinstance(cap, dict) and cap.get("canonical_project_id")
        }
    else:
        capsules_by_id = {}
        for proj in canonical_payload.get("projects", []):
            pid = proj["canonical_project_id"]
            cap_file = capsules_dir / f"{pid}.json"
            if cap_file.is_file():
                capsules_by_id[pid] = json.loads(cap_file.read_text(encoding="utf-8"))

    current_snapshot = build_run_snapshot(
        run_id=run_id,
        compiled_at=compiled_at,
        input_observed_at=observed_at,
        health_state=health_state,
        sources=sources,
        observations=observations,
        canonical_projects=canonical_payload.get("projects", []),
        review_items=review_payload.get("items", []),
        cross_project_weak_overlaps=evidence_payload.get("cross_project_weak_overlaps", []),
        capsules_by_id=capsules_by_id,
    )
    change_feed_payload = materialize_changes(
        previous_snapshot=previous_snapshot,
        current_snapshot=current_snapshot,
        state_dir=state_dir,
    )

    def _rel_artifact(path: Path) -> str:
        return portable_artifact_path(path, state_dir=state_dir)

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
            "project_capsules": canonical_payload["canonical_project_count"],
            "identity_evidence": evidence_payload["identity_evidence_count"],
            "review_items": review_payload["review_count"],
            "change_events": change_feed_payload["change_count"],
        },
        "changes": {
            "baseline_state": change_feed_payload["baseline_state"],
            "previous_run_id": change_feed_payload["previous_run_id"],
            "change_count": change_feed_payload["change_count"],
            "by_change_type": change_feed_payload["summary"]["by_change_type"],
            "by_severity": change_feed_payload["summary"]["by_severity"],
        },
        "checkpoints": checkpoints_block,
        "sources": sources,
        "capabilities": {
            "compat_observations": capability("available"),
            "typed_observations": capability("available"),
            "sidecar_declarations": capability("available"),
            "source_health": capability("available"),
            "source_checkpoints": capability("available"),
            "canonical_projects": capability("available"),
            "identity_evidence": capability("available"),
            "project_capsules": capability("available"),
            "review_queue": capability("available"),
            "change_feed": capability("available"),
            "gbrain_page_sync": capability("available"),
        },
        "artifacts": {
            "compat_observations": _rel_artifact(compat_output_path),
            "typed_observations": _rel_artifact(observations_path),
            "source_checkpoints": _rel_artifact(checkpoint_file),
            "canonical_projects": _rel_artifact(canonical_path),
            "canonical_markdown": _rel_artifact(state_dir / "canonical-projects.md"),
            "project_capsules": _rel_artifact(capsules_dir),
            "identity_evidence": _rel_artifact(evidence_path),
            "review_queue": _rel_artifact(review_path),
            "change_feed": _rel_artifact(changes_path),
            "system_manifest": _rel_artifact(manifest_path),
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
        "changes": manifest.get("changes"),
        "checkpoints": manifest.get("checkpoints"),
        "available_capabilities": sorted(available),
        "unavailable_capabilities": sorted(unavailable),
    }
