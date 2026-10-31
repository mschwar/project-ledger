"""Tests for GBrain `projects/<slug>.md` reality projection (`ledger/gbrain_pages.py`)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ledger.gbrain import disambiguate_project_gbrain_links


class GBrainPageProjectorTests(unittest.TestCase):
    def test_disambiguate_colliding_gbrain_page_keys_deterministically(self) -> None:
        projects = [
            {
                "canonical_project_id": "prj_caddc1739c010ecf4aed9431",
                "project_key": "the-garden",
                "normalized_remotes": [],
                "gbrain_source_slug": "the-garden",
                "gbrain": {
                    "state": "mapped",
                    "source_slug": "the-garden",
                    "project_slug": "the-garden",
                    "page_key": "projects/the-garden",
                },
            },
            {
                "canonical_project_id": "prj_df71fe0a1dbda091ea1a237b",
                "project_key": "github.com/mschwar/the-garden",
                "normalized_remotes": ["github.com/mschwar/the-garden"],
                "gbrain_source_slug": "active-repos",
                "gbrain": {
                    "state": "mapped",
                    "source_slug": "active-repos",
                    "project_slug": "the-garden",
                    "page_key": "projects/the-garden",
                },
            },
        ]
        disambiguate_project_gbrain_links(projects)
        by_id = {p["canonical_project_id"]: p for p in projects}
        # Remote-anchored active repo retains primary `projects/the-garden`
        self.assertEqual(
            by_id["prj_df71fe0a1dbda091ea1a237b"]["gbrain"]["page_key"],
            "projects/the-garden",
        )
        # Local-only distinct project receives deterministic <=32-char disambiguated slug
        sec_gbrain = by_id["prj_caddc1739c010ecf4aed9431"]["gbrain"]
        self.assertEqual(sec_gbrain["page_key"], "projects/the-garden-caddc1")
        self.assertEqual(sec_gbrain["disambiguated_from"], "projects/the-garden")
        self.assertLessEqual(len(sec_gbrain["project_slug"]), 32)

    def test_sync_gbrain_creates_stubs_preserves_prose_and_timeline_and_is_zero_churn_idempotent(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repos_root = base / "repos"
            p_stub = repos_root / "stub-alpha"
            p_prose = repos_root / "prose-beta"
            p_timeline = repos_root / "timeline-gamma"
            for d in (p_stub, p_prose, p_timeline):
                d.mkdir(parents=True)
                (d / "README.md").write_text(f"# {d.name}\n\nPurpose of {d.name}.\n", encoding="utf-8")

            # Include tricky YAML characters in stub display_name (`#`, `:`, `'`, `<p>`)
            (p_stub / ".project-ledger.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "project_key": "stub-alpha",
                        "display_name": "<p>Alpha: #1 'Core'</p>",
                        "status": "active",
                        "tags": ["infra", "core"],
                    }
                ),
                encoding="utf-8",
            )

            cfg_path = base / "ledger_config.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "roots": [
                            {
                                "id": "central-active",
                                "label": "central-active",
                                "path": str(repos_root),
                                "discovery": "children",
                                "source_class": "live",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            out_dir = base / "output"
            state_dir = base / "state"
            dec_path = base / "decisions.json"
            dec_path.write_text(json.dumps({"schema_version": "1.0.0", "decisions": []}), encoding="utf-8")

            refresh = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "ledger",
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
                ],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(refresh.returncode, 0, refresh.stdout + refresh.stderr)

            # Initialize a fake /home/prime/brain git repo with existing pages AND an unrelated dirty file
            brain_dir = base / "brain"
            (brain_dir / "projects").mkdir(parents=True)
            (brain_dir / "tools").mkdir(parents=True)
            subprocess.run(["git", "-C", str(brain_dir), "init"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(brain_dir), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(brain_dir), "config", "user.name", "Test"], check=True)

            prose_orig = (
                "---\n"
                "type: project\n"
                "title: Prose Beta\n"
                "ingested_via: 'mcp:put_page'\n"
                "---\n\n"
                "# Prose Beta\n\n"
                "Hand-authored architectural notes that MUST survive untouched.\n"
            )
            timeline_orig = (
                "---\n"
                "type: project\n"
                "title: Timeline Gamma\n"
                "---\n\n"
                "# Timeline Gamma\n\n"
                "Curated summary.\n\n"
                "<!-- timeline -->\n\n"
                "## Timeline\n\n"
                "- **2026-09-14** | Initial milestone.\n"
            )
            (brain_dir / "projects" / "prose-beta.md").write_text(prose_orig, encoding="utf-8")
            (brain_dir / "projects" / "timeline-gamma.md").write_text(timeline_orig, encoding="utf-8")
            (brain_dir / "tools" / "herdr.md").write_text("# Herdr\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(brain_dir), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(brain_dir), "commit", "-m", "initial brain"], check=True, capture_output=True)

            # Simulate concurrent unstaged MCP edit in tools/herdr.md that sync-gbrain MUST NOT stage/commit!
            (brain_dir / "tools" / "herdr.md").write_text("# Herdr\n\nConcurrent MCP edit.\n", encoding="utf-8")

            fake_gbrain_log = base / "gbrain_calls.jsonl"
            fake_gbrain = base / "fake-gbrain"
            fake_gbrain.write_text(
                f"#!/usr/bin/env python3\nimport json, sys\nopen({str(fake_gbrain_log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n",
                encoding="utf-8",
            )
            os.chmod(fake_gbrain, 0o755)

            # First sync: 1 created, 2 updated, committed & ingested
            sync1 = subprocess.run(
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
                    "--gbrain-bin",
                    str(fake_gbrain),
                    "--json",
                ],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(sync1.returncode, 0, sync1.stdout + sync1.stderr)
            res1 = json.loads(sync1.stdout)
            self.assertEqual(res1["created_count"], 1)
            self.assertEqual(res1["updated_count"], 2)
            self.assertEqual(res1["unchanged_count"], 0)
            self.assertEqual(res1["ingest"]["status"], "ingested")
            self.assertTrue(res1["ingest"]["git_committed"])
            self.assertTrue(res1["ingest"]["gbrain_synced"])

            # Verify tools/herdr.md was NOT committed by sync-gbrain
            git_status = subprocess.run(
                ["git", "-C", str(brain_dir), "status", "--porcelain"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            self.assertEqual(git_status, "M tools/herdr.md")

            # Verify frontmatter, prose, and timeline ordering
            stub_text = (brain_dir / "projects" / "stub-alpha.md").read_text(encoding="utf-8")
            self.assertIn("title: '<p>Alpha: #1 ''Core''</p>'", stub_text)
            self.assertIn("<!-- BEGIN PROJECT-LEDGER REALITY -->", stub_text)
            self.assertIn("<!-- timeline -->", stub_text)

            prose_after = (brain_dir / "projects" / "prose-beta.md").read_text(encoding="utf-8")
            self.assertIn("ingested_via: 'mcp:put_page'", prose_after)
            self.assertIn("Hand-authored architectural notes that MUST survive untouched.", prose_after)
            self.assertIn("<!-- BEGIN PROJECT-LEDGER REALITY -->", prose_after)

            timeline_after = (brain_dir / "projects" / "timeline-gamma.md").read_text(encoding="utf-8")
            self.assertLess(
                timeline_after.index("<!-- END PROJECT-LEDGER REALITY -->"),
                timeline_after.index("<!-- timeline -->"),
            )
            self.assertIn("- **2026-09-14** | Initial milestone.", timeline_after)

            # Second sync: 100% zero-write idempotent (0 created, 0 updated, 3 unchanged, noop_unchanged)
            sync2 = subprocess.run(
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
                    "--gbrain-bin",
                    str(fake_gbrain),
                    "--json",
                ],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(sync2.returncode, 0, sync2.stdout + sync2.stderr)
            res2 = json.loads(sync2.stdout)
            self.assertEqual(res2["created_count"], 0)
            self.assertEqual(res2["updated_count"], 0)
            self.assertEqual(res2["unchanged_count"], 3)
            self.assertEqual(res2["ingest"]["status"], "noop_unchanged")

            # gbrain sync was invoked exactly once (during sync1, not sync2)
            calls = [json.loads(line) for line in fake_gbrain_log.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(
                calls,
                [[
                    "sync",
                    "--source",
                    "default",
                    "--repo",
                    str(brain_dir.resolve()),
                    "--src-subpath",
                    "projects",
                    "--no-pull",
                    "--no-embed",
                    "--no-extract",
                ]],
            )


if __name__ == "__main__":
    unittest.main()
