from __future__ import annotations

from pathlib import Path

import build_ledger


def scan_without_markdown_mirror(*, config_path: Path, output_dir: Path) -> dict[str, Path]:
    """Run the compatibility scanner without mutating the repository mirror.

    External provider consumers need the same CSV/JSON/Markdown compatibility
    outputs as ``build_ledger.py`` but must not dirty the Project Ledger checkout.
    The legacy script keeps its historical mirror-writing behavior; this helper is
    the explicit automation-safe path used by ``ledger refresh`` when requested.
    """
    config_path = config_path.resolve()
    output_dir = build_ledger.ensure_output_dir(output_dir.resolve())
    config = build_ledger.read_json(config_path)
    entries = build_ledger.collect_entries(config, config_path.parent, output_dir)
    root_summaries = build_ledger.summarize_roots(config, config_path.parent)

    csv_path = output_dir / "projects.csv"
    json_path = output_dir / "projects.json"
    md_path = output_dir / "projects.md"

    build_ledger.write_csv(entries, csv_path)
    build_ledger.write_json(entries, json_path, config_path)
    build_ledger.write_markdown(entries, md_path, root_summaries)

    return {
        "csv": csv_path,
        "json": json_path,
        "markdown": md_path,
    }
