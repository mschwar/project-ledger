from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import unittest

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ledger.models import (
    classify_project_type,
    coerce_bool,
    normalize_remote_url,
    repo_name_from_remote,
)
from ledger.checkpoints import collect_entries_incremental
from ledger.sources import base as sources_base
from ledger.sources.base import FilesystemAdapter, InventoryPolicyAdapter, collect_entries
from ledger.sources.filesystem import build_entry, discover_children
from ledger.sources.git import gather_git_metadata, git_output


class ScratchDir:
    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory()
        return Path(self._tmp.name)

    def __exit__(self, exc_type, exc, tb) -> None:
        self._tmp.cleanup()


class NormalizationTests(unittest.TestCase):
    def test_normalize_remote_url_scp_style(self) -> None:
        self.assertEqual(
            normalize_remote_url("git@github.com:mschwar/project-ledger.git"),
            "github.com/mschwar/project-ledger",
        )

    def test_normalize_remote_url_https_with_auth(self) -> None:
        self.assertEqual(
            normalize_remote_url("https://git@github.com/mschwar/homelab.git"),
            "github.com/mschwar/homelab",
        )

    def test_normalize_remote_url_bare_and_trailing_slash(self) -> None:
        self.assertEqual(normalize_remote_url("github.com/a/b/"), "github.com/a/b")

    def test_repo_name_from_remote_takes_last_segment(self) -> None:
        self.assertEqual(repo_name_from_remote("https://github.com/mschwar/project-ledger"), "project-ledger")

    def test_coerce_bool_and_classify_project_type(self) -> None:
        self.assertIs(coerce_bool("yes"), True)
        self.assertIs(coerce_bool(0), False)
        self.assertIsNone(coerce_bool("maybe"))
        self.assertEqual(classify_project_type(True, True, 1), "git+obsidian")
        self.assertEqual(classify_project_type(True, False, 1), "git")
        self.assertEqual(classify_project_type(False, True, 1), "obsidian")
        self.assertEqual(classify_project_type(False, False, 12), "notes")
        self.assertEqual(classify_project_type(False, False, 3), "directory")


