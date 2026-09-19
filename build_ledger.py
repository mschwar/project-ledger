#!/usr/bin/env python3
"""Compatibility entrypoint for the Project Ledger scanner.

The source adapters and normalization logic have been extracted into the
``ledger/`` package (see ``ledger/models.py`` and ``ledger/sources/``) per
P2.1, frozen by the existing compatibility tests. This file remains the stable
CLI/entrypoint that ``ledger refresh`` and homelab consumers call: it re-exports
the extracted names unchanged and owns only the orchestration glue (argument
parsing, output writers, and the mirror step) so existing behavior is preserved.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

from ledger.models import (
    CSV_FIELDS,
    classify_project_type,
    coerce_bool,
    escape_md_cell,
    isoformat_from_ts,
    markdown_link,
    markdown_target,
    normalize_remote_url,
    normalize_tags,
    parse_iso_datetime,
    repo_name_from_remote,
    sha256_hex,
    slugify,
)
from ledger.sources.base import collect_entries
from ledger.sources.common import (
    CODE_EXTENSIONS,
    DEFAULT_EXCLUDES,
    README_CANDIDATES,
    SIDECAR_CANDIDATES,
    SPECIAL_FILES,
    find_first_existing,
    infer_storage_scope,
    iter_shallow_files,
    load_sidecar,
    merge_exclude_names,
    parse_readme_summary,
    read_json,
    resolve_path,
    safe_path_exists,
    safe_path_is_dir,
)
from ledger.sources.filesystem import (
    build_entry,
    discover_children,
    discover_git_repos,
    inspect_candidate,
    summarize_tree,
)
from ledger.sources.git import gather_git_metadata, git_output
from ledger.sources.inventory_policy import (
    build_google_drive_url,
    build_inventory_entry,
    discover_inventory_policy_candidates,
    inventory_candidate_summary,
    load_inventory_jsonl,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a ledger for projects, repos, and Obsidian vaults."
    )
    parser.add_argument(
        "--config",
        default="ledger_config.json",
        help="Path to the ledger config JSON. Defaults to ledger_config.json.",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory for generated CSV/JSON/Markdown outputs. Defaults to output/.",
    )
    return parser.parse_args()


def summarize_roots(config: dict, config_dir: Path) -> list[dict]:
    summaries: list[dict] = []
    for root_cfg_raw in config.get("roots", []):
        root_cfg = dict(root_cfg_raw)
        root_path = resolve_path(root_cfg["path"], config_dir)
        summaries.append(
            {
                "label": str(root_cfg.get("label", "")).strip() or root_path.name,
                "path": str(root_path),
                "discovery": str(root_cfg.get("discovery", "children")).strip() or "children",
                "exists": safe_path_exists(root_path),
            }
        )
    return summaries


def write_csv(entries: list[dict], output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for entry in entries:
            row = dict(entry)
            row["tags"] = "|".join(entry.get("tags", []))
            writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})


def write_json(entries: list[dict], output_path: Path, config_path: Path) -> None:
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "config_path": str(config_path.resolve()),
        "entry_count": len(entries),
        "entries": entries,
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def render_location_cell(entry: dict, markdown_path: Path) -> str:
    if entry["canonical_url"]:
        return f"[url]({entry['canonical_url']})"
    target = Path(entry["path"])
    return markdown_link(target, markdown_path.parent, escape_md_cell(Path(entry["path"]).name))


def render_last_push(entry: dict) -> str:
    return entry["last_push_at"] or entry["last_remote_ref_at"]


def write_markdown(entries: list[dict], output_path: Path, root_summaries: list[dict] | None = None) -> None:
    git_count = sum(1 for entry in entries if entry["git"])
    obsidian_count = sum(1 for entry in entries if entry["obsidian"])
    shared_count = sum(1 for entry in entries if entry["shared"])

    lines = [
        "# Project Ledger",
        "",
        f"Generated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        "",
        f"- Entries: {len(entries)}",
        f"- Git repos: {git_count}",
        f"- Obsidian vaults: {obsidian_count}",
        f"- Shared/synced: {shared_count}",
        "- Refresh notes: live recency data refreshed from configured roots.",
        "",
        "> `Last Push` uses `last_push_at` from the sidecar when available; otherwise it falls back to the newest local remote-ref timestamp.",
        "",
    ]

    if root_summaries is not None:
        mirror_roots = [root for root in root_summaries if "/central/registry/mirrors/matthews-macbook-air-2/" in root["path"]]
        missing_roots = [root for root in root_summaries if not root["exists"]]
        lines.extend([
            "## Scan coverage and gaps",
            "",
        ])
        if mirror_roots:
            lines.append("**MacBook mirror roots scanned this refresh:**")
            for root in mirror_roots:
                lines.append(f"- `{root['label']}` — `{root['path']}`")
            lines.append("")
        lines.append("**Configured roots:**")
        for root in root_summaries:
            status = "missing" if not root["exists"] else "present"
            lines.append(f"- `{root['label']}` — `{root['path']}` — {status} ({root['discovery']})")
        lines.append("")
        if missing_roots:
            lines.append("**Known gaps / inaccessible roots:**")
            for root in missing_roots:
                lines.append(f"- `{root['path']}` ({root['label']})")
            lines.append("")
        else:
            lines.append("**Known gaps / inaccessible roots:** none detected in configured scan roots.")
            lines.append("")

    lines.extend([
        "| Name | Type | Scope | Git | Obsidian | Last Touch | README | Location | Repo | Last Push |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ])

    for entry in entries:
        location_cell = render_location_cell(entry, output_path)
        last_push = render_last_push(entry)
        readme_cell = entry["readme_link_md"] or ""
        row = [
            escape_md_cell(entry["name"]),
            escape_md_cell(entry["project_type"]),
            escape_md_cell(entry["storage_scope"]),
            "yes" if entry["git"] else "",
            "yes" if entry["obsidian"] else "",
            escape_md_cell(entry["last_touch_at"]),
            readme_cell,
            location_cell,
            escape_md_cell(entry["repo_name"]),
            escape_md_cell(last_push),
        ]
        lines.append("| " + " | ".join(row) + " |")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_markdown_mirror(source_path: Path, mirror_path: Path) -> None:
    mirror_path.parent.mkdir(parents=True, exist_ok=True)
    mirror_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")


def ensure_output_dir(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def main() -> int:
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    config_path = resolve_path(args.config, script_dir)
    output_dir = ensure_output_dir(resolve_path(args.output_dir, script_dir))
    config = read_json(config_path)
    entries = collect_entries(config, config_path.parent, output_dir)
    root_summaries = summarize_roots(config, config_path.parent)

    csv_path = output_dir / "projects.csv"
    json_path = output_dir / "projects.json"
    md_path = output_dir / "projects.md"

    write_csv(entries, csv_path)
    write_json(entries, json_path, config_path)
    write_markdown(entries, md_path, root_summaries)

    mirror_path = script_dir / "docs" / "ledgers" / "projects-ledger.md"
    write_markdown_mirror(md_path, mirror_path)

    print(f"Wrote {len(entries)} entries")
    print(f"  CSV:  {csv_path}")
    print(f"  JSON: {json_path}")
    print(f"  MD:   {md_path}")
    print(f"  Mirror: {mirror_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())