"""Git source extraction.

Read-only git interrogation of a candidate directory: repository root resolution,
origin remote URL, HEAD branch/commit, relative recency signals. Suppresses
non-zero-exit and timeout output so a bad repo yields empty fields, not a crash.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..models import normalize_remote_url, repo_name_from_remote


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