class AdapterRegistryTests(unittest.TestCase):
    def test_registry_covers_all_supported_discovery_modes(self) -> None:
        self.assertEqual(
            set(sources_base.ADAPTERS.keys()),
            {"children", "git_repos", "self", "inventory_policy"},
        )
        for mode in ("children", "git_repos", "self"):
            self.assertIsInstance(sources_base.get_adapter(mode), FilesystemAdapter)
        self.assertIsInstance(
            sources_base.get_adapter("inventory_policy"), InventoryPolicyAdapter
        )

    def test_filesystem_adapter_self_dispatches_to_build_entry(self) -> None:
        with ScratchDir() as root:
            project = root / "sample"
            project.mkdir()
            (project / "README.md").write_text("# Sample\nDesc\n", encoding="utf-8")
            root_cfg = {
                "label": "test-root",
                "discovery": "self",
                "_resolved_path": root,
            }
            defaults = {"tree_scan_max_entries": 5000, "tree_scan_max_depth": 8}
            output_dir = root / "output"
            output_dir.mkdir()

            direct = build_entry(project, root_cfg, defaults, output_dir)
            via_adapter = sources_base.get_adapter("self").build(project, root_cfg, defaults, output_dir)

            self.assertIsNotNone(direct)
            self.assertIsNotNone(via_adapter)
            assert via_adapter is not None
            self.assertEqual(direct, via_adapter)
            self.assertEqual(via_adapter["name"], "Sample")

    def test_shadowed_intra_root_and_cross_root_symlinks_are_suppressed_while_external_symlinks_remain(self) -> None:
        with ScratchDir() as root:
            projects_root = root / "projects"
            umbrella = projects_root / "relational-physics"
            sub_proj = umbrella / "01-sprint" / "coherence"
            sub_proj.mkdir(parents=True)
            (umbrella / "README.md").write_text("# Relational Physics\nUmbrella.\n", encoding="utf-8")
            (sub_proj / "README.md").write_text("# Coherence\nMoved subproject.\n", encoding="utf-8")
            # Compatibility redirect symlink inside same children root -> must be suppressed.
            (projects_root / "coherence").symlink_to(sub_proj)

            # External directory outside configured real candidates -> unshadowed symlink must remain.
            external_dir = root / "external-mount" / "ext-proj"
            external_dir.mkdir(parents=True)
            (external_dir / "README.md").write_text("# External Project\nMounted.\n", encoding="utf-8")
            (projects_root / "ext-proj").symlink_to(external_dir)

            discovered = [p.name for p in discover_children(projects_root, set())]
            self.assertEqual(discovered, ["ext-proj", "relational-physics"])

            # Cross-root compatibility symlink listed BEFORE the real root must yield to the real root.
            tooling_root = root / "agent-tooling"
            tooling_root.mkdir()
            research_root = root / "research-ml"
            real_nanochat = research_root / "nanochat-master"
            real_nanochat.mkdir(parents=True)
            (real_nanochat / "README.md").write_text("# Nanochat\nReal checkout.\n", encoding="utf-8")
            (tooling_root / "nanochat-master").symlink_to(real_nanochat)

            cfg = {
                "roots": [
                    {"source_id": "src-tooling", "label": "src-tooling", "path": str(tooling_root), "discovery": "children"},
                    {"source_id": "src-research", "label": "src-research", "path": str(research_root), "discovery": "children"},
                    {"source_id": "src-projects", "label": "src-projects", "path": str(projects_root), "discovery": "children"},
                ]
            }
            out_dir = root / "output"
            out_dir.mkdir()
            full_entries = collect_entries(cfg, root, out_dir)
            inc_entries, _ = collect_entries_incremental(
                cfg, root, out_dir, checkpoint_path=root / "state" / "source-checkpoints.json"
            )
            self.assertEqual(full_entries, inc_entries)
            by_name = {e["name"]: e for e in full_entries}
            self.assertEqual(set(by_name), {"Nanochat", "Relational Physics", "External Project"})
            self.assertEqual(by_name["Nanochat"]["source_label"], "src-research")


