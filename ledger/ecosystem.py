"""Pure-Python cross-repository ecosystem dependency and blast-radius compiler.

Extracts deterministic, provenance-backed inter-project dependency edges across
resolved live canonical checkouts with zero subprocess spawns and zero target
writes (Architectural Laws 7 and 10).
"""

from __future__ import annotations

import configparser
import json
import os
import re
import tomllib
from pathlib import Path
from typing import Any

from .identity import normalize_remote_identity

_MAX_OPS_FILES_PER_REPO = 192
_MAX_CODE_FILES_PER_REPO = 64
_MAX_FILE_BYTES = 131072

_ROOT_MANIFEST_CANDIDATES: tuple[str, ...] = (
    ".gitmodules",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "Makefile",
    "justfile",
)

_OPS_SUBDIRS: tuple[str, ...] = (
    "ops",
    "scripts",
    "systemd",
    ".github/workflows",
)

_SKIP_SCAN_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        "target",
        "tests",
        "test",
        "docs",
        "doc",
        "archive",
        "archives",
        "artifacts",
        "inventory",
        "fixtures",
        "vendor",
    }
)

_OPS_TEXT_SUFFIXES: frozenset[str] = frozenset(
    {
        ".py",
        ".sh",
        ".bash",
        ".zsh",
        ".ts",
        ".js",
        ".mjs",
        ".rs",
        ".go",
        ".yml",
        ".yaml",
        ".toml",
        ".json",
        ".service",
        ".timer",
    }
)

_CODE_TEXT_SUFFIXES: frozenset[str] = frozenset(
    {
        ".py",
        ".ts",
        ".js",
        ".mjs",
        ".rs",
        ".go",
    }
)

_CENTRAL_PATH_RE = re.compile(
    r"(/central/(?:repos/[A-Za-z0-9._-]+|projects|git|seed-bank)/[A-Za-z0-9._-]+)"
)
_GITHUB_SLUG_RE = re.compile(
    r"github\.com[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"
)
_PYPROJECT_DEP_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")

# Generic single-word package or CLI names must never match by bare name alone; only
# multi-segment (`a-b`, `@scope/pkg`) or distinctive coined internal names qualify.
_AMBIGUOUS_PACKAGE_NAMES: frozenset[str] = frozenset(
    {
        "agent",
        "airstream",
        "baha",
        "brain",
        "bridgeai",
        "common",
        "context",
        "cpr",
        "crawler",
        "edifice",
        "federation",
        "flow",
        "homelab",
        "initiate",
        "intone",
        "janus",
        "joy",
        "landos",
        "ledger",
        "letters",
        "lighthouse",
        "machina",
        "miscellania",
        "orgatlas",
        "paperclip",
        "plots",
        "prompts",
        "scratchpad",
        "sdlc",
        "sentinel",
        "servers",
        "substrate",
        "toned",
        "unslop",
    }
)


def _norm_pkg_name(raw: str) -> str:
    return re.sub(r"[-_.]+", "-", raw.strip().lower()).strip("-")


def _is_distinctive_package_name(norm_name: str) -> bool:
    if not norm_name or norm_name in _AMBIGUOUS_PACKAGE_NAMES:
        return False
    if norm_name.startswith("@") and "/" in norm_name:
        return True
    if "-" in norm_name and len(norm_name) >= 7:
        return True
    return len(norm_name) >= 10


def _is_distinctive_cli_name(raw_name: str) -> bool:
    norm_s = raw_name.strip()
    if len(norm_s) < 6:
        return False
    norm_pkg = _norm_pkg_name(norm_s)
    if norm_pkg in _AMBIGUOUS_PACKAGE_NAMES:
        return False
    return True


def _read_bounded_text(path: Path) -> str | None:
    try:
        st = path.stat()
        if st.st_size <= 0 or st.st_size > _MAX_FILE_BYTES:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:4096]:
        return None
    return raw.decode("utf-8", errors="replace")


