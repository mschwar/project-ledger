"""Shared source-adapter helpers and constants.

These are the filesystem-safety primitives and candidate constants that both the
filesystem adapter and the inventory-policy adapter rely on. Nothing here decides
canonical identity.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Iterable

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


def find_first_existing(directory: Path, candidates: Iterable[str]) -> Path | None:
    for name in candidates:
        candidate = directory / name
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return None


def merge_exclude_names(root_cfg: dict, defaults: dict) -> set[str]:
    exclude_names = set(DEFAULT_EXCLUDES)
    exclude_names.update(defaults.get("exclude_names", []))
    exclude_names.update(root_cfg.get("exclude_names", []))
    return exclude_names


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
        return read_json(sidecar_path)
    except Exception as exc:  # noqa: BLE001
        return {"_sidecar_error": str(exc)}


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