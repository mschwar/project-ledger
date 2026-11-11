"""GitHub remote inventory discovery, SSH checkout sync, and 1:1 git parity audit.

Closes the remote -> local -> Project Ledger discovery loop while preserving
Law 10 (read-only sensing in `compile`/`refresh`) and Law 11 (local, explicit
degraded-mode containment when GitHub is unreachable).

Key invariants:
- All git network operations (`clone`, `fetch`) run over SSH (`git@github.com:...`
  and `-c url.git@github.com:.insteadOf=https://github.com/`) with
  `GIT_TERMINAL_PROMPT=0` so HTTPS credential helpers (e.g. uncached 1Password
  `homelab-op` wrappers) are never triggered per repo.
- GitHub REST API inventory (`user/repos`) resolves tokens from environment, a
  `0600` user-runtime token cache (6h TTL), `gh auth token`, or an optional
  `INV-FLEET-GAME-01`-guarded SSH peer `gh api` fallback without persisting or
  logging secrets.
- Archived remote repos are never auto-cloned when absent locally.
- Fast-forward (`git merge --ff-only`) runs ONLY on `origin` checkouts whose
  working tree and index are completely clean, whose `HEAD` branch equals the
  remote default branch, and whose `HEAD` is a strict ancestor of
  `origin/<default_branch>`. Dirty, WIP-branch, ahead, or diverged checkouts
  are fetched only so `ledger refresh` surfaces accurate drift warnings.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import COMPILER_VERSION, GITHUB_SYNC_SCHEMA_VERSION
from .config import classify_source, read_config, resolve_config_path, validate_config
from .identity import load_canonical_projects, normalize_remote_identity
from .models import atomic_write_text
from .sources.base import get_adapter
from .sources.common import merge_exclude_names, safe_path_exists, safe_path_is_file
from .sources.filesystem import is_shadowed_symlink
from .sources.git import is_autosnapshot_git_repo, list_git_remotes

GITHUB_SYNC_FILENAME = "github-sync.json"
DEFAULT_CLONE_ROOT = "/central/repos/active"
DEFAULT_TOKEN_CACHE_TTL_SECONDS = 21600
DEFAULT_FLEET_PULSE_PATH = Path("/run/user/1000/homelab/fleet-pulse.json")


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _default_token_cache_path() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if runtime_dir:
        base = Path(runtime_dir) / "project-ledger"
    else:
        base = Path.home() / ".cache" / "project-ledger"
    return base / "gh-token.json"


def _read_cached_token(cache_path: Path, ttl_seconds: int) -> str | None:
    if ttl_seconds <= 0 or not safe_path_is_file(cache_path):
        return None
    try:
        st = cache_path.stat()
        if (st.st_mode & 0o077) != 0:
            return None
        doc = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(doc, dict):
        return None
    token = str(doc.get("token") or "").strip()
    fetched_at = doc.get("fetched_at_epoch")
    if not token or not isinstance(fetched_at, (int, float)):
        return None
    if (time.time() - float(fetched_at)) > ttl_seconds:
        return None
    return token


def _write_cached_token(cache_path: Path, token: str) -> None:
    if not token.strip():
        return
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(cache_path.parent, 0o700)
        tmp_path = cache_path.parent / f".{cache_path.name}.tmp.{os.getpid()}"
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"token": token.strip(), "fetched_at_epoch": time.time()}, fh)
            fh.write("\n")
        os.replace(tmp_path, cache_path)
    except OSError:
        pass


def _parse_next_link(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        segments = [seg.strip() for seg in part.split(";")]
        if len(segments) < 2:
            continue
        url_part = segments[0]
        if any(seg == 'rel="next"' for seg in segments[1:]):
            if url_part.startswith("<") and url_part.endswith(">"):
                return url_part[1:-1]
    return None


def _normalize_repo_record(raw: dict[str, Any]) -> dict[str, Any] | None:
    full_name = str(raw.get("full_name") or "").strip().strip("/")
    if not full_name or "/" not in full_name:
        return None
    owner_login, repo_name = full_name.split("/", 1)
    name = str(raw.get("name") or repo_name).strip() or repo_name
    ssh_url = str(raw.get("ssh_url") or "").strip() or f"git@github.com:{full_name}.git"
    default_branch = str(raw.get("default_branch") or "main").strip() or "main"
    pushed_at = str(raw.get("pushed_at") or "").strip() or None
    return {
        "name": name,
        "full_name": full_name,
        "owner": owner_login,
        "private": bool(raw.get("private", False)),
        "fork": bool(raw.get("fork", False)),
        "archived": bool(raw.get("archived", False)),
        "default_branch": default_branch,
        "pushed_at": pushed_at,
        "ssh_url": ssh_url,
    }


def _fetch_via_github_api(
    token: str,
    *,
    affiliation: str = "owner,collaborator,organization_member",
    timeout: float = 15.0,
) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"per_page": 100, "affiliation": affiliation})
    next_url: str | None = f"https://api.github.com/user/repos?{query}"
    repos: list[dict[str, Any]] = []
    pages = 0
    while next_url and pages < 50:
        pages += 1
        req = urllib.request.Request(
            next_url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "project-ledger-github-sync",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            link_header = resp.headers.get("Link")
        page_items = json.loads(body)
        if not isinstance(page_items, list):
            raise ValueError("GitHub /user/repos response was not a JSON array.")
        for item in page_items:
            if isinstance(item, dict):
                norm = _normalize_repo_record(item)
                if norm is not None:
                    repos.append(norm)
        next_url = _parse_next_link(link_header)
    return repos


def _check_fleet_pulse_allows_ssh(
    target: str,
    *,
    pulse_path: Path = DEFAULT_FLEET_PULSE_PATH,
) -> tuple[bool, str | None]:
    """Enforce INV-FLEET-GAME-01 before dispatching any SSH fallback command."""
    host_part = target.split("@", 1)[-1].strip().lower()
    if not host_part:
        return False, "empty_ssh_fallback_target"
    if not safe_path_is_file(pulse_path):
        return True, None
    try:
        pulse = json.loads(pulse_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "fleet_pulse_unreadable"
    nodes = pulse.get("nodes") or pulse.get("hosts") or {}
    if not isinstance(nodes, dict):
        return True, None

    host_aliases = {
        "100.82.59.43": "mac",
        "100.81.51.72": "iz",
        "100.116.7.73": "thing1",
        "100.99.233.15": "matty-pc",
    }
    node_key = host_aliases.get(host_part, host_part.removesuffix("-lan"))
    node_info = nodes.get(node_key)
    if not isinstance(node_info, dict):
        return True, None
    occupancy = str(node_info.get("occupancy_state") or "").upper()
    if bool(node_info.get("gaming_active")) or occupancy in {"GAMING", "HUMAN_HEAVY"}:
        return False, f"blocked_by_gaming_guard:{node_key}:{occupancy or 'GAMING'}"
    if node_info.get("dispatchable") is False:
        return False, f"node_not_dispatchable:{node_key}"
    return True, None


def _fetch_via_ssh_gh_fallback(
    ssh_target: str,
    *,
    affiliation: str = "owner,collaborator,organization_member",
    timeout: float = 20.0,
    pulse_path: Path = DEFAULT_FLEET_PULSE_PATH,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    allowed, block_reason = _check_fleet_pulse_allows_ssh(ssh_target, pulse_path=pulse_path)
    if not allowed:
        return None, block_reason

    ssh_cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=6",
    ]
    default_key = Path.home() / ".ssh" / "id_ed25519_github_ai_server"
    if safe_path_is_file(default_key):
        ssh_cmd.extend(["-i", str(default_key), "-o", "IdentitiesOnly=yes"])
    remote_script = (
        'export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"; '
        f'gh api "user/repos?per_page=100&affiliation={affiliation}" --paginate '
        "--jq '.[] | {name, full_name, private, fork, archived, default_branch, pushed_at, ssh_url}'"
    )
    ssh_cmd.extend([ssh_target, remote_script])
    try:
        proc = subprocess.run(
            ssh_cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"ssh_fallback_failed:{exc}"
    if proc.returncode != 0:
        stderr_line = (proc.stderr or "").strip().splitlines()
        msg = stderr_line[-1] if stderr_line else f"exit_{proc.returncode}"
        return None, f"ssh_fallback_failed:{msg}"

    repos: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            return None, f"ssh_fallback_invalid_json:{exc}"
        if isinstance(raw, dict):
            norm = _normalize_repo_record(raw)
            if norm is not None:
                repos.append(norm)
    return repos, None


def fetch_github_repo_inventory(
    *,
    inventory_file: Path | None = None,
    affiliation: str = "owner,collaborator,organization_member",
    token_cache_path: Path | None = None,
    token_cache_ttl_seconds: int = DEFAULT_TOKEN_CACHE_TTL_SECONDS,
    ssh_fallback_host: str | None = None,
    timeout: float = 15.0,
    pulse_path: Path = DEFAULT_FLEET_PULSE_PATH,
) -> tuple[list[dict[str, Any]], str, str | None]:
    """Return `(repos, inventory_source, error_detail)`."""
    if inventory_file is not None:
        resolved = inventory_file.resolve()
        raw_doc = json.loads(resolved.read_text(encoding="utf-8"))
        raw_items = raw_doc.get("repos") if isinstance(raw_doc, dict) else raw_doc
        if not isinstance(raw_items, list):
            raise ValueError(f"GitHub inventory file must contain a JSON array: {resolved}")
        repos = []
        for item in raw_items:
            if isinstance(item, dict):
                norm = _normalize_repo_record(item)
                if norm is not None:
                    repos.append(norm)
        repos.sort(key=lambda r: str(r["full_name"]).casefold())
        return repos, "inventory_file", None

    errors: list[str] = []
    env_token = (
        os.environ.get("PROJECT_LEDGER_GITHUB_TOKEN", "").strip()
        or os.environ.get("GH_TOKEN", "").strip()
        or os.environ.get("GITHUB_TOKEN", "").strip()
    )
    if env_token:
        try:
            repos = _fetch_via_github_api(env_token, affiliation=affiliation, timeout=timeout)
            repos.sort(key=lambda r: str(r["full_name"]).casefold())
            return repos, "env_token", None
        except (OSError, ValueError, urllib.error.URLError) as exc:
            errors.append(f"env_token:{exc}")

    cache_file = token_cache_path or _default_token_cache_path()
    cached_token = _read_cached_token(cache_file, token_cache_ttl_seconds)
    if cached_token:
        try:
            repos = _fetch_via_github_api(cached_token, affiliation=affiliation, timeout=timeout)
            repos.sort(key=lambda r: str(r["full_name"]).casefold())
            return repos, "cached_token", None
        except (OSError, ValueError, urllib.error.URLError) as exc:
            errors.append(f"cached_token:{exc}")

    try:
        gh_proc = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        cli_token = (gh_proc.stdout or "").strip() if gh_proc.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        cli_token = ""
        errors.append(f"gh_cli_exec:{exc}")

    if cli_token:
        try:
            repos = _fetch_via_github_api(cli_token, affiliation=affiliation, timeout=timeout)
            _write_cached_token(cache_file, cli_token)
            repos.sort(key=lambda r: str(r["full_name"]).casefold())
            return repos, "gh_cli", None
        except (OSError, ValueError, urllib.error.URLError) as exc:
            errors.append(f"gh_cli_api:{exc}")
    else:
        errors.append("gh_cli_token_unavailable")

    eff_fallback = (
        ssh_fallback_host
        if ssh_fallback_host is not None
        else os.environ.get("PROJECT_LEDGER_GH_SSH_FALLBACK", "")
    ).strip()
    if eff_fallback:
        ssh_repos, ssh_err = _fetch_via_ssh_gh_fallback(
            eff_fallback,
            affiliation=affiliation,
            timeout=max(timeout, 20.0),
            pulse_path=pulse_path,
        )
        if ssh_repos is not None:
            ssh_repos.sort(key=lambda r: str(r["full_name"]).casefold())
            return ssh_repos, "ssh_fallback", None
        if ssh_err:
            errors.append(ssh_err)

    return [], "unavailable", "; ".join(errors) if errors else "no_github_inventory_source"


def _git_ssh_env(timeout_sec: int = 10) -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    env.setdefault(
        "GIT_SSH_COMMAND",
        f"ssh -o BatchMode=yes -o ConnectTimeout={max(2, int(timeout_sec))}",
    )
    return env


def _run_git(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = 60.0,
    use_ssh_insteadof: bool = False,
) -> subprocess.CompletedProcess[str]:
    cmd = ["git", "--no-optional-locks"]
    if use_ssh_insteadof:
        cmd.extend(["-c", "url.git@github.com:.insteadOf=https://github.com/"])
    if cwd is not None:
        cmd.extend(["-C", str(cwd)])
    cmd.extend(args)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        env=_git_ssh_env(),
    )


def _normalize_sync_remote(raw: str, *, allow_file_remotes: bool = False) -> str | None:
    norm = normalize_remote_identity(raw)
    if norm:
        return norm
    if allow_file_remotes:
        text = str(raw or "").strip()
        if text.lower().startswith("file://"):
            parsed = urllib.parse.urlparse(text)
            if parsed.path:
                return f"file://{Path(parsed.path).resolve()}".removesuffix(".git").casefold()
    return None


def _discover_local_git_checkouts(
    config: dict,
    config_dir: Path,
    *,
    allow_file_remotes: bool = False,
) -> list[dict[str, Any]]:
    """Discover local git checkouts across configured `live` and `mirror` roots."""
    defaults = config.get("defaults", {})
    roots = config.get("roots", [])
    prepared: list[tuple[dict, str, list[Path], set[str]]] = []
    all_real_dirs: list[Path] = []

    for idx, raw_root in enumerate(roots):
        root_cfg = dict(raw_root)
        discovery = str(root_cfg.get("discovery", "children")).strip() or "children"
        if discovery == "inventory_policy":
            continue
        source_class = classify_source(root_cfg)
        if source_class not in {"live", "mirror"}:
            continue
        root_path = resolve_config_path(str(root_cfg["path"]), config_dir)
        if not safe_path_exists(root_path) or not os.access(root_path, os.R_OK):
            continue
        root_cfg["_resolved_path"] = root_path
        exclude_names = merge_exclude_names(root_cfg, defaults)
        adapter = get_adapter(discovery)
        candidates = [
            c for c in adapter.discover_candidates(root_cfg, defaults, exclude_names) if isinstance(c, Path)
        ]
        force_include = {str(x) for x in root_cfg.get("force_include_names", []) if str(x)}
        for cand in candidates:
            try:
                if not cand.is_symlink() and safe_path_exists(cand):
                    all_real_dirs.append(cand.resolve())
            except OSError:
                continue
        prepared.append((root_cfg, source_class, candidates, force_include))

    seen_paths: set[str] = set()
    results: list[dict[str, Any]] = []
    for root_cfg, source_class, candidates, force_include in prepared:
        exclude_autosnapshot = bool(
            root_cfg.get(
                "exclude_autosnapshot_repos",
                defaults.get("exclude_autosnapshot_repos", False),
            )
        )
        for cand in candidates:
            if is_shadowed_symlink(cand, all_real_dirs, force_include):
                continue
            try:
                resolved = cand.resolve()
            except OSError:
                continue
            key = str(resolved).lower()
            if key in seen_paths:
                continue
            seen_paths.add(key)
            if not safe_path_exists(resolved / ".git"):
                continue
            if exclude_autosnapshot and is_autosnapshot_git_repo(resolved):
                continue
            raw_remotes = list_git_remotes(resolved)
            norm_by_name: dict[str, str] = {}
            for rname, rurl in raw_remotes.items():
                norm = _normalize_sync_remote(rurl, allow_file_remotes=allow_file_remotes)
                if norm:
                    norm_by_name[rname] = norm
            results.append(
                {
                    "path": resolved,
                    "source_class": source_class,
                    "raw_remotes": raw_remotes,
                    "norm_remotes": norm_by_name,
                }
            )
    return results


def _has_active_git_operation(repo_dir: Path) -> bool:
    git_marker = repo_dir / ".git"
    git_dir = git_marker
    if safe_path_is_file(git_marker):
        try:
            raw = git_marker.read_text(encoding="utf-8", errors="replace").strip()
            if raw.lower().startswith("gitdir:"):
                rel = raw.split(":", 1)[1].strip()
                git_dir = Path(rel) if Path(rel).is_absolute() else (repo_dir / rel).resolve()
        except OSError:
            return True
    for lock_name in (
        "index.lock",
        "MERGE_HEAD",
        "REBASE_HEAD",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "rebase-merge",
        "rebase-apply",
    ):
        if safe_path_exists(git_dir / lock_name):
            return True
    return False


def _load_prior_remote_cache(sync_state_path: Path | None) -> dict[str, str]:
    if sync_state_path is None or not safe_path_is_file(sync_state_path):
        return {}
    try:
        doc = json.loads(sync_state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(doc, dict) or not isinstance(doc.get("remote_pushed_at"), dict):
        return {}
    return {
        str(k): str(v)
        for k, v in doc["remote_pushed_at"].items()
        if isinstance(k, str) and isinstance(v, str) and v
    }


def _aliased_remotes_from_state(state_dir: Path | None) -> set[str]:
    if state_dir is None or not safe_path_is_file(state_dir / "canonical-projects.json"):
        return set()
    try:
        canonical = load_canonical_projects(state_dir)
    except ValueError:
        return set()
    aliased: set[str] = set()
    for proj in canonical.get("projects", []):
        for ref in proj.get("referents", []):
            if not isinstance(ref, dict):
                continue
            if ref.get("kind") in {"decision_alias", "normalized_remote"}:
                norm = normalize_remote_identity(str(ref.get("value") or ""))
                if norm:
                    aliased.add(norm)
    return aliased


def _choose_clone_target(clone_root: Path, owner: str, repo_name: str) -> Path:
    primary = clone_root / repo_name
    try:
        occupied = safe_path_exists(primary) or primary.is_symlink()
    except OSError:
        occupied = True
    if not occupied:
        return primary
    return clone_root / f"{owner}--{repo_name}"


def _effective_clone_url(repo_record: dict[str, Any]) -> str:
    raw_ssh = str(repo_record.get("ssh_url") or "").strip()
    if raw_ssh.startswith(("/", "file://")):
        return raw_ssh
    return f"git@github.com:{repo_record['full_name']}.git"


def _is_github_remote_url(raw_url: str) -> bool:
    lower = raw_url.strip().lower()
    return "github.com:" in lower or "github.com/" in lower


def _git_stdout(repo_dir: Path, *args: str) -> str:
    proc = _run_git(list(args), cwd=repo_dir, timeout=15.0)
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def _needs_fetch_or_ff(
    checkouts: list[tuple[Path, str]],
    *,
    default_branch: str,
    remote_pushed_at: str | None,
    cached_pushed_at: str | None,
    force_fetch: bool,
) -> bool:
    if force_fetch or not remote_pushed_at or remote_pushed_at != cached_pushed_at:
        return True
    for repo_dir, rname in checkouts:
        remote_ref = f"refs/remotes/{rname}/{default_branch}"
        remote_sha = _git_stdout(repo_dir, "rev-parse", "--verify", "--quiet", remote_ref)
        if not remote_sha:
            return True
        if rname == "origin":
            head_branch = _git_stdout(repo_dir, "rev-parse", "--abbrev-ref", "HEAD")
            head_sha = _git_stdout(repo_dir, "rev-parse", "HEAD")
            if head_branch == default_branch and head_sha and head_sha != remote_sha:
                is_anc = _run_git(
                    ["merge-base", "--is-ancestor", head_sha, remote_ref],
                    cwd=repo_dir,
                    timeout=5.0,
                )
                if is_anc.returncode == 0 and not _git_stdout(repo_dir, "status", "--porcelain"):
                    return True
    return False


def _inspect_and_maybe_ff(
    repo_dir: Path,
    *,
    remote_name: str,
    default_branch: str,
    allow_ff: bool,
    do_mutate: bool,
) -> tuple[str, str | None, str | None, str | None]:
    """Return `(outcome, head_branch, old_commit, new_or_remote_commit)`."""
    head_branch = _git_stdout(repo_dir, "rev-parse", "--abbrev-ref", "HEAD") or None
    old_sha = _git_stdout(repo_dir, "rev-parse", "HEAD") or None
    if remote_name != "origin":
        return "secondary_remote_only", head_branch, old_sha, None

    remote_ref = f"refs/remotes/origin/{default_branch}"
    remote_sha = _git_stdout(repo_dir, "rev-parse", "--verify", "--quiet", remote_ref) or None
    if not remote_sha:
        return "missing_remote_tracking_ref", head_branch, old_sha, None

    if head_branch != default_branch:
        local_def_sha = _git_stdout(
            repo_dir, "rev-parse", "--verify", "--quiet", f"refs/heads/{default_branch}"
        )
        if local_def_sha and local_def_sha == remote_sha:
            return "up_to_date_non_default_branch", head_branch, old_sha, remote_sha
        return "non_default_branch", head_branch, old_sha, remote_sha

    if old_sha == remote_sha:
        return "up_to_date", head_branch, old_sha, remote_sha

    if _has_active_git_operation(repo_dir):
        return "active_git_operation", head_branch, old_sha, remote_sha

    if _git_stdout(repo_dir, "status", "--porcelain"):
        return "dirty_worktree", head_branch, old_sha, remote_sha

    if not old_sha:
        return "unborn_head", head_branch, old_sha, remote_sha

    anc = _run_git(["merge-base", "--is-ancestor", old_sha, remote_ref], cwd=repo_dir, timeout=5.0)
    if anc.returncode != 0:
        rev_anc = _run_git(
            ["merge-base", "--is-ancestor", remote_ref, old_sha], cwd=repo_dir, timeout=5.0
        )
        if rev_anc.returncode == 0:
            return "local_ahead", head_branch, old_sha, remote_sha
        return "diverged", head_branch, old_sha, remote_sha

    if not allow_ff:
        return "ff_disabled", head_branch, old_sha, remote_sha
    if not do_mutate:
        return "would_fast_forward", head_branch, old_sha, remote_sha

    ff_proc = _run_git(["merge", "--ff-only", "--quiet", remote_ref], cwd=repo_dir, timeout=30.0)
    if ff_proc.returncode != 0:
        return "ff_merge_failed", head_branch, old_sha, remote_sha
    new_sha = _git_stdout(repo_dir, "rev-parse", "HEAD") or remote_sha
    return "fast_forwarded", head_branch, old_sha, new_sha


def sync_github_estate(
    *,
    config_path: Path,
    state_dir: Path,
    clone_root: Path | None = None,
    mode: str = "sync",
    clone_missing: bool = True,
    fetch_existing: bool = True,
    ff_clean: bool = True,
    normalize_ssh_remotes: bool = True,
    force_fetch: bool = False,
    inventory_file: Path | None = None,
    ssh_fallback_host: str | None = None,
    token_cache_path: Path | None = None,
    token_cache_ttl_seconds: int = DEFAULT_TOKEN_CACHE_TTL_SECONDS,
    remote_timeout: float = 45.0,
    pulse_path: Path = DEFAULT_FLEET_PULSE_PATH,
    persist_state: bool = True,
) -> dict[str, Any]:
    """Reconcile GitHub remote repositories against local checkouts and audit 1:1 parity."""
    if mode not in {"sync", "dry_run", "check"}:
        raise ValueError(f"Unsupported sync mode: {mode!r}; expected 'sync', 'dry_run', or 'check'.")
    do_mutate = mode == "sync"

    config_path = config_path.resolve()
    state_dir = state_dir.resolve()
    config = read_config(config_path)
    validate_config(config, config_path.parent, check_artifacts=False)

    eff_clone_root = (clone_root or Path(DEFAULT_CLONE_ROOT)).expanduser().resolve()
    sync_state_path = state_dir / GITHUB_SYNC_FILENAME
    prior_pushed_cache = _load_prior_remote_cache(sync_state_path)
    synced_at = _now_utc()

    remote_repos, inventory_source, inventory_error = fetch_github_repo_inventory(
        inventory_file=inventory_file,
        token_cache_path=token_cache_path,
        token_cache_ttl_seconds=token_cache_ttl_seconds,
        ssh_fallback_host=ssh_fallback_host,
        pulse_path=pulse_path,
    )

    allow_file_remotes = inventory_file is not None
    local_checkouts = _discover_local_git_checkouts(
        config,
        config_path.parent,
        allow_file_remotes=allow_file_remotes,
    )
    aliased_remotes = _aliased_remotes_from_state(state_dir)

    live_by_remote: dict[str, list[tuple[Path, str, str]]] = {}
    any_by_remote: dict[str, list[tuple[Path, str, str]]] = {}
    for chk in local_checkouts:
        chk_path: Path = chk["path"]
        sclass: str = chk["source_class"]
        for rname, norm_rem in chk["norm_remotes"].items():
            raw_url = chk["raw_remotes"].get(rname, "")
            any_by_remote.setdefault(norm_rem, []).append((chk_path, rname, raw_url))
            if sclass == "live":
                live_by_remote.setdefault(norm_rem, []).append((chk_path, rname, raw_url))

    cloned: list[dict[str, Any]] = []
    fast_forwarded: list[dict[str, Any]] = []
    fetched_only: list[dict[str, Any]] = []
    skipped_archived: list[dict[str, Any]] = []
    missing_local: list[dict[str, Any]] = []
    ssh_rewritten_count = 0
    fetched_count = 0
    skipped_unchanged_count = 0
    skipped_dirty_or_non_default = 0
    errors: list[dict[str, str]] = []
    next_pushed_cache: dict[str, str] = dict(prior_pushed_cache)

    remote_active_count = 0
    remote_archived_count = 0
    matched_local_count = 0

    for repo in remote_repos:
        full_name = str(repo["full_name"])
        norm_remote = normalize_remote_identity(f"github.com/{full_name}")
        if not norm_remote:
            continue
        alt_norm_remote = _normalize_sync_remote(
            str(repo.get("ssh_url") or ""),
            allow_file_remotes=allow_file_remotes,
        )
        candidate_norms = [norm_remote]
        if alt_norm_remote and alt_norm_remote != norm_remote:
            candidate_norms.append(alt_norm_remote)

        is_archived = bool(repo.get("archived"))
        default_branch = str(repo.get("default_branch") or "main")
        pushed_at = repo.get("pushed_at")
        live_matches: list[tuple[Path, str, str]] = []
        seen_live: set[tuple[Path, str]] = set()
        for cn in candidate_norms:
            for item in live_by_remote.get(cn, []):
                key = (item[0], item[1])
                if key not in seen_live:
                    seen_live.add(key)
                    live_matches.append(item)
        any_matches: list[tuple[Path, str, str]] = []
        seen_any: set[tuple[Path, str]] = set()
        for cn in candidate_norms:
            for item in any_by_remote.get(cn, []):
                key = (item[0], item[1])
                if key not in seen_any:
                    seen_any.add(key)
                    any_matches.append(item)

        if is_archived:
            remote_archived_count += 1
            if any_matches:
                resolution = "local_present"
                matched_local_count += 1
            elif norm_remote in aliased_remotes:
                resolution = "aliased_or_superseded"
            else:
                resolution = "archived_remote_only"
            skipped_archived.append({"full_name": full_name, "resolution": resolution})
            continue

        remote_active_count += 1

        if not live_matches:
            clone_url = _effective_clone_url(repo)
            target_dir = _choose_clone_target(eff_clone_root, str(repo["owner"]), str(repo["name"]))
            if not clone_missing or not do_mutate:
                missing_local.append(
                    {
                        "full_name": full_name,
                        "clone_url": clone_url,
                        "target_path": str(target_dir),
                    }
                )
                continue

            target_dir.parent.mkdir(parents=True, exist_ok=True)
            use_ssh = _is_github_remote_url(clone_url)
            try:
                c_proc = _run_git(
                    ["clone", "--quiet", clone_url, str(target_dir)],
                    timeout=max(remote_timeout, 60.0),
                    use_ssh_insteadof=use_ssh,
                )
            except subprocess.TimeoutExpired:
                errors.append(
                    {"full_name": full_name, "stage": "clone", "detail": "git clone timed out"}
                )
                missing_local.append(
                    {
                        "full_name": full_name,
                        "clone_url": clone_url,
                        "target_path": str(target_dir),
                    }
                )
                continue
            if c_proc.returncode != 0:
                err_msg = ((c_proc.stderr or "").strip().splitlines() or ["git clone failed"])[-1]
                errors.append({"full_name": full_name, "stage": "clone", "detail": err_msg})
                missing_local.append(
                    {
                        "full_name": full_name,
                        "clone_url": clone_url,
                        "target_path": str(target_dir),
                    }
                )
                continue

            head_sha = _git_stdout(target_dir, "rev-parse", "HEAD") or None
            cloned.append(
                {
                    "full_name": full_name,
                    "clone_url": clone_url,
                    "target_path": str(target_dir),
                    "default_branch": default_branch,
                    "head_commit": head_sha,
                }
            )
            matched_local_count += 1
            if isinstance(pushed_at, str) and pushed_at:
                next_pushed_cache[norm_remote] = pushed_at
            local_checkouts.append(
                {
                    "path": target_dir,
                    "source_class": "live",
                    "raw_remotes": {"origin": clone_url},
                    "norm_remotes": {"origin": norm_remote},
                }
            )
            continue

        matched_local_count += 1
        checkout_pairs: list[tuple[Path, str]] = []
        for chk_path, rname, raw_url in live_matches:
            checkout_pairs.append((chk_path, rname))
            if (
                normalize_ssh_remotes
                and do_mutate
                and raw_url.strip().lower().startswith("https://github.com/")
            ):
                ssh_target_url = f"git@github.com:{full_name}.git"
                set_proc = _run_git(
                    ["remote", "set-url", rname, ssh_target_url],
                    cwd=chk_path,
                    timeout=10.0,
                )
                if set_proc.returncode == 0:
                    ssh_rewritten_count += 1

        if not fetch_existing:
            skipped_unchanged_count += 1
            continue

        should_fetch = _needs_fetch_or_ff(
            checkout_pairs,
            default_branch=default_branch,
            remote_pushed_at=pushed_at if isinstance(pushed_at, str) else None,
            cached_pushed_at=prior_pushed_cache.get(norm_remote),
            force_fetch=force_fetch,
        )
        if not should_fetch:
            skipped_unchanged_count += 1
            continue

        repo_fetch_failed = False
        for chk_path, rname in checkout_pairs:
            if do_mutate:
                raw_u = list_git_remotes(chk_path).get(rname, "")
                use_ssh = _is_github_remote_url(raw_u)
                try:
                    f_proc = _run_git(
                        ["fetch", "--prune", "--quiet", rname],
                        cwd=chk_path,
                        timeout=remote_timeout,
                        use_ssh_insteadof=use_ssh,
                    )
                except subprocess.TimeoutExpired:
                    repo_fetch_failed = True
                    errors.append(
                        {
                            "full_name": full_name,
                            "stage": "fetch",
                            "detail": f"git fetch timed out at {chk_path}",
                        }
                    )
                    continue
                if f_proc.returncode != 0:
                    repo_fetch_failed = True
                    err_msg = ((f_proc.stderr or "").strip().splitlines() or ["git fetch failed"])[-1]
                    errors.append(
                        {
                            "full_name": full_name,
                            "stage": "fetch",
                            "detail": f"{chk_path}: {err_msg}",
                        }
                    )
                    continue
                fetched_count += 1

            outcome, head_branch, old_sha, target_sha = _inspect_and_maybe_ff(
                chk_path,
                remote_name=rname,
                default_branch=default_branch,
                allow_ff=ff_clean,
                do_mutate=do_mutate,
            )
            if outcome == "fast_forwarded":
                fast_forwarded.append(
                    {
                        "full_name": full_name,
                        "path": str(chk_path),
                        "branch": head_branch or default_branch,
                        "old_commit": old_sha or "",
                        "new_commit": target_sha or "",
                    }
                )
            elif outcome in {
                "dirty_worktree",
                "non_default_branch",
                "local_ahead",
                "diverged",
                "active_git_operation",
                "would_fast_forward",
                "ff_disabled",
            }:
                if outcome in {"dirty_worktree", "non_default_branch", "local_ahead", "diverged"}:
                    skipped_dirty_or_non_default += 1
                fetched_only.append(
                    {
                        "full_name": full_name,
                        "path": str(chk_path),
                        "reason": outcome,
                        "head_branch": head_branch,
                        "default_branch": default_branch,
                    }
                )
            elif outcome == "ff_merge_failed":
                repo_fetch_failed = True
                errors.append(
                    {
                        "full_name": full_name,
                        "stage": "fast_forward",
                        "detail": f"git merge --ff-only failed at {chk_path}",
                    }
                )

        if not repo_fetch_failed and isinstance(pushed_at, str) and pushed_at and do_mutate:
            next_pushed_cache[norm_remote] = pushed_at

    # Reverse 1:1 check: inspect local live git checkouts & compiled canonical projects.
    local_without_remote: list[dict[str, Any]] = []
    seen_no_remote_paths: set[str] = set()
    for chk in local_checkouts:
        if chk["source_class"] != "live":
            continue
        if not chk["norm_remotes"]:
            p_str = str(chk["path"])
            if p_str not in seen_no_remote_paths:
                seen_no_remote_paths.add(p_str)
                local_without_remote.append(
                    {
                        "canonical_project_id": None,
                        "project_key": chk["path"].name.lower(),
                        "preferred_path": p_str,
                    }
                )

    non_git_canonical_projects = 0
    if safe_path_is_file(state_dir / "canonical-projects.json") and safe_path_is_file(
        state_dir / "observations.json"
    ):
        try:
            cp_doc = load_canonical_projects(state_dir)
            obs_doc = json.loads((state_dir / "observations.json").read_text(encoding="utf-8"))
            obs_by_id = {
                str(o.get("observation_id")): o
                for o in (obs_doc.get("observations") or [])
                if isinstance(o, dict)
            }
            for proj in cp_doc.get("projects", []):
                p_obs = [obs_by_id[oid] for oid in proj.get("observation_ids", []) if oid in obs_by_id]
                has_git = any(bool((o.get("compat_entry") or {}).get("git")) for o in p_obs)
                if not has_git:
                    non_git_canonical_projects += 1
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    if inventory_source == "unavailable":
        status = "degraded"
        status_reason = f"github_inventory_unavailable:{inventory_error or 'unknown'}"
        in_1to1_parity = False
    else:
        in_1to1_parity = (
            len(missing_local) == 0
            and len(local_without_remote) == 0
            and non_git_canonical_projects == 0
            and len(errors) == 0
        )
        if in_1to1_parity:
            status = "ok"
            status_reason = "reconciled" if (cloned or fast_forwarded) else "in_parity"
        else:
            status = "drift"
            status_reason = "parity_drift_detected"

    payload: dict[str, Any] = {
        "schema_version": GITHUB_SYNC_SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "synced_at": synced_at,
        "mode": mode,
        "inventory_source": inventory_source,
        "status": status,
        "status_reason": status_reason,
        "clone_root": str(eff_clone_root),
        "in_1to1_parity": in_1to1_parity,
        "counts": {
            "remote_total": len(remote_repos),
            "remote_active": remote_active_count,
            "remote_archived": remote_archived_count,
            "matched_local": matched_local_count,
            "cloned": len(cloned),
            "fetched": fetched_count,
            "fast_forwarded": len(fast_forwarded),
            "ssh_remotes_normalized": ssh_rewritten_count,
            "skipped_unchanged": skipped_unchanged_count,
            "skipped_archived": len(skipped_archived),
            "skipped_dirty_or_non_default": skipped_dirty_or_non_default,
            "missing_local": len(missing_local),
            "local_without_remote": len(local_without_remote),
            "non_git_canonical_projects": non_git_canonical_projects,
            "errors": len(errors),
        },
        "cloned": cloned,
        "fast_forwarded": fast_forwarded,
        "fetched_only": fetched_only,
        "skipped_archived": skipped_archived,
        "missing_local": missing_local,
        "local_without_remote": local_without_remote,
        "errors": errors,
        "remote_pushed_at": dict(sorted(next_pushed_cache.items())),
    }

    if persist_state and do_mutate:
        state_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            sync_state_path,
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
        )

    return payload