def _extract_declared_identities(repo_dir: Path) -> tuple[set[str], set[str]]:
    """Return ``(distinctive_package_names, distinctive_cli_scripts)`` declared by ``repo_dir``."""
    pkg_names: set[str] = set()
    cli_scripts: set[str] = set()

    pyproject_path = repo_dir / "pyproject.toml"
    raw_py = _read_bounded_text(pyproject_path)
    if raw_py:
        try:
            doc = tomllib.loads(raw_py)
            proj = doc.get("project") if isinstance(doc.get("project"), dict) else {}
            pname = _norm_pkg_name(str(proj.get("name") or ""))
            if _is_distinctive_package_name(pname):
                pkg_names.add(pname)
            scripts = proj.get("scripts") if isinstance(proj.get("scripts"), dict) else {}
            for sname in scripts:
                norm_s = str(sname or "").strip()
                if _is_distinctive_cli_name(norm_s):
                    cli_scripts.add(norm_s)
        except (tomllib.TOMLDecodeError, TypeError, ValueError):
            pass

    package_json_path = repo_dir / "package.json"
    raw_pkg = _read_bounded_text(package_json_path)
    if raw_pkg:
        try:
            doc = json.loads(raw_pkg)
            if isinstance(doc, dict):
                pname = str(doc.get("name") or "").strip().lower()
                norm_p = pname if pname.startswith("@") else _norm_pkg_name(pname)
                if _is_distinctive_package_name(norm_p):
                    pkg_names.add(norm_p)
                bins = doc.get("bin")
                if isinstance(bins, dict):
                    for bname in bins:
                        norm_b = str(bname or "").strip()
                        if _is_distinctive_cli_name(norm_b):
                            cli_scripts.add(norm_b)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    cargo_path = repo_dir / "Cargo.toml"
    raw_cargo = _read_bounded_text(cargo_path)
    if raw_cargo:
        try:
            doc = tomllib.loads(raw_cargo)
            pkg = doc.get("package") if isinstance(doc.get("package"), dict) else {}
            cname = _norm_pkg_name(str(pkg.get("name") or ""))
            if _is_distinctive_package_name(cname):
                pkg_names.add(cname)
        except (tomllib.TOMLDecodeError, TypeError, ValueError):
            pass

    return pkg_names, cli_scripts


def _collect_bounded_dir_files(
    repo_dir: Path,
    base_dir: Path,
    allowed_suffixes: frozenset[str],
    limit: int,
    allow_extensionless: bool,
    max_depth: int = 2,
) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []

    def _walk(cur: Path, depth: int) -> None:
        if len(out) >= limit or depth > max_depth:
            return
        try:
            with os.scandir(cur) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            return
        subdirs: list[Path] = []
        for entry in entries:
            if len(out) >= limit:
                return
            name = entry.name
            if name.startswith(".") or name in _SKIP_SCAN_DIRS:
                continue
            try:
                if entry.is_symlink():
                    continue
                if entry.is_file(follow_symlinks=False):
                    p = Path(entry.path)
                    if "." in name:
                        if p.suffix.lower() not in allowed_suffixes:
                            continue
                    elif not allow_extensionless:
                        continue
                    out.append((p.relative_to(repo_dir).as_posix(), p))
                elif depth < max_depth and entry.is_dir(follow_symlinks=False):
                    subdirs.append(Path(entry.path))
            except OSError:
                continue
        for sub in subdirs:
            if len(out) >= limit:
                return
            _walk(sub, depth + 1)

    _walk(base_dir, 1)
    return out


def _iter_ops_files(repo_dir: Path) -> list[tuple[str, Path]]:
    """Collect bounded deterministic manifest, ops, and primary package files inside ``repo_dir``."""
    collected: list[tuple[str, Path]] = []
    seen_rels: set[str] = set()

    for rel_name in _ROOT_MANIFEST_CANDIDATES:
        cand = repo_dir / rel_name
        if cand.is_file() and not cand.is_symlink():
            collected.append((rel_name, cand))
            seen_rels.add(rel_name)

    ops_candidates: list[tuple[str, Path]] = []
    for subdir in _OPS_SUBDIRS:
        if len(ops_candidates) >= _MAX_OPS_FILES_PER_REPO:
            break
        sub_path = repo_dir / subdir
        if not sub_path.is_dir() or sub_path.is_symlink():
            continue
        rem = _MAX_OPS_FILES_PER_REPO - len(ops_candidates)
        for rel, p in _collect_bounded_dir_files(
            repo_dir,
            sub_path,
            _OPS_TEXT_SUFFIXES,
            rem,
            allow_extensionless=True,
            max_depth=2,
        ):
            if rel not in seen_rels:
                ops_candidates.append((rel, p))
                seen_rels.add(rel)
    collected.extend(ops_candidates)

    code_roots: list[Path] = []
    src_dir = repo_dir / "src"
    if src_dir.is_dir() and not src_dir.is_symlink():
        code_roots.append(src_dir)
    try:
        with os.scandir(repo_dir) as it:
            top_entries = sorted(it, key=lambda e: e.name)
    except OSError:
        top_entries = []
    for entry in top_entries:
        name = entry.name
        if name.startswith(".") or name in _SKIP_SCAN_DIRS or name in _OPS_SUBDIRS or name == "src":
            continue
        try:
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        cand_dir = Path(entry.path)
        if (cand_dir / "__init__.py").is_file():
            code_roots.append(cand_dir)

    code_candidates: list[tuple[str, Path]] = []
    for croot in code_roots:
        if len(code_candidates) >= _MAX_CODE_FILES_PER_REPO:
            break
        rem = _MAX_CODE_FILES_PER_REPO - len(code_candidates)
        for rel, p in _collect_bounded_dir_files(
            repo_dir,
            croot,
            _CODE_TEXT_SUFFIXES,
            rem,
            allow_extensionless=False,
            max_depth=2,
        ):
            if rel not in seen_rels:
                code_candidates.append((rel, p))
                seen_rels.add(rel)
    collected.extend(code_candidates)
    return collected


