from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import build_ledger
from ledger import cli


class NonMutatingRefreshTests(unittest.TestCase):
    def test_refresh_can_compile_without_writing_repo_markdown_mirror(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            (source / "README.md").write_text("# Example\nProvider fixture.\n", encoding="utf-8")

            config_path = root / "ledger_config.json"
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
            output_dir = root / "output"
            state_dir = root / "state"

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

            self.assertEqual(result, 0)
            self.assertTrue((output_dir / "projects.csv").is_file())
            self.assertTrue((output_dir / "projects.json").is_file())
            self.assertTrue((output_dir / "projects.md").is_file())
            self.assertTrue((state_dir / "system-manifest.json").is_file())
            self.assertTrue((state_dir / "observations.json").is_file())


if __name__ == "__main__":
    unittest.main()
