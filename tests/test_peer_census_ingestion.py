"""Tests for passive fleet-peer unpushed-repo census ingestion (`WQ-5`)."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from ledger.checkpoints import collect_entries_incremental
from ledger.compat import scan_without_markdown_mirror
from ledger.compiler import compile_state
from ledger.config import LedgerConfigError, describe_sources, validate_config
from ledger.github_sync import sync_github_estate
from ledger.sources.base import collect_entries


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


class PeerCensusIngestionTests(unittest.TestCase):
    def test_optional_inventory_policy_validates_and_contains_missing_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live_dir = root / "active"
            live_dir.mkdir()
            repo = live_dir / "hub-app"
            repo.mkdir()
            _git(repo, "init", "-b", "main")
            _git(repo, "config", "user.name", "Test")
            _git(repo, "config", "user.email", "test@example.com")
            _git(repo, "remote", "add", "origin", "git@github.com:mschwar/hub-app.git")
            (repo / "README.md").write_text("# Hub App\n\nCore hub service.\n", encoding="utf-8")
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "init")

            cfg = {
                "defaults": {"require_git": True, "exclude_autosnapshot_repos": True},
                "roots": [
                    {
                        "source_id": "central-active",
                        "label": "central-active",
                        "path": str(live_dir),
                        "discovery": "children",
                    },
                    {
                        "source_id": "peer-census-iz",
                        "label": "peer-census-iz",
                        "path": str(root / "census"),
                        "discovery": "inventory_policy",
                        "inventory_kind": "peer_census",
                        "optional": True,
                        "inventory_jsonl": str(root / "census" / "iz.jsonl"),
                        "policy_path": str(root / "census" / "iz.policy.json"),
                        "remote_name": "iz",
                        "machine_name": "iz",
                        "storage_scope": "local",
                    },
                ],
            }
            validate_config(cfg, root, check_artifacts=True)

            # Invalid inventory_kind must still fail closed.
            bad_cfg = json.loads(json.dumps(cfg))
            bad_cfg["roots"][1]["inventory_kind"] = "unknown_kind"
            with self.assertRaises(LedgerConfigError) as ctx:
                validate_config(bad_cfg, root, check_artifacts=True)
            self.assertEqual(ctx.exception.code, "INVENTORY_KIND_INVALID")

            # Missing optional artifacts do not crash collect_entries or collect_entries_incremental.
            out_dir = root / "out"
            out_dir.mkdir()
            direct = collect_entries(cfg, root, out_dir)
            self.assertEqual([e["name"] for e in direct], ["Hub App"])

            inc, cp = collect_entries_incremental(cfg, root, out_dir)
            self.assertEqual(direct, inc)
            self.assertEqual(cp["sources"]["peer-census-iz"]["status"], "unavailable")

            sources = describe_sources(cfg, root)
            by_id = {s["source_id"]: s for s in sources}
            self.assertEqual(by_id["peer-census-iz"]["status"], "unavailable")
            self.assertEqual(
                by_id["peer-census-iz"]["status_reason"], "optional_inventory_artifact_missing"
            )

    def test_peer_census_surfaces_unpushed_repo_in_capsules_and_local_without_remote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live_dir = root / "active"
            live_dir.mkdir()
            hub_repo = live_dir / "hub-app"
            hub_repo.mkdir()
            _git(hub_repo, "init", "-b", "main")
            _git(hub_repo, "config", "user.name", "Test")
            _git(hub_repo, "config", "user.email", "test@example.com")
            _git(hub_repo, "remote", "add", "origin", "git@github.com:mschwar/hub-app.git")
            (hub_repo / "README.md").write_text("# Hub App\n\nCore hub service.\n", encoding="utf-8")
            (hub_repo / "app.py").write_text("print('hub')\n", encoding="utf-8")
            _git(hub_repo, "add", ".")
            _git(hub_repo, "commit", "-m", "init hub")

            census_dir = root / "census"
            census_dir.mkdir()
            (census_dir / "thing1.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "root_label": "win-unpushed-lab",
                                "path": "win-unpushed-lab",
                                "name": "win-unpushed-lab",
                                "is_dir": True,
                                "mod_time": "2026-11-19T05:00:00Z",
                            }
                        ),
                        json.dumps(
                            {
                                "root_label": "win-unpushed-lab",
                                "path": "win-unpushed-lab/.git",
                                "name": ".git",
                                "is_dir": True,
                                "mod_time": "2026-11-19T05:00:00Z",
                            }
                        ),
                        json.dumps(
                            {
                                "root_label": "win-unpushed-lab",
                                "path": "win-unpushed-lab/README.md",
                                "name": "README.md",
                                "is_dir": False,
                                "mod_time": "2026-11-19T05:00:00Z",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (census_dir / "thing1.policy.json").write_text(
                json.dumps(
                    {
                        "generated_at": "2026-11-19T05:00:00Z",
                        "host": "thing1",
                        "status": "observed",
                        "unpushed_repo_count": 1,
                        "roots": {
                            "win-unpushed-lab": {
                                "crawl_treatment": "project_discovery",
                                "project_ledger_candidate": True,
                                "root_class": "unpushed_peer_git_repo",
                                "peer_host": "thing1",
                                "peer_path": "C:/Users/thing1/Developer/win-unpushed-lab",
                                "repo_name": "win-unpushed-lab",
                                "display_name": "Win Unpushed Lab",
                                "description": "Unpushed Windows experimental git init repository.",
                                "primary_language": "Rust",
                                "head_branch": "main",
                                "head_commit": "b" * 40,
                                "head_commit_at": "2026-11-19T05:00:00Z",
                                "head_commit_subject": "initial prototype",
                                "remote_url": "",
                                "rationale": "Local git init repo on thing1.",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            cfg = {
                "defaults": {"require_git": True, "exclude_autosnapshot_repos": True},
                "roots": [
                    {
                        "source_id": "central-active",
                        "label": "central-active",
                        "path": str(live_dir),
                        "discovery": "children",
                    },
                    {
                        "source_id": "peer-census-thing1",
                        "label": "peer-census-thing1",
                        "path": str(census_dir),
                        "discovery": "inventory_policy",
                        "inventory_kind": "peer_census",
                        "optional": True,
                        "inventory_jsonl": str(census_dir / "thing1.jsonl"),
                        "policy_path": str(census_dir / "thing1.policy.json"),
                        "remote_name": "thing1",
                        "machine_name": "thing1",
                        "storage_scope": "local",
                    },
                ],
            }
            config_path = root / "ledger_config.json"
            config_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            out_dir = root / "out"
            state_dir = root / "state"

            outputs = scan_without_markdown_mirror(
                config_path=config_path,
                output_dir=out_dir,
                checkpoint_path=state_dir / "source-checkpoints.json",
            )
            manifest = compile_state(
                config_path=config_path,
                compat_output_path=outputs["json"],
                state_dir=state_dir,
                use_checkpoint_compile_cache=True,
            )
            self.assertEqual(manifest["health"]["state"], "ok")
            self.assertEqual(manifest["counts"]["canonical_projects"], 2)

            cp_doc = json.loads((state_dir / "canonical-projects.json").read_text(encoding="utf-8"))
            by_key = {p["project_key"]: p for p in cp_doc["projects"]}
            self.assertIn("peer:thing1:win-unpushed-lab", by_key)
            peer_proj = by_key["peer:thing1:win-unpushed-lab"]
            peer_cid = peer_proj["canonical_project_id"]

            cap = json.loads((state_dir / "projects" / f"{peer_cid}.json").read_text(encoding="utf-8"))
            self.assertEqual(cap["display_name"], "Win Unpushed Lab")
            self.assertEqual(cap["primary_language"], "Rust")
            self.assertEqual(
                cap["preferred_location"]["location"],
                "thing1:C:/Users/thing1/Developer/win-unpushed-lab",
            )
            self.assertEqual(cap["preferred_location"]["machine_name"], "thing1")
            self.assertEqual(cap["last_commit"]["sha"], "b" * 40)
            self.assertNotIn("google-drive", cap["current_state"]["tags"])
            self.assertIn("peer-census", cap["current_state"]["tags"])

            # Verify sync_github_estate surfaces the unpushed peer repo in local_without_remote.
            gh_inv = root / "gh-repos.json"
            gh_inv.write_text(
                json.dumps(
                    [
                        {
                            "full_name": "mschwar/hub-app",
                            "name": "hub-app",
                            "ssh_url": "git@github.com:mschwar/hub-app.git",
                            "default_branch": "main",
                            "archived": False,
                            "pushed_at": "2026-11-19T05:00:00Z",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            sync_doc = sync_github_estate(
                config_path=config_path,
                state_dir=state_dir,
                clone_root=live_dir,
                mode="check",
                fetch_existing=False,
                inventory_file=gh_inv,
                persist_state=False,
            )
            self.assertEqual(sync_doc["counts"]["local_without_remote"], 1)
            self.assertEqual(
                sync_doc["local_without_remote"],
                [
                    {
                        "canonical_project_id": peer_cid,
                        "project_key": "peer:thing1:win-unpushed-lab",
                        "preferred_path": "thing1:C:/Users/thing1/Developer/win-unpushed-lab",
                    }
                ],
            )


if __name__ == "__main__":
    unittest.main()
