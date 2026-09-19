"""Filesystem source adapter.

Discovers project candidates on the local filesystem via one of three strategies
(children / git_repos / self), inspects a candidate for evidence, summarizes its
tree, and builds a compatibility observation. Read-only toward the scanned tree.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

from ..models import (
    classify_project_type,
    coerce_bool,
    isoformat_from_ts,
    markdown_link,
    normalize_tags,
    sha256_hex,
    slugify,
)
from .common import (
    CODE_EXTENSIONS,
    README_CANDIDATES,
    SIDECAR_CANDIDATES,
    SPECIAL_FILES,
    find_first_existing,
    infer_storage_scope,
    iter_shallow_files,
    load_sidecar,
    merge_exclude_names,
    parse_readme_summary,
    safe_path_exists,
    safe_path_is_dir,
)
from .git import gather_git_metadata


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


def build_entry(
    directory: Path,
    root_cfg: dict,
    defaults: dict,
    output_dir: Path,
) -> dict | None:
    exclude_names = merge_exclude_names(root_cfg, defaults)

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