"""Source fingerprint and incremental extraction checkpoint cache (P5.1).

Computes deterministic, pure-Python adapter-native candidate and source
fingerprints so ``ledger refresh`` skips expensive ``git`` subprocess bursts
and redundant metadata extraction across unchanged sources/candidates while
preserving byte-identical compatibility entries to ``collect_entries``.

Unlike Wave 1's shallow ``probe_fingerprint`` (root-stat/artifact probe only,
which SCHEMA.md §1.1 explicitly forbids treating as proof that descendant
filesystem trees are unchanged), ``checkpoint_fingerprint`` incorporates:

- effective source and default configuration plus ``output_dir`` link base;
- pure-Python ``.git`` state (``HEAD``, ``config``, ``packed-refs``, and all
  loose refs under ``refs/heads`` and ``refs/remotes``, including gitfile
  worktree ``gitdir`` / ``commondir`` pointers);
- ``.obsidian`` vault presence, ``README`` content digest, and sidecar content
  digest;
- bounded descendant filesystem tree metadata (visited directories and file
  ``(rel_path, st_mtime_ns, st_size)`` tuples using the exact same pruning and
  ``max_entries`` / ``max_depth`` traversal bounds as ``summarize_tree``);
- ``inventory_policy`` candidate records, policy root metadata, and any mounted
  mirror README/sidecar/git state.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
from datetime import datetime, timezone
from pathlib import Path

from . import COMPILER_VERSION, SOURCE_CHECKPOINT_SCHEMA_VERSION
from .ids import digest_json, source_id_for
from .sources.base import ADAPTERS, get_adapter
from .sources.filesystem import is_shadowed_symlink
from .sources.common import (
    README_CANDIDATES,
    SIDECAR_CANDIDATES,
    find_first_existing,
    iter_shallow_files,
    merge_exclude_names,
    resolve_path,
    safe_path_exists,
    safe_path_is_dir,
)

CHECKPOINT_FILENAME = "source-checkpoints.json"
CHECKPOINT_STATUS_VALUES = frozenset(
    {"hit", "partial", "miss", "unavailable", "bypassed", "not_applicable"}
)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _feed(hasher: hashlib._Hash, tag: str, value: object) -> None:
    hasher.update(tag.encode("utf-8"))
    hasher.update(b"\x00")
    if isinstance(value, bytes):
        hasher.update(value)
    elif isinstance(value, str):
        hasher.update(value.encode("utf-8", errors="surrogatepass"))
    else:
        hasher.update(
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
                "utf-8"
            )
        )
    hasher.update(b"\x1e")


def _file_bytes_sha256(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "unreadable"


def _resolve_git_dirs(directory: Path) -> tuple[Path, Path] | None:
    git_marker = directory / ".git"
    if not safe_path_exists(git_marker):
        return None
    if safe_path_is_dir(git_marker):
        return git_marker, git_marker
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
    return git_dir, common_dir


def _hash_refs_tree(hasher: hashlib._Hash, scope: str, refs_root: Path) -> None:
    if not safe_path_is_dir(refs_root):
        _feed(hasher, f"refs:{scope}:absent", True)
        return
    ref_records: list[tuple[str, bytes]] = []
    for root, dirs, files in os.walk(refs_root, topdown=True):
        dirs.sort()
        files.sort()
        root_path = Path(root)
        for name in files:
            fp = root_path / name
            try:
                rel = fp.relative_to(refs_root).as_posix()
                data = fp.read_bytes()
            except OSError:
                continue
            ref_records.append((rel, data))
    _feed(hasher, f"refs:{scope}:count", len(ref_records))
    for rel, data in ref_records:
        _feed(hasher, f"ref:{scope}:{rel}", data)


def git_repo_fingerprint(directory: Path) -> str | None:
    """Compute a pure-Python fingerprint of ``.git`` metadata read by ``gather_git_metadata``."""
    resolved = _resolve_git_dirs(directory)
    if resolved is None:
        return None
    git_dir, common_dir = resolved
    h = hashlib.sha256()
    try:
        _feed(h, "dir", str(directory.resolve()))
    except OSError:
        _feed(h, "dir", str(directory))

    for label, target in (
        ("HEAD", git_dir / "HEAD"),
        ("index", git_dir / "index"),
        ("config", common_dir / "config"),
        ("packed-refs", common_dir / "packed-refs"),
    ):
        try:
            _feed(h, f"file:{label}", target.read_bytes())
        except OSError:
            _feed(h, f"file:{label}:missing", True)

    _hash_refs_tree(h, "git:heads", git_dir / "refs" / "heads")
    _hash_refs_tree(h, "git:remotes", git_dir / "refs" / "remotes")
    if common_dir != git_dir:
        _hash_refs_tree(h, "common:heads", common_dir / "refs" / "heads")
        _hash_refs_tree(h, "common:remotes", common_dir / "refs" / "remotes")
    return h.hexdigest()


def _local_tz_offset_seconds() -> int:
    offset = datetime.now().astimezone().utcoffset()
    return int(offset.total_seconds()) if offset is not None else 0


def _effective_machine_fallback(root_cfg: dict) -> str:
    return (
        str(root_cfg.get("machine_name", "")).strip()
        or platform.node()
        or os.environ.get("COMPUTERNAME", "")
    )


def _source_config_digest(root_cfg: dict, defaults: dict, output_dir: Path, source_id: str) -> str:
    exclude_names = sorted(merge_exclude_names(root_cfg, defaults))
    discovery = str(root_cfg.get("discovery", "children")).strip() or "children"
    payload = {
        "checkpoint_schema_version": SOURCE_CHECKPOINT_SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "source_id": source_id,
        "discovery": discovery,
        "resolved_path": str(root_cfg["_resolved_path"]),
        "label": str(root_cfg.get("label", "")),
        "storage_scope": str(root_cfg.get("storage_scope", "")),
        "machine_fallback": _effective_machine_fallback(root_cfg),
        "remote_name": str(root_cfg.get("remote_name", "")),
        "exclude_names": exclude_names,
        "force_include_names": sorted(str(x) for x in root_cfg.get("force_include_names", [])),
        "min_score": int(root_cfg.get("min_score", defaults.get("min_score", 2))),
        "max_depth": int(root_cfg.get("max_depth", defaults.get("git_repo_max_depth", 6))),
        "tree_scan_max_entries": int(
            root_cfg.get("tree_scan_max_entries", defaults.get("tree_scan_max_entries", 5000))
        ),
        "tree_scan_max_depth": int(
            root_cfg.get("tree_scan_max_depth", defaults.get("tree_scan_max_depth", 8))
        ),
        "policy_crawl_treatments": sorted(
            str(item).strip()
            for item in root_cfg.get("policy_crawl_treatments", ["project_discovery"])
            if str(item).strip()
        ),
        "require_project_ledger_candidate": bool(
            root_cfg.get("require_project_ledger_candidate", True)
        ),
        "local_tz_offset_seconds": _local_tz_offset_seconds(),
    }
    return digest_json(payload)


def filesystem_candidate_fingerprint(
    directory: Path,
    root_cfg: dict,
    defaults: dict,
    output_dir: Path,
    *,
    source_cfg_digest: str,
) -> str | None:
    """Compute a deterministic fingerprint covering all inputs to ``build_entry``."""
    if not safe_path_exists(directory) or not safe_path_is_dir(directory):
        return None
    exclude_names = merge_exclude_names(root_cfg, defaults)
    max_entries = int(
        root_cfg.get("tree_scan_max_entries", defaults.get("tree_scan_max_entries", 5000))
    )
    max_depth = int(
        root_cfg.get("tree_scan_max_depth", defaults.get("tree_scan_max_depth", 8))
    )

    h = hashlib.sha256()
    _feed(h, "source_cfg_digest", source_cfg_digest)
    try:
        _feed(h, "directory", str(directory.resolve()))
    except OSError:
        _feed(h, "directory", str(directory))

    try:
        dir_stat = directory.stat()
        _feed(h, "dir_mtime_ns", dir_stat.st_mtime_ns)
    except OSError:
        _feed(h, "dir_mtime_ns", None)

    _feed(h, "git_fp", git_repo_fingerprint(directory))
    _feed(h, "has_obsidian", safe_path_is_dir(directory / ".obsidian"))

    readme_path = find_first_existing(directory, README_CANDIDATES)
    _feed(
        h,
        "readme",
        [
            readme_path.relative_to(directory).as_posix() if readme_path else None,
            _file_bytes_sha256(readme_path),
        ],
    )

    sidecar_path = find_first_existing(directory, SIDECAR_CANDIDATES)
    _feed(
        h,
        "sidecar",
        [
            sidecar_path.relative_to(directory).as_posix() if sidecar_path else None,
            _file_bytes_sha256(sidecar_path),
        ],
    )
    machina_state_path = directory / ".machina" / "state.json"
    _feed(
        h,
        "machina_state",
        _file_bytes_sha256(machina_state_path if safe_path_exists(machina_state_path) else None),
    )

    shallow_rels = sorted(
        fp.relative_to(directory).as_posix()
        for fp in iter_shallow_files(directory, exclude_names)
    )
    _feed(h, "shallow_files", shallow_rels)

    base_str = str(directory)
    base_prefix_len = len(base_str) + (0 if base_str.endswith(os.sep) else 1)
    scanned_entries = 0
    truncated = False
    visited_files: list[str] = []
    visited_dirs: list[str] = []

    stack: list[tuple[str, int]] = [(base_str, 0)]
    while stack:
        curr_dir, rel_depth = stack.pop()
        if rel_depth > 0:
            visited_dirs.append(curr_dir[base_prefix_len:].replace(os.sep, "/"))
        try:
            with os.scandir(curr_dir) as it:
                dir_entries = list(it)
        except OSError:
            continue

        sub_dirs: list[tuple[str, int]] = []
        for entry in dir_entries:
            name = entry.name
            try:
                is_sym = entry.is_symlink()
                if entry.is_dir(follow_symlinks=False):
                    if name in exclude_names or rel_depth + 1 > max_depth:
                        continue
                    sub_dirs.append((entry.path, rel_depth + 1))
                elif is_sym and entry.is_dir(follow_symlinks=True):
                    continue
                elif entry.is_file(follow_symlinks=True):
                    st = entry.stat(follow_symlinks=True)
                    scanned_entries += 1
                    rel_file = entry.path[base_prefix_len:].replace(os.sep, "/")
                    visited_files.append(f"{rel_file}\x00{st.st_mtime_ns}\x00{st.st_size}")
                    if scanned_entries >= max_entries:
                        truncated = True
                        break
            except OSError:
                continue
        if truncated:
            break
        for sd in reversed(sub_dirs):
            stack.append(sd)

    visited_dirs.sort()
    visited_files.sort()
    _feed(h, "visited_dirs", "\x1f".join(visited_dirs))
    _feed(h, "tree_meta", [scanned_entries, truncated])
    _feed(h, "visited_files", "\x1f".join(visited_files))

    return h.hexdigest()


def inventory_candidate_fingerprint(
    candidate: dict,
    root_cfg: dict,
    defaults: dict,
    output_dir: Path,
    *,
    source_cfg_digest: str,
) -> str:
    """Compute a deterministic fingerprint covering all inputs to ``build_inventory_entry``."""
    h = hashlib.sha256()
    _feed(h, "source_cfg_digest", source_cfg_digest)
    _feed(h, "candidate_kind", str(candidate.get("candidate_kind", "")))
    _feed(h, "inventory_path", str(candidate.get("inventory_path", "")))
    _feed(h, "root_label", str(candidate.get("root_label", "")))
    _feed(h, "policy_root", candidate.get("policy_root") or {})

    summary = dict(candidate.get("summary") or {})
    latest_ts = summary.get("latest_ts")
    if isinstance(latest_ts, datetime):
        summary["latest_ts"] = latest_ts.isoformat()
    _feed(h, "summary", summary)

    filesystem_path = Path(candidate["filesystem_path"])
    fs_exists = safe_path_exists(filesystem_path)
    fs_is_dir = safe_path_is_dir(filesystem_path) if fs_exists else False
    fs_resolved: str | None = None
    if fs_exists:
        try:
            fs_resolved = str(filesystem_path.resolve())
        except OSError:
            fs_resolved = None
    _feed(h, "fs_state", [fs_exists, fs_is_dir, fs_resolved])

    if fs_is_dir:
        readme_path = find_first_existing(filesystem_path, README_CANDIDATES)
        sidecar_path = find_first_existing(filesystem_path, SIDECAR_CANDIDATES)
        _feed(
            h,
            "readme",
            [
                readme_path.relative_to(filesystem_path).as_posix() if readme_path else None,
                _file_bytes_sha256(readme_path),
            ],
        )
        _feed(
            h,
            "sidecar",
            [
                sidecar_path.relative_to(filesystem_path).as_posix() if sidecar_path else None,
                _file_bytes_sha256(sidecar_path),
            ],
        )
        machina_state_path = filesystem_path / ".machina" / "state.json"
        _feed(
            h,
            "machina_state",
            _file_bytes_sha256(machina_state_path if safe_path_exists(machina_state_path) else None),
        )
        _feed(h, "git_fp", git_repo_fingerprint(filesystem_path))

    return h.hexdigest()


def load_source_checkpoints(path: Path | None) -> dict | None:
    """Load persisted source checkpoints, returning None on missing/corrupt/incompatible files."""
    if path is None or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != SOURCE_CHECKPOINT_SCHEMA_VERSION:
        return None
    if not isinstance(payload.get("sources"), dict):
        return None
    return payload


def write_source_checkpoints(path: Path, payload: dict) -> None:
    """Atomically persist ``state/source-checkpoints.json``."""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.tmp"
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def collect_entries_incremental(
    config: dict,
    config_dir: Path,
    output_dir: Path,
    *,
    checkpoint_path: Path | None = None,
    bypass_cache: bool = False,
    now_iso: str | None = None,
) -> tuple[list[dict], dict]:
    """Collect compatibility entries using per-source/per-candidate fingerprint checkpoints.

    Returns ``(entries, checkpoint_payload)`` where ``entries`` is byte-identical
    in content and sort order to ``ledger.sources.base.collect_entries``.
    """
    defaults = config.get("defaults", {})
    roots = config.get("roots", [])
    if not roots:
        raise ValueError("Config must contain at least one root in roots[].")

    checked_at = now_iso or _now_utc()
    prior_payload = None if bypass_cache else load_source_checkpoints(checkpoint_path)
    prior_sources = (
        prior_payload.get("sources", {})
        if isinstance(prior_payload, dict) and isinstance(prior_payload.get("sources"), dict)
        else {}
    )

    entries: list[dict] = []
    seen_paths: set[str] = set()
    sources_checkpoint: dict[str, dict] = {}

    hit_source_count = 0
    partial_source_count = 0
    miss_source_count = 0
    unavailable_source_count = 0
    bypassed_source_count = 0
    total_candidate_count = 0
    total_reused_candidates = 0
    total_rebuilt_candidates = 0

    prepared_roots: list[dict] = []
    all_real_dirs: list[Path] = []

    for root_index, root_cfg_raw in enumerate(roots):
        if not isinstance(root_cfg_raw, dict):
            raise ValueError(f"roots[{root_index}] must be a JSON object.")

        root_cfg = dict(root_cfg_raw)
        raw_root_path = str(root_cfg.get("path", "")).strip()
        if not raw_root_path:
            raise ValueError(f"roots[{root_index}].path is required.")

        discovery = str(root_cfg.get("discovery", "children")).strip() or "children"
        if discovery not in ADAPTERS:
            raise ValueError(f"Unsupported discovery mode in roots[{root_index}]: {discovery}")

        source_id = source_id_for(root_cfg, index=root_index)
        root_path = resolve_path(raw_root_path, config_dir)
        root_cfg["_resolved_path"] = root_path

        if discovery == "inventory_policy":
            inventory_jsonl_raw = str(root_cfg.get("inventory_jsonl", "")).strip()
            policy_path_raw = str(root_cfg.get("policy_path", "")).strip()
            missing_fields = [
                field_name
                for field_name, field_value in (
                    ("inventory_jsonl", inventory_jsonl_raw),
                    ("policy_path", policy_path_raw),
                )
                if not field_value
            ]
            if missing_fields:
                joined = ", ".join(missing_fields)
                raise ValueError(
                    f"roots[{root_index}] with discovery=inventory_policy requires: {joined}."
                )

            inventory_jsonl_path = resolve_path(inventory_jsonl_raw, config_dir)
            policy_path = resolve_path(policy_path_raw, config_dir)
            if not safe_path_exists(inventory_jsonl_path):
                raise ValueError(
                    f"roots[{root_index}].inventory_jsonl does not exist: {inventory_jsonl_path}"
                )
            if not safe_path_exists(policy_path):
                raise ValueError(
                    f"roots[{root_index}].policy_path does not exist: {policy_path}"
                )
            root_cfg["_inventory_jsonl_path"] = inventory_jsonl_path
            root_cfg["_policy_path"] = policy_path
            source_available = True
        else:
            if "inventory_jsonl" in root_cfg:
                root_cfg["_inventory_jsonl_path"] = resolve_path(
                    root_cfg["inventory_jsonl"], config_dir
                )
            if "policy_path" in root_cfg:
                root_cfg["_policy_path"] = resolve_path(root_cfg["policy_path"], config_dir)
            source_available = safe_path_exists(root_path)

        source_cfg_digest = _source_config_digest(root_cfg, defaults, output_dir, source_id)
        if not source_available:
            prepared_roots.append(
                {
                    "source_id": source_id,
                    "discovery": discovery,
                    "root_cfg": root_cfg,
                    "source_available": False,
                    "source_cfg_digest": source_cfg_digest,
                }
            )
            continue

        exclude_names = merge_exclude_names(root_cfg, defaults)
        adapter = get_adapter(discovery)
        candidates = adapter.discover_candidates(root_cfg, defaults, exclude_names)
        force_include = {str(x) for x in root_cfg.get("force_include_names", []) if str(x)}
        for candidate in candidates:
            if isinstance(candidate, Path):
                try:
                    if not candidate.is_symlink() and safe_path_exists(candidate):
                        all_real_dirs.append(candidate.resolve())
                except OSError:
                    continue
        prepared_roots.append(
            {
                "source_id": source_id,
                "discovery": discovery,
                "root_cfg": root_cfg,
                "source_available": True,
                "source_cfg_digest": source_cfg_digest,
                "adapter": adapter,
                "candidates": candidates,
                "force_include": force_include,
            }
        )

    for prep in prepared_roots:
        source_id = prep["source_id"]
        discovery = prep["discovery"]
        root_cfg = prep["root_cfg"]
        source_cfg_digest = prep["source_cfg_digest"]

        if not prep["source_available"]:
            unavailable_source_count += 1
            unavail_fp = digest_json(
                {
                    "source_id": source_id,
                    "config_digest": source_cfg_digest,
                    "status": "unavailable",
                }
            )
            prior_src = prior_sources.get(source_id) if isinstance(prior_sources, dict) else None
            sources_checkpoint[source_id] = {
                "source_id": source_id,
                "discovery": discovery,
                "status": "unavailable",
                "checkpoint_status": "unavailable",
                "checkpoint_fingerprint": unavail_fp,
                "config_digest": source_cfg_digest,
                "checked_at": checked_at,
                "last_extracted_at": (
                    prior_src.get("last_extracted_at") if isinstance(prior_src, dict) else None
                ),
                "candidate_count": 0,
                "reused_candidate_count": 0,
                "rebuilt_candidate_count": 0,
                "entry_count": 0,
                "candidates": {},
            }
            continue

        adapter = prep["adapter"]
        candidates = prep["candidates"]
        force_include = prep["force_include"]

        prior_src = prior_sources.get(source_id) if isinstance(prior_sources, dict) else None
        prior_cands: dict[str, dict] = {}
        if (
            not bypass_cache
            and isinstance(prior_src, dict)
            and prior_src.get("config_digest") == source_cfg_digest
            and isinstance(prior_src.get("candidates"), dict)
        ):
            prior_cands = prior_src["candidates"]

        current_cands: dict[str, dict] = {}
        ordered_cand_fps: list[list[str | None]] = []
        source_reused = 0
        source_rebuilt = 0
        source_entries = 0

        for candidate in candidates:
            if isinstance(candidate, dict):
                cand_key = str(
                    candidate.get("path_key", candidate.get("inventory_path", ""))
                ).lower()
                if cand_key in seen_paths:
                    continue
                seen_paths.add(cand_key)
                cand_fp = inventory_candidate_fingerprint(
                    candidate,
                    root_cfg,
                    defaults,
                    output_dir,
                    source_cfg_digest=source_cfg_digest,
                )
            else:
                if is_shadowed_symlink(candidate, all_real_dirs, force_include):
                    continue
                cand_key = str(candidate.resolve()).lower()
                if cand_key in seen_paths:
                    continue
                seen_paths.add(cand_key)
                cand_fp = filesystem_candidate_fingerprint(
                    candidate,
                    root_cfg,
                    defaults,
                    output_dir,
                    source_cfg_digest=source_cfg_digest,
                )

            ordered_cand_fps.append([cand_key, cand_fp])
            cached_cand = prior_cands.get(cand_key) if cand_fp is not None else None
            if (
                isinstance(cached_cand, dict)
                and cached_cand.get("fingerprint") == cand_fp
                and isinstance(cached_cand.get("included"), bool)
                and (not cached_cand["included"] or isinstance(cached_cand.get("entry"), dict))
            ):
                source_reused += 1
                included = bool(cached_cand["included"])
                entry = dict(cached_cand["entry"]) if included else None
            else:
                source_rebuilt += 1
                entry = adapter.build(candidate, root_cfg, defaults, output_dir)
                included = entry is not None

            cand_record: dict[str, object] = {
                "fingerprint": cand_fp,
                "included": included,
                "entry": entry,
            }
            if (
                included
                and cand_fp is not None
                and isinstance(cached_cand, dict)
                and cached_cand.get("fingerprint") == cand_fp
                and cached_cand.get("compile_cache_fingerprint") == cand_fp
            ):
                cand_record["compile_cache_fingerprint"] = cand_fp
                if isinstance(cached_cand.get("sidecar_envelope"), dict):
                    cand_record["sidecar_envelope"] = cached_cand["sidecar_envelope"]
                if isinstance(cached_cand.get("git_drift"), dict):
                    cand_record["git_drift"] = cached_cand["git_drift"]
            current_cands[cand_key] = cand_record
            if entry is not None:
                source_entries += 1
                entries.append(entry)

        cand_total = source_reused + source_rebuilt
        total_candidate_count += cand_total
        total_reused_candidates += source_reused
        total_rebuilt_candidates += source_rebuilt

        checkpoint_fp = digest_json(
            {
                "source_id": source_id,
                "config_digest": source_cfg_digest,
                "status": "available",
                "candidates": ordered_cand_fps,
            }
        )

        if bypass_cache:
            checkpoint_status = "bypassed"
            bypassed_source_count += 1
            last_extracted_at = checked_at
        elif (
            isinstance(prior_src, dict)
            and prior_src.get("checkpoint_fingerprint") == checkpoint_fp
            and source_rebuilt == 0
        ):
            checkpoint_status = "hit"
            hit_source_count += 1
            last_extracted_at = str(prior_src.get("last_extracted_at") or checked_at)
        elif source_reused > 0 and source_rebuilt > 0:
            checkpoint_status = "partial"
            partial_source_count += 1
            last_extracted_at = checked_at
        else:
            checkpoint_status = "miss"
            miss_source_count += 1
            last_extracted_at = checked_at

        sources_checkpoint[source_id] = {
            "source_id": source_id,
            "discovery": discovery,
            "status": "available",
            "checkpoint_status": checkpoint_status,
            "checkpoint_fingerprint": checkpoint_fp,
            "config_digest": source_cfg_digest,
            "checked_at": checked_at,
            "last_extracted_at": last_extracted_at,
            "candidate_count": cand_total,
            "reused_candidate_count": source_reused,
            "rebuilt_candidate_count": source_rebuilt,
            "entry_count": source_entries,
            "candidates": current_cands,
        }

    entries.sort(key=lambda item: (item["last_touch_at"], item["name"].lower()), reverse=True)

    if checkpoint_path is not None:
        from .models import portable_artifact_path

        portable_output_dir = portable_artifact_path(
            output_dir, state_dir=checkpoint_path.resolve().parent
        )
    else:
        portable_output_dir = output_dir.resolve().name or "output"

    checkpoint_payload = {
        "schema_version": SOURCE_CHECKPOINT_SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "updated_at": checked_at,
        "config_digest": digest_json(config),
        "output_dir": portable_output_dir,
        "mode": "bypassed" if bypass_cache else "incremental",
        "summary": {
            "source_count": len(sources_checkpoint),
            "hit_source_count": hit_source_count,
            "partial_source_count": partial_source_count,
            "miss_source_count": miss_source_count,
            "unavailable_source_count": unavailable_source_count,
            "bypassed_source_count": bypassed_source_count,
            "candidate_count": total_candidate_count,
            "reused_candidate_count": total_reused_candidates,
            "rebuilt_candidate_count": total_rebuilt_candidates,
        },
        "sources": sources_checkpoint,
    }

    if checkpoint_path is not None:
        write_source_checkpoints(checkpoint_path, checkpoint_payload)

    return entries, checkpoint_payload
