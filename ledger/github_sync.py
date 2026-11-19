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
DEFAULT_UNMATCHED_FETCH_INTERVAL_SECONDS = 86400
DEFAULT_FLEET_PULSE_PATH = Path("/run/user/1000/homelab/fleet-pulse.json")


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_iso_epoch(raw: str | None) -> float | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


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
    raw_desc = raw.get("description")
    description = str(raw_desc).replace("\r", " ").replace("\n", " ").strip()[:240] or None if isinstance(raw_desc, str) else None
    raw_lang = raw.get("language")
    language = str(raw_lang).strip() or None if isinstance(raw_lang, str) else None
    raw_topics = raw.get("topics")
    topics = (
        sorted({str(t).strip() for t in raw_topics if isinstance(t, str) and str(t).strip()})
        if isinstance(raw_topics, list)
        else []
    )
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
        "description": description,
        "language": language,
        "topics": topics,
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


def _extract_remote_host(raw_url: str) -> str | None:
    text = str(raw_url or "").strip()
    if not text or text.startswith("/"):
        return None
    lower = text.lower()
    if lower.startswith(("https://", "http://", "ssh://", "git://")):
        parsed = urllib.parse.urlsplit(text)
        return (parsed.hostname or "").strip().lower() or None
    if lower.startswith("file://"):
        return None
    if ":" in text:
        colon_idx = text.find(":")
        slash_idx = text.find("/")
        if slash_idx == -1 or colon_idx < slash_idx:
            host_part = text[:colon_idx].split("@", 1)[-1].strip().lower()
            return host_part or None
    return None


