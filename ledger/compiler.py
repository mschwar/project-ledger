from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import COMPAT_FLAT_SCHEMA_VERSION, COMPILER_VERSION, MANIFEST_SCHEMA_VERSION, OBSERVATION_SCHEMA_VERSION
from .config import describe_sources, read_config, validate_config
from .contracts import capability
from .ids import digest_json, observation_id_for, stable_id


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
    return payload


def _resolve_source_id(source_label: str, sources: list[dict]) -> tuple[str, str]:
    candidates = sorted(sources, key=lambda item: len(item["label"]), reverse=True)
    for source in candidates:
        label = source["label"]
        if source_label == label or source_label.startswith(label + ":"):
            return source["source_id"], "resolved"
    return stable_id("src", "unresolved", source_label), "unresolved"


def compile_state(
    *,
    config_path: Path,
    compat_output_path: Path,
    state_dir: Path,
    generated_at: str | None = None,
) -> dict:
    config_path = config_path.resolve()
    compat_output_path = compat_output_path.resolve()
    state_dir = state_dir.resolve()
    config = read_config(config_path)
    validate_config(config, config_path.parent, check_artifacts=True)
    sources = describe_sources(config, config_path.parent)
    compat = read_compat_output(compat_output_path)

    compiled_at = generated_at or now_utc()
    observed_at = str(compat.get("generated_at") or compiled_at)
    config_digest = digest_json(config)
    run_id = stable_id("run", compiled_at, config_digest, str(compat_output_path))

    observations: list[dict] = []
    unresolved_source_count = 0
    for entry in compat["entries"]:
        if not isinstance(entry, dict):
            raise ValueError("Each compatibility entry must be a JSON object.")
        source_label = str(entry.get("source_label", "")).strip()
        source_id, resolution = _resolve_source_id(source_label, sources)
        if resolution == "unresolved":
            unresolved_source_count += 1
        observations.append(
            {
                "schema_version": OBSERVATION_SCHEMA_VERSION,
                "observation_id": observation_id_for(entry, source_id),
                "source_id": source_id,
                "source_resolution": resolution,
                "observed_at": observed_at,
                "project_key": str(entry.get("project_key", "")).strip() or None,
                "location": str(entry.get("path", "")).strip()
                or str(entry.get("canonical_url", "")).strip()
                or None,
                "compat_entry": entry,
            }
        )

    observations.sort(key=lambda item: item["observation_id"])
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
            "unresolved_observation_source_count": unresolved_source_count,
        },
        "counts": {
            "sources": len(sources),
            "observations": len(observations),
            "canonical_projects": None,
            "review_items": None,
        },
        "sources": sources,
        "capabilities": {
            "compat_observations": capability("available"),
            "typed_observations": capability("available"),
            "source_health": capability("available"),
            "canonical_projects": capability(
                "unavailable",
                reason_code="WAVE2_NOT_IMPLEMENTED",
                detail="Canonical identity compilation is a Wave 2 capability.",
            ),
            "project_capsules": capability(
                "unavailable",
                reason_code="WAVE3_NOT_IMPLEMENTED",
                detail="Project capsules require canonical projects.",
            ),
            "review_queue": capability(
                "unavailable",
                reason_code="WAVE2_NOT_IMPLEMENTED",
                detail="Identity/review queue is not implemented yet.",
            ),
            "change_feed": capability(
                "unavailable",
                reason_code="WAVE4_NOT_IMPLEMENTED",
                detail="Historical semantic change feed is not implemented yet.",
            ),
        },
        "artifacts": {
            "compat_observations": str(compat_output_path),
            "typed_observations": str(observations_path),
            "system_manifest": str(manifest_path),
        },
        "limitations": [
            "Canonical project identity is not implemented; observation count is not project count.",
            "Source freshness is reported as unknown unless an upstream source contract supplies stronger semantics.",
            "Current-state/session fields inside compatibility entries remain legacy declarations until the Wave 4 resolver lands.",
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
