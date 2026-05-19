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
        if candidate.exists():
            return candidate
    return None


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


def inspect_candidate(directory: Path, ignore_names: set[str]) -> dict:
    readme_path = find_first_existing(directory, README_CANDIDATES)
    sidecar_path = find_first_existing(directory, SIDECAR_CANDIDATES)
    has_git = (directory / ".git").exists()
    has_obsidian = (directory / ".obsidian").is_dir()

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
    for child in sorted(root_path.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir() or child.name in exclude_names:
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
            if dirname in exclude_names or candidate.is_symlink():
                continue
            if depth + 1 > max_depth:
                continue
            filtered_dirs.append(dirname)
        dirs[:] = filtered_dirs

        if ".git" in dirs or (root_path_current / ".git").exists():
            results.append(root_path_current)
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
        else:
            raise ValueError(f"Unsupported discovery mode: {discovery}")

        for candidate in candidates:
            resolved = str(candidate.resolve()).lower()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            entry = build_entry(candidate, root_cfg, defaults, output_dir)
            if entry:
                entries.append(entry)

    entries.sort(key=lambda item: (item["last_touch_at"], item["name"].lower()), reverse=True)
    return entries


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


def write_markdown(entries: list[dict], output_path: Path) -> None:
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
        "",
        "> `Last Push` uses `last_push_at` from the sidecar when available; otherwise it falls back to the newest local remote-ref timestamp.",
        "",
        "| Name | Type | Scope | Git | Obsidian | Last Touch | README | Location | Repo | Last Push |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

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

    csv_path = output_dir / "projects.csv"
    json_path = output_dir / "projects.json"
    md_path = output_dir / "projects.md"

    write_csv(entries, csv_path)
    write_json(entries, json_path, config_path)
    write_markdown(entries, md_path)

    print(f"Wrote {len(entries)} entries")
    print(f"  CSV:  {csv_path}")
    print(f"  JSON: {json_path}")
    print(f"  MD:   {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
