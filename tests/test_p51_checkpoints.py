"""Durable tests for P5.1 source fingerprint/checkpoint cache and post-CONV-001 triage batch."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ledger import IDENTITY_DECISION_SCHEMA_VERSION, SOURCE_CHECKPOINT_SCHEMA_VERSION, cli
from ledger.checkpoints import (
    CHECKPOINT_FILENAME,
    collect_entries_incremental,
    git_repo_fingerprint,
    load_source_checkpoints,
)
from ledger.compiler import load_manifest
from ledger.identity import load_identity_decisions
from ledger.sources import filesystem as fs_mod
from ledger.sources.base import collect_entries
from ledger.triage import load_candidate_decisions


def _init_git_repo(repo_dir: Path, *, remote_url: str | None = None) -> str:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    if remote_url:
        subprocess.run(
            ["git", "remote", "add", "origin", remote_url],
            cwd=repo_dir,
            check=True,
            capture_output=True,
        )
    if not (repo_dir / "README.md").exists():
        (repo_dir / "README.md").write_text(f"# {repo_dir.name}\nInitial.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True, capture_output=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_dir, check=True, capture_output=True, text=True
    )
    return head.stdout.strip()


class P51SourceCheckpointTests(unittest.TestCase):
    def _build_mixed_estate(self, root: Path) -> tuple[Path, Path, Path, dict[str, Path]]:
        children_root = root / "active"
        children_root.mkdir()

        repo_a = children_root / "alpha"
        _init_git_repo(repo_a, remote_url="https://github.com/example/alpha.git")

        repo_b = children_root / "beta"
        _init_git_repo(repo_b, remote_url="https://github.com/example/beta.git")
        (repo_b / ".project-ledger.json").write_text(
            json.dumps({"project_key": "github.com/example/beta", "status": "active"}),
            encoding="utf-8",
        )

        # Low-score directory that fails min_score=2 initially.
        low_score = children_root / "scratch-empty"
        low_score.mkdir()
        (low_score / "notes.txt").write_text("plain text\n", encoding="utf-8")

        self_root = root / "standalone"
        self_root.mkdir()
        (self_root / "README.md").write_text("# Standalone\nSelf root.\n", encoding="utf-8")

        # Duplicate self reference to repo_a to exercise cross-root seen_paths deduplication.
        nested_git_root = root / "infra"
        nested_repo = nested_git_root / "ops" / "gamma"
        _init_git_repo(nested_repo, remote_url="https://github.com/example/gamma.git")

        inv_mount = root / "drive"
        inv_mount.mkdir()
        inv_jsonl = root / "inventory.jsonl"
        inv_jsonl.write_text(
            "\n".join(
                json.dumps(rec, sort_keys=True)
                for rec in [
                    {
                        "is_dir": True,
                        "mod_time": "2026-10-01T00:00:00+00:00",
                        "name": "delta",
                        "path": "delta",
                        "root_label": "delta",
                    },
                    {
                        "is_dir": False,
                        "mod_time": "2026-10-01T00:00:00+00:00",
                        "name": "README.md",
                        "path": "delta/README.md",
                        "root_label": "delta",
                    },
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        inv_policy = root / "policy.json"
        inv_policy.write_text(
            json.dumps(
                {
                    "roots": {
                        "delta": {
                            "crawl_treatment": "project_discovery",
                            "project_ledger_candidate": True,
                            "root_class": "repo_like",
                            "rationale": "Delta inventory root.",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        missing_root = root / "offline-mirror"

        config = {
            "defaults": {
                "min_score": 2,
                "tree_scan_max_entries": 5000,
                "tree_scan_max_depth": 8,
                "git_repo_max_depth": 6,
            },
            "roots": [
                {
                    "source_id": "src-active",
                    "path": str(children_root),
                    "label": "src-active",
                    "discovery": "children",
                },
                {
                    "source_id": "src-dup-alpha",
                    "path": str(repo_a),
                    "label": "src-dup-alpha",
                    "discovery": "self",
                },
                {
                    "source_id": "src-standalone",
                    "path": str(self_root),
                    "label": "src-standalone",
                    "discovery": "self",
                },
                {
                    "source_id": "src-infra",
                    "path": str(nested_git_root),
                    "label": "src-infra",
                    "discovery": "git_repos",
                    "max_depth": 4,
                },
                {
                    "source_id": "src-inventory",
                    "path": str(inv_mount),
                    "label": "matty-pc",
                    "discovery": "inventory_policy",
                    "inventory_jsonl": str(inv_jsonl),
                    "policy_path": str(inv_policy),
                    "remote_name": "matty-pc",
                },
                {
                    "source_id": "src-offline",
                    "path": str(missing_root),
                    "label": "src-offline",
                    "discovery": "children",
                },
            ],
        }
        config_path = root / "ledger_config.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        output_dir = root / "output"
        output_dir.mkdir()
        state_dir = root / "state"
        paths = {
            "children_root": children_root,
            "repo_a": repo_a,
            "repo_b": repo_b,
            "low_score": low_score,
            "self_root": self_root,
            "nested_repo": nested_repo,
            "inv_jsonl": inv_jsonl,
            "inv_policy": inv_policy,
            "missing_root": missing_root,
        }
        return config_path, output_dir, state_dir, paths

    def test_incremental_entries_are_byte_identical_and_second_run_skips_git_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path, output_dir, state_dir, _ = self._build_mixed_estate(root)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            cp_path = state_dir / CHECKPOINT_FILENAME

            baseline_entries = collect_entries(config, config_path.parent, output_dir)
            inc_entries_1, cp_1 = collect_entries_incremental(
                config,
                config_path.parent,
                output_dir,
                checkpoint_path=cp_path,
                now_iso="2026-10-30T15:00:00Z",
            )
            self.assertEqual(inc_entries_1, baseline_entries)
            self.assertEqual(cp_1["schema_version"], SOURCE_CHECKPOINT_SCHEMA_VERSION)
            self.assertEqual(cp_1["summary"]["hit_source_count"], 0)
            self.assertEqual(cp_1["summary"]["unavailable_source_count"], 1)
            self.assertGreater(cp_1["summary"]["rebuilt_candidate_count"], 0)

            # Second run with no changes must reuse 100% of candidates without spawning gather_git_metadata.
            with mock.patch.object(
                fs_mod,
                "gather_git_metadata",
                side_effect=AssertionError("unchanged candidate must not invoke gather_git_metadata"),
            ):
                inc_entries_2, cp_2 = collect_entries_incremental(
                    config,
                    config_path.parent,
                    output_dir,
                    checkpoint_path=cp_path,
                    now_iso="2026-10-30T15:05:00Z",
                )

            self.assertEqual(inc_entries_2, baseline_entries)
            self.assertEqual(cp_2["summary"]["rebuilt_candidate_count"], 0)
            self.assertEqual(cp_2["summary"]["reused_candidate_count"], cp_1["summary"]["candidate_count"])
            self.assertEqual(cp_2["sources"]["src-active"]["checkpoint_status"], "hit")
            self.assertEqual(
                cp_2["sources"]["src-active"]["last_extracted_at"], "2026-10-30T15:00:00Z"
            )
            self.assertEqual(cp_2["sources"]["src-offline"]["checkpoint_status"], "unavailable")

    def test_selective_invalidation_on_git_readme_sidecar_low_score_and_inventory_edits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path, output_dir, state_dir, paths = self._build_mixed_estate(root)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            cp_path = state_dir / CHECKPOINT_FILENAME

            collect_entries_incremental(
                config, config_path.parent, output_dir, checkpoint_path=cp_path
            )

            # 1. Commit in repo_a only -> src-active is "partial" (1 rebuilt, 2 reused), repo_b untouched!
            (paths["repo_a"] / "feature.py").write_text("x = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=paths["repo_a"], check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "feat"], cwd=paths["repo_a"], check=True, capture_output=True
            )
            new_head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=paths["repo_a"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            real_git_meta = fs_mod.gather_git_metadata
            called_dirs: list[Path] = []

            def spy_git_meta(directory: Path) -> dict:
                called_dirs.append(directory.resolve())
                return real_git_meta(directory)

            with mock.patch.object(fs_mod, "gather_git_metadata", side_effect=spy_git_meta):
                entries_after_commit, cp_commit = collect_entries_incremental(
                    config, config_path.parent, output_dir, checkpoint_path=cp_path
                )

            self.assertEqual(called_dirs, [paths["repo_a"].resolve()])
            self.assertEqual(cp_commit["sources"]["src-active"]["checkpoint_status"], "partial")
            self.assertEqual(cp_commit["sources"]["src-active"]["rebuilt_candidate_count"], 1)
            self.assertEqual(cp_commit["sources"]["src-active"]["reused_candidate_count"], 2)
            alpha_entry = next(e for e in entries_after_commit if e["repo_name"] == "alpha")
            self.assertEqual(alpha_entry["head_commit"], new_head)
            self.assertEqual(
                entries_after_commit, collect_entries(config, config_path.parent, output_dir)
            )

            # 2. Edit sidecar in repo_b + promote low_score directory by adding README.md.
            (paths["repo_b"] / ".project-ledger.json").write_text(
                json.dumps(
                    {
                        "project_key": "github.com/example/beta",
                        "status": "paused",
                        "next_step": "ship v2",
                    }
                ),
                encoding="utf-8",
            )
            (paths["low_score"] / "README.md").write_text(
                "# Promoted Project\nNow passes min_score.\n", encoding="utf-8"
            )
            entries_after_promote, cp_promote = collect_entries_incremental(
                config, config_path.parent, output_dir, checkpoint_path=cp_path
            )
            self.assertEqual(cp_promote["sources"]["src-active"]["rebuilt_candidate_count"], 2)
            self.assertEqual(cp_promote["sources"]["src-active"]["reused_candidate_count"], 1)
            beta_entry = next(e for e in entries_after_promote if e["repo_name"] == "beta")
            self.assertEqual(beta_entry["status"], "paused")
            self.assertEqual(beta_entry["next_step"], "ship v2")
            self.assertTrue(any(e["name"] == "Promoted Project" for e in entries_after_promote))
            self.assertEqual(
                entries_after_promote, collect_entries(config, config_path.parent, output_dir)
            )

            # 3. Update inventory.jsonl -> src-inventory rebuilds while filesystem sources hit.
            paths["inv_jsonl"].write_text(
                paths["inv_jsonl"].read_text(encoding="utf-8")
                + json.dumps(
                    {
                        "is_dir": False,
                        "mod_time": "2026-10-30T12:00:00+00:00",
                        "name": "extra.md",
                        "path": "delta/extra.md",
                        "root_label": "delta",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            entries_after_inv, cp_inv = collect_entries_incremental(
                config, config_path.parent, output_dir, checkpoint_path=cp_path
            )
            self.assertEqual(cp_inv["sources"]["src-active"]["checkpoint_status"], "hit")
            self.assertEqual(cp_inv["sources"]["src-inventory"]["checkpoint_status"], "miss")
            self.assertEqual(cp_inv["sources"]["src-inventory"]["rebuilt_candidate_count"], 1)
            self.assertEqual(
                entries_after_inv, collect_entries(config, config_path.parent, output_dir)
            )

    def test_git_worktree_gitfile_fingerprint_tracks_commits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main_repo = root / "main-repo"
            _init_git_repo(main_repo, remote_url="https://github.com/example/wt.git")
            wt_dir = root / "wt-branch"
            subprocess.run(
                ["git", "worktree", "add", "-b", "feat/wt", str(wt_dir)],
                cwd=main_repo,
                check=True,
                capture_output=True,
            )
            fp1 = git_repo_fingerprint(wt_dir)
            self.assertIsNotNone(fp1)
            (wt_dir / "wt.txt").write_text("worktree edit\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=wt_dir, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "wt commit"], cwd=wt_dir, check=True, capture_output=True
            )
            fp2 = git_repo_fingerprint(wt_dir)
            self.assertNotEqual(fp1, fp2)

    def test_cli_refresh_manifest_checkpoints_full_scan_and_corrupt_cache_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path, output_dir, state_dir, _ = self._build_mixed_estate(root)

            def _run_cli(argv: list[str]) -> int:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    return cli.main(argv)

            # 1. Initial refresh populates state/source-checkpoints.json.
            rc1 = _run_cli(
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
            self.assertEqual(rc1, 0)
            cp_path = state_dir / CHECKPOINT_FILENAME
            self.assertTrue(cp_path.is_file())
            m1 = load_manifest(state_dir)
            self.assertEqual(m1["capabilities"]["source_checkpoints"]["state"], "available")
            self.assertEqual(m1["checkpoints"]["state"], "active")
            self.assertEqual(m1["checkpoints"]["hit_source_count"], 0)

            # 2. Second refresh hits checkpoints across all available non-empty sources.
            rc2 = _run_cli(
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
            self.assertEqual(rc2, 0)
            m2 = load_manifest(state_dir)
            self.assertEqual(m2["checkpoints"]["state"], "active")
            self.assertEqual(m2["checkpoints"]["rebuilt_candidate_count"], 0)
            self.assertGreater(m2["checkpoints"]["hit_source_count"], 0)
            active_src = next(s for s in m2["sources"] if s["source_id"] == "src-active")
            self.assertEqual(active_src["checkpoint_status"], "hit")
            self.assertEqual(len(str(active_src["checkpoint_fingerprint"])), 64)

            # 3. --full-scan bypasses checkpoints and forces full extraction.
            rc3 = _run_cli(
                [
                    "refresh",
                    "--config",
                    str(config_path),
                    "--output-dir",
                    str(output_dir),
                    "--state-dir",
                    str(state_dir),
                    "--no-markdown-mirror",
                    "--full-scan",
                ]
            )
            self.assertEqual(rc3, 0)
            m3 = load_manifest(state_dir)
            self.assertEqual(m3["checkpoints"]["state"], "bypassed")
            self.assertGreater(m3["checkpoints"]["rebuilt_candidate_count"], 0)

            # 4. Corrupt checkpoint file degrades locally to a clean rebuild without failing refresh.
            cp_path.write_text("{corrupt json", encoding="utf-8")
            self.assertIsNone(load_source_checkpoints(cp_path))
            rc4 = _run_cli(
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
            self.assertEqual(rc4, 0)
            self.assertIsNotNone(load_source_checkpoints(cp_path))

    def test_committed_triage_candidate_batches_validate_cleanly(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        candidates_path = repo_root / "registry" / "weak-overlap-triage-candidates.json"
        candidates = load_candidate_decisions(candidates_path)
        self.assertEqual(len(candidates), 62)

        with tempfile.TemporaryDirectory() as tmp:
            temp_decisions = Path(tmp) / "decisions.json"
            temp_decisions.write_text(
                json.dumps(
                    {
                        "schema_version": IDENTITY_DECISION_SCHEMA_VERSION,
                        "decisions": candidates,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            validated = load_identity_decisions(temp_decisions)
            self.assertEqual(len(validated["decisions"]), 62)


if __name__ == "__main__":
    unittest.main()
