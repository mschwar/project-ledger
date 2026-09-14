from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .contracts import SUPPORTED_DISCOVERY_MODES
from .ids import digest_json, normalize_locator, path_digest, source_id_for, stable_id


class LedgerConfigError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def read_config(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LedgerConfigError("CONFIG_NOT_FOUND", f"Config does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise LedgerConfigError("CONFIG_INVALID_JSON", f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise LedgerConfigError("CONFIG_NOT_OBJECT", "Config must contain a JSON object.")
    return value


def resolve_config_path(raw: str, config_dir: Path) -> Path:
    from os.path import expanduser, expandvars

    text = expandvars(expanduser(raw))
    path = Path(text)
    if not path.is_absolute():
        path = config_dir / path
    return path.resolve()


def _require_nonempty_string(mapping: dict, key: str, *, code: str, where: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise LedgerConfigError(code, f"{where}.{key} must be a non-empty string.")
    return value.strip()


def validate_config(config: dict, config_dir: Path, *, check_artifacts: bool = True) -> None:
    roots = config.get("roots")
    if not isinstance(roots, list) or not roots:
        raise LedgerConfigError("ROOTS_REQUIRED", "Config must contain a non-empty roots array.")

    defaults = config.get("defaults", {})
    if defaults is not None and not isinstance(defaults, dict):
        raise LedgerConfigError("DEFAULTS_NOT_OBJECT", "defaults must be an object when present.")
    for key in ("min_score", "tree_scan_max_entries", "tree_scan_max_depth", "git_repo_max_depth"):
        if key in defaults and (not isinstance(defaults[key], int) or defaults[key] < 0):
            raise LedgerConfigError("DEFAULT_INVALID_INTEGER", f"defaults.{key} must be a non-negative integer.")

    seen_source_ids: set[str] = set()
    for index, root in enumerate(roots):
        where = f"roots[{index}]"
        if not isinstance(root, dict):
            raise LedgerConfigError("ROOT_NOT_OBJECT", f"{where} must be an object.")
        _require_nonempty_string(root, "path", code="ROOT_PATH_REQUIRED", where=where)
        discovery = str(root.get("discovery", "children")).strip() or "children"
        if discovery not in SUPPORTED_DISCOVERY_MODES:
            raise LedgerConfigError(
                "DISCOVERY_UNSUPPORTED",
                f"{where}.discovery={discovery!r} is unsupported; expected one of {sorted(SUPPORTED_DISCOVERY_MODES)}.",
            )
        for key in ("label", "machine_name", "storage_scope", "remote_name", "source_id"):
            if key in root and not isinstance(root[key], str):
                raise LedgerConfigError("ROOT_FIELD_TYPE", f"{where}.{key} must be a string when present.")
        explicit_source_id = str(root.get("source_id", "")).strip()
        if "source_id" in root and not explicit_source_id:
            raise LedgerConfigError("SOURCE_ID_EMPTY", f"{where}.source_id must be non-empty when present.")
        effective_source_id = source_id_for(root, index=index)
        if effective_source_id in seen_source_ids:
            raise LedgerConfigError("SOURCE_ID_DUPLICATE", f"{where} resolves to duplicate source_id {effective_source_id!r}.")
        seen_source_ids.add(effective_source_id)
        for key in ("max_depth", "min_score", "tree_scan_max_entries", "tree_scan_max_depth"):
            if key in root and (not isinstance(root[key], int) or root[key] < 0):
                raise LedgerConfigError("ROOT_INVALID_INTEGER", f"{where}.{key} must be a non-negative integer.")

        if discovery == "inventory_policy":
            inventory_raw = _require_nonempty_string(
                root, "inventory_jsonl", code="INVENTORY_PATH_REQUIRED", where=where
            )
            policy_raw = _require_nonempty_string(root, "policy_path", code="POLICY_PATH_REQUIRED", where=where)
            treatments = root.get("policy_crawl_treatments", ["project_discovery"])
            if not isinstance(treatments, list) or not all(isinstance(item, str) and item.strip() for item in treatments):
                raise LedgerConfigError(
                    "POLICY_TREATMENTS_INVALID",
                    f"{where}.policy_crawl_treatments must be a list of non-empty strings.",
                )
            if "require_project_ledger_candidate" in root and not isinstance(
                root["require_project_ledger_candidate"], bool
            ):
                raise LedgerConfigError(
                    "POLICY_CANDIDATE_FLAG_INVALID",
                    f"{where}.require_project_ledger_candidate must be boolean.",
                )
            if check_artifacts:
                for key, raw, code in (
                    ("inventory_jsonl", inventory_raw, "INVENTORY_ARTIFACT_MISSING"),
                    ("policy_path", policy_raw, "POLICY_ARTIFACT_MISSING"),
                ):
                    path = resolve_config_path(raw, config_dir)
                    if not path.is_file():
                        raise LedgerConfigError(code, f"{where}.{key} does not exist or is not a file: {path}")


def classify_source(root: dict) -> str:
    label = str(root.get("label", "")).lower()
    path = normalize_locator(str(root.get("path", ""))).lower()
    discovery = str(root.get("discovery", "children"))
    if discovery == "inventory_policy":
        return "inventory"
    if "backup" in label or "/backup" in path or "/archive" in path:
        return "backup"
    if "mirror" in label or "/mirrors/" in path:
        return "mirror"
    return "live"


def _mtime_iso(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except OSError:
        return None


def describe_sources(config: dict, config_dir: Path) -> list[dict]:
    sources: list[dict] = []
    for index, root_raw in enumerate(config["roots"]):
        root = dict(root_raw)
        discovery = str(root.get("discovery", "children")).strip() or "children"
        path = resolve_config_path(str(root["path"]), config_dir)
        sid = source_id_for(root, index=index)
        artifacts: list[dict] = []
        artifact_ok = True
        if discovery == "inventory_policy":
            for role, key in (("inventory", "inventory_jsonl"), ("policy", "policy_path")):
                artifact_path = resolve_config_path(str(root[key]), config_dir)
                exists = artifact_path.is_file()
                artifact_ok = artifact_ok and exists
                artifacts.append(
                    {
                        "role": role,
                        "path": str(artifact_path),
                        "state": "known" if exists else "unavailable",
                        "as_of": _mtime_iso(artifact_path) if exists else None,
                        "sha256": path_digest(artifact_path) if exists else None,
                    }
                )
            status = "available" if artifact_ok else "unavailable"
            status_reason = "inventory_artifacts_available" if artifact_ok else "required_inventory_artifact_missing"
        else:
            exists = path.exists()
            status = "available" if exists else "unavailable"
            status_reason = "root_accessible" if exists else "root_missing_or_unavailable"

        fingerprint_payload = {
            "source_id": sid,
            "discovery": discovery,
            "path": normalize_locator(str(path)),
            "path_exists": path.exists(),
            "path_mtime": _mtime_iso(path) if path.exists() else None,
            "artifacts": [{k: item[k] for k in ("role", "state", "sha256")} for item in artifacts],
        }
        input_fingerprint = digest_json(fingerprint_payload)
        sources.append(
            {
                "source_id": sid,
                "snapshot_id": stable_id("snap", sid, input_fingerprint),
                "label": str(root.get("label", "")).strip() or path.name or f"root-{index}",
                "source_class": classify_source(root),
                "discovery": discovery,
                "machine_name": str(root.get("machine_name", "")).strip() or None,
                "storage_scope": str(root.get("storage_scope", "")).strip() or None,
                "path": str(path),
                "path_state": "known" if path.exists() else "unavailable",
                "status": status,
                "status_reason": status_reason,
                "freshness_state": "unknown",
                "as_of": _mtime_iso(path) if path.exists() else None,
                "input_fingerprint": input_fingerprint,
                "artifacts": artifacts,
            }
        )
    return sources
