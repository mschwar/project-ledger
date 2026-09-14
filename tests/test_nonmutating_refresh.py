from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import build_ledger
from ledger import cli


def test_refresh_can_compile_without_writing_repo_markdown_mirror(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("# Example\nProvider fixture.\n", encoding="utf-8")

    config_path = tmp_path / "ledger_config.json"
    config_path.write_text(
        json.dumps(
            {
                "roots": [
                    {
                        "path": str(source),
                        "label": "fixture",
                        "source_id": "fixture-source",
                        "discovery": "self",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "output"
    state_dir = tmp_path / "state"

    with mock.patch.object(
        build_ledger,
        "write_markdown_mirror",
        side_effect=AssertionError("provider refresh must not write the repository mirror"),
    ):
        result = cli.main(
            [
                "refresh",
                "--config",
                str(config_path),
                "--output-dir",
                str(output_dir),
                "--state-dir",
                str(state_dir),
                "--no-markdown-mirror",
            ]
        )

    assert result == 0
    assert (output_dir / "projects.csv").is_file()
    assert (output_dir / "projects.json").is_file()
    assert (output_dir / "projects.md").is_file()
    assert (state_dir / "system-manifest.json").is_file()
    assert (state_dir / "observations.json").is_file()
