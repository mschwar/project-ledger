from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

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

    def test_find_first_existing_skips_unreadable_candidate(self) -> None:
        with ScratchDir() as root:
            project = root / "sample-project"
            project.mkdir()
            readable = project / "Readme.md"
            readable.write_text("# Sample\n", encoding="utf-8")

            real_exists = Path.exists

            def side_effect(self: Path) -> bool:
                if self.name == "README.md":
                    raise OSError("simulated read error")
                return real_exists(self)

            with mock.patch.object(Path, "exists", new=side_effect):
                found = build_ledger.find_first_existing(project, build_ledger.README_CANDIDATES)

            self.assertEqual(found, readable)

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
                        "shared": True,
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
                "_resolved_path": root,
            }
            defaults = {
                "tree_scan_max_entries": 5000,
                "tree_scan_max_depth": 8,
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

    def test_inventory_policy_ingests_project_discovery_roots(self) -> None:
        with ScratchDir() as root:
            config_path = self._make_inventory_policy_fixture(root)
            output_dir = root / "output"
            output_dir.mkdir()

            config = build_ledger.read_json(config_path)
            entries = build_ledger.collect_entries(config, config_path.parent, output_dir)
            by_key = {entry["project_key"]: entry for entry in entries}

            self.assertIn("google-drive:projects", by_key)
            self.assertIn("google-drive:projects/lmntl", by_key)
            self.assertIn("google-drive:omi", by_key)
            self.assertIn("google-drive:repos-other/gstack-main", by_key)
            self.assertNotIn("google-drive:writing", by_key)

            lmntl = by_key["google-drive:projects/lmntl"]
            self.assertEqual(lmntl["source_label"], "google-drive-policy:Projects")
            self.assertTrue(lmntl["shared"])
            self.assertIn("google-drive", lmntl["tags"])
            self.assertEqual(lmntl["storage_scope"], "shared")
            self.assertEqual(lmntl["path_from_root"], "Projects/LMNTL")
            self.assertEqual(lmntl["canonical_url"], "gdrive://googledrive/Projects/LMNTL")
            self.assertIn("policy-treatment:project_discovery", lmntl["include_reason"])

            omi = by_key["google-drive:omi"]
            self.assertEqual(omi["path_from_root"], "OMI")
            self.assertEqual(omi["canonical_url"], "gdrive://googledrive/OMI")

    def test_inventory_policy_identity_is_stable_across_reruns(self) -> None:
        with ScratchDir() as root:
            config_path = self._make_inventory_policy_fixture(root)
            output_dir = root / "output"
            output_dir.mkdir()
            config = build_ledger.read_json(config_path)

            first_entries = build_ledger.collect_entries(config, config_path.parent, output_dir)
            second_entries = build_ledger.collect_entries(config, config_path.parent, output_dir)

            first = {entry["project_key"]: entry["project_hash"] for entry in first_entries}
            second = {entry["project_key"]: entry["project_hash"] for entry in second_entries}
            self.assertEqual(first, second)
            self.assertEqual(
                first["google-drive:projects/lmntl"],
                build_ledger.sha256_hex("google-drive:projects/lmntl"),
            )

    def test_inventory_policy_preserves_long_paths_and_encodes_urls(self) -> None:
        with ScratchDir() as root:
            long_name = "Long Project Name With Spaces And Enough Characters To Exercise Path Handling 2026"
            config_path = self._make_inventory_policy_fixture(root, extra_project_name=long_name)
            output_dir = root / "output"
            output_dir.mkdir()
            config = build_ledger.read_json(config_path)

            entries = build_ledger.collect_entries(config, config_path.parent, output_dir)
            target_key = f"google-drive:projects/{long_name.lower()}"
            by_key = {entry["project_key"]: entry for entry in entries}
            self.assertIn(target_key, by_key)

            entry = by_key[target_key]
            self.assertEqual(entry["path_from_root"], f"Projects/{long_name}")
            self.assertIn("Long%20Project%20Name%20With%20Spaces", entry["canonical_url"])
            self.assertTrue(entry["path"].endswith(long_name))

    def _make_inventory_policy_fixture(self, root: Path, extra_project_name: str | None = None) -> Path:
        mount_root = root / "GoogleDrive"
        mount_root.mkdir()

        projects = mount_root / "Projects"
        projects.mkdir()
        (projects / "LMNTL").mkdir()
        (projects / "LMNTL" / "README.md").write_text("# LMNTL\nLedger candidate\n", encoding="utf-8")

        if extra_project_name:
            (projects / extra_project_name).mkdir()
            (projects / extra_project_name / "README.md").write_text(
                f"# {extra_project_name}\nLong path test\n",
                encoding="utf-8",
            )

        repos_other = mount_root / "repos-other"
        repos_other.mkdir()
        (repos_other / "gstack-main").mkdir()
        (repos_other / "gstack-main" / "README.md").write_text("# gstack\nRepo-like root\n", encoding="utf-8")

        (mount_root / "OMI").mkdir()
        (mount_root / "Writing").mkdir()

        inventory_records = [
            self._inventory_record("Projects", True, "Projects"),
            self._inventory_record("Projects/LMNTL", True, "Projects"),
            self._inventory_record("Projects/LMNTL/README.md", False, "Projects"),
            self._inventory_record("OMI", True, "OMI"),
            self._inventory_record("repos-other", True, "repos-other"),
            self._inventory_record("repos-other/gstack-main", True, "repos-other"),
            self._inventory_record("repos-other/gstack-main/README.md", False, "repos-other"),
            self._inventory_record("Writing", True, "Writing"),
        ]
        if extra_project_name:
            inventory_records.extend(
                [
                    self._inventory_record(f"Projects/{extra_project_name}", True, "Projects"),
                    self._inventory_record(f"Projects/{extra_project_name}/README.md", False, "Projects"),
                ]
            )

        inventory_path = root / "inventory.jsonl"
        inventory_path.write_text(
            "\n".join(json.dumps(record, sort_keys=True) for record in inventory_records) + "\n",
            encoding="utf-8",
        )

        policy = {
            "roots": {
                "Projects": {
                    "crawl_treatment": "project_discovery",
                    "project_ledger_candidate": True,
                    "root_class": "project_roots",
                    "rationale": "Primary project bucket.",
                },
                "OMI": {
                    "crawl_treatment": "project_discovery",
                    "project_ledger_candidate": True,
                    "root_class": "project_roots",
                    "rationale": "Named initiative root.",
                },
                "repos-other": {
                    "crawl_treatment": "project_discovery",
                    "project_ledger_candidate": True,
                    "root_class": "repo_like",
                    "rationale": "Repository-like collection.",
                },
                "Writing": {
                    "crawl_treatment": "content_extract",
                    "project_ledger_candidate": False,
                    "root_class": "knowledge_docs",
                    "rationale": "Document-focused root.",
                },
            }
        }
        policy_path = root / "policy.json"
        policy_path.write_text(json.dumps(policy, indent=2), encoding="utf-8")

        config = {
            "defaults": {
                "exclude_names": [".git", ".obsidian", "node_modules", "__pycache__"],
                "min_score": 2,
                "tree_scan_max_entries": 5000,
                "tree_scan_max_depth": 8,
                "git_repo_max_depth": 6,
            },
            "roots": [
                {
                    "path": str(mount_root),
                    "label": "google-drive-policy",
                    "discovery": "inventory_policy",
                    "inventory_jsonl": str(inventory_path),
                    "policy_path": str(policy_path),
                    "policy_crawl_treatments": ["project_discovery"],
                    "require_project_ledger_candidate": True,
                    "remote_name": "googledrive",
                    "storage_scope": "shared",
                }
            ],
        }
        config_path = root / "ledger_config.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        return config_path

    def _inventory_record(self, path_text: str, is_dir: bool, root_label: str) -> dict:
        name = Path(path_text).name
        return {
            "crawl_timestamp": "2026-05-19T01:54:47.846166+00:00",
            "id": f"id-{path_text.replace('/', '-').replace(' ', '-').lower()}",
            "is_dir": is_dir,
            "mime_type": "inode/directory" if is_dir else "text/markdown",
            "mod_time": "2026-05-19T01:54:47.846166+00:00",
            "name": name,
            "original_id": "",
            "path": path_text,
            "remote": "googledrive:",
            "root_label": root_label,
            "size": -1 if is_dir else 128,
        }


if __name__ == "__main__":
    unittest.main()
