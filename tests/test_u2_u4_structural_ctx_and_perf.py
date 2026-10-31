"""Durable contract tests for Strategic Unlocks U2 (read-only structural ctx) and U4 (fast warm refresh)."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ledger import cli
from ledger.capsules import CAPSULE_MAX_BYTES, show_payload
from ledger.changes import _read_snapshot_header, _save_run_snapshot, build_run_snapshot, changes_payload
from ledger.checkpoints import filesystem_candidate_fingerprint
from ledger.config import LedgerConfigError, validate_config
from ledger.identity import materialize_identity, resolve_payload
from ledger.sources.filesystem import discover_git_repos
from ledger.structural_ctx import extract_structural_ctx
from tests.test_state_schemas import validate_json_schema

REPO_ROOT = Path(__file__).resolve().parents[1]


def _init_git_repo(path: Path, remote_url: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", remote_url], check=True)


def _tree_snapshot(root: Path) -> dict[str, str]:
    snap: dict[str, str] = {}
    for item in sorted(root.rglob("*")):
        rel = item.relative_to(root).as_posix()
        if item.is_file():
            snap[rel] = hashlib.sha256(item.read_bytes()).hexdigest()
        elif item.is_dir():
            snap[rel] = "dir"
    return snap


class U2StructuralCtxTests(unittest.TestCase):
    def test_extract_structural_ctx_multilang_is_read_only_and_schema_valid(self) -> None:
        capsule_schema = json.loads(
            (REPO_ROOT / "schemas" / "project-capsule.schema.json").read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            active_root = base / "active"
            repo = active_root / "polyglot"
            _init_git_repo(repo, "https://github.com/example/polyglot.git")

            (repo / ".gitignore").write_text("ignored_dump/\n/root_scratch/\n", encoding="utf-8")
            (repo / "ignored_dump").mkdir()
            (repo / "ignored_dump" / "noise.py").write_text("NOISE = 1\n", encoding="utf-8")
            (repo / "root_scratch").mkdir()
            (repo / "root_scratch" / "temp.py").write_text("TEMP = 1\n", encoding="utf-8")

            (repo / "AGENTS.md").write_text(
                "# AGENTS\n\n## Operating Rules\n\nAuthoritative agent contract.\n",
                encoding="utf-8",
            )
            (repo / "ARCHITECTURE.md").write_text(
                "# Architecture\n\n## Dataflow\n\nCore module boundaries.\n",
                encoding="utf-8",
            )
            (repo / "README.md").write_text(
                "# Polyglot Engine\n\nMulti-language structural scan specimen.\n",
                encoding="utf-8",
            )
            (repo / "pyproject.toml").write_text(
                '[project]\nname = "polyglot"\nversion = "0.1.0"\n\n'
                '[project.scripts]\npoly = "core.cli:main"\n',
                encoding="utf-8",
            )
            (repo / "package.json").write_text(
                json.dumps({"name": "poly-web", "main": "web/index.ts", "bin": {"poly-ui": "web/cli.ts"}}),
                encoding="utf-8",
            )

            (repo / "core").mkdir()
            (repo / "core" / "engine.py").write_text(
                "import json\nimport httpx\n\nclass Engine:\n    pass\n\ndef run_engine() -> None:\n    pass\n\ndef _private() -> None:\n    pass\n",
                encoding="utf-8",
            )
            (repo / "core" / "cli.py").write_text(
                "from .engine import Engine\nimport helpers.utils\n\ndef main() -> int:\n    return 0\n",
                encoding="utf-8",
            )
            (repo / "helpers").mkdir()
            (repo / "helpers" / "utils.py").write_text(
                "def format_label(val: str) -> str:\n    return val.strip()\n",
                encoding="utf-8",
            )
            (repo / "web").mkdir()
            (repo / "web" / "index.ts").write_text(
                'import React from "react";\nimport fs from "node:fs";\nexport function renderDashboard() { return null; }\n',
                encoding="utf-8",
            )
            (repo / "rust_ext").mkdir()
            (repo / "rust_ext" / "lib.rs").write_text(
                "use serde::Serialize;\npub struct FastIndex {}\npub fn build_index() {}\n",
                encoding="utf-8",
            )

            # Also create 18 small extra subsystems to exercise CAPSULE_MAX_BYTES / subsystem truncation.
            for idx in range(18):
                sdir = repo / f"mod_{idx:02d}"
                sdir.mkdir()
                (sdir / "worker.py").write_text(
                    f"def worker_{idx:02d}() -> int:\n    return {idx}\n",
                    encoding="utf-8",
                )

            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init polyglot"], check=True)

            before_tree = _tree_snapshot(repo)
            skel = extract_structural_ctx(repo)
            self.assertIsNotNone(skel)
            assert skel is not None
            self.assertTrue(skel["structural_hash"].startswith("sha256:"))
            self.assertTrue(skel["boundary_doc_hash"].startswith("sha256:"))
            self.assertIn("cli:poly -> core.cli:main", skel["entrypoints"])
            self.assertIn("npm:main -> web/index.ts", skel["entrypoints"])
            self.assertIn("npm:bin:poly-ui -> web/cli.ts", skel["entrypoints"])
            # Authority > architecture > overview ordering.
            self.assertEqual(
                [doc["path"] for doc in skel["boundary_docs"][:3]],
                ["AGENTS.md", "ARCHITECTURE.md", "README.md"],
            )
            # Gitignored directories must not appear as subsystems.
            sub_paths = {s["path"] for s in skel["subsystems"]}
            self.assertNotIn("ignored_dump", sub_paths)
            self.assertNotIn("root_scratch", sub_paths)
            self.assertIn("core", sub_paths)
            core_sub = next(s for s in skel["subsystems"] if s["path"] == "core")
            self.assertIn("class Engine", core_sub["key_symbols"])
            self.assertIn("fn run_engine", core_sub["key_symbols"])
            self.assertNotIn("fn _private", core_sub["key_symbols"])
            self.assertIn("helpers", core_sub["internal_deps"])
            self.assertIn("httpx", core_sub["external_deps"])
            self.assertNotIn("json", core_sub["external_deps"])

            # End-to-end refresh must synthesize compiled_structural_scan, cache it on warm refresh,
            # and write ZERO bytes into the target repo (Architectural Law 10).
            config_path = base / "ledger_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "roots": [
                            {
                                "source_id": "central-active",
                                "label": "central-active",
                                "path": str(active_root),
                                "discovery": "children",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            decisions_path = base / "decisions.json"
            decisions_path.write_text(
                json.dumps({"schema_version": "1.0.0", "decisions": []}),
                encoding="utf-8",
            )
            out_dir = base / "output"
            state_dir = base / "state"

            def _refresh() -> int:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    return cli.main(
                        [
                            "refresh",
                            "--config",
                            str(config_path),
                            "--output-dir",
                            str(out_dir),
                            "--state-dir",
                            str(state_dir),
                            "--identity-decisions",
                            str(decisions_path),
                            "--no-markdown-mirror",
                        ]
                    )

            self.assertEqual(_refresh(), 0)
            self.assertEqual(_tree_snapshot(repo), before_tree)
            self.assertFalse((repo / ".ctx").exists())

            cap = show_payload(state_dir, "github.com/example/polyglot")
            self.assertEqual(cap["ctx"]["state"], "available")
            self.assertEqual(cap["ctx"]["source_format"], "compiled_structural_scan")
            self.assertIsNone(cap["ctx"]["artifact_path"])
            self.assertTrue(cap["ctx"]["subsystems_truncated"])
            self.assertGreater(cap["ctx"]["subsystem_count"], 12)
            cap_file = state_dir / "projects" / f"{cap['canonical_project_id']}.json"
            self.assertLessEqual(len(cap_file.read_bytes()), CAPSULE_MAX_BYTES)
            raw_capsule_doc = json.loads(cap_file.read_text(encoding="utf-8"))
            self.assertEqual(validate_json_schema(raw_capsule_doc, capsule_schema), [])

            # Warm refresh must reuse cached ctx_raw_skeleton without re-scanning repo.
            import ledger.compiler as compiler_mod

            with mock.patch.object(
                compiler_mod,
                "extract_structural_ctx",
                side_effect=AssertionError("warm refresh must reuse cached ctx_raw_skeleton"),
            ):
                self.assertEqual(_refresh(), 0)

            # Adding an explicit on-disk `.ctx/skeleton.json` invalidates the fingerprint and takes precedence.
            (repo / ".ctx").mkdir()
            (repo / ".ctx" / "skeleton.json").write_text(
                json.dumps(
                    {
                        "structural_hash": "sha256:explicit999",
                        "boundary_doc_hash": "sha256:explicit888",
                        "total_files": 5,
                        "languages": {"Python": 5},
                        "entrypoints": ["cli:explicit -> core:main"],
                        "boundary_docs": [],
                        "subsystems": [],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(_refresh(), 0)
            cap_explicit = show_payload(state_dir, "github.com/example/polyglot")
            self.assertEqual(cap_explicit["ctx"]["state"], "available")
            self.assertEqual(cap_explicit["ctx"]["source_format"], "skeleton_json")
            self.assertEqual(cap_explicit["ctx"]["structural_hash"], "sha256:explicit999")


class U4PerformancePrimitivesTests(unittest.TestCase):
    def test_discover_git_repos_scandir_respects_depth_excludes_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            r1 = root / "a" / "service"
            _init_git_repo(r1, "https://github.com/example/service.git")
            r2 = root / "b" / "nested" / "deep"
            _init_git_repo(r2, "https://github.com/example/deep.git")
            too_deep = root / "c" / "l2" / "l3" / "l4"
            _init_git_repo(too_deep, "https://github.com/example/too-deep.git")
            excluded = root / "node_modules" / "pkg"
            _init_git_repo(excluded, "https://github.com/example/excluded.git")
            sym = root / "a" / "sym-service"
            sym.symlink_to(r1)

            found = discover_git_repos(root, {"node_modules", ".git"}, max_depth=3)
            self.assertEqual(found, sorted([r1, r2], key=lambda p: str(p).lower()))

    def test_single_pass_fingerprint_short_circuits_low_score_and_validates_config_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            validate_config(
                {
                    "defaults": {"checkpoint_stat_max_files": 256},
                    "roots": [{"path": str(root), "discovery": "children", "checkpoint_stat_max_files": 128}],
                },
                root,
            )
            with self.assertRaises(LedgerConfigError):
                validate_config(
                    {
                        "defaults": {"checkpoint_stat_max_files": -1},
                        "roots": [{"path": str(root), "discovery": "children"}],
                    },
                    root,
                )

            low_dir = root / "scratch"
            (low_dir / "deep" / "sub").mkdir(parents=True)
            (low_dir / "notes.txt").write_text("not enough score\n", encoding="utf-8")
            (low_dir / "deep" / "sub" / "blob.dat").write_bytes(b"123")

            root_cfg = {"discovery": "children", "min_score": 2, "_resolved_path": root.resolve()}
            fp1 = filesystem_candidate_fingerprint(
                low_dir, root_cfg, {}, root / "out", source_cfg_digest="cfg1"
            )
            self.assertIsNotNone(fp1)

            # Promoting low_dir with README.md changes shallow score -> invalidates fingerprint immediately.
            (low_dir / "README.md").write_text("# Promoted\n\nNow a project.\n", encoding="utf-8")
            fp2 = filesystem_candidate_fingerprint(
                low_dir, root_cfg, {}, root / "out", source_cfg_digest="cfg1"
            )
            self.assertNotEqual(fp1, fp2)

            # Deep file edit in an included project invalidates fingerprint.
            (low_dir / "deep" / "sub" / "blob.dat").write_bytes(b"123456")
            fp3 = filesystem_candidate_fingerprint(
                low_dir, root_cfg, {}, root / "out", source_cfg_digest="cfg1"
            )
            self.assertNotEqual(fp2, fp3)

    def test_snapshot_header_scan_and_selective_since_query_avoid_full_history_decode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            for idx in range(1, 6):
                snap = build_run_snapshot(
                    run_id=f"run_0{idx}",
                    compiled_at=f"2026-10-31T10:0{idx}:00Z",
                    input_observed_at=f"2026-10-31T10:0{idx}:00Z",
                    health_state="ok",
                    sources=[],
                    observations=[],
                    canonical_projects=[],
                    review_items=[],
                )
                retained_ids = _save_run_snapshot(state_dir, snap, max_runs=4)

            self.assertEqual(retained_ids, ["run_02", "run_03", "run_04", "run_05"])
            hdr = _read_snapshot_header(state_dir / "runs" / "run_05.json")
            self.assertIsNotNone(hdr)
            assert hdr is not None
            self.assertEqual(hdr["run_id"], "run_05")
            self.assertEqual(hdr["sequence"], 5)

            # Also supports compact single-line JSON fallback.
            compact_path = state_dir / "runs" / "run_compact.json"
            compact_path.write_text(
                json.dumps({"run_id": "run_compact", "compiled_at": "2026-10-31T11:00:00Z", "sequence": 9}),
                encoding="utf-8",
            )
            compact_hdr = _read_snapshot_header(compact_path)
            self.assertIsNotNone(compact_hdr)
            assert compact_hdr is not None
            self.assertEqual(compact_hdr["sequence"], 9)
            compact_path.unlink()

            (state_dir / "changes.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "compiler_version": "0.1.0",
                        "run_id": "run_05",
                        "previous_run_id": "run_04",
                        "compiled_at": "2026-10-31T10:05:00Z",
                        "baseline_state": "compared",
                        "available_run_ids": retained_ids,
                        "change_count": 0,
                        "summary": {"by_change_type": {}, "by_entity_kind": {}, "by_materiality": {}, "by_severity": {}},
                        "changes": [],
                    }
                ),
                encoding="utf-8",
            )

            import ledger.changes as changes_mod

            real_loader = changes_mod._load_json_dict
            loaded_files: list[str] = []

            def spy_loader(p: Path) -> dict | None:
                loaded_files.append(p.name)
                return real_loader(p)

            with mock.patch.object(changes_mod, "_load_json_dict", side_effect=spy_loader):
                res_default = changes_payload(state_dir)
                self.assertEqual(res_default["available_run_ids"], retained_ids)
                # Default changes query must load ZERO run_*.json snapshots!
                self.assertEqual([f for f in loaded_files if f.startswith("run_")], [])

                loaded_files.clear()
                res_since = changes_payload(state_dir, since_run_id="run_02")
                self.assertEqual(res_since["previous_run_id"], "run_02")
                # --since query must load ONLY the 2 endpoint snapshots (run_02 and run_05), not run_03/run_04!
                self.assertEqual(
                    sorted(f for f in loaded_files if f.startswith("run_")),
                    ["run_02.json", "run_05.json"],
                )

    def test_historical_id_recovery_falls_back_to_runs_when_canonical_projects_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            obs_local = {
                "schema_version": "1.0.0",
                "observation_id": "obs_111111111111111111111111",
                "source_id": "central-active",
                "snapshot_id": "snap_1",
                "source_resolution": "resolved",
                "observed_at": "2026-10-31T10:00:00Z",
                "project_key": "demo",
                "location": "/tmp/demo",
                "sidecar": {"present": False, "status": "not_present", "declarations": {}, "issues": [], "warnings": []},
                "compat_entry": {
                    "name": "Demo",
                    "project_key": "demo",
                    "source_label": "central-active",
                    "path": "/tmp/demo",
                    "remote_url": "",
                    "canonical_url": "",
                },
            }
            c1, _ = materialize_identity(
                observations=[obs_local],
                decisions_path=None,
                state_dir=state_dir,
                run_id="run_a",
                compiled_at="2026-10-31T10:00:00Z",
            )
            old_pid = c1["projects"][0]["canonical_project_id"]
            snap = build_run_snapshot(
                run_id="run_a",
                compiled_at="2026-10-31T10:00:00Z",
                input_observed_at="2026-10-31T10:00:00Z",
                health_state="ok",
                sources=[],
                observations=[obs_local],
                canonical_projects=c1["projects"],
                review_items=[],
            )
            _save_run_snapshot(state_dir, snap)

            # Delete canonical-projects.json so _enrich_historical_project_ids_from_state must recover from state/runs/.
            (state_dir / "canonical-projects.json").unlink()

            obs_remote = json.loads(json.dumps(obs_local))
            obs_remote["compat_entry"]["remote_url"] = "https://github.com/example/demo.git"
            obs_remote["compat_entry"]["canonical_url"] = "https://github.com/example/demo"
            c2, _ = materialize_identity(
                observations=[obs_remote],
                decisions_path=None,
                state_dir=state_dir,
                run_id="run_b",
                compiled_at="2026-10-31T11:00:00Z",
            )
            new_pid = c2["projects"][0]["canonical_project_id"]
            self.assertNotEqual(old_pid, new_pid)
            res = resolve_payload(state_dir, old_pid)
            self.assertEqual(res["status"], "resolved")
            self.assertEqual(res["canonical_project_id"], new_pid)

    def test_warm_refresh_spawns_zero_subprocesses_for_truncated_repos_and_fast_hits_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            active_root = base / "active"
            big_repo = active_root / "big-truncated"
            _init_git_repo(big_repo, "https://github.com/example/big-truncated.git")
            # Force tree_scan_truncated=True via tree_scan_max_entries=3 while having >3 files.
            for idx in range(5):
                (big_repo / f"f{idx}.py").write_text(f"X_{idx} = {idx}\n", encoding="utf-8")

            inv_dir = base / "inv"
            inv_dir.mkdir()
            inv_jsonl = inv_dir / "inv.jsonl"
            inv_jsonl.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "root_label": "AlphaProj",
                                "path": "AlphaProj/README.md",
                                "name": "README.md",
                                "is_dir": False,
                                "mod_time": "2026-10-31T10:00:00Z",
                            }
                        ),
                        json.dumps(
                            {
                                "root_label": "AlphaProj",
                                "path": "AlphaProj/pyproject.toml",
                                "name": "pyproject.toml",
                                "is_dir": False,
                                "mod_time": "2026-10-31T10:00:00Z",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            pol_json = inv_dir / "policy.json"
            pol_json.write_text(
                json.dumps(
                    {
                        "roots": {
                            "AlphaProj": {
                                "crawl_treatment": "project_discovery",
                                "project_ledger_candidate": True,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            config_path = base / "ledger_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "defaults": {"tree_scan_max_entries": 3},
                        "roots": [
                            {
                                "source_id": "central-active",
                                "label": "central-active",
                                "path": str(active_root),
                                "discovery": "children",
                            },
                            {
                                "source_id": "test-inv",
                                "label": "test-inv",
                                "path": str(inv_dir),
                                "discovery": "inventory_policy",
                                "inventory_jsonl": str(inv_jsonl),
                                "policy_path": str(pol_json),
                                "remote_name": "testdrive",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            decisions_path = base / "decisions.json"
            decisions_path.write_text(
                json.dumps({"schema_version": "1.0.0", "decisions": []}),
                encoding="utf-8",
            )
            out_dir = base / "output"
            state_dir = base / "state"

            def _refresh() -> int:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    return cli.main(
                        [
                            "refresh",
                            "--config",
                            str(config_path),
                            "--output-dir",
                            str(out_dir),
                            "--state-dir",
                            str(state_dir),
                            "--identity-decisions",
                            str(decisions_path),
                            "--no-markdown-mirror",
                        ]
                    )

            # Cold refresh succeeds and marks big-truncated as tree_scan_truncated=True.
            self.assertEqual(_refresh(), 0)
            obs_doc = json.loads((state_dir / "observations.json").read_text(encoding="utf-8"))
            big_obs = [
                o for o in obs_doc["observations"] if o["source_id"] == "central-active"
            ][0]
            self.assertTrue(big_obs["compat_entry"]["tree_scan_truncated"])

            # Warm refresh must spawn ZERO subprocesses (even for truncated repo) AND skip JSONL parsing!
            import ledger.sources.inventory_policy as inv_mod

            with (
                mock.patch(
                    "subprocess.run",
                    side_effect=AssertionError("warm refresh must spawn 0 subprocesses"),
                ),
                mock.patch.object(
                    inv_mod,
                    "load_inventory_jsonl",
                    side_effect=AssertionError("warm refresh must hit fast_inventory_hit"),
                ),
            ):
                self.assertEqual(_refresh(), 0)

            # Updating inv_jsonl invalidates inv_stat_sig and parses new candidate cleanly.
            inv_jsonl.write_text(
                inv_jsonl.read_text(encoding="utf-8")
                + json.dumps(
                    {
                        "root_label": "AlphaProj",
                        "path": "AlphaProj/main.py",
                        "name": "main.py",
                        "is_dir": False,
                        "mod_time": "2026-10-31T12:00:00Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertEqual(_refresh(), 0)


if __name__ == "__main__":
    unittest.main()
