"""Inventory-policy source adapter.

Senses an rclone/Google-Drive-style inventory JSONL plus a policy file to derive
project candidates that may or may not be mounted on the local filesystem. Emits
compatibility observations anchored to inventory paths rather than to a live git
tree. Read-only toward both the inventory artifacts and any mounted mirror.
"""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from urllib.parse import quote

from ..models import (
    classify_project_type,
    coerce_bool,
    isoformat_from_ts,
    markdown_link,
    normalize_tags,
    parse_iso_datetime,
    sha256_hex,
)
from .common import (
    CODE_EXTENSIONS,
    README_CANDIDATES,
    SIDECAR_CANDIDATES,
    SPECIAL_FILES,
    find_first_existing,
    load_sidecar,
    parse_readme_summary,
    read_json,
    safe_path_exists,
    safe_path_is_dir,
)
from .git import gather_git_metadata


def build_google_drive_url(remote_name: str, inventory_path: str) -> str:
    remote = str(remote_name).strip().rstrip(":") or "googledrive"
    normalized = "/".join(part for part in str(inventory_path).split("/") if part)
    return f"gdrive://{remote}/{quote(normalized, safe='/-_.()')}"


def inventory_candidate_summary(records: list[dict]) -> dict:
    latest_ts = None
    markdown_count = 0
    code_count = 0
    has_special_file = False
    has_readme = False
    has_obsidian = False
    has_git = False

    for record in records:
        path_text = str(record.get("path", ""))
        parts = [part for part in path_text.split("/") if part]
        if record.get("is_dir"):
            if ".obsidian" in parts:
                has_obsidian = True
            if ".git" in parts:
                has_git = True
        name = str(record.get("name") or (parts[-1] if parts else "")).strip()
        if name in README_CANDIDATES:
            has_readme = True
        if name in SPECIAL_FILES:
            has_special_file = True
        suffix = Path(name).suffix.lower()
        if not record.get("is_dir") and suffix == ".md":
            markdown_count += 1
        if not record.get("is_dir") and suffix in CODE_EXTENSIONS:
            code_count += 1
        ts = parse_iso_datetime(str(record.get("mod_time", "")))
        if ts is not None and (latest_ts is None or ts > latest_ts):
            latest_ts = ts

    reasons: list[str] = ["inventory-policy"]
    if has_readme:
        reasons.append("inventory-readme")
    if has_special_file:
        reasons.append("inventory-project-files")
    if has_obsidian:
        reasons.append("inventory-obsidian")
    if has_git:
        reasons.append("inventory-git")
    if markdown_count >= 4:
        reasons.append(f"inventory-markdown:{markdown_count}")
    if code_count >= 3:
        reasons.append(f"inventory-code:{code_count}")

    return {
        "latest_ts": latest_ts,
        "markdown_count": markdown_count,
        "code_count": code_count,
        "has_special_file": has_special_file,
        "has_readme": has_readme,
        "has_obsidian": has_obsidian,
        "has_git": has_git,
        "reasons": reasons,
        "truncated": False,
    }


def load_inventory_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            text = raw.strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL record at {path}:{line_no}: {exc}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"Inventory record at {path}:{line_no} must be a JSON object.")
            records.append(record)
    return records


