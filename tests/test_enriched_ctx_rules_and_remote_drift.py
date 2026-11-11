from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from ledger import cli, rules
from ledger.capsules import ingest_project_ctx_capsule, show_payload
from ledger.sources.git import inspect_git_worktree_state
from ledger.structural_ctx import extract_structural_ctx
from tests.test_state_schemas import validate_json_schema


def _init_git_repo(repo_dir: Path, remote_url: str) -> None:
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
    subprocess.run(
        ["git", "-C", str(repo_dir), "remote", "add", "origin", remote_url],
        check=True,
        capture_output=True,
    )


class EnrichedCtxRulesAndRemoteDriftTests(unittest.TestCase):
    def test_enriched_ctx_operational_surfaces_dependency_cycles_and_capsule_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "svc-repo"
            _init_git_repo(repo, "https://github.com/example/svc-repo.git")
            (repo / "AGENTS.md").write_text("# AGENTS\n\nGovernance contract.\n", encoding="utf-8")
            (repo / "pyproject.toml").write_text(
                '[project]\nname = "svc-repo"\nversion = "0.1.0"\n'
                '[project.scripts]\nsvc = "svc.cli:main"\n',
                encoding="utf-8",
            )
            pkg = repo / "svc"
            pkg.mkdir()
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            (pkg / "a.py").write_text(
                "import os\nimport svc.b\n"
                "def get_mode() -> str:\n"
                '    return os.getenv("SVC_RUNTIME_MODE", "prod")\n',
                encoding="utf-8",
            )
            (pkg / "b.py").write_text(
                "import svc.a\n"
                "def helper() -> str:\n"
                "    return svc.a.get_mode()\n",
                encoding="utf-8",
            )
            (pkg / "cli.py").write_text(
                "import argparse\nimport svc.a\n"
                "def main() -> None:\n"
                "    p = argparse.ArgumentParser(prog='svc')\n"
                "    sub = p.add_subparsers(dest='cmd')\n"
                "    sub.add_parser('deploy')\n"
                "    sub.add_parser('status')\n",
                encoding="utf-8",
            )
            (pkg / "api_server.py").write_text(
                "class App:\n"
                "    def get(self, p): return lambda f: f\n"
                "app = App()\n"
                '@app.get("/v1/health")\n'
                "def health(): return {'ok': True}\n"
                '_TOOLS = [{"name": "svc_inspect", "description": "Inspect"}]\n'
                '_STATIC_RESOURCES = [{"uri": "svc://manifest", "mimeType": "application/json"}]\n',
                encoding="utf-8",
            )

            extracted = extract_structural_ctx(repo)
            self.assertIsNotNone(extracted)
            assert extracted is not None
            kinds = {s["kind"] for s in extracted["operational_surfaces"]}
            self.assertTrue({"cli_command", "http_route", "mcp_tool", "mcp_resource", "env_var"} <= kinds)
            self.assertGreaterEqual(extracted["operational_surface_count"], 5)
            self.assertIn("internal_edge_count", extracted["dependency_summary"])
            self.assertIn("cycle_count", extracted["dependency_summary"])
            self.assertTrue(all("arch_role" in sub for sub in extracted["subsystems"]))

            # Verify `.ctx/CONTEXT-0.md` ingestion for deterministic mode
            ctx_dir = repo / ".ctx"
            ctx_dir.mkdir()
            (ctx_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "mode": "deterministic",
                        "structural_hash": "sha256:1111111111111111",
                        "boundary_doc_hash": "sha256:2222222222222222",
                    }
                ),
                encoding="utf-8",
            )
            (ctx_dir / "CONTEXT-0.md").write_text(
                "---\ngenerated: '2026-11-01T00:00:00Z'\nfiles: 6\n---\n"
                "# svc-repo — Repository Architecture & Invariants\n\n"
                "## Authority & Architecture Boundaries\n"
                "### Governance & Architecture Docs\n"
                "- **`AGENTS.md`** — AGENTS\n\n"
                "### Entrypoints & Operational Surfaces\n"
                "- `pyproject.toml [project.scripts]: svc = svc.cli:main`\n"
                "- **CLI:** `svc deploy` (`svc/cli.py`), `svc status` (`svc/cli.py`)\n"
                "- **HTTP:** `GET /v1/health` (`svc/api_server.py`)\n"
                "- **MCP Tools:** `svc_inspect` (`svc/api_server.py`)\n"
                "- **Env Config:** `SVC_RUNTIME_MODE`\n\n"
                "## Subsystem Map\n"
                "| Subsystem | Role | Files | Languages | Key Exports |\n"
                "|-----------|------|------:|-----------|-------------|\n"
                "| `svc` | `entrypoint_interface` | 5 | Python | `cli.py:fn main` |\n",
                encoding="utf-8",
            )
            ingested = ingest_project_ctx_capsule(
                [
                    {
                        "observation_id": "obs_1",
                        "source_class": "live",
                        "source_status": "available",
                        "location": str(repo),
                        "score_components": {"accessible_worktree": True},
                    }
                ],
                observations_by_id={},
            )
            self.assertEqual(ingested["source_format"], "tier2_ctx_dir")
            self.assertEqual(ingested["subsystems"][0]["arch_role"], "entrypoint_interface")
            self.assertGreaterEqual(ingested["operational_surface_count"], 4)

            for child in list(ctx_dir.iterdir()):
                child.unlink()
            ctx_dir.rmdir()
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-m", "svc"], check=True, capture_output=True)

            repos_parent = Path(tmp) / "repos_root"
            repos_parent.mkdir(parents=True, exist_ok=True)
            repo_moved = repos_parent / "svc-repo"
            repo.rename(repo_moved)

            ws = Path(tmp) / "ws_schema"
            cfg_path = ws / "ledger_config.json"
            ws.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(
                json.dumps(
                    {
                        "roots": [
                            {
                                "source_id": "local-svc",
                                "label": "local-svc",
                                "path": str(repos_parent),
                                "discovery": "children",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            state_dir = ws / "state"
            out_dir = ws / "output"
            dec_path = ws / "registry" / "identity-decisions.json"
            dec_path.parent.mkdir(parents=True, exist_ok=True)
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
                        "--json",
                    ]
                )
            self.assertEqual(rc, 0)
            schema_path = Path(__file__).resolve().parents[1] / "schemas" / "project-capsule.schema.json"
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            capsules = list((state_dir / "projects").glob("*.json"))
            self.assertEqual(len(capsules), 1)
            raw_bytes = capsules[0].read_bytes()
            self.assertLessEqual(len(raw_bytes), 16384)
            capsule_doc = json.loads(raw_bytes.decode("utf-8"))
            self.assertEqual(validate_json_schema(capsule_doc, schema), [])
            self.assertGreaterEqual(capsule_doc["ctx"]["operational_surface_count"], 5)
            self.assertIn("dependency_summary", capsule_doc["ctx"])

    def test_root_context_md_does_not_lobotomize_structural_ctx_extraction(self) -> None:
        """Regression test: repos containing a root CONTEXT.md must still run extract_structural_ctx."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "ctx-md-repo"
            _init_git_repo(repo, "https://github.com/example/ctx-md-repo.git")
            (repo / "CONTEXT.md").write_text(
                "---\n"
                "generated: '2026-10-30T00:00:00Z'\n"
                "generator: ctx/1.1.0\n"
                "files: 42\n"
                "dirs: 5\n"
                "---\n"
                "# Legacy Manifest\n\n"
                "Legacy Tier-1 manifest that previously short-circuited structural extraction.\n",
                encoding="utf-8",
            )
            (repo / "pyproject.toml").write_text(
                '[project]\nname = "ctx-md-repo"\nversion = "0.2.0"\n'
                '[project.scripts]\nctxmd = "ctxmd.cli:main"\n',
                encoding="utf-8",
            )
            pkg = repo / "ctxmd"
            pkg.mkdir()
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            (pkg / "cli.py").write_text(
                "import argparse\n"
                "def main() -> None:\n"
                "    p = argparse.ArgumentParser(prog='ctxmd')\n"
                "    sub = p.add_subparsers(dest='cmd')\n"
                "    sub.add_parser('inspect')\n",
                encoding="utf-8",
            )

            ingested = ingest_project_ctx_capsule(
                [
                    {
                        "observation_id": "obs_ctx_md",
                        "source_class": "live",
                        "source_status": "available",
                        "location": str(repo),
                        "score_components": {"accessible_worktree": True},
                    }
                ],
                observations_by_id={},
            )
            self.assertEqual(ingested["state"], "available")
            self.assertEqual(ingested["source_format"], "compiled_structural_scan")
            self.assertIn("python", {k.lower() for k in ingested["languages"]})
            self.assertTrue(any("ctxmd" in ep for ep in ingested["entrypoints"]))
            self.assertGreaterEqual(len(ingested["operational_surfaces"]), 1)
            self.assertGreaterEqual(len(ingested["subsystems"]), 1)

    def test_deterministic_lifecycle_and_urgency_rules_preserve_law3_and_law5(self) -> None:
        # Active dirty + handoff
        self.assertEqual(
            rules.classify_project_lifecycle(
                {
                    "source_classes": ["live"],
                    "days_since_last_commit": 0.4,
                    "dirty_worktree": True,
                    "ahead_count": 2,
                    "has_machina_handoff": True,
                    "total_files": 94,
                    "subsystem_count": 8,
                    "operational_surface_count": 19,
                    "has_governance_docs": True,
                    "open_review_count": 0,
                }
            )[0],
            "active_development",
        )
        # Maintained stable library
        self.assertEqual(
            rules.classify_project_lifecycle(
                {
                    "source_classes": ["live", "mirror"],
                    "days_since_last_commit": 48.0,
                    "dirty_worktree": False,
                    "ahead_count": 0,
                    "has_machina_handoff": False,
                    "total_files": 62,
                    "subsystem_count": 5,
                    "operational_surface_count": 11,
                    "has_governance_docs": True,
                    "open_review_count": 0,
                }
            )[0],
            "maintained_stable",
        )
        # Ephemeral experiment
        self.assertEqual(
            rules.classify_project_lifecycle(
                {
                    "source_classes": ["live"],
                    "days_since_last_commit": 65.0,
                    "dirty_worktree": False,
                    "ahead_count": 0,
                    "has_machina_handoff": False,
                    "total_files": 2,
                    "subsystem_count": 1,
                    "operational_surface_count": 0,
                    "has_governance_docs": False,
                    "open_review_count": 0,
                }
            )[0],
            "ephemeral_experiment",
        )
        # Law 5: absent ctx (`total_files = None`) never masquerades as ephemeral_experiment
        absent_ctx_capsule = {
            "observations": [{"source_class": "live"}],
            "git_facts": {"head_commit_at": None, "warning_codes": []},
            "freshness": {"content_as_of": None},
            "current_state": {"lifecycle_state": "paused", "machina_continuity": {"state": "not_present"}},
            "ctx": {"state": "absent", "total_files": None, "subsystems": [], "operational_surfaces": []},
            "review": {"open_review_count": 0},
            "preferred_location": {"state": "resolved"},
        }
        posture = rules.infer_capsule_posture(absent_ctx_capsule)
        self.assertEqual(posture["lifecycle_posture"], "dormant_archive")
        self.assertEqual(absent_ctx_capsule["current_state"]["lifecycle_state"], "paused")

        # Capsule dict authority_boundaries (`kind: "authority"` / `"architecture"`) set has_governance_docs
        governed_capsule = {
            "observations": [{"source_class": "live"}],
            "git_facts": {"head_commit_at": "2026-09-01T00:00:00Z", "warning_codes": []},
            "freshness": {"content_as_of": "2026-09-01T00:00:00Z"},
            "current_state": {"lifecycle_state": None, "machina_continuity": {"state": "not_present"}},
            "ctx": {
                "state": "available",
                "total_files": 4,
                "subsystems": [],
                "operational_surfaces": [],
                "authority_boundaries": [{"kind": "authority", "path": "docs/GOVERNANCE.md"}],
            },
            "review": {"open_review_count": 0},
            "preferred_location": {"state": "resolved"},
        }
        gov_state = rules.capsule_to_lifecycle_state(
            governed_capsule,
            reference_now=rules.dt.datetime(2026, 11, 11, tzinfo=rules.dt.timezone.utc),
        )
        self.assertTrue(gov_state["has_governance_docs"])
        self.assertEqual(
            rules.infer_capsule_posture(
                governed_capsule,
                reference_now=rules.dt.datetime(2026, 11, 11, tzinfo=rules.dt.timezone.utc),
            )["lifecycle_posture"],
            "maintained_stable",
        )

        # Urgency levels 0..3 including GIT_REMOTE_AHEAD
        self.assertEqual(
            rules.classify_attention_urgency(
                {
                    "git_drift_codes": [],
                    "dirty_checkout_count": 0,
                    "ahead_total": 0,
                    "behind_total": 0,
                    "diverged": False,
                    "open_review_count": 0,
                    "high_severity_review_count": 0,
                    "source_health_state": "healthy",
                }
            )[0],
            0,
        )
        self.assertEqual(
            rules.classify_attention_urgency(
                {
                    "git_drift_codes": ["GIT_REMOTE_AHEAD"],
                    "dirty_checkout_count": 0,
                    "ahead_total": 0,
                    "behind_total": 0,
                    "diverged": False,
                    "open_review_count": 0,
                    "high_severity_review_count": 0,
                    "source_health_state": "healthy",
                }
            )[0],
            1,
        )
        self.assertEqual(
            rules.classify_attention_urgency(
                {
                    "git_drift_codes": ["GIT_DIRTY_WORKTREE"],
                    "dirty_checkout_count": 1,
                    "ahead_total": 0,
                    "behind_total": 0,
                    "diverged": False,
                    "open_review_count": 0,
                    "high_severity_review_count": 0,
                    "source_health_state": "healthy",
                }
            )[0],
            2,
        )
        self.assertEqual(
            rules.classify_attention_urgency(
                {
                    "git_drift_codes": ["GIT_REMOTE_AHEAD", "GIT_AHEAD_OF_UPSTREAM"],
                    "dirty_checkout_count": 0,
                    "ahead_total": 1,
                    "behind_total": 0,
                    "diverged": True,
                    "open_review_count": 0,
                    "high_severity_review_count": 0,
                    "source_health_state": "healthy",
                }
            )[0],
            3,
        )
        # 400-digit integer rejected cleanly without CPU spin
        with self.assertRaises(ValueError):
            rules.classify_attention_urgency({"ahead_total": 10**400})

    def test_hermetic_bare_remote_ahead_sensing_and_cli_check_remote_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bare_origin = root / "origin.git"
            subprocess.run(["git", "init", "--bare", "-b", "main", str(bare_origin)], check=True, capture_output=True)

            # Seed bare origin via pusher clone
            pusher = root / "pusher"
            subprocess.run(["git", "clone", str(bare_origin), str(pusher)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(pusher), "config", "user.email", "p@example.com"], check=True)
            subprocess.run(["git", "-C", str(pusher), "config", "user.name", "Pusher"], check=True)
            (pusher / "README.md").write_text("# Remote Drift Demo\n\nInitial commit.\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(pusher), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(pusher), "commit", "-m", "c1"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(pusher), "push", "-u", "origin", "main"], check=True, capture_output=True)

            # Clone active checkout (in sync at c1)
            active_root = root / "active"
            active_root.mkdir()
            local_clone = active_root / "demo-repo"
            subprocess.run(["git", "clone", str(bare_origin), str(local_clone)], check=True, capture_output=True)

            # Advance bare origin by 2 commits from pusher WITHOUT fetching in local_clone
            (pusher / "README.md").write_text("# Remote Drift Demo\n\nCommit 2.\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(pusher), "commit", "-am", "c2"], check=True, capture_output=True)
            (pusher / "README.md").write_text("# Remote Drift Demo\n\nCommit 3.\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(pusher), "commit", "-am", "c3"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(pusher), "push", "origin", "main"], check=True, capture_output=True)
            upstream_sha = subprocess.check_output(
                ["git", "-C", str(pusher), "rev-parse", "HEAD"], text=True
            ).strip()

            # 1. Offline mode (default): sees local tracking ref at c1 -> state == "clean"
            offline_drift = inspect_git_worktree_state(local_clone)
            self.assertEqual(offline_drift["state"], "clean")
            self.assertEqual(offline_drift["warning_codes"], [])

            # 2. Remote-enabled mode: `git ls-remote` catches unfetched c3 -> state == "remote_ahead"
            remote_drift = inspect_git_worktree_state(local_clone, check_remote=True, remote_timeout=3.0)
            self.assertEqual(remote_drift["state"], "remote_ahead")
            self.assertIn("GIT_REMOTE_AHEAD", remote_drift["warning_codes"])
            self.assertEqual(remote_drift["remote_head_commit"], upstream_sha)

            # 3. End-to-end `ledger refresh` vs `ledger refresh --check-remote` + capsule schema conformance
            ws = root / "ws"
            ws.mkdir()
            cfg_path = ws / "ledger_config.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "roots": [
                            {
                                "source_id": "central-active",
                                "label": "central-active",
                                "path": str(active_root),
                                "discovery": "children",
                                "source_class": "live",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            out_dir = ws / "output"
            state_dir = ws / "state"
            dec_path = ws / "decisions.json"
            dec_path.write_text('{"schema_version": "1.0.0", "decisions": []}\n', encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()):
                rc_offline = cli.main(
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
                        "--json",
                    ]
                )
            self.assertEqual(rc_offline, 0)
            cap_offline = show_payload(state_dir, str(local_clone))
            self.assertEqual(cap_offline["status"], "resolved")
            self.assertEqual(cap_offline["git_facts"]["drift_state"], "clean")

            with contextlib.redirect_stdout(io.StringIO()):
                rc_remote = cli.main(
                    [
                        "refresh",
                        "--no-markdown-mirror",
                        "--check-remote",
                        "--remote-timeout",
                        "3.0",
                        "--config",
                        str(cfg_path),
                        "--output-dir",
                        str(out_dir),
                        "--state-dir",
                        str(state_dir),
                        "--identity-decisions",
                        str(dec_path),
                        "--json",
                    ]
                )
            self.assertEqual(rc_remote, 0)
            cap_remote = show_payload(state_dir, str(local_clone))
            self.assertEqual(cap_remote["status"], "resolved")
            self.assertEqual(cap_remote["git_facts"]["drift_state"], "remote_ahead")
            self.assertIn("GIT_REMOTE_AHEAD", cap_remote["git_facts"]["warning_codes"])
            self.assertEqual(cap_remote["git_facts"]["remote_head_commit"], upstream_sha)
            self.assertEqual(cap_remote["posture"]["attention_urgency"], 1)

            schema_path = Path(__file__).resolve().parents[1] / "schemas" / "project-capsule.schema.json"
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            capsule_files = list((state_dir / "projects").glob("*.json"))
            self.assertEqual(len(capsule_files), 1)
            self.assertEqual(
                validate_json_schema(json.loads(capsule_files[0].read_text(encoding="utf-8")), schema),
                [],
            )

            # 4. Unreachable remote fails gracefully without crashing
            broken_repo = root / "broken-remote"
            _init_git_repo(broken_repo, str(root / "nonexistent-origin.git"))
            (broken_repo / "README.md").write_text("# Broken\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(broken_repo), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(broken_repo), "commit", "-m", "init"], check=True, capture_output=True)
            broken_drift = inspect_git_worktree_state(broken_repo, check_remote=True, remote_timeout=1.0)
            self.assertEqual(broken_drift["state"], "no_upstream")
            self.assertNotIn("GIT_REMOTE_AHEAD", broken_drift["warning_codes"])


if __name__ == "__main__":
    unittest.main()
