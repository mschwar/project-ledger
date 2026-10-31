"""Project capsules and preferred-location resolver (Wave 3 — P3.2, P3.4, P3.5).

Materializes one bounded semantic cache per canonical project at
``state/projects/<canonical_project_id>.json`` and exposes the agent read-plane
query helpers ``show_payload`` (``ledger show <project>``) and ``locate_payload``
(``ledger locate <project>``).

Design invariants:

- Every location ranking is deterministic and explainable with explicit reason
  codes and confidence (no hidden heuristics).
- Live accessible working trees outrank mirrors, which outrank backups, which
  outrank off-host inventory-only entries; unavailable sources never outrank
  accessible ones.
- Each capsule is bounded by an explicit byte/context budget
  (``CAPSULE_MAX_BYTES = 16384``) and links to deeper provenance artifacts via
  portable output-root-relative POSIX pointers rather than duplicating full
  observation or evidence payloads or leaking ephemeral ``/tmp/tmp.*`` staging paths.
- Read-only ingestion of ``.ctx/skeleton.json`` and ``ctx`` Tier-2 fleet capsules
  (``FleetCapsuleResult`` / ``RepoSkeleton`` / Tier-2 ``.ctx/``) enriches project
  capsules with deterministic structural hashes, authority boundaries, entrypoints,
  and bounded subsystem maps when present on disk.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from . import COMPILER_VERSION, PROJECT_CAPSULE_SCHEMA_VERSION
from .gbrain import resolve_gbrain_link
from .human_view import write_canonical_markdown
from .identity import observation_facts, resolve_payload
from .models import parse_iso_datetime, portable_artifact_path
from .sources.git import resolve_observation_git_drift
from .structural_ctx import extract_structural_ctx

CAPSULE_MAX_BYTES = 16384
CAPSULE_MAX_OBSERVATION_SUMMARIES = 25
CAPSULE_MAX_PROVENANCE_REFS = 5
CAPSULE_PURPOSE_MAX_CHARS = 240
CAPSULE_MAX_CTX_LANGUAGES = 10
CAPSULE_MAX_CTX_ENTRYPOINTS = 10
CAPSULE_MAX_CTX_BOUNDARY_DOCS = 8
CAPSULE_MAX_CTX_SUBSYSTEMS = 12
CAPSULE_MAX_CTX_SYMBOLS = 6
CAPSULE_MAX_CTX_DEPS = 8

SOURCE_CLASS_TIER = {
    "live": 4,
    "mirror": 3,
    "backup": 2,
    "inventory": 1,
    "unknown": 0,
}

SOURCE_CLASS_REASON = {
    "live": "SOURCE_CLASS_LIVE",
    "mirror": "SOURCE_CLASS_MIRROR",
    "backup": "SOURCE_CLASS_BACKUP",
    "inventory": "SOURCE_CLASS_INVENTORY",
    "unknown": "SOURCE_CLASS_UNKNOWN",
}

_FRONTMATTER_KV_RE = re.compile(r'^([a-zA-Z0-9_]+):\s*(.+?)\s*$')


def _parse_iso_instant(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = re.sub(r"\.(\d{6})\d+(?=[+-]\d{2}:\d{2}$|Z$|$)", r".\1", text)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _iso_epoch(value: str | None) -> float:
    epoch = parse_iso_datetime(str(value or ""))
    return epoch if epoch is not None else -1.0


def _commits_prefix_match(commit_a: str, commit_b: str) -> bool:
    a = commit_a.strip().lower()
    b = commit_b.strip().lower()
    if not a or not b or a in {"none", "null", "unknown"} or b in {"none", "null", "unknown"}:
        return False
    return a.startswith(b) or b.startswith(a)


def _infer_source_class(observation: dict, compat: dict) -> str:
    label = str(compat.get("source_label") or observation.get("source_id") or "").lower()
    loc = str(observation.get("location") or compat.get("path") or "").lower()
    if "inventory" in label or (":" in loc and not loc.startswith("/")):
        return "inventory"
    if "backup" in label or "/backup" in loc or "/archive" in loc:
        return "backup"
    if "mirror" in label or "/mirrors/" in loc:
        return "mirror"
    if loc.startswith("/"):
        return "live"
    return "unknown"


def _observation_content_as_of(compat: dict) -> tuple[str | None, str | None, str | None, float]:
    """Return (content_as_of, basis, reason_code, epoch) for one observation."""
    for field, basis, reason in (
        ("last_remote_ref_at", "compat.entry.last_remote_ref_at", "FRESH_REMOTE_REF"),
        ("head_commit_at", "compat.entry.head_commit_at", "FRESH_GIT_COMMIT"),
        ("last_touch_at", "compat.entry.last_touch_at", "FRESH_FILESYSTEM_TOUCH"),
    ):
        raw = str(compat.get(field) or "").strip()
        epoch = _iso_epoch(raw)
        if epoch >= 0.0:
            return raw, basis, reason, epoch
    return None, None, None, -1.0


def _absent_ctx_capsule() -> dict[str, Any]:
    return {
        "state": "absent",
        "source_format": None,
        "observation_id": None,
        "artifact_path": None,
        "structural_hash": None,
        "boundary_doc_hash": None,
        "total_files": None,
        "languages": {},
        "entrypoints": [],
        "authority_boundaries": [],
        "subsystems": [],
        "subsystem_count": 0,
        "subsystems_truncated": False,
    }


def _parse_tier2_frontmatter(md_path: Path) -> dict[str, str]:
    try:
        lines = md_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    if not lines or lines[0].strip() != "---":
        return {}
    result: dict[str, str] = {}
    for line in lines[1:25]:
        stripped = line.strip()
        if stripped == "---":
            break
        match = _FRONTMATTER_KV_RE.match(stripped)
        if match:
            result[match.group(1)] = match.group(2).strip("\"'")
    return result


_CTX_LANG_PAIR_RE = re.compile(r"([A-Za-z0-9_+#.-]+)\s*\((\d+)\)")
_CTX_BOUNDARY_LINE_RE = re.compile(
    r"^-\s+\*\*([^*]+)\*\*\s+\[`([^`]+)`\](?:\s+\(([^)]*)\))?(?:\s+—\s+Sections:\s*(.+))?$"
)


def _parse_tier2_context_0_body(md_path: Path) -> dict[str, Any]:
    """Extract languages, boundary docs, entrypoints, and subsystem table from CONTEXT-0.md."""
    try:
        lines = md_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    languages: dict[str, int] = {}
    authority_boundaries: list[dict[str, Any]] = []
    entrypoints: list[str] = []
    subsystems: list[dict[str, Any]] = []
    section = ""
    in_frontmatter = False
    for idx, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if idx == 0 and stripped == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if stripped == "---":
                in_frontmatter = False
            continue
        if "**Languages:**" in stripped:
            lang_part = stripped.split("**Languages:**", 1)[1]
            for name, count_str in _CTX_LANG_PAIR_RE.findall(lang_part):
                languages[name] = int(count_str)
            continue
        if stripped.startswith("## "):
            section = stripped[3:].strip().lower()
            continue
        if section.startswith("authority boundaries") and stripped.startswith("- **"):
            bmatch = _CTX_BOUNDARY_LINE_RE.match(stripped)
            if bmatch:
                headings = (
                    [h.strip() for h in bmatch.group(4).split(",") if h.strip()]
                    if bmatch.group(4)
                    else []
                )
                authority_boundaries.append(
                    {
                        "path": bmatch.group(1).strip(),
                        "kind": bmatch.group(2).strip(),
                        "summary": (bmatch.group(3) or "").strip(),
                        "headings": headings,
                    }
                )
        elif section.startswith("entrypoints") and stripped.startswith("- `") and stripped.endswith("`"):
            ep = stripped[3:-1].strip()
            if ep:
                entrypoints.append(ep)
        elif section.startswith("subsystem overview") and stripped.startswith("| `"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if len(cells) >= 4:
                sub_path = cells[0].strip("`").strip()
                fcount = int(cells[1]) if cells[1].isdigit() else 0
                sub_langs = [x.strip() for x in cells[2].split(",") if x.strip() and x.strip() != "-"]
                syms = [x.strip().strip("`") for x in cells[3].split(",") if x.strip() and x.strip() != "-"]
                if sub_path:
                    subsystems.append(
                        {
                            "path": sub_path,
                            "file_count": fcount,
                            "languages": sub_langs,
                            "key_symbols": syms,
                            "internal_deps": [],
                            "external_deps": [],
                        }
                    )
    return {
        "languages": languages,
        "authority_boundaries": authority_boundaries,
        "entrypoints": entrypoints,
        "subsystems": subsystems,
    }


def _extract_tier1_overview_summary(md_path: Path) -> str:
    try:
        lines = md_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    in_frontmatter = False
    for idx, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if idx == 0 and stripped == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if stripped == "---":
                in_frontmatter = False
            continue
        if not stripped or stripped.startswith("#") or stripped.startswith("<!--"):
            continue
        if stripped.startswith("- "):
            break
        return stripped[:160]
    return ""


def _normalize_ctx_mapping(
    raw: dict[str, Any],
    *,
    source_format: str,
    observation_id: str,
    artifact_path: str | None,
    max_subsystems: int = CAPSULE_MAX_CTX_SUBSYSTEMS,
) -> dict[str, Any]:
    structural_hash = str(raw.get("structural_hash") or "").strip() or None
    boundary_doc_hash = str(raw.get("boundary_doc_hash") or "").strip() or None
    raw_total = raw.get("total_files", raw.get("files"))
    total_files = int(raw_total) if isinstance(raw_total, int) and not isinstance(raw_total, bool) else None

    raw_langs = raw.get("languages")
    languages: dict[str, int] = {}
    if isinstance(raw_langs, dict):
        lang_pairs = [
            (str(k).strip(), int(v))
            for k, v in raw_langs.items()
            if str(k).strip() and isinstance(v, int) and not isinstance(v, bool)
        ]
        lang_pairs.sort(key=lambda pair: (-pair[1], pair[0]))
        for k, v in lang_pairs[:CAPSULE_MAX_CTX_LANGUAGES]:
            languages[k] = v

    raw_eps = raw.get("entrypoints")
    entrypoints: list[str] = []
    if isinstance(raw_eps, list):
        for ep in raw_eps:
            ep_str = str(ep or "").strip()
            if ep_str:
                entrypoints.append(ep_str[:160])
            if len(entrypoints) >= CAPSULE_MAX_CTX_ENTRYPOINTS:
                break

    # Accept both FleetCapsuleResult (`authority_boundaries`) and RepoSkeleton (`boundary_docs`).
    raw_boundaries = raw.get("authority_boundaries")
    if not isinstance(raw_boundaries, list):
        raw_boundaries = raw.get("boundary_docs")
    authority_boundaries: list[dict[str, Any]] = []
    if isinstance(raw_boundaries, list):
        for item in raw_boundaries:
            if not isinstance(item, dict):
                continue
            doc_path = str(item.get("path") or item.get("relative_path") or "").strip()
            if not doc_path:
                continue
            doc_kind = str(item.get("kind") or "overview").strip()
            raw_headings = item.get("headings")
            headings = (
                [str(h).strip()[:80] for h in raw_headings if str(h or "").strip()][:6]
                if isinstance(raw_headings, list)
                else []
            )
            summary = str(item.get("summary") or item.get("summary_line") or "").strip()[:160]
            content_hash = str(item.get("content_hash") or "").strip() or None
            authority_boundaries.append(
                {
                    "path": doc_path,
                    "kind": doc_kind,
                    "headings": headings,
                    "summary": summary,
                    "content_hash": content_hash,
                }
            )
            if len(authority_boundaries) >= CAPSULE_MAX_CTX_BOUNDARY_DOCS:
                break

    raw_subs = raw.get("subsystems")
    all_subsystems: list[dict[str, Any]] = []
    if isinstance(raw_subs, list):
        for sub in raw_subs:
            if not isinstance(sub, dict):
                continue
            sub_path = str(sub.get("path") or "").strip()
            if not sub_path:
                continue
            fcount_raw = sub.get("file_count", 0)
            file_count = int(fcount_raw) if isinstance(fcount_raw, int) and not isinstance(fcount_raw, bool) else 0
            sub_langs = (
                [str(x).strip() for x in sub.get("languages", []) if str(x or "").strip()][:3]
                if isinstance(sub.get("languages"), list)
                else []
            )
            key_syms = (
                [str(x).strip()[:80] for x in sub.get("key_symbols", []) if str(x or "").strip()][
                    :CAPSULE_MAX_CTX_SYMBOLS
                ]
                if isinstance(sub.get("key_symbols"), list)
                else []
            )
            int_deps = (
                [str(x).strip()[:80] for x in sub.get("internal_deps", []) if str(x or "").strip()][
                    :CAPSULE_MAX_CTX_DEPS
                ]
                if isinstance(sub.get("internal_deps"), list)
                else []
            )
            ext_deps = (
                [str(x).strip()[:80] for x in sub.get("external_deps", []) if str(x or "").strip()][
                    :CAPSULE_MAX_CTX_DEPS
                ]
                if isinstance(sub.get("external_deps"), list)
                else []
            )
            all_subsystems.append(
                {
                    "path": sub_path,
                    "file_count": file_count,
                    "languages": sub_langs,
                    "key_symbols": key_syms,
                    "internal_deps": int_deps,
                    "external_deps": ext_deps,
                }
            )

    declared_sub_count = raw.get("subsystem_count")
    if isinstance(declared_sub_count, int) and not isinstance(declared_sub_count, bool):
        total_sub_count = max(declared_sub_count, len(all_subsystems))
    else:
        total_sub_count = len(all_subsystems)

    shown_subs = all_subsystems[: max(0, max_subsystems)]
    truncated = len(shown_subs) < len(all_subsystems)

    result: dict[str, Any] = {
        "state": "available",
        "source_format": source_format,
        "observation_id": observation_id,
        "artifact_path": artifact_path,
        "structural_hash": structural_hash,
        "boundary_doc_hash": boundary_doc_hash,
        "total_files": total_files,
        "languages": languages,
        "entrypoints": entrypoints,
        "authority_boundaries": authority_boundaries,
        "subsystems": shown_subs,
        "subsystem_count": total_sub_count,
        "subsystems_truncated": truncated,
    }
    repo_id = str(raw.get("repo_id") or "").strip()
    if repo_id:
        result["repo_id"] = repo_id
    return result


def ingest_project_ctx_capsule(
    ranked_observations: list[dict],
    observations_by_id: dict[str, dict],
    *,
    max_subsystems: int = CAPSULE_MAX_CTX_SUBSYSTEMS,
    ctx_resolver: Callable[[dict, Path], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Ingest ``.ctx/`` capsules or synthesize a read-only structural scan (U2, Law 10)."""
    for item in ranked_observations:
        obs_id = str(item["observation_id"])
        obs = observations_by_id.get(obs_id) or {}

        # Support deterministic in-memory unit tests supplying `obs["ctx_capsule"]`.
        inline_ctx = obs.get("ctx_capsule")
        if isinstance(inline_ctx, dict):
            fmt = (
                "fleet_capsule_json"
                if "authority_boundaries" in inline_ctx or "capsule_markdown" in inline_ctx
                else "skeleton_json"
            )
            return _normalize_ctx_mapping(
                inline_ctx,
                source_format=fmt,
                observation_id=obs_id,
                artifact_path=str(inline_ctx.get("artifact_path") or "") or None,
                max_subsystems=max_subsystems,
            )

        if not item.get("score_components", {}).get("accessible_worktree"):
            continue
        loc = str(item.get("location") or "").strip()
        if not loc or not loc.startswith("/"):
            continue
        repo_dir = Path(loc)
        ctx_dir = repo_dir / ".ctx"
        if ctx_dir.is_dir():
            for candidate_name, default_fmt in (
                ("skeleton.json", "skeleton_json"),
                ("fleet-capsule.json", "fleet_capsule_json"),
                ("capsule.json", "fleet_capsule_json"),
            ):
                candidate_path = ctx_dir / candidate_name
                if not candidate_path.is_file():
                    continue
                try:
                    parsed = json.loads(candidate_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    return {
                        **_absent_ctx_capsule(),
                        "state": "invalid",
                        "source_format": default_fmt,
                        "observation_id": obs_id,
                        "artifact_path": str(candidate_path),
                        "error": f"Invalid ctx JSON in {candidate_path}: {exc}",
                    }
                if not isinstance(parsed, dict):
                    return {
                        **_absent_ctx_capsule(),
                        "state": "invalid",
                        "source_format": default_fmt,
                        "observation_id": obs_id,
                        "artifact_path": str(candidate_path),
                        "error": f"Expected JSON object in {candidate_path}.",
                    }
                detected_fmt = (
                    "fleet_capsule_json"
                    if ("authority_boundaries" in parsed or "capsule_markdown" in parsed)
                    and candidate_name != "skeleton.json"
                    else (
                        "fleet_capsule_json"
                        if "capsule_markdown" in parsed and "boundary_docs" not in parsed
                        else default_fmt
                    )
                )
                return _normalize_ctx_mapping(
                    parsed,
                    source_format=detected_fmt,
                    observation_id=obs_id,
                    artifact_path=str(candidate_path),
                    max_subsystems=max_subsystems,
                )

            # Fallback: Tier-2 `.ctx/metadata.json` + `.ctx/CONTEXT-0.md` when initialized via `ctx init --tier2`.
            meta_path = ctx_dir / "metadata.json"
            c0_path = ctx_dir / "CONTEXT-0.md"
            meta: dict[str, Any] = {}
            if meta_path.is_file():
                try:
                    loaded_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    if isinstance(loaded_meta, dict):
                        meta = loaded_meta
                except (OSError, json.JSONDecodeError):
                    meta = {}
            fm = _parse_tier2_frontmatter(c0_path) if c0_path.is_file() else {}
            if meta.get("mode") == "tier2" or fm.get("mode") == "tier2":
                body_parsed = _parse_tier2_context_0_body(c0_path) if c0_path.is_file() else {}
                total_files: int | None = None
                sub_count = 0
                if fm.get("files", "").isdigit():
                    total_files = int(fm["files"])
                if fm.get("subsystems", "").isdigit():
                    sub_count = int(fm["subsystems"])
                synthesized = {
                    "repo_id": meta.get("repo_id"),
                    "structural_hash": meta.get("structural_hash") or fm.get("structural_hash"),
                    "boundary_doc_hash": meta.get("boundary_doc_hash") or fm.get("boundary_doc_hash"),
                    "total_files": total_files,
                    "subsystem_count": sub_count,
                    "languages": body_parsed.get("languages") or {},
                    "entrypoints": body_parsed.get("entrypoints") or [],
                    "authority_boundaries": body_parsed.get("authority_boundaries") or [],
                    "subsystems": body_parsed.get("subsystems") or [],
                }
                artifact_ref = str(c0_path if c0_path.is_file() else meta_path)
                return _normalize_ctx_mapping(
                    synthesized,
                    source_format="tier2_ctx_dir",
                    observation_id=obs_id,
                    artifact_path=artifact_ref,
                    max_subsystems=max_subsystems,
                )

        # Tier-1 root `CONTEXT.md` fallback when generated by `ctx` (`generator: ctx/...`).
        root_ctx_md = repo_dir / "CONTEXT.md"
        if root_ctx_md.is_file():
            t1_fm = _parse_tier2_frontmatter(root_ctx_md)
            if str(t1_fm.get("generator") or "").startswith("ctx/"):
                t1_files = int(t1_fm["files"]) if t1_fm.get("files", "").isdigit() else None
                t1_dirs = int(t1_fm["dirs"]) if t1_fm.get("dirs", "").isdigit() else 0
                t1_hash = str(t1_fm.get("content_hash") or "").strip() or None
                t1_summary = _extract_tier1_overview_summary(root_ctx_md)
                t1_boundaries = (
                    [
                        {
                            "path": "CONTEXT.md",
                            "kind": "overview",
                            "headings": [],
                            "summary": t1_summary,
                            "content_hash": t1_hash,
                        }
                    ]
                    if (t1_summary or t1_hash)
                    else []
                )
                return _normalize_ctx_mapping(
                    {
                        "boundary_doc_hash": t1_hash,
                        "total_files": t1_files,
                        "subsystem_count": t1_dirs,
                        "languages": {},
                        "entrypoints": [],
                        "authority_boundaries": t1_boundaries,
                        "subsystems": [],
                    },
                    source_format="tier1_context_md",
                    observation_id=obs_id,
                    artifact_path=str(root_ctx_md),
                    max_subsystems=max_subsystems,
                )

    # U2 fallback: read-only stdlib structural skeleton scan when no on-disk `.ctx/` artifact exists.
    structural_fn = ctx_resolver or (lambda _obs, target_dir: extract_structural_ctx(target_dir))
    for item in ranked_observations:
        if not item.get("score_components", {}).get("accessible_worktree"):
            continue
        loc = str(item.get("location") or "").strip()
        if not loc or not loc.startswith("/"):
            continue
        repo_dir = Path(loc)
        if not repo_dir.is_dir():
            continue
        obs_id = str(item["observation_id"])
        obs = observations_by_id.get(obs_id) or {}
        scanned = structural_fn(obs, repo_dir)
        if isinstance(scanned, dict):
            return _normalize_ctx_mapping(
                scanned,
                source_format="compiled_structural_scan",
                observation_id=obs_id,
                artifact_path=None,
                max_subsystems=max_subsystems,
            )

    return _absent_ctx_capsule()


def rank_project_observations(
    project: dict,
    observations_by_id: dict[str, dict],
    sources_by_id: dict[str, dict] | None = None,
    *,
    git_drift_resolver: Callable[[dict, dict], dict] | None = None,
) -> tuple[list[dict], dict]:
    """Rank a canonical project's observations and resolve its preferred location.

    Returns ``(ranked_observations, preferred_location)``.
    """
    drift_fn = git_drift_resolver or resolve_observation_git_drift
    sources_map = sources_by_id or {}
    project_remotes = {str(r).casefold() for r in project.get("normalized_remotes", []) if str(r).strip()}
    member_ids = sorted(set(project.get("observation_ids", [])))

    # Identify freshest live git HEAD across the canonical project's available observations
    # so secondary checkouts with lagging May 19 sidecars also emit `stale_sidecar`.
    freshest_head_commit: str | None = None
    freshest_head_epoch = -1.0
    for oid in member_ids:
        cand_obs = observations_by_id.get(oid)
        if not cand_obs:
            continue
        cand_compat = cand_obs.get("compat_entry") if isinstance(cand_obs.get("compat_entry"), dict) else {}
        cand_src = sources_map.get(str(cand_obs.get("source_id") or "").strip()) or {}
        cand_status = (
            str(cand_src["status"])
            if "status" in cand_src
            else ("available" if cand_obs.get("source_resolution") == "resolved" else "unavailable")
        )
        if cand_status != "available" or not cand_compat.get("git"):
            continue
        c_sha = str(cand_compat.get("head_commit") or "").strip()
        c_epoch = _iso_epoch(str(cand_compat.get("head_commit_at") or "").strip())
        if c_sha and c_epoch > freshest_head_epoch:
            freshest_head_epoch = c_epoch
            freshest_head_commit = c_sha

    scored_items: list[tuple[tuple, dict]] = []
    for obs_id in member_ids:
        obs = observations_by_id.get(obs_id)
        if obs is None:
            continue
        compat = obs.get("compat_entry") if isinstance(obs.get("compat_entry"), dict) else {}
        facts = observation_facts(obs)
        source_id = str(obs.get("source_id") or "").strip()
        source = sources_map.get(source_id) or {}

        source_class = str(source.get("source_class") or "").strip() or _infer_source_class(obs, compat)
        if source_class not in SOURCE_CLASS_TIER:
            source_class = "unknown"
        source_tier = SOURCE_CLASS_TIER[source_class]

        if "status" in source:
            source_status = str(source["status"])
        else:
            source_status = "available" if obs.get("source_resolution") == "resolved" else "unavailable"

        location = (
            str(obs.get("location") or "").strip()
            or str(compat.get("path") or "").strip()
            or str(compat.get("canonical_url") or "").strip()
            or None
        )
        accessible_worktree = bool(
            source_status == "available"
            and source_class in {"live", "mirror", "backup"}
            and location
        )

        obs_remotes = {str(r).casefold() for r in facts.get("remotes", set())}
        canonical_remote_match = bool(obs_remotes and (obs_remotes & project_remotes))

        git_enabled = bool(compat.get("git"))
        head_commit = str(compat.get("head_commit") or "").strip() or None
        head_branch = str(compat.get("head_branch") or "").strip() or None
        has_git_commit = bool(git_enabled and head_commit)
        primary_branch = bool(head_branch and head_branch.lower() in {"main", "master"})

        primary_active_root = bool(
            source_id == "central-active"
            or (location and str(location).startswith("/central/repos/active/"))
        )

        session_at = str(compat.get("last_session_at") or "").strip() or None
        session_summary = str(compat.get("last_session_summary") or "").strip() or None
        next_step = str(compat.get("next_step") or "").strip() or None
        has_session = bool(session_at or session_summary or next_step)
        session_epoch = _iso_epoch(session_at)

        content_as_of, content_basis, freshness_reason, content_epoch = _observation_content_as_of(compat)
        git_drift = drift_fn(obs, compat)

        obs_warning_codes = list(git_drift.get("warning_codes", []))
        obs_warnings = list(git_drift.get("warnings", []))

        sidecar_env = obs.get("sidecar") if isinstance(obs.get("sidecar"), dict) else {}
        sidecar_warnings = sidecar_env.get("warnings") if isinstance(sidecar_env.get("warnings"), list) else []
        if sidecar_warnings:
            for sw in sidecar_warnings:
                if not isinstance(sw, dict):
                    continue
                sw_code = str(sw.get("code") or "stale_sidecar")
                sw_detail = str(sw.get("detail") or sw_code)
                if sw_code not in obs_warning_codes:
                    obs_warning_codes.append(sw_code)
                formatted_warn = sw_detail if sw_detail.startswith("warning:") else f"warning: {sw_detail}"
                if formatted_warn not in obs_warnings:
                    obs_warnings.append(formatted_warn)
        elif sidecar_env.get("present") and freshest_head_commit:
            decls = sidecar_env.get("declarations") if isinstance(sidecar_env.get("declarations"), dict) else {}
            git_decl = decls.get("git") if isinstance(decls.get("git"), dict) else {}
            decl_commit = str(
                git_decl.get("commit") or decls.get("head_commit") or decls.get("commit") or ""
            ).strip()
            decl_epoch = _iso_epoch(str(decls.get("updated_at") or decls.get("last_session_at") or ""))
            obs_head_epoch = _iso_epoch(str(compat.get("head_commit_at") or ""))
            effective_obs_epoch = max(decl_epoch, obs_head_epoch)
            if (
                decl_commit
                and not _commits_prefix_match(decl_commit, freshest_head_commit)
                and (effective_obs_epoch < 0 or effective_obs_epoch < freshest_head_epoch)
            ):
                if "stale_sidecar" not in obs_warning_codes:
                    obs_warning_codes.append("stale_sidecar")
                obs_warnings.append(
                    f"warning: stale_sidecar: declared commit {decl_commit!r} lags canonical project HEAD {freshest_head_commit[:12]}"
                )

        reasons: list[str] = []
        if source_status != "available":
            reasons.append("SOURCE_UNAVAILABLE")
        elif accessible_worktree:
            reasons.append("ACCESSIBLE_SOURCE")
        elif source_class == "inventory":
            reasons.append("INVENTORY_ONLY_LOCATOR")

        reasons.append(SOURCE_CLASS_REASON[source_class])

        if canonical_remote_match:
            reasons.append("CANONICAL_REMOTE_MATCH")
        if primary_active_root:
            reasons.append("PRIMARY_ACTIVE_ROOT")
        if has_session:
            reasons.append("RECENT_SESSION_EVIDENCE")
        if has_git_commit:
            reasons.append("GIT_WORKTREE")
        if primary_branch:
            reasons.append("PRIMARY_BRANCH")
        if freshness_reason:
            reasons.append(freshness_reason)

        if not accessible_worktree:
            confidence = "low"
        elif source_class == "live" and (canonical_remote_match or len(member_ids) == 1):
            confidence = "high"
        else:
            confidence = "medium"

        sort_key = (
            -int(accessible_worktree),
            -int(source_status == "available"),
            -source_tier,
            -int(canonical_remote_match),
            -int(has_session),
            -session_epoch,
            -int(has_git_commit),
            -int(primary_active_root),
            -content_epoch,
            -int(primary_branch),
            obs_id,
        )

        summary = {
            "observation_id": obs_id,
            "source_id": source_id,
            "source_label": str(compat.get("source_label") or source.get("label") or "").strip() or None,
            "source_class": source_class,
            "source_status": source_status,
            "machine_name": str(compat.get("machine_name") or source.get("machine_name") or "").strip() or None,
            "location": location,
            "project_type": str(compat.get("project_type") or "").strip() or None,
            "git": git_enabled,
            "head_branch": head_branch,
            "head_commit": head_commit,
            "git_drift": git_drift,
            "warning_codes": obs_warning_codes,
            "warnings": obs_warnings,
            "content_as_of": content_as_of,
            "content_as_of_basis": content_basis,
            "canonical_remote_match": canonical_remote_match,
            "confidence": confidence,
            "reason_codes": reasons,
            "score_components": {
                "accessible_worktree": accessible_worktree,
                "source_available": source_status == "available",
                "source_class_tier": source_tier,
                "canonical_remote_match": canonical_remote_match,
                "has_session_evidence": has_session,
                "has_git_commit": has_git_commit,
                "primary_branch": primary_branch,
                "primary_active_root": primary_active_root,
                "content_as_of": content_as_of,
            },
        }
        scored_items.append((sort_key, summary))

    scored_items.sort(key=lambda pair: pair[0])
    ranked: list[dict] = []
    for idx, (_key, item) in enumerate(scored_items, start=1):
        entry = dict(item)
        entry["rank"] = idx
        ranked.append(entry)

    if not ranked:
        preferred = {
            "state": "unavailable",
            "observation_id": None,
            "source_id": None,
            "source_class": None,
            "machine_name": None,
            "location": None,
            "confidence": "none",
            "reason_codes": ["SOURCE_UNAVAILABLE"],
            "git_drift": {
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
            },
            "warning_codes": [],
            "warnings": [],
            "explanation": "No observations are available for this canonical project.",
        }
        return ranked, preferred

    top = ranked[0]
    if top["score_components"]["accessible_worktree"]:
        pref_state = "resolved"
        pref_confidence = top["confidence"]
        pref_explanation = (
            f"Selected {top['source_class']} observation {top['observation_id']} at "
            f"{top['location']} ({', '.join(top['reason_codes'])})."
        )
    elif top["source_status"] == "available" and top["source_class"] == "inventory":
        pref_state = "inventory_only"
        pref_confidence = "low"
        pref_explanation = (
            f"Only inventory-backed observations exist (top: {top['observation_id']} at "
            f"{top['location']}); no local working tree is mounted."
        )
    else:
        pref_state = "unavailable"
        pref_confidence = "none"
        pref_explanation = (
            f"All configured sources for this project are currently unavailable "
            f"(top candidate: {top['observation_id']})."
        )

    preferred = {
        "state": pref_state,
        "observation_id": top["observation_id"],
        "source_id": top["source_id"],
        "source_class": top["source_class"],
        "machine_name": top["machine_name"],
        "location": top["location"],
        "confidence": pref_confidence,
        "reason_codes": list(top["reason_codes"]),
        "git_drift": dict(top["git_drift"]),
        "warning_codes": list(top["warning_codes"]),
        "warnings": list(top["warnings"]),
        "explanation": pref_explanation,
    }
    return ranked, preferred


def _project_freshness(
    ranked_observations: list[dict],
    observations_by_id: dict[str, dict],
    input_observed_at: str,
) -> dict:
    available_count = sum(1 for item in ranked_observations if item["source_status"] == "available")
    unavailable_count = len(ranked_observations) - available_count

    best_as_of: str | None = None
    best_basis: str | None = None
    for field, basis in (
        ("last_remote_ref_at", "compat.entry.last_remote_ref_at"),
        ("head_commit_at", "compat.entry.head_commit_at"),
        ("last_touch_at", "compat.entry.last_touch_at"),
    ):
        best_epoch = -1.0
        candidate_val: str | None = None
        for item in ranked_observations:
            obs = observations_by_id.get(item["observation_id"]) or {}
            compat = obs.get("compat_entry") if isinstance(obs.get("compat_entry"), dict) else {}
            raw = str(compat.get(field) or "").strip()
            epoch = _iso_epoch(raw)
            if epoch > best_epoch and epoch >= 0.0:
                best_epoch = epoch
                candidate_val = raw
        if candidate_val is not None:
            best_as_of = candidate_val
            best_basis = basis
            break

    if available_count == 0 and ranked_observations:
        state = "unavailable"
    elif best_as_of is not None:
        state = "known"
    else:
        state = "unknown"

    return {
        "state": state,
        "input_observed_at": input_observed_at,
        "content_as_of": best_as_of,
        "content_as_of_basis": best_basis,
        "available_observation_count": available_count,
        "unavailable_observation_count": unavailable_count,
    }


def _project_git_facts(ranked_observations: list[dict], observations_by_id: dict[str, dict]) -> dict:
    for item in ranked_observations:
        if not item.get("git"):
            continue
        obs = observations_by_id.get(item["observation_id"]) or {}
        compat = obs.get("compat_entry") if isinstance(obs.get("compat_entry"), dict) else {}
        head_commit = str(compat.get("head_commit") or "").strip() or None
        head_branch = str(compat.get("head_branch") or "").strip() or None
        head_commit_at = str(compat.get("head_commit_at") or "").strip() or None
        last_remote_ref_at = str(compat.get("last_remote_ref_at") or "").strip() or None
        remote_url = str(compat.get("remote_url") or compat.get("canonical_url") or "").strip() or None
        git_drift = item.get("git_drift") or {}
        state = "known" if (head_commit or head_branch or remote_url) else "unknown"
        return {
            "state": state,
            "observation_id": item["observation_id"],
            "head_branch": head_branch,
            "head_commit": head_commit,
            "head_commit_at": head_commit_at,
            "last_remote_ref_at": last_remote_ref_at,
            "remote_url": remote_url,
            "upstream_ref": git_drift.get("upstream_ref"),
            "ahead_count": git_drift.get("ahead_count"),
            "behind_count": git_drift.get("behind_count"),
            "main_behind_count": git_drift.get("main_behind_count"),
            "dirty": git_drift.get("dirty"),
            "drift_state": git_drift.get("state", "unknown"),
            "warning_codes": list(git_drift.get("warning_codes", [])),
            "warnings": list(git_drift.get("warnings", [])),
        }
    return {
        "state": "not_applicable",
        "observation_id": None,
        "head_branch": None,
        "head_commit": None,
        "head_commit_at": None,
        "last_remote_ref_at": None,
        "remote_url": None,
        "upstream_ref": None,
        "ahead_count": None,
        "behind_count": None,
        "main_behind_count": None,
        "dirty": None,
        "drift_state": "not_applicable",
        "warning_codes": [],
        "warnings": [],
    }


def _read_machina_continuity(location: str | None) -> dict[str, Any] | None:
    """Read bounded session/unit continuity from ``.machina/state.json`` in read-only mode.

    Intentional boundary: Project Ledger does not import Machina lifecycle state
    vocabulary or run a competing receipt store; it extracts read-only continuity
    timestamps, unit identifiers, summaries, and ``next_action`` hints.
    """
    loc = str(location or "").strip()
    if not loc or not loc.startswith("/"):
        return None
    state_file = Path(loc) / ".machina" / "state.json"
    if not state_file.is_file():
        return None
    try:
        raw = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None

    updated_at = str(raw.get("updated_at") or "").strip() or None
    raw_seq = raw.get("sequence")
    sequence = int(raw_seq) if isinstance(raw_seq, int) and not isinstance(raw_seq, bool) else None
    active_unit = raw.get("active_unit") if isinstance(raw.get("active_unit"), dict) else None
    active_unit_id = str(active_unit.get("id") or "").strip() or None if active_unit else None
    active_unit_title = (
        str(active_unit.get("title") or "").strip()[:CAPSULE_PURPOSE_MAX_CHARS] or None
        if active_unit
        else None
    )

    last_completed = raw.get("last_completed") if isinstance(raw.get("last_completed"), dict) else None
    lc_unit = (
        last_completed.get("unit")
        if (last_completed and isinstance(last_completed.get("unit"), dict))
        else None
    )
    last_completed_unit_id = str(lc_unit.get("id") or "").strip() or None if lc_unit else None
    last_completed_unit_title = (
        str(lc_unit.get("title") or "").strip()[:CAPSULE_PURPOSE_MAX_CHARS] or None
        if lc_unit
        else None
    )
    last_completed_at = str(last_completed.get("timestamp") or "").strip() or None if last_completed else None
    last_completed_summary = (
        str(last_completed.get("summary") or "").strip()[:CAPSULE_PURPOSE_MAX_CHARS] or None
        if last_completed
        else None
    )

    last_checkpoint = raw.get("last_checkpoint") if isinstance(raw.get("last_checkpoint"), dict) else None
    last_checkpoint_at = (
        str(last_checkpoint.get("timestamp") or "").strip() or None if last_checkpoint else None
    )
    last_checkpoint_summary = (
        str(last_checkpoint.get("summary") or "").strip()[:CAPSULE_PURPOSE_MAX_CHARS] or None
        if last_checkpoint
        else None
    )
    next_action = str(raw.get("next_action") or "").strip()[:CAPSULE_PURPOSE_MAX_CHARS] or None
    has_checkpoint = last_checkpoint is not None
    has_frontier = raw.get("frontier") is not None

    receipt_candidates: list[tuple[float, str | None, str | None]] = []
    if last_completed_at or last_completed_summary:
        receipt_candidates.append(
            (_iso_epoch(last_completed_at), last_completed_at, last_completed_summary)
        )
    if last_checkpoint_at or last_checkpoint_summary:
        receipt_candidates.append(
            (_iso_epoch(last_checkpoint_at), last_checkpoint_at, last_checkpoint_summary)
        )
    latest_at: str | None = None
    latest_summary: str | None = None
    if receipt_candidates:
        receipt_candidates.sort(key=lambda item: item[0], reverse=True)
        _, latest_at, latest_summary = receipt_candidates[0]
    elif updated_at:
        latest_at = updated_at

    return {
        "updated_at": updated_at,
        "sequence": sequence,
        "active_unit_id": active_unit_id,
        "active_unit_title": active_unit_title,
        "last_completed_unit_id": last_completed_unit_id,
        "last_completed_unit_title": last_completed_unit_title,
        "last_completed_at": last_completed_at,
        "has_checkpoint": has_checkpoint,
        "has_frontier": has_frontier,
        "latest_receipt_at": latest_at,
        "latest_receipt_summary": latest_summary,
        "next_action": next_action,
    }


def _project_current_state(ranked_observations: list[dict], observations_by_id: dict[str, dict]) -> dict:
    lifecycle_state = "unknown"
    lifecycle_basis = "unknown"
    last_session_at: str | None = None
    last_session_summary: str | None = None
    next_step: str | None = None
    tags: set[str] = set()
    machina_info: dict[str, Any] | None = None
    machina_obs_id: str | None = None

    for item in ranked_observations:
        obs = observations_by_id.get(item["observation_id"]) or {}
        compat = obs.get("compat_entry") if isinstance(obs.get("compat_entry"), dict) else {}
        status = str(compat.get("status") or "").strip()
        if lifecycle_state == "unknown" and status:
            lifecycle_state = status
            lifecycle_basis = "declared_sidecar"
        if last_session_at is None and str(compat.get("last_session_at") or "").strip():
            last_session_at = str(compat.get("last_session_at")).strip()
        if last_session_summary is None and str(compat.get("last_session_summary") or "").strip():
            last_session_summary = str(compat.get("last_session_summary")).strip()[:CAPSULE_PURPOSE_MAX_CHARS]
        if next_step is None and str(compat.get("next_step") or "").strip():
            next_step = str(compat.get("next_step")).strip()[:CAPSULE_PURPOSE_MAX_CHARS]
        raw_tags = compat.get("tags")
        if isinstance(raw_tags, list):
            for tag in raw_tags:
                if str(tag or "").strip():
                    tags.add(str(tag).strip())
        if machina_info is None and item.get("score_components", {}).get("accessible_worktree"):
            candidate_machina = _read_machina_continuity(item.get("location"))
            if candidate_machina is not None:
                machina_info = candidate_machina
                machina_obs_id = item["observation_id"]

    if machina_info is not None:
        session_receipts_state = "external_provider"
        machina_latest_at = machina_info.get("latest_receipt_at")
        machina_latest_summary = machina_info.get("latest_receipt_summary")
        machina_next_action = machina_info.get("next_action")
        sidecar_epoch = _iso_epoch(last_session_at)
        machina_epoch = _iso_epoch(machina_latest_at)
        if machina_latest_at and (last_session_at is None or machina_epoch > sidecar_epoch):
            last_session_at = machina_latest_at
            if machina_latest_summary:
                last_session_summary = machina_latest_summary
            if machina_next_action:
                next_step = machina_next_action
        if last_session_summary is None and machina_latest_summary:
            last_session_summary = machina_latest_summary
        if next_step is None and machina_next_action:
            next_step = machina_next_action
        machina_continuity = {
            "state": "available",
            "observation_id": machina_obs_id,
            "updated_at": machina_info.get("updated_at"),
            "sequence": machina_info.get("sequence"),
            "active_unit_id": machina_info.get("active_unit_id"),
            "active_unit_title": machina_info.get("active_unit_title"),
            "last_completed_unit_id": machina_info.get("last_completed_unit_id"),
            "last_completed_unit_title": machina_info.get("last_completed_unit_title"),
            "last_completed_at": machina_info.get("last_completed_at"),
            "has_checkpoint": bool(machina_info.get("has_checkpoint")),
            "has_frontier": bool(machina_info.get("has_frontier")),
        }
    else:
        session_receipts_state = "unavailable"
        machina_continuity = {
            "state": "not_present",
            "observation_id": None,
            "updated_at": None,
            "sequence": None,
            "active_unit_id": None,
            "active_unit_title": None,
            "last_completed_unit_id": None,
            "last_completed_unit_title": None,
            "last_completed_at": None,
            "has_checkpoint": False,
            "has_frontier": False,
        }

    return {
        "lifecycle_state": lifecycle_state,
        "lifecycle_basis": lifecycle_basis,
        "session_receipts_state": session_receipts_state,
        "session_receipts_reason_code": "OWNED_BY_MACHINA",
        "machina_continuity": machina_continuity,
        "last_session_at": last_session_at,
        "last_session_summary": last_session_summary,
        "next_step": next_step,
        "tags": sorted(tags),
    }


def build_project_capsule(
    *,
    project: dict,
    observations_by_id: dict[str, dict],
    sources_by_id: dict[str, dict] | None = None,
    reviews: list[dict] | None = None,
    state_dir: Path,
    run_id: str,
    compiled_at: str,
    input_observed_at: str,
    max_bytes: int = CAPSULE_MAX_BYTES,
    max_observation_summaries: int = CAPSULE_MAX_OBSERVATION_SUMMARIES,
    git_drift_resolver: Callable[[dict, dict], dict] | None = None,
    ctx_resolver: Callable[[dict, Path], dict[str, Any] | None] | None = None,
) -> dict:
    """Build one bounded, deterministic project capsule for ``project``."""
    state_dir = state_dir.resolve()
    canonical_id = project["canonical_project_id"]
    capsule_path = state_dir / "projects" / f"{canonical_id}.json"

    ranked_observations, preferred_location = rank_project_observations(
        project,
        observations_by_id,
        sources_by_id=sources_by_id,
        git_drift_resolver=git_drift_resolver,
    )

    ctx_sub_limit = CAPSULE_MAX_CTX_SUBSYSTEMS
    ctx_block = ingest_project_ctx_capsule(
        ranked_observations,
        observations_by_id,
        max_subsystems=ctx_sub_limit,
        ctx_resolver=ctx_resolver,
    )

    # Resolve bounded purpose and canonical_url from ranked observations (with ctx boundary fallback).
    purpose: str | None = None
    canonical_url: str | None = None
    if project.get("normalized_remotes"):
        canonical_url = f"https://{project['normalized_remotes'][0]}"
    for item in ranked_observations:
        obs = observations_by_id.get(item["observation_id"]) or {}
        compat = obs.get("compat_entry") if isinstance(obs.get("compat_entry"), dict) else {}
        desc = str(compat.get("description") or "").strip()
        if purpose is None and desc:
            purpose = desc[:CAPSULE_PURPOSE_MAX_CHARS]
        raw_url = str(compat.get("canonical_url") or compat.get("remote_url") or "").strip()
        if canonical_url is None and raw_url:
            canonical_url = raw_url

    if purpose is None and ctx_block.get("state") == "available":
        for bdoc in ctx_block.get("authority_boundaries", []):
            bsummary = str(bdoc.get("summary") or "").strip()
            if bsummary:
                purpose = bsummary[:CAPSULE_PURPOSE_MAX_CHARS]
                break

    aliases: set[str] = set()
    for hint in project.get("project_key_hints", []):
        if str(hint or "").strip():
            aliases.add(str(hint).strip())
    for remote in project.get("normalized_remotes", []):
        if str(remote or "").strip():
            aliases.add(str(remote).strip())
    for ref in project.get("referents", []):
        if ref.get("kind") in {"decision_alias", "project_key_hint", "display_name"}:
            val = str(ref.get("value") or "").strip()
            if val:
                aliases.add(val)

    member_set = set(project.get("observation_ids", []))
    project_reviews = [
        {
            "review_id": item["review_id"],
            "code": item["code"],
            "severity": item.get("severity", "warning"),
            "state": item.get("state", "open"),
            "detail": item["detail"],
            "resolution_actions": list(item.get("resolution_actions", [])),
        }
        for item in (reviews or [])
        if set(item.get("affected_observation_ids", [])) & member_set
    ]
    project_reviews.sort(key=lambda item: item["review_id"])

    freshness = _project_freshness(ranked_observations, observations_by_id, input_observed_at)
    git_facts = _project_git_facts(ranked_observations, observations_by_id)
    current_state = _project_current_state(ranked_observations, observations_by_id)
    gbrain_link = (
        dict(project["gbrain"])
        if isinstance(project.get("gbrain"), dict)
        else resolve_gbrain_link(project, observations_by_id)
    )
    gbrain_source_slug = str(project.get("gbrain_source_slug") or gbrain_link["source_slug"])
    capsule_warnings = list(preferred_location.get("warnings") or [])

    shown_count = min(len(ranked_observations), max_observation_summaries)
    observations_truncated = shown_count < len(ranked_observations)

    bounded_resolved_fields: dict[str, dict] = {}
    for field_name, field_info in (project.get("resolved_fields") or {}).items():
        if not isinstance(field_info, dict):
            continue
        prov = list(field_info.get("provenance") or [])
        entry = {
            "value": field_info.get("value"),
            "provenance": prov[:CAPSULE_MAX_PROVENANCE_REFS],
        }
        if len(prov) > CAPSULE_MAX_PROVENANCE_REFS:
            entry["provenance_count"] = len(prov)
            entry["provenance_truncated"] = True
        bounded_resolved_fields[field_name] = entry

    def _rel(target: Path) -> str:
        return portable_artifact_path(target, state_dir=state_dir)

    def _assemble(limit: int, truncated: bool, current_ctx: dict[str, Any]) -> dict:
        return {
            "schema_version": PROJECT_CAPSULE_SCHEMA_VERSION,
            "compiler_version": COMPILER_VERSION,
            "run_id": run_id,
            "compiled_at": compiled_at,
            "as_of": input_observed_at,
            "canonical_project_id": canonical_id,
            "project_key": project["project_key"],
            "display_name": project["display_name"],
            "aliases": sorted(aliases, key=str.casefold),
            "purpose": purpose,
            "canonical_url": canonical_url,
            "normalized_remotes": list(project.get("normalized_remotes", [])),
            "identity_anchor": dict(project.get("identity_anchor", {})),
            "resolved_fields": bounded_resolved_fields,
            "gbrain_source_slug": gbrain_source_slug,
            "gbrain": gbrain_link,
            "ctx": current_ctx,
            "freshness": freshness,
            "preferred_location": preferred_location,
            "warnings": capsule_warnings,
            "git_facts": git_facts,
            "current_state": current_state,
            "observation_count": len(ranked_observations),
            "observations": ranked_observations[:limit],
            "review": {
                "open_review_count": len(project_reviews),
                "items": project_reviews,
            },
            "pointers": {
                "capsule_path": _rel(capsule_path),
                "canonical_projects_path": _rel(state_dir / "canonical-projects.json"),
                "observations_path": _rel(state_dir / "observations.json"),
                "identity_evidence_path": _rel(state_dir / "identity-evidence.json"),
                "review_queue_path": _rel(state_dir / "review-queue.json"),
                "change_feed_path": _rel(state_dir / "changes.json"),
                "observation_ids": list(project.get("observation_ids", [])),
                "merge_decision_ids": list(project.get("merge_decision_ids", [])),
                "canonical_key_decision_id": project.get("canonical_key_decision_id"),
                "task_system": {
                    "state": "external",
                    "provider": "machina",
                    "canonical_project_id": canonical_id,
                    "project_key": project["project_key"],
                },
                "knowledge_system": {
                    "state": "external",
                    "provider": "gbrain",
                    "canonical_project_id": canonical_id,
                    "project_key": project["project_key"],
                    "gbrain_source_slug": gbrain_source_slug,
                    "gbrain_project_slug": gbrain_link["project_slug"],
                    "gbrain_page_key": gbrain_link["page_key"],
                },
            },
            "budget": {
                "max_bytes": max_bytes,
                "max_observation_summaries": max_observation_summaries,
                "observations_truncated": truncated,
                "within_budget": True,
            },
        }

    def _encoded_len(payload: dict) -> int:
        return len((json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"))

    capsule = _assemble(shown_count, observations_truncated, ctx_block)
    while _encoded_len(capsule) > max_bytes:
        if len(ctx_block.get("subsystems") or []) > 2:
            ctx_sub_limit = len(ctx_block["subsystems"]) - 1
            ctx_block = dict(ctx_block)
            ctx_block["subsystems"] = list(ctx_block["subsystems"][:ctx_sub_limit])
            ctx_block["subsystems_truncated"] = True
            capsule = _assemble(shown_count, observations_truncated, ctx_block)
        elif shown_count > 1:
            shown_count -= 1
            observations_truncated = True
            capsule = _assemble(shown_count, observations_truncated, ctx_block)
        elif len(ctx_block.get("subsystems") or []) > 0:
            ctx_block = dict(ctx_block)
            ctx_block["subsystems"] = []
            ctx_block["subsystems_truncated"] = True
            capsule = _assemble(shown_count, observations_truncated, ctx_block)
        else:
            break

    encoded_size = _encoded_len(capsule)
    capsule["budget"]["within_budget"] = encoded_size <= max_bytes
    return capsule


def materialize_capsules(
    *,
    canonical_payload: dict,
    observations: list[dict],
    sources: list[dict] | None = None,
    review_payload: dict | None = None,
    state_dir: Path,
    run_id: str,
    compiled_at: str,
    input_observed_at: str,
    git_drift_resolver: Callable[[dict, dict], dict] | None = None,
    ctx_resolver: Callable[[dict, Path], dict[str, Any] | None] | None = None,
) -> dict:
    """Materialize ``state/projects/<canonical_project_id>.json`` for every project."""
    state_dir = state_dir.resolve()
    capsules_dir = state_dir / "projects"
    capsules_dir.mkdir(parents=True, exist_ok=True)

    observations_by_id = {str(item["observation_id"]): item for item in observations}
    sources_by_id = {str(src["source_id"]): src for src in (sources or []) if src.get("source_id")}
    review_items = (review_payload or {}).get("items", [])

    active_filenames: set[str] = set()
    capsules: list[dict] = []
    for project in canonical_payload.get("projects", []):
        capsule = build_project_capsule(
            project=project,
            observations_by_id=observations_by_id,
            sources_by_id=sources_by_id,
            reviews=review_items,
            state_dir=state_dir,
            run_id=run_id,
            compiled_at=compiled_at,
            input_observed_at=input_observed_at,
            git_drift_resolver=git_drift_resolver,
            ctx_resolver=ctx_resolver,
        )
        filename = f"{project['canonical_project_id']}.json"
        active_filenames.add(filename)
        (capsules_dir / filename).write_text(
            json.dumps(capsule, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        capsules.append(capsule)

    # Remove stale capsule files from prior compiles in the same state_dir.
    for existing in capsules_dir.glob("*.json"):
        if existing.name not in active_filenames:
            existing.unlink()

    canonical_md_path = write_canonical_markdown(
        output_path=state_dir / "canonical-projects.md",
        capsules=capsules,
        source_count=len(sources or []),
        observation_count=len(observations),
        review_count=len(review_items),
        run_id=run_id,
        compiled_at=compiled_at,
        input_observed_at=input_observed_at,
    )

    return {
        "schema_version": PROJECT_CAPSULE_SCHEMA_VERSION,
        "capsule_count": len(capsules),
        "capsules_dir": portable_artifact_path(capsules_dir, state_dir=state_dir),
        "canonical_markdown_path": portable_artifact_path(canonical_md_path, state_dir=state_dir),
        "capsules": capsules,
    }


def load_project_capsule(state_dir: Path, canonical_project_id: str) -> dict:
    path = state_dir.resolve() / "projects" / f"{canonical_project_id}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(
            f"Project capsule does not exist: {path}; run `python -m ledger compile` first."
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Project capsule is invalid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("canonical_project_id") != canonical_project_id:
        raise ValueError(f"Project capsule is malformed: {path}")
    return payload


def show_payload(state_dir: Path, referent: str) -> dict:
    """Resolve ``referent`` and return the project capsule (or ambiguity/unresolved)."""
    resolution = resolve_payload(state_dir, referent)
    if resolution["status"] != "resolved":
        return resolution
    capsule = load_project_capsule(state_dir, resolution["canonical_project_id"])
    result = dict(capsule)
    result["status"] = "resolved"
    result["referent"] = resolution["referent"]
    result["matched_by"] = resolution["matched_by"]
    return result


def locate_payload(state_dir: Path, referent: str) -> dict:
    """Resolve ``referent`` and return the preferred location and ranked observations."""
    resolution = resolve_payload(state_dir, referent)
    if resolution["status"] != "resolved":
        return resolution
    capsule = load_project_capsule(state_dir, resolution["canonical_project_id"])
    return {
        "schema_version": capsule["schema_version"],
        "compiler_version": capsule["compiler_version"],
        "run_id": capsule["run_id"],
        "compiled_at": capsule["compiled_at"],
        "as_of": capsule["as_of"],
        "status": "resolved",
        "referent": resolution["referent"],
        "matched_by": resolution["matched_by"],
        "canonical_project_id": capsule["canonical_project_id"],
        "project_key": capsule["project_key"],
        "display_name": capsule["display_name"],
        "gbrain_source_slug": capsule.get("gbrain_source_slug"),
        "gbrain": capsule.get("gbrain"),
        "preferred_location": capsule["preferred_location"],
        "warnings": list(capsule.get("warnings") or []),
        "candidate_count": capsule["observation_count"],
        "locations": capsule["observations"],
    }
