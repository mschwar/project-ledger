#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import ntpath
import os
import platform
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

README_CANDIDATES = [
    "README.md",
    "Readme.md",
    "readme.md",
    "README.txt",
    "Readme.txt",
    "readme.txt",
]

SIDECAR_CANDIDATES = [
    ".project-ledger.json",
    "project-ledger.json",
]

CODE_EXTENSIONS = {
    ".c",
    ".cpp",
    ".cs",
    ".go",
    ".java",
    ".js",
    ".jsx",
    ".mjs",
    ".ps1",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".ts",
    ".tsx",
}

SPECIAL_FILES = {
    "AGENTS.md",
    "Cargo.toml",
    "MOC.md",
    "PRD.md",
    "README.md",
    "docker-compose.yml",
    "docker-compose.yaml",
    "environment.yml",
    "go.mod",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
}

DEFAULT_EXCLUDES = {
    ".git",
    ".idea",
    ".mypy_cache",
    ".obsidian",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".vscode",
    "__pycache__",
    "node_modules",
    "venv",
}

CSV_FIELDS = [
    "project_hash",
    "project_key",
    "name",
    "project_type",
    "source_label",
    "machine_name",
    "storage_scope",
    "shared",
    "git",
    "obsidian",
    "repo_name",
    "remote_url",
    "canonical_url",
    "path",
    "path_from_root",
    "readme_path",
    "readme_link_md",
    "path_link_md",
    "last_touch_at",
    "head_branch",
    "head_commit",
    "head_commit_at",
    "last_remote_ref_at",
    "last_push_at",
    "markdown_file_count",
    "obsidian_note_count",
    "tree_scan_truncated",
    "readme_sha256",
    "include_reason",
    "status",
    "tags",
    "description",
    "next_step",
    "last_session_at",
    "last_session_summary",
    "sidecar_path",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a ledger for projects, repos, and Obsidian vaults."
    )
    parser.add_argument(
        "--config",
        default="ledger_config.json",
        help="Path to the ledger config JSON. Defaults to ledger_config.json.",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory for generated CSV/JSON/Markdown outputs. Defaults to output/.",
    )
    return parser.parse_args()


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return data


def resolve_path(raw_path: str, config_dir: Path) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(raw_path))
    path = Path(expanded)
    if not path.is_absolute():
        path = (config_dir / path).resolve()
    return path.resolve()


def isoformat_from_ts(timestamp: float | None) -> str:
    if timestamp is None:
        return ""
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")


