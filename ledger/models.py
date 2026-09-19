"""Normalization and model helpers for Project Ledger compatibility observations.

This module holds the pure, deterministic helpers that appeared in the original
``build_ledger.py`` monolith: string/URL/timestamp/boolean/tag normalization,
markdown rendering primitives, project-type classification, and the compatibility
observation field list. These are read-only transforms (no filesystem/git side
effects) and must not decide canonical identity.
"""

from __future__ import annotations

import hashlib
import ntpath
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

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


def sha256_hex(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def isoformat_from_ts(timestamp: float | None) -> str:
    if timestamp is None:
        return ""
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")


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