def _check_fleet_pulse_allows_ssh(
    target: str,
    *,
    pulse_path: Path = DEFAULT_FLEET_PULSE_PATH,
) -> tuple[bool, str | None]:
    """Enforce INV-FLEET-GAME-01 before dispatching any SSH or peer-network command."""
    host_part = target.split("@", 1)[-1].strip().lower()
    if ":" in host_part and not host_part.startswith("["):
        host_part = host_part.split(":", 1)[0].strip()
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

    base_host = host_part.split(".", 1)[0] if ".tail" in host_part or host_part.endswith(".ts.net") else host_part
    base_host = base_host.removesuffix("-lan")
    host_aliases = {
        "100.82.59.43": "mac",
        "matthews-macbook-air-2": "mac",
        "mattymac": "mac",
        "mac": "mac",
        "100.81.51.72": "iz",
        "192.168.1.115": "iz",
        "iz": "iz",
        "100.116.7.73": "thing1",
        "192.168.1.70": "thing1",
        "thing1": "thing1",
        "100.99.233.15": "matty-pc",
        "192.168.1.36": "matty-pc",
        "mattypc": "matty-pc",
        "matty-pc": "matty-pc",
    }
    node_key = host_aliases.get(host_part, host_aliases.get(base_host, base_host))
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
    candidate_keys = [
        Path.home() / ".ssh" / "id_ed25519",
        Path.home() / ".ssh" / "id_ed25519_github_ai_server",
    ]
    present_keys = [k for k in candidate_keys if safe_path_is_file(k)]
    if present_keys:
        for key_path in present_keys:
            ssh_cmd.extend(["-i", str(key_path)])
        ssh_cmd.extend(["-o", "IdentitiesOnly=yes"])
    remote_script = (
        'export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"; '
        f'gh api "user/repos?per_page=100&affiliation={affiliation}" --paginate '
        "--jq '.[] | {name, full_name, private, fork, archived, default_branch, pushed_at, ssh_url, description, language, topics}'"
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
    env["GIT_ASKPASS"] = "/bin/false"
    env["SSH_ASKPASS"] = "/bin/false"
    env.setdefault(
        "GIT_SSH_COMMAND",
        f"ssh -o BatchMode=yes -o ConnectTimeout={max(2, int(timeout_sec))}",
    )
    return env


def _credential_clear_args(raw_url: str | None = None) -> list[str]:
    """Build git `-c` flags that clear generic and URL-scoped credential helpers."""
    flags = [
        "-c",
        "credential.helper=",
        "-c",
        "credential.https://github.com.helper=",
        "-c",
        "credential.https://gist.github.com.helper=",
    ]
    text = str(raw_url or "").strip()
    if text.lower().startswith(("https://", "http://")):
        parsed = urllib.parse.urlsplit(text)
        if parsed.scheme and parsed.netloc:
            origin_key = f"{parsed.scheme.lower()}://{parsed.netloc}"
            candidate = f"credential.{origin_key}.helper="
            if candidate not in flags:
                flags.extend(["-c", candidate])
    return flags


def _run_git(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = 60.0,
    use_ssh_insteadof: bool = False,
    disable_credential_helper: bool = False,
    credential_url: str | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = ["git", "--no-optional-locks"]
    if use_ssh_insteadof:
        cmd.extend(["-c", "url.git@github.com:.insteadOf=https://github.com/"])
    if disable_credential_helper:
        cmd.extend(_credential_clear_args(credential_url))
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


def _fetch_remote_with_fallback(
    repo_dir: Path,
    remote_name: str,
    raw_url: str,
    *,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    """Fetch ``remote_name`` over SSH first for GitHub URLs, with anonymous HTTP(S) and zero credential helpers."""
    use_ssh = _is_github_remote_url(raw_url)
    is_http = raw_url.strip().lower().startswith(("https://", "http://"))
    proc = _run_git(
        ["fetch", "--prune", "--quiet", remote_name],
        cwd=repo_dir,
        timeout=timeout,
        use_ssh_insteadof=use_ssh,
        disable_credential_helper=(is_http and not use_ssh),
        credential_url=raw_url if is_http else None,
    )
    if (
        proc.returncode != 0
        and use_ssh
        and raw_url.strip().lower().startswith("https://github.com/")
    ):
        fallback_proc = _run_git(
            ["fetch", "--prune", "--quiet", remote_name],
            cwd=repo_dir,
            timeout=timeout,
            use_ssh_insteadof=False,
            disable_credential_helper=True,
            credential_url=raw_url,
        )
        if fallback_proc.returncode == 0:
            return fallback_proc
    return proc


def _is_network_remote_url(raw_url: str, *, allow_file_remotes: bool = False) -> bool:
    text = str(raw_url or "").strip()
    if not text:
        return False
    lower = text.lower()
    if lower.startswith(("https://", "http://", "ssh://", "git://")):
        return True
    if allow_file_remotes and lower.startswith("file://"):
        return True
    if not text.startswith("/") and ":" in text:
        colon_idx = text.find(":")
        slash_idx = text.find("/")
        if slash_idx == -1 or colon_idx < slash_idx:
            return True
    return False


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


def _load_prior_remote_cache(
    sync_state_path: Path | None,
) -> tuple[dict[str, str], dict[str, str], dict[str, dict[str, Any]]]:
    if sync_state_path is None or not safe_path_is_file(sync_state_path):
        return {}, {}, {}
    try:
        doc = json.loads(sync_state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, {}, {}
    if not isinstance(doc, dict):
        return {}, {}, {}
    pushed = (
        {
            str(k): str(v)
            for k, v in doc["remote_pushed_at"].items()
            if isinstance(k, str) and isinstance(v, str) and v
        }
        if isinstance(doc.get("remote_pushed_at"), dict)
        else {}
    )
    fetched = (
        {
            str(k): str(v)
            for k, v in doc["remote_fetched_at"].items()
            if isinstance(k, str) and isinstance(v, str) and v
        }
        if isinstance(doc.get("remote_fetched_at"), dict)
        else {}
    )
    meta: dict[str, dict[str, Any]] = {}
    if isinstance(doc.get("remote_metadata"), dict):
        for k, v in doc["remote_metadata"].items():
            if isinstance(k, str) and isinstance(v, dict):
                meta[str(k)] = dict(v)
    return pushed, fetched, meta


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


def _configured_upstream_ref(repo_dir: Path, branch: str | None) -> str | None:
    if not branch or branch == "HEAD":
        return None
    up = _git_stdout(
        repo_dir,
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        f"{branch}@{{u}}",
    )
    return up or None


def _detect_origin_default_branch(repo_dir: Path) -> str:
    sym = _git_stdout(repo_dir, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD")
    if sym.startswith("origin/"):
        branch = sym.split("/", 1)[1].strip()
        if branch:
            return branch
    for cand in ("main", "master"):
        if _git_stdout(repo_dir, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{cand}"):
            return cand
    hb = _git_stdout(repo_dir, "rev-parse", "--abbrev-ref", "HEAD") or _git_stdout(
        repo_dir, "symbolic-ref", "--short", "-q", "HEAD"
    )
    if hb and hb != "HEAD":
        if _git_stdout(repo_dir, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{hb}"):
            return hb
    refs_out = _git_stdout(repo_dir, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin")
    remote_branches = [
        line.split("/", 1)[1].strip()
        for line in refs_out.splitlines()
        if line.startswith("origin/") and line.strip() != "origin/HEAD"
    ]
    if len(remote_branches) == 1 and remote_branches[0]:
        return remote_branches[0]
    return hb if (hb and hb != "HEAD") else "main"


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
        if rname == "origin":
            remote_ref = f"refs/remotes/origin/{default_branch}"
            remote_sha = _git_stdout(repo_dir, "rev-parse", "--verify", "--quiet", remote_ref)
            if not remote_sha:
                return True
            head_sha = _git_stdout(repo_dir, "rev-parse", "--verify", "--quiet", "HEAD")
            if not head_sha:
                has_local_branches = bool(
                    _git_stdout(repo_dir, "for-each-ref", "--count=1", "--format=%(refname)", "refs/heads")
                )
                if not has_local_branches and not _git_stdout(repo_dir, "status", "--porcelain"):
                    return True
                continue
            head_branch = _git_stdout(repo_dir, "rev-parse", "--abbrev-ref", "HEAD")
            if head_branch == default_branch and head_sha != remote_sha:
                is_anc = _run_git(
                    ["merge-base", "--is-ancestor", head_sha, remote_ref],
                    cwd=repo_dir,
                    timeout=5.0,
                )
                if is_anc.returncode == 0 and not _git_stdout(repo_dir, "status", "--porcelain"):
                    return True
        else:
            head_sha = _git_stdout(repo_dir, "rev-parse", "--verify", "--quiet", "HEAD")
            head_branch = _git_stdout(repo_dir, "rev-parse", "--abbrev-ref", "HEAD") if head_sha else ""

        if head_sha and head_branch and head_branch != "HEAD":
            up_ref = _configured_upstream_ref(repo_dir, head_branch)
            if up_ref == f"{rname}/{head_branch}":
                tracked_ref = f"refs/remotes/{rname}/{head_branch}"
                tracked_sha = _git_stdout(repo_dir, "rev-parse", "--verify", "--quiet", tracked_ref)
                if not tracked_sha:
                    return True
                if head_sha != tracked_sha:
                    is_anc = _run_git(
                        ["merge-base", "--is-ancestor", head_sha, tracked_ref],
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
    old_sha = _git_stdout(repo_dir, "rev-parse", "--verify", "--quiet", "HEAD") or None
    if old_sha:
        head_branch = _git_stdout(repo_dir, "rev-parse", "--abbrev-ref", "HEAD") or None
    else:
        head_branch = _git_stdout(repo_dir, "symbolic-ref", "--short", "-q", "HEAD") or None

    if remote_name != "origin":
        up_ref = _configured_upstream_ref(repo_dir, head_branch)
        if old_sha and head_branch and up_ref == f"{remote_name}/{head_branch}":
            tracked_ref = f"refs/remotes/{remote_name}/{head_branch}"
            tracked_sha = _git_stdout(repo_dir, "rev-parse", "--verify", "--quiet", tracked_ref) or None
            if tracked_sha and old_sha != tracked_sha:
                if _has_active_git_operation(repo_dir):
                    return "active_git_operation", head_branch, old_sha, tracked_sha
                if _git_stdout(repo_dir, "status", "--porcelain"):
                    return "dirty_worktree", head_branch, old_sha, tracked_sha
                anc = _run_git(
                    ["merge-base", "--is-ancestor", old_sha, tracked_ref],
                    cwd=repo_dir,
                    timeout=5.0,
                )
                if anc.returncode == 0:
                    if not allow_ff:
                        return "ff_disabled", head_branch, old_sha, tracked_sha
                    if not do_mutate:
                        return "would_fast_forward", head_branch, old_sha, tracked_sha
                    ff_proc = _run_git(
                        ["merge", "--ff-only", "--quiet", tracked_ref],
                        cwd=repo_dir,
                        timeout=30.0,
                    )
                    if ff_proc.returncode != 0:
                        return "ff_merge_failed", head_branch, old_sha, tracked_sha
                    new_sha = _git_stdout(repo_dir, "rev-parse", "HEAD") or tracked_sha
                    return "fast_forwarded", head_branch, old_sha, new_sha
        return "secondary_remote_only", head_branch, old_sha, None

    remote_ref = f"refs/remotes/origin/{default_branch}"
    remote_sha = _git_stdout(repo_dir, "rev-parse", "--verify", "--quiet", remote_ref) or None

    if not old_sha:
        if not remote_sha:
            return "missing_remote_tracking_ref", head_branch, None, None
        has_local_branches = bool(
            _git_stdout(repo_dir, "for-each-ref", "--count=1", "--format=%(refname)", "refs/heads")
        )
        if has_local_branches:
            return "non_default_branch", head_branch, None, remote_sha
        if _has_active_git_operation(repo_dir):
            return "active_git_operation", head_branch, None, remote_sha
        if _git_stdout(repo_dir, "status", "--porcelain"):
            return "dirty_worktree", head_branch, None, remote_sha
        if not allow_ff:
            return "ff_disabled", head_branch or default_branch, None, remote_sha
        if not do_mutate:
            return "would_fast_forward", head_branch or default_branch, None, remote_sha
        co_proc = _run_git(
            ["checkout", "-q", "-b", default_branch, "--track", f"origin/{default_branch}"],
            cwd=repo_dir,
            timeout=30.0,
        )
        if co_proc.returncode != 0:
            return "ff_merge_failed", head_branch, None, remote_sha
        new_sha = _git_stdout(repo_dir, "rev-parse", "HEAD") or remote_sha
        return "fast_forwarded", default_branch, "", new_sha

    if head_branch != default_branch:
        local_def_sha = _git_stdout(
            repo_dir, "rev-parse", "--verify", "--quiet", f"refs/heads/{default_branch}"
        )
        was_def_up_to_date = bool(local_def_sha and remote_sha and local_def_sha == remote_sha)
        if (
            local_def_sha
            and remote_sha
            and local_def_sha != remote_sha
            and allow_ff
            and do_mutate
            and not _has_active_git_operation(repo_dir)
        ):
            def_anc = _run_git(
                ["merge-base", "--is-ancestor", local_def_sha, remote_ref],
                cwd=repo_dir,
                timeout=5.0,
            )
            if def_anc.returncode == 0:
                _run_git(
                    ["fetch", "--quiet", ".", f"{remote_ref}:refs/heads/{default_branch}"],
                    cwd=repo_dir,
                    timeout=15.0,
                )

        up_ref = _configured_upstream_ref(repo_dir, head_branch)
        if head_branch and up_ref == f"origin/{head_branch}":
            branch_remote_ref = f"refs/remotes/origin/{head_branch}"
            branch_remote_sha = (
                _git_stdout(repo_dir, "rev-parse", "--verify", "--quiet", branch_remote_ref) or None
            )
            if branch_remote_sha and old_sha != branch_remote_sha:
                if _has_active_git_operation(repo_dir):
                    return "active_git_operation", head_branch, old_sha, branch_remote_sha
                if _git_stdout(repo_dir, "status", "--porcelain"):
                    return "dirty_worktree", head_branch, old_sha, branch_remote_sha
                b_anc = _run_git(
                    ["merge-base", "--is-ancestor", old_sha, branch_remote_ref],
                    cwd=repo_dir,
                    timeout=5.0,
                )
                if b_anc.returncode == 0:
                    if not allow_ff:
                        return "ff_disabled", head_branch, old_sha, branch_remote_sha
                    if not do_mutate:
                        return "would_fast_forward", head_branch, old_sha, branch_remote_sha
                    ff_proc = _run_git(
                        ["merge", "--ff-only", "--quiet", branch_remote_ref],
                        cwd=repo_dir,
                        timeout=30.0,
                    )
                    if ff_proc.returncode != 0:
                        return "ff_merge_failed", head_branch, old_sha, branch_remote_sha
                    new_sha = _git_stdout(repo_dir, "rev-parse", "HEAD") or branch_remote_sha
                    return "fast_forwarded", head_branch, old_sha, new_sha

        if not remote_sha:
            return "missing_remote_tracking_ref", head_branch, old_sha, None
        if was_def_up_to_date:
            return "up_to_date_non_default_branch", head_branch, old_sha, remote_sha
        return "non_default_branch", head_branch, old_sha, remote_sha

    if not remote_sha:
        return "missing_remote_tracking_ref", head_branch, old_sha, None

    if old_sha == remote_sha:
        return "up_to_date", head_branch, old_sha, remote_sha

    if _has_active_git_operation(repo_dir):
        return "active_git_operation", head_branch, old_sha, remote_sha

    if _git_stdout(repo_dir, "status", "--porcelain"):
        return "dirty_worktree", head_branch, old_sha, remote_sha

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
    sync_all_local_remotes: bool = True,
    unmatched_fetch_interval_seconds: int = DEFAULT_UNMATCHED_FETCH_INTERVAL_SECONDS,
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
    prior_pushed_cache, prior_fetched_cache, prior_remote_meta = _load_prior_remote_cache(sync_state_path)
    synced_at = _now_utc()
    now_epoch = _parse_iso_epoch(synced_at) or time.time()

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
    next_fetched_cache: dict[str, str] = dict(prior_fetched_cache)
    next_remote_meta: dict[str, dict[str, Any]] = dict(prior_remote_meta)
    handled_checkout_remotes: set[tuple[Path, str]] = set()

    remote_active_count = 0
    remote_archived_count = 0
    matched_local_count = 0

    for repo in remote_repos:
        full_name = str(repo["full_name"])
        norm_remote = normalize_remote_identity(f"github.com/{full_name}")
        if not norm_remote:
            continue
        next_remote_meta[norm_remote] = {
            "full_name": full_name,
            "description": repo.get("description"),
            "language": repo.get("language"),
            "archived": bool(repo.get("archived")),
            "fork": bool(repo.get("fork")),
            "private": bool(repo.get("private")),
            "default_branch": str(repo.get("default_branch") or "main"),
            "pushed_at": repo.get("pushed_at"),
            "topics": list(repo.get("topics") or []),
        }
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

            head_sha = _git_stdout(target_dir, "rev-parse", "--verify", "--quiet", "HEAD") or None
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
            handled_checkout_remotes.add((target_dir, "origin"))
            next_fetched_cache[norm_remote] = synced_at
            if head_sha and isinstance(pushed_at, str) and pushed_at:
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
            handled_checkout_remotes.add((chk_path, rname))
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
        unborn_origin_remaining = False
        for chk_path, rname in checkout_pairs:
            if do_mutate:
                raw_u = list_git_remotes(chk_path).get(rname, "")
                try:
                    f_proc = _fetch_remote_with_fallback(
                        chk_path,
                        rname,
                        raw_u,
                        timeout=remote_timeout,
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
                next_fetched_cache[norm_remote] = synced_at

            outcome, head_branch, old_sha, target_sha = _inspect_and_maybe_ff(
                chk_path,
                remote_name=rname,
                default_branch=default_branch,
                allow_ff=ff_clean,
                do_mutate=do_mutate,
            )
            if rname == "origin" and outcome == "missing_remote_tracking_ref" and old_sha is None:
                unborn_origin_remaining = True
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

        if (
            not repo_fetch_failed
            and not unborn_origin_remaining
            and isinstance(pushed_at, str)
            and pushed_at
            and do_mutate
        ):
            next_pushed_cache[norm_remote] = pushed_at

    # Universal pass: regularly fetch and fast-forward all remaining live local `origin`
    # checkouts (third-party upstream clones, fork `origin` remotes, archived local
    # checkouts, and non-GitHub network remotes) on a configurable TTL or `--force-fetch`.
    # Law 11: when GitHub inventory is unavailable (WAN outage), skip universal network
    # probing even under `--force-fetch` so full-scan timers never stall across 100+ repos.
    if sync_all_local_remotes and fetch_existing and inventory_source != "unavailable":
        for chk in local_checkouts:
            if chk["source_class"] != "live":
                continue
            chk_path = chk["path"]
            if (chk_path, "origin") in handled_checkout_remotes:
                continue
            raw_origin = str(chk["raw_remotes"].get("origin") or "").strip()
            norm_origin = chk["norm_remotes"].get("origin")
            if not raw_origin or not norm_origin:
                continue
            if not _is_network_remote_url(raw_origin, allow_file_remotes=allow_file_remotes):
                continue
            handled_checkout_remotes.add((chk_path, "origin"))

            meta_entry = next_remote_meta.get(norm_origin) or {}
            full_label = (
                str(meta_entry.get("full_name") or "").strip()
                or (
                    norm_origin.removeprefix("github.com/")
                    if norm_origin.startswith("github.com/")
                    else norm_origin
                )
            )
            def_branch = (
                str(meta_entry.get("default_branch") or "").strip()
                or _detect_origin_default_branch(chk_path)
            )
            remote_host = _extract_remote_host(raw_origin)
            if remote_host and remote_host != "github.com":
                peer_allowed, peer_reason = _check_fleet_pulse_allows_ssh(
                    remote_host,
                    pulse_path=pulse_path,
                )
                if not peer_allowed:
                    cur_branch = _git_stdout(chk_path, "rev-parse", "--abbrev-ref", "HEAD") or None
                    fetched_only.append(
                        {
                            "full_name": full_label,
                            "path": str(chk_path),
                            "reason": peer_reason or "fleet_peer_unavailable",
                            "head_branch": cur_branch,
                            "default_branch": def_branch,
                        }
                    )
                    continue
            last_fetched_epoch = _parse_iso_epoch(prior_fetched_cache.get(norm_origin))
            ttl_expired = (
                last_fetched_epoch is None
                or unmatched_fetch_interval_seconds <= 0
                or (now_epoch - last_fetched_epoch) >= unmatched_fetch_interval_seconds
            )
            needs_local_ff = _needs_fetch_or_ff(
                [(chk_path, "origin")],
                default_branch=def_branch,
                remote_pushed_at="cached",
                cached_pushed_at="cached",
                force_fetch=False,
            )
            if not force_fetch and not ttl_expired and not needs_local_ff:
                skipped_unchanged_count += 1
                continue

            if do_mutate and (
                force_fetch
                or ttl_expired
                or not _git_stdout(
                    chk_path, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{def_branch}"
                )
            ):
                try:
                    u_proc = _fetch_remote_with_fallback(
                        chk_path,
                        "origin",
                        raw_origin,
                        timeout=remote_timeout,
                    )
                except subprocess.TimeoutExpired:
                    errors.append(
                        {
                            "full_name": full_label,
                            "stage": "fetch",
                            "detail": f"git fetch timed out at {chk_path}",
                        }
                    )
                    continue
                if u_proc.returncode != 0:
                    err_msg = ((u_proc.stderr or "").strip().splitlines() or ["git fetch failed"])[-1]
                    errors.append(
                        {
                            "full_name": full_label,
                            "stage": "fetch",
                            "detail": f"{chk_path}: {err_msg}",
                        }
                    )
                    continue
                fetched_count += 1
                next_fetched_cache[norm_origin] = synced_at
                if not meta_entry.get("default_branch"):
                    def_branch = _detect_origin_default_branch(chk_path)

            outcome, head_branch, old_sha, target_sha = _inspect_and_maybe_ff(
                chk_path,
                remote_name="origin",
                default_branch=def_branch,
                allow_ff=ff_clean,
                do_mutate=do_mutate,
            )
            if outcome == "fast_forwarded":
                fast_forwarded.append(
                    {
                        "full_name": full_label,
                        "path": str(chk_path),
                        "branch": head_branch or def_branch,
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
                        "full_name": full_label,
                        "path": str(chk_path),
                        "reason": outcome,
                        "head_branch": head_branch,
                        "default_branch": def_branch,
                    }
                )
            elif outcome == "ff_merge_failed":
                errors.append(
                    {
                        "full_name": full_label,
                        "stage": "fast_forward",
                        "detail": f"git merge --ff-only failed at {chk_path}",
                    }
                )

    # Reverse 1:1 check: inspect local live git checkouts & passive peer-census unpushed repos.
    cid_by_key_or_loc: dict[str, str] = {}
    if safe_path_is_file(state_dir / "canonical-projects.json"):
        try:
            cp_for_map = load_canonical_projects(state_dir)
            for proj in cp_for_map.get("projects", []):
                cid = str(proj.get("canonical_project_id") or "").strip()
                if not cid:
                    continue
                pkey = str(proj.get("project_key") or "").strip().lower()
                if pkey:
                    cid_by_key_or_loc.setdefault(f"key:{pkey}", cid)
                for ref in proj.get("referents", []):
                    if isinstance(ref, dict) and ref.get("kind") == "path":
                        rval = str(ref.get("value") or "").strip()
                        if rval:
                            cid_by_key_or_loc.setdefault(f"loc:{rval}", cid)
        except (OSError, ValueError):
            pass

    local_without_remote: list[dict[str, Any]] = []
    seen_no_remote_paths: set[str] = set()
    for chk in local_checkouts:
        if chk["source_class"] != "live":
            continue
        if not chk["norm_remotes"]:
            p_str = str(chk["path"])
            if p_str not in seen_no_remote_paths:
                seen_no_remote_paths.add(p_str)
                pkey = chk["path"].name.lower()
                local_without_remote.append(
                    {
                        "canonical_project_id": cid_by_key_or_loc.get(f"loc:{p_str}")
                        or cid_by_key_or_loc.get(f"key:{pkey}"),
                        "project_key": pkey,
                        "preferred_path": p_str,
                    }
                )

    defaults = config.get("defaults", {}) if isinstance(config.get("defaults"), dict) else {}
    inv_adapter = get_adapter("inventory_policy")
    for raw_root in config.get("roots", []):
        if not isinstance(raw_root, dict):
            continue
        if str(raw_root.get("discovery", "")).strip() != "inventory_policy":
            continue
        if str(raw_root.get("inventory_kind", "")).strip() != "peer_census":
            continue
        inv_raw = str(raw_root.get("inventory_jsonl", "")).strip()
        pol_raw = str(raw_root.get("policy_path", "")).strip()
        root_raw = str(raw_root.get("path", "")).strip()
        if not (inv_raw and pol_raw and root_raw):
            continue
        inv_path = resolve_config_path(inv_raw, config_path.parent)
        pol_path = resolve_config_path(pol_raw, config_path.parent)
        if not (safe_path_is_file(inv_path) and safe_path_is_file(pol_path)):
            continue
        rcfg = dict(raw_root)
        rcfg["_resolved_path"] = resolve_config_path(root_raw, config_path.parent)
        rcfg["_inventory_jsonl_path"] = inv_path
        rcfg["_policy_path"] = pol_path
        try:
            excl = merge_exclude_names(rcfg, defaults)
            cands = inv_adapter.discover_candidates(rcfg, defaults, excl)
            for cand in cands:
                entry = inv_adapter.build(cand, rcfg, defaults, state_dir)
                if not entry or not entry.get("git"):
                    continue
                rem = _normalize_sync_remote(
                    str(entry.get("remote_url") or ""), allow_file_remotes=allow_file_remotes
                )
                if rem:
                    continue
                p_str = str(entry.get("path") or "").strip()
                pkey = str(entry.get("project_key") or "").strip()
                if p_str and pkey and p_str not in seen_no_remote_paths:
                    seen_no_remote_paths.add(p_str)
                    local_without_remote.append(
                        {
                            "canonical_project_id": cid_by_key_or_loc.get(f"loc:{p_str}")
                            or cid_by_key_or_loc.get(f"key:{pkey.lower()}"),
                            "project_key": pkey,
                            "preferred_path": p_str,
                        }
                    )
        except (OSError, ValueError):
            continue

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
        "remote_fetched_at": dict(sorted(next_fetched_cache.items())),
        "remote_metadata": dict(sorted(next_remote_meta.items())),
    }

    if persist_state and do_mutate:
        state_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            sync_state_path,
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
        )

    return payload

