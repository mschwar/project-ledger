"""Git source extraction and working-tree drift sensing.

Read-only git interrogation of a candidate directory: repository root resolution,
origin remote URL, HEAD branch/commit, relative recency signals, and upstream
ahead/behind/dirty working-tree drift warnings for Reality Provider consumers
(``machina``, ``homelab``, ``Hermes``). Suppresses non-zero-exit and timeout
output so a bad repo yields empty/unknown fields, not a crash.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from ..models import normalize_remote_url, repo_name_from_remote
from .common import safe_path_exists, safe_path_is_dir

DEFAULT_REMOTE_TIMEOUT_SECONDS = 3.0
_HEX_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_GIT_SECTION_RE = re.compile(r'^\[\s*([A-Za-z0-9_-]+)(?:\s+"((?:\\.|[^"\\])*)")?\s*\]\s*$')
_GIT_KV_RE = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*(?:=\s*(.*?))?\s*$")


def _resolve_git_common_config(directory: Path) -> Path | None:
    """Resolve the repository's `.git/config` (including linked worktree `commondir`) without subprocesses."""
    git_marker = directory / ".git"
    if not safe_path_exists(git_marker):
        return None
    if safe_path_is_dir(git_marker):
        cfg = git_marker / "config"
        return cfg if safe_path_exists(cfg) else None
    try:
        raw = git_marker.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not raw.lower().startswith("gitdir:"):
        return None
    gitdir_text = raw.split(":", 1)[1].strip()
    if not gitdir_text:
        return None
    git_dir = Path(gitdir_text)
    if not git_dir.is_absolute():
        git_dir = (directory / git_dir).resolve()
    common_dir = git_dir
    commondir_file = git_dir / "commondir"
    if safe_path_exists(commondir_file):
        try:
            rel_common = commondir_file.read_text(encoding="utf-8", errors="replace").strip()
            if rel_common:
                cand_common = Path(rel_common)
                common_dir = (
                    cand_common if cand_common.is_absolute() else (git_dir / cand_common).resolve()
                )
        except OSError:
            common_dir = git_dir
    cfg = common_dir / "config"
    return cfg if safe_path_exists(cfg) else None


def _strip_git_config_value(raw_val: str | None) -> str:
    if raw_val is None:
        return "true"
    out: list[str] = []
    in_quotes = False
    escaped = False
    for ch in raw_val:
        if escaped:
            out.append(ch)
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_quotes = not in_quotes
            continue
        if not in_quotes and ch in {"#", ";"}:
            break
        out.append(ch)
    return "".join(out).strip()


def list_git_remotes(directory: Path) -> dict[str, str]:
    """Return all configured `{remote_name: url}` entries from `.git/config` in read-only mode."""
    cfg_path = _resolve_git_common_config(directory)
    if cfg_path is None:
        return {}
    try:
        lines = cfg_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    remotes: dict[str, str] = {}
    current_remote: str | None = None
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        sec = _GIT_SECTION_RE.match(stripped)
        if sec:
            sec_type = sec.group(1).lower()
            sec_name = sec.group(2)
            current_remote = sec_name.strip() if sec_type == "remote" and sec_name else None
            continue
        if current_remote:
            kv = _GIT_KV_RE.match(raw_line)
            if kv and kv.group(1).lower() == "url":
                url_val = _strip_git_config_value(kv.group(2))
                if url_val and current_remote not in remotes:
                    remotes[current_remote] = url_val
    return dict(sorted(remotes.items()))


def is_autosnapshot_git_repo(directory: Path) -> bool:
    """Return True when `.git/config` marks the repository with `gbrain.autosnapshot = true`."""
    cfg_path = _resolve_git_common_config(directory)
    if cfg_path is None:
        return False
    try:
        text = cfg_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if "autosnapshot" not in text.lower():
        return False
    in_gbrain = False
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        sec = _GIT_SECTION_RE.match(stripped)
        if sec:
            in_gbrain = sec.group(1).lower() == "gbrain" and not sec.group(2)
            continue
        if in_gbrain:
            kv = _GIT_KV_RE.match(raw_line)
            if kv and kv.group(1).lower() == "autosnapshot":
                val = _strip_git_config_value(kv.group(2)).lower()
                return val in {"true", "yes", "on", "1"}
    return False


