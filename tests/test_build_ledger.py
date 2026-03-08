from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import build_ledger  # noqa: E402

TMP_ROOT = Path(__file__).resolve().parent / ".tmp"
TMP_ROOT.mkdir(exist_ok=True)


class ScratchDir:
    def __enter__(self) -> Path:
        self.path = TMP_ROOT / uuid.uuid4().hex
        self.path.mkdir(parents=True, exist_ok=False)
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


class ProjectLedgerTests(unittest.TestCase):
    def test_inspect_candidate_finds_nested_readme(self) -> None:
        with ScratchDir() as root:
            (root / "AI").mkdir()
            (root / "AI" / "dumping").mkdir()
            (root / "AI" / "dumping" / "README.md").write_text("# AI Dumping\n", encoding="utf-8")

            result = build_ledger.inspect_candidate(root / "AI", set())

            self.assertGreaterEqual(result["score"], 2)
            self.assertIn("readme", result["reasons"])

    def test_markdown_link_encodes_spaces(self) -> None:
        base = Path(r"C:\temp\output")
        target = Path(r"C:\temp\My Folder\README.md")
        link = build_ledger.markdown_link(target, base, "README")
        self.assertEqual(link, "[README](../My%20Folder/README.md)")

    def test_build_entry_reads_git_and_sidecar(self) -> None:
        with ScratchDir() as root:
            project = root / "sample-project"
            project.mkdir()
            (project / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
            (project / "README.md").write_text("# Sample Project\nA useful project.\n", encoding="utf-8")
            (project / ".project-ledger.json").write_text(
                json.dumps(
                    {
                        "project_key": "sample-project",
                        "display_name": "Sample Project",
                        "status": "active",
                        "shared": True
                    }
                ),
                encoding="utf-8",
            )

            subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=project, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=project, check=True, capture_output=True)
            subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=project, check=True, capture_output=True)

            root_cfg = {
                "label": "test-root",
                "discovery": "children",
                "_resolved_path": root
            }
            defaults = {
                "tree_scan_max_entries": 5000,
                "tree_scan_max_depth": 8
            }
            output_dir = root / "output"
            output_dir.mkdir()

            entry = build_ledger.build_entry(project, root_cfg, defaults, output_dir)

            self.assertIsNotNone(entry)
            assert entry is not None
            self.assertTrue(entry["git"])
            self.assertEqual(entry["project_key"], "sample-project")
            self.assertEqual(entry["status"], "active")
            self.assertTrue(entry["shared"])
            self.assertEqual(entry["project_type"], "git")


if __name__ == "__main__":
    unittest.main()
