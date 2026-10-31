"""Regression tests for P5.1 checkpoint portability across ephemeral staging tempdirs."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from ledger import cli
from ledger.checkpoints import (
    CHECKPOINT_FILENAME,
    _source_config_digest,
    collect_entries_incremental,
)
from ledger.compiler import load_manifest
from ledger.config import read_config


def _init_git_repo(path: Path, remote_url: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", remote_url], check=True)
    (path / "README.md").write_text(f"# {path.name}\n\nCheckpoint regression fixture.\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)


class SharedCheckpointTwoTempdirTests(unittest.TestCase):
    def test_source_config_digest_ignores_ephemeral_output_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            repo_root = Path(tmp1) / "repos"
            repo_root.mkdir()
            root_cfg = {
                "source_id": "central-active",
                "label": "central-active",
                "discovery": "children",
                "_resolved_path": repo_root.resolve(),
            }
            defaults = {"min_score": 2}
            digest_a = _source_config_digest(
                root_cfg, defaults, Path(tmp1) / "stage-a" / "output", "central-active"
            )
            digest_b = _source_config_digest(
                root_cfg, defaults, Path(tmp2) / "stage-b" / "output", "central-active"
            )
            self.assertEqual(digest_a, digest_b)

    def test_two_tempdir_shared_checkpoint_hits_100_percent_and_emits_no_dangling_tmp_paths(self) -> None:
        with (
            tempfile.TemporaryDirectory() as estate_tmp,
            tempfile.TemporaryDirectory() as stage1_tmp,
            tempfile.TemporaryDirectory() as stage2_tmp,
        ):
            estate = Path(estate_tmp)
            active_root = estate / "active"
            apps_root = estate / "apps"
            _init_git_repo(active_root / "alpha", "https://github.com/example/alpha.git")
            _init_git_repo(active_root / "beta", "https://github.com/example/beta.git")
            _init_git_repo(apps_root / "gamma", "https://github.com/example/gamma.git")

            config_path = estate / "ledger_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "roots": [
                            {
                                "source_id": "central-active",
                                "label": "central-active",
                                "path": str(active_root),
                                "discovery": "children",
                            },
                            {
                                "source_id": "central-apps",
                                "label": "central-apps",
                                "path": str(apps_root),
                                "discovery": "children",
                            },
                        ]
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            decisions_path = estate / "decisions.json"
            decisions_path.write_text(
                json.dumps({"schema_version": "1.0.0", "decisions": []}),
                encoding="utf-8",
            )

            stage1 = Path(stage1_tmp)
            stage2 = Path(stage2_tmp)

            def _run_refresh(stage_root: Path) -> int:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    return cli.main(
                        [
                            "refresh",
                            "--config",
                            str(config_path),
                            "--output-dir",
                            str(stage_root / "output"),
                            "--state-dir",
                            str(stage_root / "state"),
                            "--identity-decisions",
                            str(decisions_path),
                            "--no-markdown-mirror",
                        ]
                    )

            # Run 1 in fresh mktemp staging dir 1 (cold cache).
            self.assertEqual(_run_refresh(stage1), 0)
            m1 = load_manifest(stage1 / "state")
            self.assertEqual(m1["checkpoints"]["miss_source_count"], 2)
            self.assertEqual(m1["checkpoints"]["rebuilt_candidate_count"], 3)

            # Seed stage2 from stage1 exactly as project-ledger-discovery-watchdog.sh does.
            (stage2 / "state").mkdir(parents=True, exist_ok=True)
            shutil.copy2(
                stage1 / "state" / CHECKPOINT_FILENAME,
                stage2 / "state" / CHECKPOINT_FILENAME,
            )
            shutil.copytree(stage1 / "state" / "runs", stage2 / "state" / "runs")
            shutil.copy2(
                stage1 / "state" / "system-manifest.json",
                stage2 / "state" / "system-manifest.json",
            )
            shutil.copy2(
                stage1 / "state" / "changes.json",
                stage2 / "state" / "changes.json",
            )

            # Run 2 in distinct mktemp staging dir 2 (warm cache): must hit 100% of sources & candidates.
            self.assertEqual(_run_refresh(stage2), 0)
            m2 = load_manifest(stage2 / "state")
            self.assertEqual(m2["checkpoints"]["source_count"], 2)
            self.assertEqual(m2["checkpoints"]["hit_source_count"], 2)
            self.assertEqual(m2["checkpoints"]["miss_source_count"], 0)
            self.assertEqual(m2["checkpoints"]["reused_candidate_count"], 3)
            self.assertEqual(m2["checkpoints"]["rebuilt_candidate_count"], 0)
            self.assertEqual(m2["changes"]["change_count"], 0)

            # Direct API verification across two distinct output_dir tempdirs.
            cfg = read_config(config_path)
            _, cp_payload = collect_entries_incremental(
                cfg,
                config_path.parent,
                stage2 / "fresh-output",
                checkpoint_path=stage2 / "state" / CHECKPOINT_FILENAME,
            )
            self.assertEqual(cp_payload["summary"]["hit_source_count"], 2)
            self.assertEqual(cp_payload["summary"]["rebuilt_candidate_count"], 0)

            # Zero materialized JSON state files may contain dangling stage1/stage2 tempdir paths.
            for json_file in (stage2 / "state").rglob("*.json"):
                raw = json_file.read_text(encoding="utf-8")
                self.assertNotIn(
                    str(stage1),
                    raw,
                    f"Dangling stage1 tempdir path leaked into {json_file.name}",
                )
                self.assertNotIn(
                    str(stage2),
                    raw,
                    f"Dangling stage2 tempdir path leaked into {json_file.name}",
                )


if __name__ == "__main__":
    unittest.main()