def inspect_entry_git_remotes(entry: dict) -> dict[str, str]:
    """Resolve all configured git remotes for a compatibility entry from fixture hint or `.git/config`."""
    explicit = entry.get("_git_remotes")
    if isinstance(explicit, dict):
        return {
            str(k).strip(): str(v).strip()
            for k, v in sorted(explicit.items(), key=lambda kv: str(kv[0]))
            if str(k).strip() and str(v).strip()
        }
    if not entry.get("git"):
        return {}
    raw_path = str(entry.get("path") or "").strip()
    if raw_path.startswith("/"):
        remotes = list_git_remotes(Path(raw_path))
        if remotes:
            return remotes
    remote_url = str(entry.get("remote_url") or "").strip()
    return {"origin": remote_url} if remote_url else {}


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


def _git_is_ancestor(directory: Path, ancestor_sha: str, descendant_ref: str) -> bool:
    if not ancestor_sha or not descendant_ref:
        return False
    try:
        proc = subprocess.run(
            [
                "git",
                "--no-optional-locks",
                "-C",
                str(directory),
                "merge-base",
                "--is-ancestor",
                ancestor_sha,
                descendant_ref,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _probe_remote_ahead(
    directory: Path,
    *,
    head_branch: str | None,
    head_commit: str | None,
    upstream_ref: str | None,
    behind_count: int | None,
    main_behind_count: int | None,
    timeout: float = DEFAULT_REMOTE_TIMEOUT_SECONDS,
) -> tuple[bool, str | None, str | None]:
    """Query ``git ls-remote origin`` with a bounded timeout to detect unfetched upstream commits."""
    remote_url = git_output(directory, "remote", "get-url", "origin")
    if not remote_url:
        return False, None, None

    remote_branch: str | None = None
    if upstream_ref and upstream_ref.startswith("origin/"):
        remote_branch = upstream_ref.split("/", 1)[1].strip() or None
    elif head_branch and head_branch != "HEAD":
        remote_branch = head_branch

    query_refs: list[str] = ["HEAD"]
    for candidate_branch in ([remote_branch] if remote_branch else []) + ["main", "master"]:
        if not candidate_branch:
            continue
        full_ref = f"refs/heads/{candidate_branch}"
        if full_ref not in query_refs:
            query_refs.append(full_ref)

    eff_timeout = max(0.1, float(timeout))
    connect_sec = max(1, int(round(eff_timeout)))
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    env.setdefault("GIT_SSH_COMMAND", f"ssh -o BatchMode=yes -o ConnectTimeout={connect_sec}")

    try:
        proc = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(directory), "ls-remote", "origin", *query_refs],
            capture_output=True,
            text=True,
            check=False,
            timeout=eff_timeout,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, None, None

    if proc.returncode != 0 or not proc.stdout.strip():
        return False, None, None

    refs_map: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        sha, ref_name = parts[0].strip().lower(), parts[1].strip()
        if _HEX_SHA_RE.match(sha):
            refs_map[ref_name] = sha

    if not refs_map:
        return False, None, None

    target_sha: str | None = None
    target_label: str | None = None
    if remote_branch and f"refs/heads/{remote_branch}" in refs_map:
        target_sha = refs_map[f"refs/heads/{remote_branch}"]
        target_label = f"origin/{remote_branch}"
    elif "HEAD" in refs_map:
        target_sha = refs_map["HEAD"]
        target_label = f"origin/{remote_branch}" if remote_branch else "origin/HEAD"
    else:
        for default_branch in ("main", "master"):
            full_ref = f"refs/heads/{default_branch}"
            if full_ref in refs_map:
                target_sha = refs_map[full_ref]
                target_label = f"origin/{default_branch}"
                remote_branch = default_branch
                break

    if target_sha and head_commit and target_sha != head_commit.lower():
        local_track_sha = (
            git_output(directory, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{remote_branch}")
            if remote_branch
            else ""
        ).lower()
        already_counted_behind = bool(
            local_track_sha and local_track_sha == target_sha and (behind_count or 0) > 0
        )
        if not already_counted_behind and not _git_is_ancestor(directory, target_sha, "HEAD"):
            return True, target_sha, target_label

    if head_branch not in {"main", "master"}:
        for candidate_main in ("main", "master"):
            remote_main_sha = refs_map.get(f"refs/heads/{candidate_main}")
            if not remote_main_sha:
                continue
            local_main_sha = git_output(
                directory, "rev-parse", "--verify", "--quiet", f"refs/heads/{candidate_main}"
            ).lower()
            if not local_main_sha or remote_main_sha == local_main_sha:
                continue
            local_origin_main_sha = git_output(
                directory, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{candidate_main}"
            ).lower()
            already_main_behind = bool(
                local_origin_main_sha == remote_main_sha and (main_behind_count or 0) > 0
            )
            if not already_main_behind and not _git_is_ancestor(
                directory, remote_main_sha, f"refs/heads/{candidate_main}"
            ):
                return True, remote_main_sha, f"origin/{candidate_main}"

    return False, target_sha, target_label


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
    remote_ahead: bool = False,
    remote_head_commit: str | None = None,
    remote_ref_label: str | None = None,
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

    if remote_ahead:
        warning_codes.append("GIT_REMOTE_AHEAD")
        rem_label = remote_ref_label or up_label
        sha_suffix = f" ({remote_head_commit[:12]})" if remote_head_commit else ""
        warnings.append(
            f"warning: remote {rem_label}{sha_suffix} has unfetched commits ahead of local {branch_label}"
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
    elif (ahead_count or 0) > 0 and ((behind_count or 0) > 0 or remote_ahead):
        state = "diverged"
    elif remote_ahead and (behind_count or 0) == 0 and (main_behind_count or 0) == 0:
        state = "remote_ahead"
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


def inspect_git_worktree_state(
    directory: Path,
    *,
    check_remote: bool = False,
    remote_timeout: float = DEFAULT_REMOTE_TIMEOUT_SECONDS,
) -> dict:
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

    remote_ahead = False
    remote_head_commit: str | None = None
    remote_ref_label: str | None = None
    if check_remote:
        remote_ahead, remote_head_commit, remote_ref_label = _probe_remote_ahead(
            directory,
            head_branch=head_branch,
            head_commit=head_commit,
            upstream_ref=upstream_ref,
            behind_count=behind_count,
            main_behind_count=main_behind_count,
            timeout=remote_timeout,
        )

    state, warning_codes, warnings = _build_drift_warnings(
        head_branch=head_branch,
        upstream_ref=upstream_ref,
        ahead_count=ahead_count,
        behind_count=behind_count,
        main_branch=main_branch,
        main_behind_count=main_behind_count,
        dirty=dirty,
        location=str(directory),
        remote_ahead=remote_ahead,
        remote_head_commit=remote_head_commit,
        remote_ref_label=remote_ref_label,
    )
    result = {
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
    if check_remote and remote_head_commit is not None:
        result["remote_head_commit"] = remote_head_commit
    return result


def resolve_observation_git_drift(
    observation: dict,
    compat: dict,
    *,
    check_remote: bool = False,
    remote_timeout: float = DEFAULT_REMOTE_TIMEOUT_SECONDS,
) -> dict:
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
            if check_remote:
                return inspect_git_worktree_state(
                    candidate,
                    check_remote=True,
                    remote_timeout=remote_timeout,
                )
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
