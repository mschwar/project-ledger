"""Idempotent Project Ledger -> GBrain `projects/<slug>.md` reality projector.

Projects compiled canonical project capsules (``state/projects/<id>.json``) into
a GBrain markdown repository (such as ``/home/prime/brain``) under ``projects/<slug>.md``:

- Existing pages preserve 100% of their YAML frontmatter, human prose, and
  ``<!-- timeline -->`` sections; only the fenced
  ``<!-- BEGIN PROJECT-LEDGER REALITY --> ... <!-- END PROJECT-LEDGER REALITY -->``
  block is inserted or updated.
- Missing pages receive a clean ``type: project`` stub with YAML-safe quoting,
  bounded purpose text, the Reality Provider fenced block, and ``<!-- timeline -->``.
- Zero volatile timestamps or run IDs are emitted inside the fenced block, so
  unchanged canonical projects produce **zero file writes** and **zero git commits**.
- With ``--ingest``, only touched ``projects/<slug>.md`` paths are staged/committed
  via path-scoped ``git add --`` / ``git commit --`` (never ``git add -A``), followed
  by ``gbrain sync --repo <brain_dir> --src-subpath projects --no-embed --no-extract``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .capsules import load_project_capsule
from .identity import load_canonical_projects, resolve_payload

REALITY_BEGIN_MARKER = "<!-- BEGIN PROJECT-LEDGER REALITY -->"
REALITY_END_MARKER = "<!-- END PROJECT-LEDGER REALITY -->"
TIMELINE_MARKER = "<!-- timeline -->"

_REALITY_BLOCK_RE = re.compile(
    re.escape(REALITY_BEGIN_MARKER) + r".*?" + re.escape(REALITY_END_MARKER),
    re.DOTALL,
)


def _yaml_quote(value: str) -> str:
    """Single-quote a YAML scalar safely (handles '#', ':', '<', quotes, etc.)."""
    cleaned = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return "'" + cleaned.replace("'", "''") + "'"


def render_reality_block(project: dict[str, Any], capsule: dict[str, Any]) -> str:
    """Render the deterministic, timestamp-free Reality Provider fenced block."""
    canonical_id = str(project.get("canonical_project_id") or capsule.get("canonical_project_id") or "")
    project_key = str(project.get("project_key") or capsule.get("project_key") or canonical_id)
    gbrain = capsule.get("gbrain") if isinstance(capsule.get("gbrain"), dict) else (project.get("gbrain") or {})
    page_key = str(gbrain.get("page_key") or "")
    source_slug = str(
        capsule.get("gbrain_source_slug")
        or project.get("gbrain_source_slug")
        or gbrain.get("source_slug")
        or "default"
    )

    pref = capsule.get("preferred_location") if isinstance(capsule.get("preferred_location"), dict) else {}
    pref_loc = str(pref.get("location") or "").strip()
    pref_state = str(pref.get("state") or "unresolved").strip()
    pref_class = str(pref.get("source_class") or "unknown").strip()

    git_state = capsule.get("git_state") if isinstance(capsule.get("git_state"), dict) else {}
    current_state = capsule.get("current_state") if isinstance(capsule.get("current_state"), dict) else {}
    canonical_url = str(capsule.get("canonical_url") or "").strip()

    lines = [
        REALITY_BEGIN_MARKER,
        "## Project Ledger Reality",
        "",
        f"- **Canonical ID:** `{canonical_id}`",
        f"- **Project Key:** `{project_key}`",
    ]
    if pref_loc:
        lines.append(f"- **Preferred Location:** `{pref_loc}` (`{pref_class}`, `{pref_state}`)")
    else:
        lines.append(f"- **Preferred Location:** *({pref_state})*")

    if git_state.get("state") == "known":
        branch = str(git_state.get("head_branch") or "detached")
        commit = str(git_state.get("head_commit") or "")[:12]
        drift = str(git_state.get("drift_state") or "unknown")
        commit_part = f" @ `{commit}`" if commit else ""
        lines.append(f"- **Git State:** `{branch}`{commit_part} (`{drift}`)")

    warning_codes = [
        str(code)
        for code in (pref.get("warning_codes") or git_state.get("warning_codes") or [])
        if str(code).strip()
    ]
    if warning_codes:
        formatted_codes = ", ".join(f"`{code}`" for code in warning_codes)
        lines.append(f"- **Drift Warnings:** {formatted_codes}")

    lifecycle = str(current_state.get("lifecycle_state") or "").strip()
    if lifecycle and lifecycle != "unknown":
        lines.append(f"- **Lifecycle:** `{lifecycle}`")

    if canonical_url:
        lines.append(f"- **Canonical URL:** <{canonical_url}>")

    lines.append(f"- **GBrain Corpus:** `{source_slug}` (`{page_key}`)")

    obs_summaries = capsule.get("observations") if isinstance(capsule.get("observations"), list) else []
    obs_total = int(capsule.get("observation_count") or len(project.get("observation_ids", [])) or len(obs_summaries))
    source_ids = sorted(
        {
            str(item.get("source_id") or "").strip()
            for item in obs_summaries
            if isinstance(item, dict) and str(item.get("source_id") or "").strip()
        }
    )
    if source_ids:
        sources_text = ", ".join(f"`{sid}`" for sid in source_ids)
        lines.append(f"- **Observations:** `{obs_total}` across {sources_text}")
    else:
        lines.append(f"- **Observations:** `{obs_total}`")

    lines.append(REALITY_END_MARKER)
    return "\n".join(lines)


def merge_reality_block_into_page(existing_text: str, reality_block: str) -> str:
    """Update or insert ``reality_block`` without modifying frontmatter, prose, or timeline."""
    normalized = existing_text.replace("\r\n", "\n")

    if REALITY_BEGIN_MARKER in normalized and REALITY_END_MARKER in normalized:
        updated = _REALITY_BLOCK_RE.sub(lambda _m: reality_block, normalized, count=1)
        if not updated.endswith("\n"):
            updated += "\n"
        return updated

    timeline_idx = normalized.find(TIMELINE_MARKER)
    if timeline_idx != -1:
        before = normalized[:timeline_idx].rstrip()
        after = normalized[timeline_idx:]
        merged = f"{before}\n\n{reality_block}\n\n{after}"
        if not merged.endswith("\n"):
            merged += "\n"
        return merged

    body = normalized.rstrip()
    return f"{body}\n\n{reality_block}\n"


def render_new_project_stub(
    project: dict[str, Any],
    capsule: dict[str, Any],
    reality_block: str,
) -> str:
    """Render a brand-new ``projects/<slug>.md`` stub page."""
    canonical_id = str(project.get("canonical_project_id") or capsule.get("canonical_project_id") or "")
    project_key = str(project.get("project_key") or capsule.get("project_key") or canonical_id)
    display_name = str(
        capsule.get("display_name") or project.get("display_name") or project_key
    ).strip() or project_key

    current_state = capsule.get("current_state") if isinstance(capsule.get("current_state"), dict) else {}
    raw_lifecycle = str(current_state.get("lifecycle_state") or "").strip()
    status_value = raw_lifecycle if (raw_lifecycle and raw_lifecycle != "unknown") else "active"

    raw_tags = current_state.get("tags") if isinstance(current_state.get("tags"), list) else []
    tag_set: list[str] = ["project-ledger"]
    for raw_tag in raw_tags:
        cleaned_tag = re.sub(r"[^a-z0-9-]+", "-", str(raw_tag or "").lower()).strip("-")
        if cleaned_tag and cleaned_tag not in tag_set:
            tag_set.append(cleaned_tag)

    purpose = str(capsule.get("purpose") or "").replace("\r", " ").strip()
    summary_paragraph = (
        purpose
        if purpose
        else "Canonical project stub materialized from Project Ledger compiled reality."
    )

    frontmatter_lines = [
        "---",
        "type: project",
        f"title: {_yaml_quote(display_name)}",
        f"status: {_yaml_quote(status_value)}",
        f"canonical_project_id: {_yaml_quote(canonical_id)}",
        f"project_key: {_yaml_quote(project_key)}",
        "ingested_via: 'project-ledger:sync-gbrain'",
        "source_kind: 'project-ledger:capsule'",
        "tags:",
    ]
    for tag in tag_set:
        frontmatter_lines.append(f"  - {_yaml_quote(tag)}")
    frontmatter_lines.append("---")

    heading = display_name.replace("\r", " ").replace("\n", " ").strip()
    parts = [
        "\n".join(frontmatter_lines),
        "",
        f"# {heading}",
        "",
        summary_paragraph,
        "",
        reality_block,
        "",
        TIMELINE_MARKER,
        "",
    ]
    return "\n".join(parts)


def _select_target_projects(
    state_dir: Path,
    canonical_projects: list[dict[str, Any]],
    referents: list[str] | None,
) -> list[dict[str, Any]]:
    if not referents:
        return list(canonical_projects)

    by_id = {str(p["canonical_project_id"]): p for p in canonical_projects}
    selected_ids: list[str] = []
    seen: set[str] = set()
    for ref in referents:
        query = str(ref or "").strip()
        if not query:
            continue
        if query in by_id:
            cid = query
        else:
            res = resolve_payload(state_dir, query)
            if res.get("status") != "resolved" or not res.get("canonical_project_id"):
                raise ValueError(
                    f"Cannot sync-gbrain for {query!r}: resolution status={res.get('status')!r}"
                )
            cid = str(res["canonical_project_id"])
        if cid not in seen and cid in by_id:
            seen.add(cid)
            selected_ids.append(cid)
    return [by_id[cid] for cid in selected_ids]


def _ingest_changed_pages(
    *,
    brain_dir: Path,
    changed_rel_paths: list[str],
    gbrain_bin: str = "gbrain",
) -> dict[str, Any]:
    """Commit only ``changed_rel_paths`` in ``brain_dir`` and run ``gbrain sync``."""
    if not changed_rel_paths:
        return {
            "status": "noop_unchanged",
            "git_committed": False,
            "git_commit": None,
            "gbrain_synced": False,
        }

    git_committed = False
    git_commit: str | None = None
    if (brain_dir / ".git").exists():
        add_proc = subprocess.run(
            ["git", "-C", str(brain_dir), "add", "--", *changed_rel_paths],
            capture_output=True,
            text=True,
            check=False,
        )
        if add_proc.returncode != 0:
            raise RuntimeError(
                f"git add failed in {brain_dir}: {(add_proc.stderr or add_proc.stdout).strip()}"
            )

        diff_proc = subprocess.run(
            ["git", "-C", str(brain_dir), "diff", "--cached", "--quiet", "--", *changed_rel_paths],
            capture_output=True,
            text=True,
            check=False,
        )
        if diff_proc.returncode == 1:
            msg = f"chore(project-ledger): sync {len(changed_rel_paths)} canonical project reality page(s)"
            commit_proc = subprocess.run(
                ["git", "-C", str(brain_dir), "commit", "-m", msg, "--", *changed_rel_paths],
                capture_output=True,
                text=True,
                check=False,
            )
            if commit_proc.returncode != 0:
                raise RuntimeError(
                    f"git commit failed in {brain_dir}: {(commit_proc.stderr or commit_proc.stdout).strip()}"
                )
            git_committed = True
            rev_proc = subprocess.run(
                ["git", "-C", str(brain_dir), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
            )
            if rev_proc.returncode == 0:
                git_commit = rev_proc.stdout.strip() or None

    resolved_bin = shutil.which(gbrain_bin) if "/" not in gbrain_bin else (
        gbrain_bin if Path(gbrain_bin).is_file() else None
    )
    if not resolved_bin:
        return {
            "status": "git_only_gbrain_bin_missing",
            "git_committed": git_committed,
            "git_commit": git_commit,
            "gbrain_synced": False,
        }

    # Explicitly pin --source default and --no-pull:
    # 1. Without --source default, running inside /central/repos/active/project-ledger
    #    causes GBrain's local_path resolver to bind to source_id='active-repos'.
    # 2. Without --no-pull, uncommitted write-through MCP edits elsewhere in /home/prime/brain
    #    could cause git pull to abort before importing projects/*.md.
    sync_proc = subprocess.run(
        [
            resolved_bin,
            "sync",
            "--source",
            "default",
            "--repo",
            str(brain_dir),
            "--src-subpath",
            "projects",
            "--no-pull",
            "--no-embed",
            "--no-extract",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if sync_proc.returncode != 0:
        raise RuntimeError(
            f"gbrain sync failed ({sync_proc.returncode}): {(sync_proc.stderr or sync_proc.stdout).strip()}"
        )

    return {
        "status": "ingested",
        "git_committed": git_committed,
        "git_commit": git_commit,
        "gbrain_synced": True,
    }


def sync_gbrain_pages(
    *,
    state_dir: Path,
    brain_dir: Path,
    project_referents: list[str] | None = None,
    dry_run: bool = False,
    ingest: bool = False,
    gbrain_bin: str = "gbrain",
) -> dict[str, Any]:
    """Project canonical project capsules into ``<brain_dir>/projects/<slug>.md``."""
    state_dir = state_dir.resolve()
    brain_dir = brain_dir.expanduser().resolve()
    canonical_payload = load_canonical_projects(state_dir)
    all_projects = list(canonical_payload.get("projects", []))
    targets = _select_target_projects(state_dir, all_projects, project_referents)

    projects_dir = brain_dir / "projects"
    if not dry_run:
        projects_dir.mkdir(parents=True, exist_ok=True)

    created_pages: list[str] = []
    updated_pages: list[str] = []
    unchanged_pages: list[str] = []
    seen_page_keys: dict[str, str] = {}

    for project in targets:
        cid = str(project["canonical_project_id"])
        capsule = load_project_capsule(state_dir, cid)
        gbrain = capsule.get("gbrain") if isinstance(capsule.get("gbrain"), dict) else (project.get("gbrain") or {})
        page_key = str(gbrain.get("page_key") or "").strip()
        if not page_key.startswith("projects/") or len(page_key.split("/")) != 2:
            raise ValueError(f"Invalid gbrain.page_key {page_key!r} for {cid}")
        if page_key in seen_page_keys:
            raise ValueError(
                f"Duplicate gbrain.page_key {page_key!r} across {seen_page_keys[page_key]} and {cid}"
            )
        seen_page_keys[page_key] = cid

        rel_md = f"{page_key}.md"
        page_path = brain_dir / rel_md
        reality_block = render_reality_block(project, capsule)

        if page_path.is_file():
            existing = page_path.read_text(encoding="utf-8")
            desired = merge_reality_block_into_page(existing, reality_block)
            if desired == existing.replace("\r\n", "\n"):
                unchanged_pages.append(rel_md)
                continue
            if not dry_run:
                tmp_path = page_path.parent / f".{page_path.name}.tmp"
                tmp_path.write_text(desired, encoding="utf-8")
                tmp_path.replace(page_path)
            updated_pages.append(rel_md)
        else:
            desired = render_new_project_stub(project, capsule, reality_block)
            if not dry_run:
                tmp_path = page_path.parent / f".{page_path.name}.tmp"
                tmp_path.write_text(desired, encoding="utf-8")
                tmp_path.replace(page_path)
            created_pages.append(rel_md)

    changed_rel_paths = sorted(created_pages + updated_pages)
    if ingest and not dry_run:
        ingest_result = _ingest_changed_pages(
            brain_dir=brain_dir,
            changed_rel_paths=changed_rel_paths,
            gbrain_bin=gbrain_bin,
        )
    else:
        ingest_result = {
            "status": "dry_run" if dry_run else "skipped",
            "git_committed": False,
            "git_commit": None,
            "gbrain_synced": False,
        }

    return {
        "ok": True,
        "brain_dir": str(brain_dir),
        "state_dir": str(state_dir),
        "dry_run": dry_run,
        "target_project_count": len(targets),
        "created_count": len(created_pages),
        "updated_count": len(updated_pages),
        "unchanged_count": len(unchanged_pages),
        "created_pages": created_pages,
        "updated_pages": updated_pages,
        "ingest": ingest_result,
    }