class InventoryAdapterTests(unittest.TestCase):
    def test_inventory_adapter_produces_project_and_omits_non_candidate_root(self) -> None:
        with ScratchDir() as root:
            mount = root / "GoogleDrive"
            mount.mkdir()
            (mount / "Projects").mkdir()
            (mount / "Projects" / "LMNTL").mkdir()
            (mount / "Writing").mkdir()

            inventory_path = root / "inventory.jsonl"
            inventory_path.write_text(
                "\n".join(
                    json.dumps(
                        {
                            "crawl_timestamp": "2026-05-19T01:54:47.846166+00:00",
                            "id": f"id-{idx}",
                            "is_dir": is_dir,
                            "mime_type": "inode/directory" if is_dir else "text/markdown",
                            "mod_time": "2026-05-19T01:54:47.846166+00:00",
                            "name": Path(p).name,
                            "original_id": "",
                            "path": p,
                            "remote": "googledrive:",
                            "root_label": rl,
                            "size": -1 if is_dir else 128,
                        }
                    )
                    for idx, (p, is_dir, rl) in enumerate(
                        [
                            ("Projects", True, "Projects"),
                            ("Projects/LMNTL", True, "Projects"),
                            ("Projects/LMNTL/README.md", False, "Projects"),
                            ("Writing", True, "Writing"),
                        ]
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            policy_path = root / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "roots": {
                            "Projects": {
                                "crawl_treatment": "project_discovery",
                                "project_ledger_candidate": True,
                                "root_class": "project_roots",
                            },
                            "Writing": {
                                "crawl_treatment": "content_extract",
                                "project_ledger_candidate": False,
                                "root_class": "knowledge_docs",
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )

            root_cfg = {
                "path": str(mount),
                "label": "google-drive-policy",
                "discovery": "inventory_policy",
                "inventory_jsonl": str(inventory_path),
                "policy_path": str(policy_path),
                "policy_crawl_treatments": ["project_discovery"],
                "require_project_ledger_candidate": True,
                "remote_name": "googledrive",
                "storage_scope": "shared",
            }
            root_cfg["_resolved_path"] = mount
            root_cfg["_inventory_jsonl_path"] = inventory_path
            root_cfg["_policy_path"] = policy_path

            adapter = sources_base.get_adapter("inventory_policy")
            candidates = adapter.discover_candidates(root_cfg, {}, set())
            keys = {c["inventory_path"] for c in candidates if isinstance(c, dict)}  # type: ignore[union-attr]
            self.assertIn("Projects", keys)
            self.assertIn("Projects/LMNTL", keys)
            self.assertNotIn("Writing", keys)

            entries = [
                adapter.build(c, root_cfg, {}, mount / "output")
                for c in candidates
            ]
            entries = [e for e in entries if e]
            keyed = {e["project_key"]: e for e in entries}
            self.assertIn("google-drive:projects/lmntl", keyed)
            self.assertEqual(
                keyed["google-drive:projects/lmntl"]["path_from_root"],
                "Projects/LMNTL",
            )
            self.assertEqual(
                keyed["google-drive:projects/lmntl"]["canonical_url"],
                "gdrive://googledrive/Projects/LMNTL",
            )


class GitAdapterTests(unittest.TestCase):
    def test_gather_git_metadata_on_real_repo(self) -> None:
        with ScratchDir() as root:
            repo = root / "repo"
            repo.mkdir()
            (repo / "README.md").write_text("# Repo\n", encoding="utf-8")
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "remote", "add", "origin", "https://github.com/mschwar/sample-repo.git"],
                cwd=repo, check=True, capture_output=True,
            )
            subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

            meta = gather_git_metadata(repo)

            self.assertTrue(meta["git"])
            self.assertEqual(meta["normalized_remote_url"], "github.com/mschwar/sample-repo")
            self.assertEqual(meta["repo_name"], "sample-repo")
            self.assertTrue(meta["head_branch"])
            self.assertTrue(meta["head_commit"])
            self.assertTrue(meta["head_commit_at"])

    def test_gather_git_metadata_non_git_dir(self) -> None:
        with ScratchDir() as root:
            plain = root / "plain"
            plain.mkdir()
            meta = gather_git_metadata(plain)
            self.assertFalse(meta["git"])
            self.assertEqual(meta["remote_url"], "")
            self.assertEqual(meta["normalized_remote_url"], "")

    def test_git_output_returns_empty_on_error(self) -> None:
            # A non-existent directory makes `git -C` fail, exercising the error path.
            # (A plain dir inside a git repo would resolve to the enclosing repo, so it
            # is not a valid "not a repo" probe.)
            self.assertEqual(git_output(Path("/nonexistent/pl-git-probe"), "rev-parse", "HEAD"), "")

    def test_cross_root_shadowed_symlink_suppressed_independent_of_root_ordering(self) -> None:
        with ScratchDir() as root:
            tooling = root / "agent-tooling"
            research = root / "research-ml"
            tooling.mkdir()
            research.mkdir()

            real_proj = research / "nanochat-master"
            real_proj.mkdir()
            (real_proj / "README.md").write_text("# Nanochat\n", encoding="utf-8")
            (real_proj / "train.py").write_text("print('ok')\n", encoding="utf-8")

            # Symlink placed in the FIRST configured root pointing into the SECOND root.
            (tooling / "nanochat-master").symlink_to(real_proj)

            cfg_symlink_first = {
                "defaults": {"min_score": 2},
                "roots": [
                    {"source_id": "tooling", "label": "tooling", "path": str(tooling), "discovery": "children"},
                    {"source_id": "research", "label": "research", "path": str(research), "discovery": "children"},
                ],
            }
            entries = sources_base.collect_entries(cfg_symlink_first, root, root / "output")
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["source_label"], "research")
            self.assertEqual(Path(entries[0]["path"]).resolve(), real_proj.resolve())


if __name__ == "__main__":
    unittest.main()