def sha256_hex(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def slugify(value: str) -> str:
    lowered = re.sub(r"[^\w\-]+", "-", value.strip().lower())
    lowered = re.sub(r"-{2,}", "-", lowered)
    return lowered.strip("-")


def coerce_bool(value) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return None


def normalize_tags(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        tags = [str(item).strip() for item in value if str(item).strip()]
    else:
        tags = [part.strip() for part in str(value).split(",") if part.strip()]
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        lowered = tag.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(tag)
    return result


def normalize_remote_url(remote_url: str) -> str:
    text = remote_url.strip()
    if not text:
        return ""
    if text.startswith("git@") and ":" in text:
        host_part, repo_part = text.split(":", 1)
        host = host_part.split("@", 1)[1]
        repo = repo_part
    elif "://" in text:
        without_scheme = text.split("://", 1)[1]
        if "@" in without_scheme and without_scheme.split("@", 1)[0].find("/") == -1:
            without_scheme = without_scheme.split("@", 1)[1]
        host, _, repo = without_scheme.partition("/")
    else:
        return text.rstrip("/").removesuffix(".git")
    normalized = f"{host}/{repo}".rstrip("/")
    return normalized.removesuffix(".git")


def repo_name_from_remote(remote_url: str) -> str:
    normalized = normalize_remote_url(remote_url)
    if not normalized:
        return ""
    return normalized.rsplit("/", 1)[-1]


def markdown_target(target: Path, base_dir: Path) -> str:
    target_text = str(target)
    base_text = str(base_dir)
    if re.match(r"^[A-Za-z]:[\\/]", target_text) and re.match(r"^[A-Za-z]:[\\/]", base_text):
        relative = ntpath.relpath(target_text, base_text)
        return quote(relative.replace("\\", "/"), safe="/-_.()")
    relative = os.path.relpath(target, base_dir)
    return quote(Path(relative).as_posix(), safe="/-_.()")


def markdown_link(target: Path | None, base_dir: Path, label: str) -> str:
    if not target:
        return ""
    return f"[{label}]({markdown_target(target, base_dir)})"


def escape_md_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def find_first_existing(directory: Path, candidates: Iterable[str]) -> Path | None:
    for name in candidates:
        candidate = directory / name
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return None


def safe_path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def safe_path_is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def iter_shallow_files(directory: Path, ignore_names: set[str], max_depth: int = 1) -> Iterable[Path]:
    try:
        for child in directory.iterdir():
            if child.name in ignore_names:
                continue
            if child.is_file():
                yield child
                continue
            if child.is_dir() and not child.is_symlink() and max_depth >= 1:
                try:
                    for nested in child.iterdir():
                        if nested.name in ignore_names:
                            continue
                        if nested.is_file():
                            yield nested
                except OSError:
                    continue
    except OSError:
        return


def parse_readme_summary(readme_path: Path | None) -> tuple[str, str]:
    if not readme_path:
        return "", ""
    try:
        lines = readme_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "", ""

    title = ""
    description = ""
    for raw in lines[:80]:
        line = raw.strip()
        if not line or line.startswith("```"):
            continue
        if line.startswith("#"):
            title = line.lstrip("#").strip()
            continue
        if not title:
            title = re.sub(r"[*_`]+", "", line)
            continue
        description = re.sub(r"[*_`]+", "", line)
        break
    return title[:160], description[:240]


def parse_iso_datetime(value: str) -> float | None:
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


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
        if not scoped_records:
            scoped_records = [record for record in root_records if str(record.get("path", "")) == root_name]

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


def inspect_candidate(directory: Path, ignore_names: set[str]) -> dict:
    readme_path = find_first_existing(directory, README_CANDIDATES)
    sidecar_path = find_first_existing(directory, SIDECAR_CANDIDATES)
    has_git = safe_path_exists(directory / ".git")
    has_obsidian = safe_path_is_dir(directory / ".obsidian")

    markdown_files = 0
    code_files = 0
    has_special_file = False
    has_nested_readme = bool(readme_path)

    for file_path in iter_shallow_files(directory, ignore_names):
        name = file_path.name
        suffix = file_path.suffix.lower()
        if name in SPECIAL_FILES:
            has_special_file = True
        if name in README_CANDIDATES:
            has_nested_readme = True
        if suffix == ".md":
            markdown_files += 1
        if suffix in CODE_EXTENSIONS:
            code_files += 1

    score = 0
    reasons: list[str] = []
    if has_git:
        score += 3
        reasons.append("git")
    if has_obsidian:
        score += 3
        reasons.append("obsidian")
    if has_nested_readme:
        score += 2
        reasons.append("readme")
    if has_special_file:
        score += 1
        reasons.append("project-files")
    if markdown_files >= 4:
        score += 1
        reasons.append(f"markdown:{markdown_files}")
    if code_files >= 3:
        score += 1
        reasons.append(f"code:{code_files}")
    if markdown_files >= 1 and code_files >= 1:
        score += 2
        reasons.append("docs+code")
    if sidecar_path:
        score += 2
        reasons.append("sidecar")

    title, description = parse_readme_summary(readme_path)

    return {
        "score": score,
        "reasons": reasons,
        "has_git": has_git,
        "has_obsidian": has_obsidian,
        "readme_path": readme_path,
        "sidecar_path": sidecar_path,
        "markdown_files_shallow": markdown_files,
        "code_files_shallow": code_files,
        "readme_title": title,
        "readme_description": description,
    }


def summarize_tree(
    directory: Path,
    ignore_names: set[str],
    max_entries: int,
    max_depth: int,
) -> dict:
    latest_ts = None
    markdown_count = 0
    scanned_entries = 0
    truncated = False

    try:
        latest_ts = directory.stat().st_mtime
    except OSError:
        latest_ts = None

    for root, dirs, files in os.walk(directory, topdown=True):
        root_path = Path(root)
        try:
            rel_depth = len(root_path.relative_to(directory).parts)
        except ValueError:
            rel_depth = 0

        filtered_dirs = []
        for dirname in dirs:
            candidate = root_path / dirname
            if dirname in ignore_names or candidate.is_symlink():
                continue
            if rel_depth + 1 > max_depth:
                continue
            filtered_dirs.append(dirname)
        dirs[:] = filtered_dirs

        for name in files:
            file_path = root_path / name
            try:
                stat = file_path.stat()
            except OSError:
                continue
            scanned_entries += 1
            if latest_ts is None or stat.st_mtime > latest_ts:
                latest_ts = stat.st_mtime
            if file_path.suffix.lower() == ".md":
                markdown_count += 1
            if scanned_entries >= max_entries:
                truncated = True
                return {
                    "latest_ts": latest_ts,
                    "markdown_count": markdown_count,
                    "scanned_entries": scanned_entries,
                    "truncated": truncated,
                }

    return {
        "latest_ts": latest_ts,
        "markdown_count": markdown_count,
        "scanned_entries": scanned_entries,
        "truncated": truncated,
    }


def infer_storage_scope(directory: Path, canonical_url: str) -> str:
    normalized = str(directory).lower()
    cloud_markers = ("onedrive", "dropbox", "google drive", "drive", "icloud")
    if any(marker in normalized for marker in cloud_markers):
        return "shared"
    if canonical_url:
        return "shared"
    return "local"


def load_sidecar(sidecar_path: Path | None) -> dict:
    if not sidecar_path:
        return {}
    try:
        data = read_json(sidecar_path)
    except Exception as exc:  # noqa: BLE001
        return {"_sidecar_error": str(exc)}
    return data


def git_output(directory: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(directory), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def gather_git_metadata(directory: Path) -> dict:
    if not (directory / ".git").exists():
        return {
            "git": False,
            "repo_root": "",
            "repo_name": "",
            "remote_url": "",
            "normalized_remote_url": "",
            "head_branch": "",
            "head_commit": "",
            "head_commit_at": "",
            "last_remote_ref_at": "",
        }

    repo_root = git_output(directory, "rev-parse", "--show-toplevel")
    remote_url = git_output(directory, "remote", "get-url", "origin")
    head_branch = git_output(directory, "rev-parse", "--abbrev-ref", "HEAD")
    head_commit = git_output(directory, "rev-parse", "HEAD")
    head_commit_at = git_output(directory, "log", "-1", "--format=%cI")
    last_remote_ref_at = git_output(
        directory,
        "for-each-ref",
        "--sort=-committerdate",
        "--count=1",
        "--format=%(committerdate:iso8601-strict)",
        "refs/remotes",
    )

    return {
        "git": True,
        "repo_root": repo_root,
        "repo_name": repo_name_from_remote(remote_url) or Path(repo_root).name,
        "remote_url": remote_url,
        "normalized_remote_url": normalize_remote_url(remote_url),
        "head_branch": head_branch,
        "head_commit": head_commit,
        "head_commit_at": head_commit_at,
        "last_remote_ref_at": last_remote_ref_at,
    }


def classify_project_type(git_enabled: bool, obsidian_enabled: bool, markdown_count: int) -> str:
    if git_enabled and obsidian_enabled:
        return "git+obsidian"
    if git_enabled:
        return "git"
    if obsidian_enabled:
        return "obsidian"
    if markdown_count >= 10:
        return "notes"
    return "directory"


def discover_children(root_path: Path, exclude_names: set[str]) -> list[Path]:
    results: list[Path] = []
    try:
        children = sorted(root_path.iterdir(), key=lambda item: item.name.lower())
    except OSError:
        return results
    for child in children:
        if child.name in exclude_names:
            continue
        if not safe_path_is_dir(child):
            continue
        results.append(child)
    return results


def discover_git_repos(root_path: Path, exclude_names: set[str], max_depth: int) -> list[Path]:
    results: list[Path] = []
    for root, dirs, _files in os.walk(root_path, topdown=True):
        root_path_current = Path(root)
        try:
            depth = len(root_path_current.relative_to(root_path).parts)
        except ValueError:
            depth = 0

        filtered_dirs = []
        for dirname in dirs:
            candidate = root_path_current / dirname
            if dirname in exclude_names:
                continue
            if depth + 1 > max_depth:
                continue
            try:
                if candidate.is_symlink():
                    continue
            except OSError:
                continue
            filtered_dirs.append(dirname)
        dirs[:] = filtered_dirs

        try:
            if ".git" in dirs or safe_path_exists(root_path_current / ".git"):
                results.append(root_path_current)
        except OSError:
            continue
    return sorted(results, key=lambda item: str(item).lower())


def build_entry(
    directory: Path,
    root_cfg: dict,
    defaults: dict,
    output_dir: Path,
) -> dict | None:
    exclude_names = set(DEFAULT_EXCLUDES)
    exclude_names.update(defaults.get("exclude_names", []))
    exclude_names.update(root_cfg.get("exclude_names", []))

    quick = inspect_candidate(directory, exclude_names)
    sidecar = load_sidecar(quick["sidecar_path"])

    force_include_names = set(root_cfg.get("force_include_names", []))
    min_score = int(root_cfg.get("min_score", defaults.get("min_score", 2)))
    must_include = root_cfg.get("discovery") != "children"
    should_include = must_include or directory.name in force_include_names or quick["score"] >= min_score
    if not should_include:
        return None

    git_meta = gather_git_metadata(directory)

    tree_summary = summarize_tree(
        directory,
        ignore_names=exclude_names,
        max_entries=int(root_cfg.get("tree_scan_max_entries", defaults.get("tree_scan_max_entries", 5000))),
        max_depth=int(root_cfg.get("tree_scan_max_depth", defaults.get("tree_scan_max_depth", 8))),
    )

    readme_path = quick["readme_path"]
    title, readme_description = parse_readme_summary(readme_path)
    readme_sha256 = ""
    if readme_path:
        try:
            readme_sha256 = sha256_hex(readme_path.read_bytes())
        except OSError:
            readme_sha256 = ""

    canonical_url = str(sidecar.get("canonical_url", "")).strip() or git_meta["remote_url"]
    storage_scope = (
        str(sidecar.get("storage_scope", "")).strip()
        or str(root_cfg.get("storage_scope", "")).strip()
        or infer_storage_scope(directory, canonical_url)
    )
    shared = coerce_bool(sidecar.get("shared"))
    if shared is None:
        shared = storage_scope.lower() in {"shared", "synced", "remote"}

    display_name = (
        str(sidecar.get("display_name", "")).strip()
        or title
        or directory.name
    )
    description = (
        str(sidecar.get("description", "")).strip()
        or readme_description
        or quick["readme_description"]
    )
    repo_name = (
        str(sidecar.get("repo_name", "")).strip()
        or git_meta["repo_name"]
        or directory.name
    )
    project_key = (
        str(sidecar.get("project_key", "")).strip()
        or git_meta["normalized_remote_url"]
        or slugify(display_name)
    )
    hash_basis = project_key or str(directory.resolve()).lower()
    project_hash = sha256_hex(hash_basis)

    last_push_at = str(sidecar.get("last_push_at", "")).strip()
    obsidian_enabled = quick["has_obsidian"]
    git_enabled = git_meta["git"]
    markdown_count = int(tree_summary["markdown_count"])
    project_type = classify_project_type(git_enabled, obsidian_enabled, markdown_count)
    machine_name = (
        str(sidecar.get("machine_name", "")).strip()
        or str(root_cfg.get("machine_name", "")).strip()
        or platform.node()
        or os.environ.get("COMPUTERNAME", "")
    )

    try:
        path_from_root = directory.relative_to(root_cfg["_resolved_path"]).as_posix()
    except ValueError:
        path_from_root = directory.name

    include_reasons = list(quick["reasons"])
    if sidecar.get("_sidecar_error"):
        include_reasons.append("sidecar-error")
    include_reason = ", ".join(sorted(set(include_reasons)))

    return {
        "project_hash": project_hash,
        "project_key": project_key,
        "name": display_name,
        "project_type": project_type,
        "source_label": root_cfg.get("label", directory.parent.name),
        "machine_name": machine_name,
        "storage_scope": storage_scope,
        "shared": shared,
        "git": git_enabled,
        "obsidian": obsidian_enabled,
        "repo_name": repo_name,
        "remote_url": git_meta["remote_url"],
        "canonical_url": canonical_url,
        "path": str(directory.resolve()),
        "path_from_root": path_from_root,
        "readme_path": str(readme_path.resolve()) if readme_path else "",
        "readme_link_md": markdown_link(readme_path, output_dir, "README") if readme_path else "",
        "path_link_md": markdown_link(directory, output_dir, "Open"),
        "last_touch_at": isoformat_from_ts(tree_summary["latest_ts"]),
        "head_branch": git_meta["head_branch"],
        "head_commit": git_meta["head_commit"],
        "head_commit_at": git_meta["head_commit_at"],
        "last_remote_ref_at": git_meta["last_remote_ref_at"],
        "last_push_at": last_push_at,
        "markdown_file_count": markdown_count,
        "obsidian_note_count": markdown_count if obsidian_enabled else 0,
        "tree_scan_truncated": tree_summary["truncated"],
        "readme_sha256": readme_sha256,
        "include_reason": include_reason,
        "status": str(sidecar.get("status", "")).strip(),
        "tags": normalize_tags(sidecar.get("tags")),
        "description": description,
        "next_step": str(sidecar.get("next_step", "")).strip(),
        "last_session_at": str(sidecar.get("last_session_at", "")).strip(),
        "last_session_summary": str(sidecar.get("last_session_summary", "")).strip(),
        "sidecar_path": str(quick["sidecar_path"].resolve()) if quick["sidecar_path"] else "",
    }


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
    project_hash = sha256_hex(project_key or candidate["path_key"])

    obsidian_enabled = summary["has_obsidian"]
    git_enabled = git_meta["git"] or summary["has_git"]
    markdown_count = int(summary["markdown_count"])
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


def collect_entries(config: dict, config_dir: Path, output_dir: Path) -> list[dict]:
    defaults = config.get("defaults", {})
    roots = config.get("roots", [])
    if not roots:
        raise ValueError("Config must contain at least one root in roots[].")

    entries: list[dict] = []
    seen_paths: set[str] = set()

    for root_cfg_raw in roots:
        root_cfg = dict(root_cfg_raw)
        root_path = resolve_path(root_cfg["path"], config_dir)
        root_cfg["_resolved_path"] = root_path
        if "inventory_jsonl" in root_cfg:
            root_cfg["_inventory_jsonl_path"] = resolve_path(root_cfg["inventory_jsonl"], config_dir)
        if "policy_path" in root_cfg:
            root_cfg["_policy_path"] = resolve_path(root_cfg["policy_path"], config_dir)

        exclude_names = set(DEFAULT_EXCLUDES)
        exclude_names.update(defaults.get("exclude_names", []))
        exclude_names.update(root_cfg.get("exclude_names", []))

        discovery = root_cfg.get("discovery", "children")
        if discovery == "children":
            candidates = discover_children(root_path, exclude_names)
        elif discovery == "git_repos":
            candidates = discover_git_repos(
                root_path,
                exclude_names=exclude_names,
                max_depth=int(root_cfg.get("max_depth", defaults.get("git_repo_max_depth", 6))),
            )
        elif discovery == "self":
            candidates = [root_path]
        elif discovery == "inventory_policy":
            candidates = discover_inventory_policy_candidates(root_cfg, exclude_names)
        else:
            raise ValueError(f"Unsupported discovery mode: {discovery}")

        for candidate in candidates:
            if isinstance(candidate, dict):
                resolved = str(candidate.get("path_key", candidate.get("inventory_path", ""))).lower()
                if resolved in seen_paths:
                    continue
                seen_paths.add(resolved)
                entry = build_inventory_entry(candidate, root_cfg, defaults, output_dir)
            else:
                resolved = str(candidate.resolve()).lower()
                if resolved in seen_paths:
                    continue
                seen_paths.add(resolved)
                entry = build_entry(candidate, root_cfg, defaults, output_dir)
            if entry:
                entries.append(entry)

    entries.sort(key=lambda item: (item["last_touch_at"], item["name"].lower()), reverse=True)
    return entries


def summarize_roots(config: dict, config_dir: Path) -> list[dict]:
    summaries: list[dict] = []
    for root_cfg_raw in config.get("roots", []):
        root_cfg = dict(root_cfg_raw)
        root_path = resolve_path(root_cfg["path"], config_dir)
        summaries.append(
            {
                "label": str(root_cfg.get("label", "")).strip() or root_path.name,
                "path": str(root_path),
                "discovery": str(root_cfg.get("discovery", "children")).strip() or "children",
                "exists": safe_path_exists(root_path),
            }
        )
    return summaries


def write_csv(entries: list[dict], output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for entry in entries:
            row = dict(entry)
            row["tags"] = "|".join(entry.get("tags", []))
            writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})


def write_json(entries: list[dict], output_path: Path, config_path: Path) -> None:
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "config_path": str(config_path.resolve()),
        "entry_count": len(entries),
        "entries": entries,
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def render_location_cell(entry: dict, markdown_path: Path) -> str:
    if entry["canonical_url"]:
        return f"[url]({entry['canonical_url']})"
    target = Path(entry["path"])
    return markdown_link(target, markdown_path.parent, escape_md_cell(Path(entry["path"]).name))


def render_last_push(entry: dict) -> str:
    return entry["last_push_at"] or entry["last_remote_ref_at"]


def write_markdown(entries: list[dict], output_path: Path, root_summaries: list[dict] | None = None) -> None:
    git_count = sum(1 for entry in entries if entry["git"])
    obsidian_count = sum(1 for entry in entries if entry["obsidian"])
    shared_count = sum(1 for entry in entries if entry["shared"])

    lines = [
        "# Project Ledger",
        "",
        f"Generated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        "",
        f"- Entries: {len(entries)}",
        f"- Git repos: {git_count}",
        f"- Obsidian vaults: {obsidian_count}",
        f"- Shared/synced: {shared_count}",
        "- Refresh notes: live recency data refreshed from configured roots.",
        "",
        "> `Last Push` uses `last_push_at` from the sidecar when available; otherwise it falls back to the newest local remote-ref timestamp.",
        "",
    ]

    if root_summaries is not None:
        mirror_roots = [root for root in root_summaries if "/central/registry/mirrors/matthews-macbook-air-2/" in root["path"]]
        missing_roots = [root for root in root_summaries if not root["exists"]]
        lines.extend([
            "## Scan coverage and gaps",
            "",
        ])
        if mirror_roots:
            lines.append("**MacBook mirror roots scanned this refresh:**")
            for root in mirror_roots:
                lines.append(f"- `{root['label']}` — `{root['path']}`")
            lines.append("")
        lines.append("**Configured roots:**")
        for root in root_summaries:
            status = "missing" if not root["exists"] else "present"
            lines.append(f"- `{root['label']}` — `{root['path']}` — {status} ({root['discovery']})")
        lines.append("")
        if missing_roots:
            lines.append("**Known gaps / inaccessible roots:**")
            for root in missing_roots:
                lines.append(f"- `{root['path']}` ({root['label']})")
            lines.append("")
        else:
            lines.append("**Known gaps / inaccessible roots:** none detected in configured scan roots.")
            lines.append("")

    lines.extend([
        "| Name | Type | Scope | Git | Obsidian | Last Touch | README | Location | Repo | Last Push |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ])

    for entry in entries:
        location_cell = render_location_cell(entry, output_path)
        last_push = render_last_push(entry)
        readme_cell = entry["readme_link_md"] or ""
        row = [
            escape_md_cell(entry["name"]),
            escape_md_cell(entry["project_type"]),
            escape_md_cell(entry["storage_scope"]),
            "yes" if entry["git"] else "",
            "yes" if entry["obsidian"] else "",
            escape_md_cell(entry["last_touch_at"]),
            readme_cell,
            location_cell,
            escape_md_cell(entry["repo_name"]),
            escape_md_cell(last_push),
        ]
        lines.append("| " + " | ".join(row) + " |")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_markdown_mirror(source_path: Path, mirror_path: Path) -> None:
    mirror_path.parent.mkdir(parents=True, exist_ok=True)
    mirror_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")


def ensure_output_dir(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def main() -> int:
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    config_path = resolve_path(args.config, script_dir)
    output_dir = ensure_output_dir(resolve_path(args.output_dir, script_dir))
    config = read_json(config_path)
    entries = collect_entries(config, config_path.parent, output_dir)
    root_summaries = summarize_roots(config, config_path.parent)

    csv_path = output_dir / "projects.csv"
    json_path = output_dir / "projects.json"
    md_path = output_dir / "projects.md"

    write_csv(entries, csv_path)
    write_json(entries, json_path, config_path)
    write_markdown(entries, md_path, root_summaries)

    mirror_path = script_dir / "docs" / "ledgers" / "projects-ledger.md"
    write_markdown_mirror(md_path, mirror_path)

    print(f"Wrote {len(entries)} entries")
    print(f"  CSV:  {csv_path}")
    print(f"  JSON: {json_path}")
    print(f"  MD:   {md_path}")
    print(f"  Mirror: {mirror_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