def discover_inventory_policy_candidates(root_cfg: dict, exclude_names: set[str]) -> list[dict]:
    inventory_records = load_inventory_jsonl(root_cfg["_inventory_jsonl_path"])
    policy = read_json(root_cfg["_policy_path"])
    policy_roots = policy.get("roots", {})
    if not isinstance(policy_roots, dict):
        raise ValueError(f"Policy file {root_cfg['_policy_path']} must contain a roots object.")

    treatments = {
        str(item).strip()
        for item in root_cfg.get("policy_crawl_treatments", ["project_discovery"])
        if str(item).strip()
    }
    require_project_candidate = bool(root_cfg.get("require_project_ledger_candidate", True))
    remote_name = str(root_cfg.get("remote_name", "googledrive")).strip().rstrip(":") or "googledrive"

    records_by_root: dict[str, list[dict]] = {}
    for record in inventory_records:
        root_label = str(record.get("root_label", "")).strip()
        if not root_label:
            continue
        records_by_root.setdefault(root_label, []).append(record)

    candidates: list[dict] = []
    seen_inventory_paths: set[str] = set()

    def add_candidate(root_name: str, candidate_kind: str, inventory_path: str, root_meta: dict) -> None:
        normalized_inventory_path = "/".join(part for part in str(inventory_path).split("/") if part)
        if not normalized_inventory_path:
            return
        key = normalized_inventory_path.lower()
        if key in seen_inventory_paths:
            return
        seen_inventory_paths.add(key)

        prefix = normalized_inventory_path + "/"
        root_records = records_by_root.get(root_name, [])
        scoped_records = [
            record
            for record in root_records
            if str(record.get("path", "")) == normalized_inventory_path
            or str(record.get("path", "")).startswith(prefix)
        ]

        filesystem_path = root_cfg["_resolved_path"].joinpath(*normalized_inventory_path.split("/"))
        candidates.append(
            {
                "candidate_kind": candidate_kind,
                "filesystem_path": filesystem_path,
                "inventory_path": normalized_inventory_path,
                "path_key": f"inventory::{remote_name}::{normalized_inventory_path.lower()}",
                "policy_root": dict(root_meta),
                "records": scoped_records,
                "root_label": root_name,
                "summary": inventory_candidate_summary(scoped_records),
            }
        )

    for root_name, root_meta_raw in sorted(policy_roots.items(), key=lambda item: item[0].lower()):
        root_meta = dict(root_meta_raw)
        if str(root_meta.get("crawl_treatment", "")).strip() not in treatments:
            continue
        if require_project_candidate and not root_meta.get("project_ledger_candidate", False):
            continue

        add_candidate(root_name, "root", root_name, root_meta)

        for record in records_by_root.get(root_name, []):
            if not record.get("is_dir"):
                continue
            candidate_path = str(record.get("path", "")).strip()
            parts = [part for part in candidate_path.split("/") if part]
            if len(parts) != 2 or parts[0] != root_name:
                continue
            child_name = parts[-1]
            if child_name in exclude_names:
                continue
            add_candidate(root_name, "direct-child", candidate_path, root_meta)

    return sorted(candidates, key=lambda item: item["inventory_path"].lower())


