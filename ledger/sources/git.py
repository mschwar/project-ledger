"""Git source extraction and working-tree drift sensing.

Read-only git interrogation of a candidate directory: repository root resolution,
origin remote URL, HEAD branch/commit, relative recency signals, and upstream
ahead/behind/dirty working-tree drift warnings for Reality Provider consumers
(``machina``, ``homelab``, ``Hermes``). Suppresses non-zero-exit and timeout
output so a bad repo yields empty/unknown fields, not a crash.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..models import normalize_remote_url, repo_name_from_remote
from .common import safe_path_exists


def git_output(directory: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(directory), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _parse_ahead_behind(raw: str) -> tuple[int | None, int | None]:
    parts = raw.strip().split()
    if len(parts) != 2:
        return None, None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None, None


def _build_drift_warnings(
    *,
    head_branch: str | None,
    upstream_ref: str | None,
    ahead_count: int | None,
    behind_count: int | None,
    main_branch: str | None = None,
    main_behind_count: int | None = None,
    dirty: bool | None = None,
    location: str | None = None,
) -> tuple[str, list[str], list[str]]:
    branch_label = head_branch or "HEAD"
    up_label = upstream_ref or "origin/main"
    warning_codes: list[str] = []
    warnings: list[str] = []

    if behind_count is not None and behind_count > 0:
        warning_codes.append("GIT_BEHIND_UPSTREAM")
        noun = "commit" if behind_count == 1 else "commits"
        warnings.append(f"warning: local {branch_label} is {behind_count} {noun} behind {up_label}")
    elif main_behind_count is not None and main_behind_count > 0 and main_branch:
        warning_codes.append("GIT_MAIN_BEHIND_ORIGIN")
        noun = "commit" if main_behind_count == 1 else "commits"
        warnings.append(
            f"warning: local {main_branch} is {main_behind_count} {noun} behind origin/{main_branch}"
        )

    if ahead_count is not None and ahead_count > 0:
        warning_codes.append("GIT_AHEAD_OF_UPSTREAM")
        noun = "commit" if ahead_count == 1 else "commits"
        warnings.append(f"warning: local {branch_label} is {ahead_count} {noun} ahead of {up_label}")

    if head_branch == "HEAD":
        warning_codes.append("GIT_DETACHED_HEAD")
        warnings.append("warning: local checkout is in detached HEAD state")

    if dirty:
        warning_codes.append("GIT_DIRTY_WORKTREE")
        loc_suffix = f" at {location}" if location else ""
        warnings.append(f"warning: local working tree{loc_suffix} has uncommitted changes")

    if head_branch == "HEAD":
        state = "detached"
    elif (ahead_count or 0) > 0 and (behind_count or 0) > 0:
        state = "diverged"
    elif (behind_count or 0) > 0 or (main_behind_count or 0) > 0:
        state = "behind"
    elif (ahead_count or 0) > 0:
        state = "ahead"
    elif dirty:
        state = "dirty"
    elif upstream_ref is None:
        state = "no_upstream"
    else:
        state = "clean"

    return state, warning_codes, warnings


def inspect_git_worktree_state(directory: Path) -> dict:
    """Inspect ahead/behind upstream drift and working-tree dirtiness in read-only mode."""
    if not safe_path_exists(directory / ".git"):
        return {
            "state": "not_applicable",
            "head_branch": None,
            "head_commit": None,
            "upstream_ref": None,
            "ahead_count": None,
            "behind_count": None,
            "main_behind_count": None,
            "dirty": None,
            "warning_codes": [],
            "warnings": [],
        }

    head_branch = git_output(directory, "rev-parse", "--abbrev-ref", "HEAD") or None
    head_commit = git_output(directory, "rev-parse", "HEAD") or None

    upstream_ref = git_output(
        directory, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"
    ) or None
    if not upstream_ref and head_branch and head_branch != "HEAD":
        if git_output(directory, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{head_branch}"):
            upstream_ref = f"origin/{head_branch}"
    if not upstream_ref:
        for default_branch in ("main", "master"):
            if git_output(
                directory, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{default_branch}"
            ):
                upstream_ref = f"origin/{default_branch}"
                break

    ahead_count: int | None = None
    behind_count: int | None = None
    if upstream_ref:
        counts = git_output(directory, "rev-list", "--left-right", "--count", f"HEAD...{upstream_ref}")
        ahead_count, behind_count = _parse_ahead_behind(counts)

    main_branch: str | None = None
    main_behind_count: int | None = None
    if head_branch not in {"main", "master"}:
        for candidate_main in ("main", "master"):
            has_local = git_output(
                directory, "rev-parse", "--verify", "--quiet", f"refs/heads/{candidate_main}"
            )
            has_remote = git_output(
                directory, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{candidate_main}"
            )
            if has_local and has_remote:
                counts = git_output(
                    directory,
                    "rev-list",
                    "--left-right",
                    "--count",
                    f"refs/heads/{candidate_main}...refs/remotes/origin/{candidate_main}",
                )
                _, m_behind = _parse_ahead_behind(counts)
                if m_behind is not None:
                    main_branch = candidate_main
                    main_behind_count = m_behind
                break

    porcelain = git_output(directory, "status", "--porcelain")
    dirty = bool(porcelain)

    state, warning_codes, warnings = _build_drift_warnings(
        head_branch=head_branch,
        upstream_ref=upstream_ref,
        ahead_count=ahead_count,
        behind_count=behind_count,
        main_branch=main_branch,
        main_behind_count=main_behind_count,
        dirty=dirty,
        location=str(directory),
    )
    return {
        "state": state,
        "head_branch": head_branch,
        "head_commit": head_commit,
        "upstream_ref": upstream_ref,
        "ahead_count": ahead_count,
        "behind_count": behind_count,
        "main_behind_count": main_behind_count,
        "dirty": dirty,
        "warning_codes": warning_codes,
        "warnings": warnings,
    }


def resolve_observation_git_drift(observation: dict, compat: dict) -> dict:
    """Resolve git working-tree drift for an observation from explicit fixture fields or live `.git`."""
    git_enabled = bool(compat.get("git"))
    if not git_enabled:
        return {
            "state": "not_applicable",
            "head_branch": None,
            "head_commit": None,
            "upstream_ref": None,
            "ahead_count": None,
            "behind_count": None,
            "main_behind_count": None,
            "dirty": None,
            "warning_codes": [],
            "warnings": [],
        }

    location = str(observation.get("location") or compat.get("path") or "").strip() or None
    head_branch = str(compat.get("head_branch") or "").strip() or None
    head_commit = str(compat.get("head_commit") or "").strip() or None

    # Allow deterministic unit-test fixtures to supply explicit drift fields in compat_entry.
    has_explicit_drift = any(
        key in compat
        for key in ("behind_count", "ahead_count", "upstream_ref", "dirty", "main_behind_count")
    )
    if has_explicit_drift:
        upstream_ref = str(compat.get("upstream_ref") or "").strip() or "origin/main"
        ahead = int(compat["ahead_count"]) if compat.get("ahead_count") is not None else 0
        behind = int(compat["behind_count"]) if compat.get("behind_count") is not None else 0
        main_behind = (
            int(compat["main_behind_count"]) if compat.get("main_behind_count") is not None else None
        )
        dirty = bool(compat["dirty"]) if compat.get("dirty") is not None else False
        state, warning_codes, warnings = _build_drift_warnings(
            head_branch=head_branch,
            upstream_ref=upstream_ref,
            ahead_count=ahead,
            behind_count=behind,
            main_branch="main" if main_behind is not None else None,
            main_behind_count=main_behind,
            dirty=dirty,
            location=location,
        )
        return {
            "state": state,
            "head_branch": head_branch,
            "head_commit": head_commit,
            "upstream_ref": upstream_ref,
            "ahead_count": ahead,
            "behind_count": behind,
            "main_behind_count": main_behind,
            "dirty": dirty,
            "warning_codes": warning_codes,
            "warnings": warnings,
        }

    if location and location.startswith("/"):
        candidate = Path(location)
        if safe_path_exists(candidate / ".git"):
            return inspect_git_worktree_state(candidate)

    return {
        "state": "unknown",
        "head_branch": head_branch,
        "head_commit": head_commit,
        "upstream_ref": None,
        "ahead_count": None,
        "behind_count": None,
        "main_behind_count": None,
        "dirty": None,
        "warning_codes": [],
        "warnings": [],
    }


def gather_git_metadata(directory: Path) -> dict:
    if not safe_path_exists(directory / ".git"):
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
