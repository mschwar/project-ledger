"""Wave 6 reality seam closure tests: GBrain shadow pruning, push containment, in-process refresh, and watchdog lineage drift."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ledger import cli
from ledger.compat import scan_with_markdown_mirror
from ledger.gbrain_pages import sync_gbrain_pages


def _init_git_repo(repo_dir: Path, remote_url: str | None = None) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main", str(repo_dir)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo_dir), "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    if remote_url:
        subprocess.run(
            ["git", "-C", str(repo_dir), "remote", "add", "origin", remote_url],
            check=True,
            capture_output=True,
        )


class Wave6RealitySeamClosureTests(unittest.TestCase):
    def test_sync_gbrain_prunes_only_ledger_shadows_enforces_brain_cwd_and_rejects_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repos_root = base / "active"
            alpha = repos_root / "alpha-service"
            _init_git_repo(alpha, "https://github.com/example/alpha-service.git")
            (alpha / "README.md").write_text("# Alpha Service\n\nCore service.\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(alpha), "add", "."], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(alpha), "commit", "-m", "init"],
                check=True,
                capture_output=True,
            )

            cfg_path = base / "ledger_config.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "defaults": {"require_git": True},
                        "roots": [
                            {
                                "source_id": "central-active",
                                "label": "central-active",
                                "path": str(repos_root),
                                "discovery": "children",
                                "storage_scope": "local",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            out_dir = base / "output"
            state_dir = base / "state"
            dec_path = base / "decisions.json"
            dec_path.write_text('{"schema_version": "1.0.0", "decisions": []}\n', encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()):
                rc = cli.main(
                    [
                        "refresh",
                        "--no-markdown-mirror",
                        "--config",
                        str(cfg_path),
                        "--output-dir",
                        str(out_dir),
                        "--state-dir",
                        str(state_dir),
                        "--identity-decisions",
                        str(dec_path),
                    ]
                )
            self.assertEqual(rc, 0)

            brain_dir = base / "brain"
            (brain_dir / "projects").mkdir(parents=True)
            _init_git_repo(brain_dir)

            # Negative guardrail: refusing --prune-shadow-source default
            with self.assertRaises(ValueError):
                sync_gbrain_pages(
                    state_dir=state_dir,
                    brain_dir=brain_dir,
                    prune_shadow_sources=["default"],
                )

            calls_log = base / "gbrain_calls.jsonl"
            fake_gbrain = base / "fake-gbrain"
            stub_doc = (
                "---\n"
                "type: project\n"
                "ingested_via: 'project-ledger:sync-gbrain'\n"
                "source_kind: 'project-ledger:capsule'\n"
                "---\n\n"
                "# Stale Shadow\n"
            )
            human_doc = (
                "---\n"
                "type: project\n"
                "ingested_via: 'mcp:put_page'\n"
                "---\n\n"
                "# Keep Me\n"
            )
            fake_gbrain.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                f"log_path = {str(calls_log)!r}\n"
                f"stub_doc = {stub_doc!r}\n"
                f"human_doc = {human_doc!r}\n"
                "args = sys.argv[1:]\n"
                "with open(log_path, 'a', encoding='utf-8') as fh:\n"
                "    fh.write(json.dumps({'cwd': os.getcwd(), 'args': args}) + '\\n')\n"
                "if args[:2] == ['sources', 'list']:\n"
                f"    print(json.dumps({{'sources': [{{'id': 'active-repos', 'local_path': {str(repos_root)!r}}}]}}))\n"
                "    sys.exit(0)\n"
                "if args[:1] == ['list']:\n"
                "    print('projects/alpha-service\\tproject\\t2026-11-10\\tAlpha\\n'\n"
                "          'projects/retired-legacy\\tproject\\t2026-11-10\\tRetired\\n'\n"
                "          'projects/human-notes\\tproject\\t2026-11-10\\tHuman Notes\\n'\n"
                "          'projects/alpha-service/decisions/adr-1\\tproject\\t2026-11-10\\tADR 1')\n"
                "    sys.exit(0)\n"
                "if args[:1] == ['get']:\n"
                "    slug = args[1]\n"
                "    if slug in ('projects/alpha-service', 'projects/retired-legacy'):\n"
                "        print(stub_doc, end='')\n"
                "        sys.exit(0)\n"
                "    if slug == 'projects/human-notes':\n"
                "        print(human_doc, end='')\n"
                "        sys.exit(0)\n"
                "    sys.exit(1)\n"
                "sys.exit(0)\n",
                encoding="utf-8",
            )
            os.chmod(fake_gbrain, 0o755)

            # Run from inside `alpha` checkout to verify gbrain sync/get/delete still bind cwd=brain_dir.
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "ledger",
                    "sync-gbrain",
                    "--state-dir",
                    str(state_dir),
                    "--brain-dir",
                    str(brain_dir),
                    "--ingest",
                    "--prune-shadow-source",
                    "active-repos",
                    "--gbrain-bin",
                    str(fake_gbrain),
                    "--json",
                ],
                cwd=str(alpha),
                env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["ingest"]["status"], "ingested")
            self.assertEqual(
                payload["shadow_prune"]["pruned_shadow_pages"],
                [
                    "active-repos:projects/alpha-service",
                    "active-repos:projects/retired-legacy",
                ],
            )

            recorded = [json.loads(line) for line in calls_log.read_text(encoding="utf-8").splitlines()]
            delete_calls = [c for c in recorded if c["args"][:1] == ["delete"]]
            self.assertEqual(
                [c["args"] for c in delete_calls],
                [
                    ["delete", "projects/alpha-service", "--source-id", "active-repos"],
                    ["delete", "projects/retired-legacy", "--source-id", "active-repos"],
                ],
            )
            for call in recorded:
                if call["args"][:1] in (["sync"], ["get"], ["delete"]):
                    self.assertEqual(Path(call["cwd"]).resolve(), brain_dir.resolve())

    def test_sync_gbrain_push_reconciles_origin_and_contains_offline_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repos_root = base / "active"
            svc = repos_root / "gamma-svc"
            _init_git_repo(svc, "https://github.com/example/gamma-svc.git")
            (svc / "README.md").write_text("# Gamma\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(svc), "add", "."], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(svc), "commit", "-m", "init"], check=True, capture_output=True)

            cfg_path = base / "ledger_config.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "defaults": {"require_git": True},
                        "roots": [
                            {
                                "source_id": "central-active",
                                "label": "central-active",
                                "path": str(repos_root),
                                "discovery": "children",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            state_dir = base / "state"
            out_dir = base / "output"
            dec_path = base / "decisions.json"
            dec_path.write_text('{"schema_version": "1.0.0", "decisions": []}\n', encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                cli.main(
                    [
                        "refresh",
                        "--no-markdown-mirror",
                        "--config",
                        str(cfg_path),
                        "--output-dir",
                        str(out_dir),
                        "--state-dir",
                        str(state_dir),
                        "--identity-decisions",
                        str(dec_path),
                    ]
                )

            bare_brain = base / "brain-origin.git"
            subprocess.run(["git", "init", "--bare", "-b", "main", str(bare_brain)], check=True, capture_output=True)
            brain_dir = base / "brain"
            _init_git_repo(brain_dir, str(bare_brain))
            (brain_dir / "README.md").write_text("# Brain\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(brain_dir), "add", "README.md"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(brain_dir), "commit", "-m", "seed"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(brain_dir), "push", "-u", "origin", "main"], check=True, capture_output=True)

            # First sync without --push leaves commit ahead of origin; second noop sync WITH --push pushes it.
            res1 = sync_gbrain_pages(
                state_dir=state_dir,
                brain_dir=brain_dir,
                ingest=True,
                push=False,
                gbrain_bin="/nonexistent/gbrain",
            )
            self.assertTrue(res1["ingest"]["git_committed"])
            self.assertFalse(res1["ingest"]["git_pushed"])

            res2 = sync_gbrain_pages(
                state_dir=state_dir,
                brain_dir=brain_dir,
                ingest=True,
                push=True,
                gbrain_bin="/nonexistent/gbrain",
            )
            self.assertEqual(res2["ingest"]["status"], "noop_unchanged")
            self.assertTrue(res2["ingest"]["git_pushed"])
            self.assertIsNone(res2["ingest"]["git_push_error"])

            remote_head = subprocess.run(
                ["git", "-C", str(bare_brain), "rev-parse", "refs/heads/main"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            local_head = subprocess.run(
                ["git", "-C", str(brain_dir), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(remote_head, local_head)

            # Law 11 offline containment: broken origin records git_push_error without failing sync-gbrain.
            subprocess.run(
                ["git", "-C", str(brain_dir), "remote", "set-url", "origin", str(base / "missing-origin.git")],
                check=True,
                capture_output=True,
            )
            (svc / "README.md").write_text("# Gamma v2\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(svc), "add", "."], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(svc), "commit", "-m", "v2"], check=True, capture_output=True)
            with contextlib.redirect_stdout(io.StringIO()):
                cli.main(
                    [
                        "refresh",
                        "--no-markdown-mirror",
                        "--config",
                        str(cfg_path),
                        "--output-dir",
                        str(out_dir),
                        "--state-dir",
                        str(state_dir),
                        "--identity-decisions",
                        str(dec_path),
                    ]
                )
            res3 = sync_gbrain_pages(
                state_dir=state_dir,
                brain_dir=brain_dir,
                ingest=True,
                push=True,
                gbrain_bin="/nonexistent/gbrain",
            )
            self.assertTrue(res3["ok"])
            self.assertTrue(res3["ingest"]["git_committed"])
            self.assertFalse(res3["ingest"]["git_pushed"])
            self.assertTrue(bool(res3["ingest"]["git_push_error"]))

    def test_scan_with_markdown_mirror_writes_mirror_in_process_and_manifest_limitations_are_current(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repos_root = base / "active"
            svc = repos_root / "delta-svc"
            _init_git_repo(svc, "https://github.com/example/delta-svc.git")
            (svc / "README.md").write_text("# Delta\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(svc), "add", "."], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(svc), "commit", "-m", "init"], check=True, capture_output=True)

            cfg_path = base / "ledger_config.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "defaults": {"require_git": True},
                        "roots": [
                            {
                                "source_id": "central-active",
                                "label": "central-active",
                                "path": str(repos_root),
                                "discovery": "children",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            out_dir = base / "output"
            mirror_path = base / "docs" / "ledgers" / "projects-ledger.md"
            outputs = scan_with_markdown_mirror(
                config_path=cfg_path,
                output_dir=out_dir,
                mirror_path=mirror_path,
                checkpoint_path=base / "state" / "source-checkpoints.json",
            )
            self.assertTrue(outputs["mirror"].is_file())
            self.assertIn("delta-svc", outputs["mirror"].read_text(encoding="utf-8"))

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.main(
                    [
                        "compile",
                        "--config",
                        str(cfg_path),
                        "--input-json",
                        str(outputs["json"]),
                        "--state-dir",
                        str(base / "state"),
                        "--identity-decisions",
                        str(base / "decisions.json") if False else "/central/repos/active/project-ledger/registry/identity-decisions.json",
                        "--json",
                    ]
                )
            self.assertEqual(rc, 0)
            manifest = json.loads(buf.getvalue())
            joined_limits = " ".join(manifest["limitations"])
            self.assertNotIn("until the later current-state resolver lands", joined_limits)
            self.assertIn("machina", joined_limits)


if __name__ == "__main__":
    unittest.main()
