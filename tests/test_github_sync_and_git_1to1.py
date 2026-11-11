"""Automated tests for 1:1 git-remote discovery, checkout sync, and git-only ledger parity."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ledger.checkpoints import collect_entries_incremental
from ledger.cli import main as ledger_main
from ledger.config import LedgerConfigError, validate_config
from ledger.github_sync import (
    _check_fleet_pulse_allows_ssh,
    _read_cached_token,
    _write_cached_token,
    sync_github_estate,
)
from ledger.lineage import lineage_status_payload, merge_project_lineage
from ledger.sources.base import collect_entries
from ledger.sources.git import is_autosnapshot_git_repo, list_git_remotes


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _init_bare_with_commit(bare_path: Path, tmp_work: Path, filename: str = "README.md", text: str = "# Repo\n") -> str:
    bare_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare_path)], check=True, capture_output=True)
    tmp_work.mkdir(parents=True, exist_ok=True)
    _git(tmp_work, "init", "-b", "main")
    _git(tmp_work, "config", "user.name", "Test User")
    _git(tmp_work, "config", "user.email", "test@example.com")
    (tmp_work / filename).write_text(text, encoding="utf-8")
    _git(tmp_work, "add", ".")
    _git(tmp_work, "commit", "-m", "initial commit")
    _git(tmp_work, "remote", "add", "origin", f"file://{bare_path}")
    _git(tmp_work, "push", "-u", "origin", "main")
    return _git(tmp_work, "rev-parse", "HEAD")


class Git1To1AndGitHubSyncTests(unittest.TestCase):
    def test_config_validates_require_git_and_exclude_autosnapshot_booleans(self) -> None:
        with self.assertRaises(LedgerConfigError) as ctx1:
            validate_config(
                {
                    "defaults": {"require_git": "yes"},
                    "roots": [{"label": "r", "path": "/tmp", "discovery": "children"}],
                },
                Path("/tmp"),
            )
        self.assertEqual(ctx1.exception.code, "DEFAULT_INVALID_BOOLEAN")

        with self.assertRaises(LedgerConfigError) as ctx2:
            validate_config(
                {
                    "defaults": {"exclude_autosnapshot_repos": 1},
                    "roots": [{"label": "r", "path": "/tmp", "discovery": "children"}],
                },
                Path("/tmp"),
            )
        self.assertEqual(ctx2.exception.code, "DEFAULT_INVALID_BOOLEAN")

        with self.assertRaises(LedgerConfigError) as ctx3:
            validate_config(
                {
                    "roots": [
                        {
                            "label": "r",
                            "path": "/tmp",
                            "discovery": "children",
                            "require_git": "true",
                        }
                    ],
                },
                Path("/tmp"),
            )
        self.assertEqual(ctx3.exception.code, "ROOT_INVALID_BOOLEAN")

        with self.assertRaises(LedgerConfigError) as ctx4:
            validate_config(
                {
                    "roots": [
                        {
                            "label": "r",
                            "path": "/tmp",
                            "discovery": "children",
                            "exclude_autosnapshot_repos": [],
                        }
                    ],
                },
                Path("/tmp"),
            )
        self.assertEqual(ctx4.exception.code, "ROOT_INVALID_BOOLEAN")

        validate_config(
            {
                "defaults": {"require_git": True, "exclude_autosnapshot_repos": True},
                "roots": [{"label": "r", "path": "/tmp", "discovery": "children", "require_git": False}],
            },
            Path("/tmp"),
        )

    def test_require_git_and_exclude_autosnapshot_across_full_and_incremental_scans(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            projects_dir = root / "projects"
            projects_dir.mkdir()

            # 1. Real git repo -> included.
            real_repo = projects_dir / "real-git-app"
            real_repo.mkdir()
            _git(real_repo, "init", "-b", "main")
            _git(real_repo, "config", "user.name", "Test")
            _git(real_repo, "config", "user.email", "test@example.com")
            _git(real_repo, "remote", "add", "origin", "git@github.com:mschwar/real-git-app.git")
            (real_repo / "README.md").write_text("# Real Git App\n", encoding="utf-8")
            _git(real_repo, "add", ".")
            _git(real_repo, "commit", "-m", "init")

            # 2. Plain non-git directory with README + sidecar -> excluded when require_git=True.
            plain_dir = projects_dir / "plain-folder"
            plain_dir.mkdir()
            (plain_dir / "README.md").write_text("# Plain Folder\n", encoding="utf-8")
            (plain_dir / ".project-ledger.json").write_text(
                json.dumps({"schema_version": "1.0.0", "project_key": "plain-folder", "status": "active"}),
                encoding="utf-8",
            )

            # 3. GBrain local auto-snapshot pseudo-repo -> excluded when exclude_autosnapshot_repos=True.
            notes_repo = projects_dir / "notes"
            notes_repo.mkdir()
            _git(notes_repo, "init", "-b", "main")
            _git(notes_repo, "config", "user.name", "GBrain")
            _git(notes_repo, "config", "user.email", "gbrain@local")
            _git(notes_repo, "config", "gbrain.autosnapshot", "true")
            (notes_repo / "note.md").write_text("# Daily Note\n", encoding="utf-8")
            _git(notes_repo, "add", ".")
            _git(notes_repo, "commit", "-m", "autosnapshot")
            self.assertTrue(is_autosnapshot_git_repo(notes_repo))
            self.assertFalse(is_autosnapshot_git_repo(real_repo))

            # 4. Inventory policy root with 1 git entry and 1 non-git entry.
            inv_root = root / "inv"
            inv_root.mkdir()
            (inv_root / "projects-policy.json").write_text(
                json.dumps(
                    {
                        "roots": {
                            "Documents": {
                                "crawl_treatment": "project_discovery",
                                "project_ledger_candidate": True,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            (inv_root / "inventory.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"root_label": "Documents", "path": "Documents/git-tool", "name": "git-tool", "is_dir": True}),
                        json.dumps({"root_label": "Documents", "path": "Documents/git-tool/.git", "name": ".git", "is_dir": True}),
                        json.dumps({"root_label": "Documents", "path": "Documents/git-tool/README.md", "name": "README.md", "is_dir": False}),
                        json.dumps({"root_label": "Documents", "path": "Documents/non-git-doc", "name": "non-git-doc", "is_dir": True}),
                        json.dumps({"root_label": "Documents", "path": "Documents/non-git-doc/README.md", "name": "README.md", "is_dir": False}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            cfg = {
                "defaults": {
                    "require_git": True,
                    "exclude_autosnapshot_repos": True,
                },
                "roots": [
                    {
                        "source_id": "projects",
                        "label": "projects",
                        "path": str(projects_dir),
                        "discovery": "children",
                    },
                    {
                        "source_id": "inv",
                        "label": "inv",
                        "path": str(inv_root),
                        "discovery": "inventory_policy",
                        "inventory_jsonl": str(inv_root / "inventory.jsonl"),
                        "policy_path": str(inv_root / "projects-policy.json"),
                    },
                ],
            }
            config_path = root / "ledger_config.json"
            config_path.write_text(json.dumps(cfg), encoding="utf-8")
            state_dir = root / "state"

            out_dir = root / "out"
            direct_entries = collect_entries(cfg, config_path.parent, out_dir)
            self.assertEqual([e["name"] for e in direct_entries], ["Real Git App", "git-tool"])

            cp_path = state_dir / "source-checkpoints.json"
            inc_entries_1, cp_1 = collect_entries_incremental(
                cfg,
                config_path.parent,
                out_dir,
                checkpoint_path=cp_path,
                bypass_cache=False,
            )
            self.assertEqual(direct_entries, inc_entries_1)
            self.assertEqual(cp_1["summary"]["reused_candidate_count"], 0)
            state_dir.mkdir(parents=True, exist_ok=True)
            cp_path.write_text(json.dumps(cp_1), encoding="utf-8")

            inc_entries_2, cp_2 = collect_entries_incremental(
                cfg,
                config_path.parent,
                out_dir,
                checkpoint_path=cp_path,
                bypass_cache=False,
            )
            self.assertEqual(direct_entries, inc_entries_2)
            self.assertGreaterEqual(cp_2["summary"]["reused_candidate_count"], 3)

    def test_secondary_fork_remote_surfaces_without_upstream_false_merge_and_resolves_owner_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repos_dir = root / "repos"
            repos_dir.mkdir()

            # Repo A: origin=upstream org, fork=mschwar/paperclip, upstream=paperclipai/paperclip
            repo_a = repos_dir / "paperclip"
            repo_a.mkdir()
            _git(repo_a, "init", "-b", "main")
            _git(repo_a, "config", "user.name", "Test")
            _git(repo_a, "config", "user.email", "test@example.com")
            _git(repo_a, "remote", "add", "origin", "https://github.com/paperclipai/paperclip.git")
            _git(repo_a, "remote", "add", "fork", "git@github.com:mschwar/paperclip.git")
            _git(repo_a, "remote", "add", "upstream", "https://github.com/shared-template/starter.git")
            (repo_a / "README.md").write_text("# Paperclip\n", encoding="utf-8")
            _git(repo_a, "add", ".")
            _git(repo_a, "commit", "-m", "init a")

            # Linked worktree of Repo A -> must resolve common .git/config remotes too.
            wt_dir = root / "wt-paperclip"
            _git(repo_a, "worktree", "add", "-b", "wt-test", str(wt_dir))
            wt_remotes = list_git_remotes(wt_dir)
            self.assertEqual(wt_remotes.get("fork"), "git@github.com:mschwar/paperclip.git")

            # Repo B: distinct repo that also has `upstream=shared-template/starter.git` -> MUST NOT merge with Repo A!
            repo_b = repos_dir / "other-fork"
            repo_b.mkdir()
            _git(repo_b, "init", "-b", "main")
            _git(repo_b, "config", "user.name", "Test")
            _git(repo_b, "config", "user.email", "test@example.com")
            _git(repo_b, "remote", "add", "origin", "git@github.com:mschwar/other-fork.git")
            _git(repo_b, "remote", "add", "upstream", "https://github.com/shared-template/starter.git")
            (repo_b / "README.md").write_text("# Other Fork\n", encoding="utf-8")
            _git(repo_b, "add", ".")
            _git(repo_b, "commit", "-m", "init b")

            config_path = root / "ledger_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {"require_git": True},
                        "roots": [{"source_id": "repos", "label": "repos", "path": str(repos_dir), "discovery": "children"}],
                    }
                ),
                encoding="utf-8",
            )
            out_dir = root / "out"
            state_dir = root / "state"
            decisions_path = root / "decisions.json"
            decisions_path.write_text(json.dumps({"schema_version": "1.0.0", "decisions": []}), encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()):
                rc = ledger_main(
                    [
                        "refresh",
                        "--no-markdown-mirror",
                        "--config",
                        str(config_path),
                        "--output-dir",
                        str(out_dir),
                        "--state-dir",
                        str(state_dir),
                        "--identity-decisions",
                        str(decisions_path),
                    ]
                )
            self.assertEqual(rc, 0)

            cp = json.loads((state_dir / "canonical-projects.json").read_text(encoding="utf-8"))
            self.assertEqual(len(cp["projects"]), 2)
            paperclip_proj = next(p for p in cp["projects"] if "paperclip" in p["project_key"])
            self.assertIn("github.com/paperclipai/paperclip", paperclip_proj["normalized_remotes"])
            self.assertIn("github.com/mschwar/paperclip", paperclip_proj["normalized_remotes"])
            self.assertNotIn("github.com/shared-template/starter", paperclip_proj["normalized_remotes"])

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc_res = ledger_main(["resolve", "MSCHWAR/PaperClip", "--state-dir", str(state_dir), "--json"])
            self.assertEqual(rc_res, 0)
            resolved = json.loads(buf.getvalue())
            self.assertEqual(resolved["status"], "resolved")
            self.assertEqual(resolved["canonical_project_id"], paperclip_proj["canonical_project_id"])

    def test_sync_github_estate_clones_ff_skips_dirty_wip_archived_and_caches_pushed_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remotes_dir = root / "remotes"
            staging_dir = root / "staging"
            active_dir = root / "active"
            active_dir.mkdir(parents=True)

            # Create 5 bare remotes:
            # 1. missing-repo -> should be auto-cloned into active_dir
            # 2. clean-behind -> existing clean checkout on main, remote advances -> fast_forwarded
            # 3. dirty-behind -> existing dirty checkout on main, remote advances -> fetched_only (dirty_worktree)
            # 4. wip-branch -> existing clean checkout on wip/feat, remote advances -> fetched_only (non_default_branch)
            # 5. archived-repo -> archived: True on remote, absent locally -> skipped_archived (NOT cloned)
            sha_missing = _init_bare_with_commit(remotes_dir / "mschwar" / "missing-repo.git", staging_dir / "missing")
            _init_bare_with_commit(remotes_dir / "mschwar" / "clean-behind.git", staging_dir / "clean")
            _init_bare_with_commit(remotes_dir / "mschwar" / "dirty-behind.git", staging_dir / "dirty")
            _init_bare_with_commit(remotes_dir / "mschwar" / "wip-branch.git", staging_dir / "wip")
            _init_bare_with_commit(remotes_dir / "mschwar" / "archived-repo.git", staging_dir / "archived")

            # Clone 2, 3, 4 into active_dir BEFORE advancing remotes:
            subprocess.run(
                ["git", "clone", "--quiet", f"file://{remotes_dir / 'mschwar' / 'clean-behind.git'}", str(active_dir / "clean-behind")],
                check=True,
            )
            old_clean_sha = _git(active_dir / "clean-behind", "rev-parse", "HEAD")

            subprocess.run(
                ["git", "clone", "--quiet", f"file://{remotes_dir / 'mschwar' / 'dirty-behind.git'}", str(active_dir / "dirty-behind")],
                check=True,
            )
            old_dirty_sha = _git(active_dir / "dirty-behind", "rev-parse", "HEAD")
            (active_dir / "dirty-behind" / "UNCOMMITTED.txt").write_text("local wip\n", encoding="utf-8")

            subprocess.run(
                ["git", "clone", "--quiet", f"file://{remotes_dir / 'mschwar' / 'wip-branch.git'}", str(active_dir / "wip-branch")],
                check=True,
            )
            _git(active_dir / "wip-branch", "checkout", "-b", "wip/feature")
            old_wip_sha = _git(active_dir / "wip-branch", "rev-parse", "HEAD")

            # Advance remotes 2, 3, 4 on `main`:
            for slug, bare_name in [
                ("clean", "clean-behind.git"),
                ("dirty", "dirty-behind.git"),
                ("wip", "wip-branch.git"),
            ]:
                work = staging_dir / slug
                (work / "NEW.md").write_text("remote advance\n", encoding="utf-8")
                _git(work, "add", ".")
                _git(work, "commit", "-m", "advance remote")
                _git(work, "push", "origin", "main")
            new_clean_sha = _git(staging_dir / "clean", "rev-parse", "HEAD")

            config_path = root / "ledger_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {"require_git": True},
                        "roots": [{"source_id": "active", "label": "active", "path": str(active_dir), "discovery": "children"}],
                    }
                ),
                encoding="utf-8",
            )
            inv_path = root / "inventory.json"
            inv_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "missing-repo",
                            "full_name": "mschwar/missing-repo",
                            "private": True,
                            "fork": False,
                            "archived": False,
                            "default_branch": "main",
                            "pushed_at": "2026-11-11T20:00:00Z",
                            "ssh_url": f"file://{remotes_dir / 'mschwar' / 'missing-repo.git'}",
                        },
                        {
                            "name": "clean-behind",
                            "full_name": "mschwar/clean-behind",
                            "private": True,
                            "fork": False,
                            "archived": False,
                            "default_branch": "main",
                            "pushed_at": "2026-11-11T20:05:00Z",
                            "ssh_url": f"file://{remotes_dir / 'mschwar' / 'clean-behind.git'}",
                        },
                        {
                            "name": "dirty-behind",
                            "full_name": "mschwar/dirty-behind",
                            "private": True,
                            "fork": False,
                            "archived": False,
                            "default_branch": "main",
                            "pushed_at": "2026-11-11T20:05:00Z",
                            "ssh_url": f"file://{remotes_dir / 'mschwar' / 'dirty-behind.git'}",
                        },
                        {
                            "name": "wip-branch",
                            "full_name": "mschwar/wip-branch",
                            "private": True,
                            "fork": False,
                            "archived": False,
                            "default_branch": "main",
                            "pushed_at": "2026-11-11T20:05:00Z",
                            "ssh_url": f"file://{remotes_dir / 'mschwar' / 'wip-branch.git'}",
                        },
                        {
                            "name": "archived-repo",
                            "full_name": "mschwar/archived-repo",
                            "private": True,
                            "fork": False,
                            "archived": True,
                            "default_branch": "main",
                            "pushed_at": "2025-05-01T00:00:00Z",
                            "ssh_url": f"file://{remotes_dir / 'mschwar' / 'archived-repo.git'}",
                        },
                    ]
                ),
                encoding="utf-8",
            )
            state_dir = root / "state"

            # First: `--check` BEFORE syncing must return exit code 6 (`missing_local` = 1) without mutating disk!
            buf_chk = io.StringIO()
            with contextlib.redirect_stdout(buf_chk):
                rc_chk = ledger_main(
                    [
                        "sync-github",
                        "--check",
                        "--config",
                        str(config_path),
                        "--state-dir",
                        str(state_dir),
                        "--clone-root",
                        str(active_dir),
                        "--inventory-file",
                        str(inv_path),
                        "--json",
                    ]
                )
            self.assertEqual(rc_chk, 6)
            chk_payload = json.loads(buf_chk.getvalue())
            self.assertFalse(chk_payload["in_1to1_parity"])
            self.assertEqual(chk_payload["counts"]["missing_local"], 1)
            self.assertFalse((active_dir / "missing-repo").exists())

            # Second: full mutating `sync-github` reconciles missing clone, fast-forwards clean-behind, preserves dirty & wip!
            res1 = sync_github_estate(
                config_path=config_path,
                state_dir=state_dir,
                clone_root=active_dir,
                inventory_file=inv_path,
            )
            self.assertTrue(res1["in_1to1_parity"])
            self.assertEqual(res1["status"], "ok")
            self.assertEqual(res1["status_reason"], "reconciled")
            self.assertEqual(res1["counts"]["cloned"], 1)
            self.assertEqual(res1["cloned"][0]["head_commit"], sha_missing)
            self.assertTrue((active_dir / "missing-repo" / "README.md").is_file())
            self.assertFalse((active_dir / "archived-repo").exists())
            self.assertEqual(res1["counts"]["skipped_archived"], 1)

            self.assertEqual(res1["counts"]["fast_forwarded"], 1)
            self.assertEqual(res1["fast_forwarded"][0]["old_commit"], old_clean_sha)
            self.assertEqual(res1["fast_forwarded"][0]["new_commit"], new_clean_sha)
            self.assertEqual(_git(active_dir / "clean-behind", "rev-parse", "HEAD"), new_clean_sha)

            # Dirty and WIP checkouts were NOT clobbered, though origin/main was fetched:
            self.assertEqual(_git(active_dir / "dirty-behind", "rev-parse", "HEAD"), old_dirty_sha)
            self.assertTrue((active_dir / "dirty-behind" / "UNCOMMITTED.txt").is_file())
            self.assertEqual(_git(active_dir / "wip-branch", "rev-parse", "HEAD"), old_wip_sha)
            reasons = {item["full_name"]: item["reason"] for item in res1["fetched_only"]}
            self.assertEqual(reasons["mschwar/dirty-behind"], "dirty_worktree")
            self.assertEqual(reasons["mschwar/wip-branch"], "non_default_branch")

            # Third: warm incremental run with unchanged `pushed_at` skips redundant fetches across all 4!
            res2 = sync_github_estate(
                config_path=config_path,
                state_dir=state_dir,
                clone_root=active_dir,
                inventory_file=inv_path,
            )
            self.assertTrue(res2["in_1to1_parity"])
            self.assertEqual(res2["counts"]["cloned"], 0)
            self.assertEqual(res2["counts"]["fetched"], 0)
            self.assertEqual(res2["counts"]["skipped_unchanged"], 4)

            # Once dirty-behind becomes clean, even with unchanged `pushed_at`, it fast-forwards automatically!
            (active_dir / "dirty-behind" / "UNCOMMITTED.txt").unlink()
            res2b = sync_github_estate(
                config_path=config_path,
                state_dir=state_dir,
                clone_root=active_dir,
                inventory_file=inv_path,
            )
            self.assertEqual(res2b["counts"]["fast_forwarded"], 1)
            self.assertEqual(res2b["fast_forwarded"][0]["full_name"], "mschwar/dirty-behind")

            # Fourth: HTTPS -> SSH remote URL normalization check
            https_repo = active_dir / "https-checkout"
            https_repo.mkdir()
            _git(https_repo, "init", "-b", "main")
            _git(https_repo, "config", "user.name", "Test")
            _git(https_repo, "config", "user.email", "test@example.com")
            _git(https_repo, "remote", "add", "origin", "https://github.com/mschwar/https-checkout.git")
            (https_repo / "README.md").write_text("# HTTPS\n", encoding="utf-8")
            _git(https_repo, "add", ".")
            _git(https_repo, "commit", "-m", "init")
            inv_https = json.loads(inv_path.read_text(encoding="utf-8"))
            inv_https.append(
                {
                    "name": "https-checkout",
                    "full_name": "mschwar/https-checkout",
                    "private": True,
                    "fork": False,
                    "archived": False,
                    "default_branch": "main",
                    "pushed_at": "2026-11-11T20:10:00Z",
                    "ssh_url": "git@github.com:mschwar/https-checkout.git",
                }
            )
            inv_path.write_text(json.dumps(inv_https), encoding="utf-8")
            res3 = sync_github_estate(
                config_path=config_path,
                state_dir=state_dir,
                clone_root=active_dir,
                inventory_file=inv_path,
                fetch_existing=False,
            )
            self.assertEqual(res3["counts"]["ssh_remotes_normalized"], 1)
            self.assertEqual(list_git_remotes(https_repo)["origin"], "git@github.com:mschwar/https-checkout.git")

    def test_gaming_guard_token_cache_permissions_and_offline_degraded_containment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pulse_path = root / "fleet-pulse.json"
            pulse_path.write_text(
                json.dumps(
                    {
                        "nodes": {
                            "matty-pc": {
                                "occupancy_state": "GAMING",
                                "gaming_active": True,
                                "dispatchable": False,
                            },
                            "thing1": {
                                "occupancy_state": "HUMAN_HEAVY",
                                "gaming_active": False,
                                "dispatchable": False,
                            },
                            "mac": {
                                "occupancy_state": "HUMAN_LIGHT",
                                "gaming_active": False,
                                "dispatchable": True,
                            },
                            "iz": {
                                "occupancy_state": "IDLE_FREE",
                                "gaming_active": False,
                                "dispatchable": True,
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )

            ok_gaming, reason_gaming = _check_fleet_pulse_allows_ssh("mschwar@100.99.233.15", pulse_path=pulse_path)
            self.assertFalse(ok_gaming)
            self.assertIn("blocked_by_gaming_guard:matty-pc", reason_gaming or "")

            ok_heavy, reason_heavy = _check_fleet_pulse_allows_ssh("thing1-lan", pulse_path=pulse_path)
            self.assertFalse(ok_heavy)
            self.assertIn("blocked_by_gaming_guard:thing1", reason_heavy or "")

            ok_mac, reason_mac = _check_fleet_pulse_allows_ssh("mschwar@100.82.59.43", pulse_path=pulse_path)
            self.assertTrue(ok_mac)
            self.assertIsNone(reason_mac)

            # Token cache permissions (0600 required; 0644 rejected)
            cache_file = root / "runtime" / "gh-token.json"
            _write_cached_token(cache_file, "tok_test_value")
            self.assertEqual(cache_file.stat().st_mode & 0o777, 0o600)
            self.assertEqual(_read_cached_token(cache_file, 3600), "tok_test_value")
            os.chmod(cache_file, 0o644)
            self.assertIsNone(_read_cached_token(cache_file, 3600))

            # Offline degraded containment when inventory is unreachable
            active_dir = root / "active"
            active_dir.mkdir()
            config_path = root / "ledger_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {"require_git": True},
                        "roots": [{"source_id": "active", "label": "active", "path": str(active_dir), "discovery": "children"}],
                    }
                ),
                encoding="utf-8",
            )
            state_dir = root / "state"
            with mock.patch(
                "ledger.github_sync.fetch_github_repo_inventory",
                return_value=([], "unavailable", "network_unreachable"),
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    rc_soft = ledger_main(
                        [
                            "sync-github",
                            "--config",
                            str(config_path),
                            "--state-dir",
                            str(state_dir),
                            "--clone-root",
                            str(active_dir),
                            "--json",
                        ]
                    )
                self.assertEqual(rc_soft, 0)
                degraded_doc = json.loads((state_dir / "github-sync.json").read_text(encoding="utf-8"))
                self.assertEqual(degraded_doc["status"], "degraded")
                self.assertFalse(degraded_doc["in_1to1_parity"])

                with contextlib.redirect_stdout(io.StringIO()):
                    rc_strict = ledger_main(
                        [
                            "sync-github",
                            "--check",
                            "--config",
                            str(config_path),
                            "--state-dir",
                            str(state_dir),
                            "--clone-root",
                            str(active_dir),
                            "--json",
                        ]
                    )
                self.assertEqual(rc_strict, 6)

    def test_merge_project_lineage_and_cli_merge_from(self) -> None:
        base_doc = {
            "schema_version": "1.0.0",
            "updated_at": "2026-11-10T00:00:00Z",
            "projects": [
                {
                    "canonical_project_id": "prj_111111111111111111111111",
                    "project_key": "github.com/mschwar/alpha",
                    "anchor_basis": "remote:github.com/mschwar/alpha",
                    "normalized_remotes": ["github.com/mschwar/alpha"],
                    "observation_ids": ["obs_111111111111111111111111"],
                    "superseded_canonical_ids": [],
                    "pinned_at": "2026-11-01T00:00:00Z",
                    "last_seen_run_id": "run_111111111111111111111111",
                }
            ],
        }
        overlay_doc = {
            "schema_version": "1.0.0",
            "updated_at": "2026-11-11T20:00:00Z",
            "projects": [
                {
                    "canonical_project_id": "prj_222222222222222222222222",
                    "project_key": "github.com/mschwar/newly-cloned",
                    "anchor_basis": "remote:github.com/mschwar/newly-cloned",
                    "normalized_remotes": ["github.com/mschwar/newly-cloned"],
                    "observation_ids": ["obs_222222222222222222222222"],
                    "superseded_canonical_ids": [],
                    "pinned_at": "2026-11-11T20:00:00Z",
                    "last_seen_run_id": "run_222222222222222222222222",
                }
            ],
        }
        merged = merge_project_lineage(base_doc, overlay_doc)
        self.assertEqual(len(merged["projects"]), 2)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "state"
            state_dir.mkdir()
            (state_dir / "canonical-projects.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "run_id": "run_333333333333333333333333",
                        "compiled_at": "2026-11-11T20:10:00Z",
                        "projects": [
                            {
                                "canonical_project_id": "prj_111111111111111111111111",
                                "project_key": "github.com/mschwar/alpha",
                                "identity_basis": "exact_normalized_remote",
                                "normalized_remotes": ["github.com/mschwar/alpha"],
                                "observation_ids": ["obs_111111111111111111111111"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            lineage_path = root / "project-lineage.json"
            lineage_path.write_text(json.dumps(base_doc), encoding="utf-8")
            overlay_path = root / "watchdog-lineage.json"
            overlay_path.write_text(json.dumps(overlay_doc), encoding="utf-8")

            status = lineage_status_payload(
                state_dir=state_dir,
                lineage_path=lineage_path,
                sync=True,
                merge_from_path=overlay_path,
            )
            self.assertTrue(status["in_sync"])
            self.assertEqual(status["lineage_project_count"], 2)

    def test_watchdog_end_to_end_clones_remote_repo_fast_forwards_pins_lineage_and_projects_gbrain(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        watchdog_script = repo_root / "ops" / "project-ledger-discovery-watchdog.sh"
        self.assertTrue(watchdog_script.is_file())
        syntax_chk = subprocess.run(["bash", "-n", str(watchdog_script)], capture_output=True, text=True, check=False)
        self.assertEqual(syntax_chk.returncode, 0, syntax_chk.stderr)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remotes_dir = root / "remotes"
            staging_dir = root / "staging"
            active_dir = root / "active"
            active_dir.mkdir(parents=True)
            watchdog_state = root / "watchdog-state"
            brain_dir = root / "brain"
            (brain_dir / "projects").mkdir(parents=True)

            sha_alpha_1 = _init_bare_with_commit(remotes_dir / "mschwar" / "alpha-svc.git", staging_dir / "alpha", text="# Alpha Service\n")
            config_path = root / "ledger_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {"require_git": True, "exclude_autosnapshot_repos": True},
                        "roots": [
                            {
                                "source_id": "central-active",
                                "label": "central-active",
                                "path": str(active_dir),
                                "discovery": "children",
                                "storage_scope": "local",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            inv_path = root / "inventory.json"
            inv_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "alpha-svc",
                            "full_name": "mschwar/alpha-svc",
                            "private": True,
                            "fork": False,
                            "archived": False,
                            "default_branch": "main",
                            "pushed_at": "2026-11-11T20:00:00Z",
                            "ssh_url": f"file://{remotes_dir / 'mschwar' / 'alpha-svc.git'}",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            base_env = {
                **os.environ,
                "PROJECT_LEDGER_REPO": str(repo_root),
                "PROJECT_LEDGER_CONFIG": str(config_path),
                "PROJECT_LEDGER_WATCHDOG_STATE_DIR": str(watchdog_state),
                "PROJECT_LEDGER_CLONE_ROOT": str(active_dir),
                "PROJECT_LEDGER_GITHUB_INVENTORY_FILE": str(inv_path),
                "PROJECT_LEDGER_BRAIN_DIR": str(brain_dir),
                "PROJECT_LEDGER_GBRAIN_SYNC_HOOK": "true",
                "PROJECT_LEDGER_SYNC_REPO_STATE": "0",
            }

            # Run 1: clones alpha-svc and establishes baseline.
            r1 = subprocess.run(["bash", str(watchdog_script)], env=base_env, capture_output=True, text=True, check=False, timeout=60)
            self.assertEqual(r1.returncode, 0, r1.stderr)
            self.assertIn("projects=1", r1.stdout)
            self.assertIn("github_sync=ok", r1.stdout)
            self.assertIn("cloned=1", r1.stdout)
            self.assertEqual(_git(active_dir / "alpha-svc", "rev-parse", "HEAD"), sha_alpha_1)
            self.assertTrue((watchdog_state / "github-sync.json").is_file())
            self.assertTrue((watchdog_state / "project-lineage.json").is_file())

            # Advance alpha-svc AND create brand-new remote repo beta-cli!
            (staging_dir / "alpha" / "CHANGELOG.md").write_text("v1.1\n", encoding="utf-8")
            _git(staging_dir / "alpha", "add", ".")
            _git(staging_dir / "alpha", "commit", "-m", "v1.1")
            _git(staging_dir / "alpha", "push", "origin", "main")
            sha_alpha_2 = _git(staging_dir / "alpha", "rev-parse", "HEAD")

            sha_beta_1 = _init_bare_with_commit(remotes_dir / "mschwar" / "beta-cli.git", staging_dir / "beta", text="# Beta CLI\n")
            inv_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "alpha-svc",
                            "full_name": "mschwar/alpha-svc",
                            "private": True,
                            "fork": False,
                            "archived": False,
                            "default_branch": "main",
                            "pushed_at": "2026-11-11T20:15:00Z",
                            "ssh_url": f"file://{remotes_dir / 'mschwar' / 'alpha-svc.git'}",
                        },
                        {
                            "name": "beta-cli",
                            "full_name": "mschwar/beta-cli",
                            "private": True,
                            "fork": False,
                            "archived": False,
                            "default_branch": "main",
                            "pushed_at": "2026-11-11T20:15:00Z",
                            "ssh_url": f"file://{remotes_dir / 'mschwar' / 'beta-cli.git'}",
                        },
                    ]
                ),
                encoding="utf-8",
            )

            # Run 2: clones beta-cli, fast-forwards alpha-svc, pins beta-cli in lineage, and syncs GBrain pages!
            r2 = subprocess.run(["bash", str(watchdog_script)], env=base_env, capture_output=True, text=True, check=False, timeout=60)
            self.assertEqual(r2.returncode, 0, r2.stderr)
            self.assertIn("projects=2", r2.stdout)
            self.assertIn("triggered=yes", r2.stdout)
            self.assertIn("cloned=1", r2.stdout)
            self.assertIn("ff=1", r2.stdout)
            self.assertEqual(_git(active_dir / "alpha-svc", "rev-parse", "HEAD"), sha_alpha_2)
            self.assertEqual(_git(active_dir / "beta-cli", "rev-parse", "HEAD"), sha_beta_1)
            self.assertTrue((brain_dir / "projects" / "alpha-svc.md").is_file())
            self.assertTrue((brain_dir / "projects" / "beta-cli.md").is_file())


if __name__ == "__main__":
    unittest.main()