def build_inventory_entry(
    candidate: dict,
    root_cfg: dict,
    defaults: dict,
    output_dir: Path,
) -> dict | None:
    filesystem_path = Path(candidate["filesystem_path"])
    summary = candidate["summary"]
    policy_root = candidate["policy_root"]
    remote_name = str(root_cfg.get("remote_name", "googledrive")).strip().rstrip(":") or "googledrive"

    readme_path = None
    sidecar_path = None
    if safe_path_exists(filesystem_path) and safe_path_is_dir(filesystem_path):
        readme_path = find_first_existing(filesystem_path, README_CANDIDATES)
        sidecar_path = find_first_existing(filesystem_path, SIDECAR_CANDIDATES)
    sidecar = load_sidecar(sidecar_path)
    git_meta = gather_git_metadata(filesystem_path)

    title, readme_description = parse_readme_summary(readme_path)
    readme_sha256 = ""
    if readme_path:
        try:
            readme_sha256 = sha256_hex(readme_path.read_bytes())
        except OSError:
            readme_sha256 = ""

    canonical_url = (
        str(sidecar.get("canonical_url", "")).strip()
        or build_google_drive_url(remote_name, candidate["inventory_path"])
    )
    storage_scope = (
        str(sidecar.get("storage_scope", "")).strip()
        or str(root_cfg.get("storage_scope", "")).strip()
        or "shared"
    )
    shared = coerce_bool(sidecar.get("shared"))
    if shared is None:
        shared = True

    display_name = (
        str(sidecar.get("display_name", "")).strip()
        or title
        or filesystem_path.name
        or candidate["inventory_path"].split("/")[-1]
    )
    description = (
        str(sidecar.get("description", "")).strip()
        or readme_description
        or str(policy_root.get("rationale", "")).strip()
    )
    repo_name = (
        str(sidecar.get("repo_name", "")).strip()
        or git_meta["repo_name"]
        or filesystem_path.name
    )
    project_key = (
        str(sidecar.get("project_key", "")).strip()
        or git_meta["normalized_remote_url"]
        or f"google-drive:{candidate['inventory_path'].lower()}"
    )
    project_hash = sha256_hex(project_key)

    obsidian_enabled = summary["has_obsidian"]
    git_enabled = git_meta["git"] or summary["has_git"]
    markdown_count = summary["markdown_count"]
    project_type = classify_project_type(git_enabled, obsidian_enabled, markdown_count)
    machine_name = (
        str(sidecar.get("machine_name", "")).strip()
        or str(root_cfg.get("machine_name", "")).strip()
        or platform.node()
        or os.environ.get("COMPUTERNAME", "")
    )

    base_source_label = str(root_cfg.get("label", "google-drive")).strip() or "google-drive"
    root_label = str(candidate["root_label"]).strip()
    source_label = f"{base_source_label}:{root_label}" if root_label else base_source_label

    inventory_path = str(candidate["inventory_path"]).strip()
    path_value = f"{remote_name}:{inventory_path}"
    if safe_path_exists(filesystem_path):
        try:
            path_value = str(filesystem_path.resolve())
        except OSError:
            path_value = f"{remote_name}:{inventory_path}"

    include_reasons = list(summary["reasons"])
    include_reasons.extend(
        [
            f"policy-root:{root_label}",
            f"policy-treatment:{policy_root.get('crawl_treatment', '')}",
            f"candidate-kind:{candidate['candidate_kind']}",
        ]
    )
    if sidecar.get("_sidecar_error"):
        include_reasons.append("sidecar-error")

    tags = normalize_tags(sidecar.get("tags"))
    tags = normalize_tags(tags + ["google-drive", root_label, str(policy_root.get("root_class", ""))])

    return {
        "project_hash": project_hash,
        "project_key": project_key,
        "name": display_name,
        "project_type": project_type,
        "source_label": source_label,
        "machine_name": machine_name,
        "storage_scope": storage_scope,
        "shared": shared,
        "git": git_enabled,
        "obsidian": obsidian_enabled,
        "repo_name": repo_name,
        "remote_url": git_meta["remote_url"],
        "canonical_url": canonical_url,
        "path": path_value,
        "path_from_root": inventory_path,
        "readme_path": str(readme_path.resolve()) if readme_path else "",
        "readme_link_md": markdown_link(readme_path, output_dir, "README") if readme_path else "",
        "path_link_md": "",
        "last_touch_at": isoformat_from_ts(summary["latest_ts"]),
        "head_branch": git_meta["head_branch"],
        "head_commit": git_meta["head_commit"],
        "head_commit_at": git_meta["head_commit_at"],
        "last_remote_ref_at": git_meta["last_remote_ref_at"],
        "last_push_at": str(sidecar.get("last_push_at", "")).strip(),
        "markdown_file_count": markdown_count,
        "obsidian_note_count": markdown_count if obsidian_enabled else 0,
        "tree_scan_truncated": summary["truncated"],
        "readme_sha256": readme_sha256,
        "include_reason": ", ".join(sorted(set(part for part in include_reasons if part))),
        "status": str(sidecar.get("status", "")).strip(),
        "tags": tags,
        "description": description,
        "next_step": str(sidecar.get("next_step", "")).strip(),
        "last_session_at": str(sidecar.get("last_session_at", "")).strip(),
        "last_session_summary": str(sidecar.get("last_session_summary", "")).strip(),
        "sidecar_path": str(sidecar_path.resolve()) if sidecar_path else "",
    }