def build_ecosystem_graph(capsules: list[dict[str, Any]]) -> dict[str, Any]:
    """Compile cross-repository dependency edges and reverse blast-radius maps."""
    project_meta: dict[str, dict[str, str]] = {}
    remote_to_pid: dict[str, str] = {}
    location_to_pid: dict[str, str] = {}
    pkg_to_pids: dict[str, set[str]] = {}
    cli_to_pids: dict[str, set[str]] = {}
    live_dirs: dict[str, Path] = {}

    for capsule in sorted(capsules, key=lambda c: str(c.get("canonical_project_id") or "")):
        pid = str(capsule.get("canonical_project_id") or "").strip()
        if not pid:
            continue
        pkey = str(capsule.get("project_key") or pid)
        dname = str(capsule.get("display_name") or pkey)
        project_meta[pid] = {
            "canonical_project_id": pid,
            "project_key": pkey,
            "display_name": dname,
        }

        for rem in capsule.get("normalized_remotes") or []:
            norm_r = str(rem or "").strip().lower()
            if norm_r and norm_r not in remote_to_pid:
                remote_to_pid[norm_r] = pid

        for obs in capsule.get("observations") or []:
            if not isinstance(obs, dict):
                continue
            loc = str(obs.get("location") or "").strip().rstrip("/")
            if loc.startswith("/"):
                location_to_pid.setdefault(loc, pid)

        pref = capsule.get("preferred_location") if isinstance(capsule.get("preferred_location"), dict) else {}
        pref_loc = str(pref.get("location") or "").strip()
        if pref.get("state") == "resolved" and pref_loc.startswith("/"):
            p_dir = Path(pref_loc)
            if p_dir.is_dir():
                live_dirs[pid] = p_dir
                location_to_pid.setdefault(pref_loc.rstrip("/"), pid)
                pkgs, clis = _extract_declared_identities(p_dir)
                for pkg in pkgs:
                    pkg_to_pids.setdefault(pkg, set()).add(pid)
                for cli_name in clis:
                    cli_to_pids.setdefault(cli_name, set()).add(pid)

    # Keep only uniquely owned package/CLI identifiers to prevent ambiguous false merges.
    unique_pkg_to_pid = {k: next(iter(v)) for k, v in pkg_to_pids.items() if len(v) == 1}
    unique_cli_to_pid = {k: next(iter(v)) for k, v in cli_to_pids.items() if len(v) == 1}

    cli_pattern: re.Pattern[str] | None = None
    if unique_cli_to_pid:
        escaped = sorted((re.escape(k) for k in unique_cli_to_pid), key=len, reverse=True)
        cli_pattern = re.compile(r"(?<![A-Za-z0-9_.-])(" + "|".join(escaped) + r")(?![A-Za-z0-9_.-])")

    # Keyed by (source_pid, target_pid, relation_kind) -> (evidence_path, matched_token)
    edge_map: dict[tuple[str, str, str], tuple[str, str]] = {}

    def _record_edge(
        src_pid: str,
        dst_pid: str,
        relation_kind: str,
        evidence_path: str,
        matched_token: str,
    ) -> None:
        if not dst_pid or src_pid == dst_pid or dst_pid not in project_meta:
            return
        key = (src_pid, dst_pid, relation_kind)
        cand = (evidence_path, matched_token)
        if key not in edge_map or cand < edge_map[key]:
            edge_map[key] = cand

    for src_pid, repo_dir in sorted(live_dirs.items()):
        for rel_path, file_path in _iter_ops_files(repo_dir):
            text = _read_bounded_text(file_path)
            if not text:
                continue

            if rel_path == ".gitmodules":
                cp = configparser.ConfigParser(interpolation=None)
                try:
                    cp.read_string(text)
                    for section in cp.sections():
                        raw_url = cp.get(section, "url", fallback="").strip()
                        norm_rem = normalize_remote_identity(raw_url)
                        if norm_rem and norm_rem.lower() in remote_to_pid:
                            _record_edge(
                                src_pid,
                                remote_to_pid[norm_rem.lower()],
                                "git_submodule",
                                rel_path,
                                norm_rem,
                            )
                except configparser.Error:
                    pass
                continue

            if rel_path == "pyproject.toml":
                try:
                    doc = tomllib.loads(text)
                    proj = doc.get("project") if isinstance(doc.get("project"), dict) else {}
                    dep_lists: list[str] = list(proj.get("dependencies") or [])
                    opt = proj.get("optional-dependencies")
                    if isinstance(opt, dict):
                        for group_deps in opt.values():
                            if isinstance(group_deps, list):
                                dep_lists.extend(str(x) for x in group_deps)
                    for raw_dep in dep_lists:
                        m = _PYPROJECT_DEP_NAME_RE.match(str(raw_dep))
                        if m:
                            dname = _norm_pkg_name(m.group(1))
                            if dname in unique_pkg_to_pid:
                                _record_edge(
                                    src_pid,
                                    unique_pkg_to_pid[dname],
                                    "package_dependency",
                                    rel_path,
                                    dname,
                                )
                except (tomllib.TOMLDecodeError, TypeError, ValueError):
                    pass

            elif rel_path == "package.json":
                try:
                    doc = json.loads(text)
                    if isinstance(doc, dict):
                        for field in ("dependencies", "devDependencies", "peerDependencies"):
                            deps_obj = doc.get(field)
                            if isinstance(deps_obj, dict):
                                for raw_k in deps_obj:
                                    k_str = str(raw_k).strip().lower()
                                    norm_k = k_str if k_str.startswith("@") else _norm_pkg_name(k_str)
                                    if norm_k in unique_pkg_to_pid:
                                        _record_edge(
                                            src_pid,
                                            unique_pkg_to_pid[norm_k],
                                            "package_dependency",
                                            rel_path,
                                            norm_k,
                                        )
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass

            for path_match in _CENTRAL_PATH_RE.finditer(text):
                matched_path = path_match.group(1).rstrip("/")
                if matched_path in location_to_pid:
                    _record_edge(
                        src_pid,
                        location_to_pid[matched_path],
                        "estate_path_reference",
                        rel_path,
                        matched_path,
                    )

            for gh_match in _GITHUB_SLUG_RE.finditer(text):
                slug = gh_match.group(1).rstrip("/")
                if slug.endswith(".git"):
                    slug = slug[:-4]
                norm_gh = f"github.com/{slug}".lower()
                if norm_gh in remote_to_pid:
                    _record_edge(
                        src_pid,
                        remote_to_pid[norm_gh],
                        "github_repo_reference",
                        rel_path,
                        f"github.com/{slug}",
                    )

            if cli_pattern is not None and rel_path not in ("pyproject.toml", "package.json", "Cargo.toml"):
                for cli_match in cli_pattern.finditer(text):
                    token = cli_match.group(1)
                    if token in unique_cli_to_pid:
                        _record_edge(
                            src_pid,
                            unique_cli_to_pid[token],
                            "cli_or_mcp_binding",
                            rel_path,
                            token,
                        )

    edges: list[dict[str, str]] = []
    outbound: dict[str, dict[str, dict[str, Any]]] = {pid: {} for pid in project_meta}
    inbound: dict[str, dict[str, dict[str, Any]]] = {pid: {} for pid in project_meta}

    for (src_pid, dst_pid, rel_kind), (ev_path, tok) in sorted(
        edge_map.items(),
        key=lambda item: (
            project_meta[item[0][0]]["project_key"],
            project_meta[item[0][1]]["project_key"],
            item[0][2],
        ),
    ):
        src_meta = project_meta[src_pid]
        dst_meta = project_meta[dst_pid]
        edges.append(
            {
                "source_project_id": src_pid,
                "source_project_key": src_meta["project_key"],
                "target_project_id": dst_pid,
                "target_project_key": dst_meta["project_key"],
                "relation_kind": rel_kind,
                "evidence_path": ev_path,
                "matched_token": tok,
            }
        )
        out_entry = outbound[src_pid].setdefault(
            dst_pid,
            {
                "canonical_project_id": dst_pid,
                "project_key": dst_meta["project_key"],
                "relation_kinds": [],
                "evidence_path": ev_path,
            },
        )
        if rel_kind not in out_entry["relation_kinds"]:
            out_entry["relation_kinds"].append(rel_kind)

        in_entry = inbound[dst_pid].setdefault(
            src_pid,
            {
                "canonical_project_id": src_pid,
                "project_key": src_meta["project_key"],
                "relation_kinds": [],
                "evidence_path": ev_path,
            },
        )
        if rel_kind not in in_entry["relation_kinds"]:
            in_entry["relation_kinds"].append(rel_kind)

    by_project: dict[str, dict[str, Any]] = {}
    for pid in project_meta:
        deps = sorted(outbound[pid].values(), key=lambda x: str(x["project_key"]))
        consumers = sorted(inbound[pid].values(), key=lambda x: str(x["project_key"]))
        by_project[pid] = {
            "depends_on_count": len(deps),
            "depended_on_by_count": len(consumers),
            "depends_on": deps,
            "depended_on_by": consumers,
        }

    return {
        "edge_count": len(edges),
        "edges": edges,
        "by_project": by_project,
    }
