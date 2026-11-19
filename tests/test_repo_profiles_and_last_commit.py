"""Tests for repository profiles (`goal`, `what_it_does`, `description`, `primary_language`) and `last_commit`."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ledger import cli
from ledger.capsules import CAPSULE_MAX_BYTES, show_payload
from ledger.catalog import is_noisy_summary_text, load_project_catalog
from ledger.explain import explain_payload
from ledger.gbrain_pages import sync_gbrain_pages
from ledger.identity import resolve_payload
from ledger.portfolio import portfolio_payload
from tests.test_state_schemas import SCHEMAS_DIR, validate_json_schema

REPO_ROOT = Path(__file__).resolve().parents[1]


def _init_git_repo(
    repo_dir: Path,
    remote_url: str,
    *,
    author_name: str = "Jane Builder",
    commit_msg: str = "feat: ship initial substrate",
) -> str:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(repo_dir), "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", author_name], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.email", "jane@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "remote", "add", "origin", remote_url], check=True)
    if not any(repo_dir.iterdir()):
        (repo_dir / "README.md").write_text("# Placeholder\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_dir), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "commit", "-q", "-m", commit_msg], check=True)
    proc = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


class RepoProfilesAndLastCommitTests(unittest.TestCase):
    def test_committed_project_catalog_has_clean_complete_profiles(self) -> None:
        catalog_path = REPO_ROOT / "registry" / "project-catalog.json"
        loaded = load_project_catalog(catalog_path, required=True)
        self.assertGreaterEqual(len(loaded["projects"]), 109)
        for rec in loaded["projects"]:
            for key in (
                "canonical_project_id",
                "project_key_hint",
                "display_name",
                "description",
                "goal",
                "what_it_does",
                "primary_language",
            ):
                val = rec.get(key)
                self.assertIsInstance(val, str, f"{rec['project_key_hint']}: {key}")
                self.assertTrue(val.strip(), f"{rec['project_key_hint']}: empty {key}")
            self.assertFalse(
                is_noisy_summary_text(rec["description"]),
                f"Noisy description on {rec['project_key_hint']}: {rec['description']!r}",
            )
            self.assertFalse(
                is_noisy_summary_text(rec["goal"]),
                f"Noisy goal on {rec['project_key_hint']}: {rec['goal']!r}",
            )
            self.assertFalse(
                is_noisy_summary_text(rec["what_it_does"]),
                f"Noisy what_it_does on {rec['project_key_hint']}: {rec['what_it_does']!r}",
            )

    def test_catalog_sidecar_precedence_last_commit_gbrain_and_warm_cache(self) -> None:
        capsule_schema = json.loads((SCHEMAS_DIR / "project-capsule.schema.json").read_text(encoding="utf-8"))
        portfolio_schema = json.loads((SCHEMAS_DIR / "portfolio-index.schema.json").read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repos = base / "repos"
            reg = base / "registry"
            reg.mkdir(parents=True)

            # Repo 1: Noisy README ("Live:") rescued by registry/project-catalog.json
            noisy_repo = repos / "noisy-home"
            noisy_repo.mkdir(parents=True)
            (noisy_repo / "README.md").write_text("# > ## Retired Banner\n\nLive:\n", encoding="utf-8")
            (noisy_repo / "app.js").write_text("export function render() { return 1; }\n", encoding="utf-8")
            sha_noisy = _init_git_repo(
                noisy_repo,
                "git@github.com:mschwar/noisy-home.git",
                author_name="Matt Schwartz",
                commit_msg="fix(home): deterministic daily selection",
            )

            # Repo 2: Enriched repo-local .project-ledger.json overrides catalog
            sidecar_repo = repos / "sidecar-override"
            sidecar_repo.mkdir(parents=True)
            (sidecar_repo / "README.md").write_text("# Sidecar Repo\n\nFallback text.\n", encoding="utf-8")
            (sidecar_repo / "main.py").write_text("def cli():\n    return 0\n", encoding="utf-8")
            (sidecar_repo / ".project-ledger.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "display_name": "Sidecar Authoritative Name",
                        "description": "Sidecar concise one-liner.",
                        "goal": "Sidecar explicit goal explaining why the repository exists.",
                        "what_it_does": "Sidecar explicit mechanism describing inputs and outputs.",
                        "primary_language": "Python",
                        "status": "active",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            sha_sidecar = _init_git_repo(
                sidecar_repo,
                "git@github.com:mschwar/sidecar-override.git",
                author_name="Operator Bot",
                commit_msg="feat(sidecar): declare goal and what_it_does",
            )

            # Repo 3: Uncurated brand-new repo auto-extracted from pyproject.toml + README sections + GitHub sync
            auto_repo = repos / "auto-extracted"
            auto_repo.mkdir(parents=True)
            (auto_repo / "pyproject.toml").write_text(
                '[project]\nname = "auto-extracted"\nversion = "0.1.0"\n'
                'description = "Manifest-backed CLI for telemetry validation."\n',
                encoding="utf-8",
            )
            (auto_repo / "README.md").write_text(
                "# Auto Extracted\n\n"
                "## Goal\n\n"
                "Catch malformed telemetry payloads before they pollute downstream storage.\n\n"
                "## What It Does\n\n"
                "Reads JSONL events from stdin, validates Draft 2020-12 schemas, and emits exit codes.\n",
                encoding="utf-8",
            )
            (auto_repo / "validator.py").write_text("def check():\n    return True\n", encoding="utf-8")
            sha_auto = _init_git_repo(
                auto_repo,
                "git@github.com:mschwar/auto-extracted.git",
                author_name="Auto Author",
                commit_msg="chore: initial validator CLI",
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
                                "path": str(repos),
                                "discovery": "children",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            dec_path = reg / "identity-decisions.json"
            dec_path.write_text(json.dumps({"schema_version": "1.0.0", "decisions": []}), encoding="utf-8")
            out_dir = base / "output"
            state_dir = base / "state"

            # Initial refresh to obtain canonical_project_id values
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
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
                    ),
                    0,
                )

            res_noisy = resolve_payload(state_dir, "mschwar/noisy-home")
            res_sidecar = resolve_payload(state_dir, "mschwar/sidecar-override")
            self.assertEqual(res_noisy["status"], "resolved")
            self.assertEqual(res_sidecar["status"], "resolved")

            # Author sibling registry/project-catalog.json covering Repo 1 and Repo 2 (not Repo 3)
            cat_path = reg / "project-catalog.json"
            cat_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "projects": [
                            {
                                "canonical_project_id": res_noisy["canonical_project_id"],
                                "project_key_hint": "github.com/mschwar/noisy-home",
                                "display_name": "Clean Curated Homepage",
                                "description": "Curated daily homepage serving static passages.",
                                "goal": "Give readers a calm, zero-tracking daily reading start page.",
                                "what_it_does": "Selects one static passage per day in client-side JavaScript.",
                                "primary_language": "JavaScript",
                            },
                            {
                                "canonical_project_id": res_sidecar["canonical_project_id"],
                                "project_key_hint": "github.com/mschwar/sidecar-override",
                                "display_name": "Catalog Fallback Name",
                                "description": "Catalog description that should yield to enriched sidecar.",
                                "goal": "Catalog goal that should yield to enriched sidecar.",
                                "what_it_does": "Catalog what_it_does that should yield to enriched sidecar.",
                                "primary_language": "Shell",
                            },
                        ],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            # Re-refresh with sibling catalog active
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
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
                    ),
                    0,
                )

            # 1. Verify Repo 1 uses catalog values & keeps both old and new display_name resolvable
            cap_noisy = show_payload(state_dir, "Clean Curated Homepage")
            self.assertEqual(cap_noisy["status"], "resolved")
            self.assertEqual(cap_noisy["display_name"], "Clean Curated Homepage")
            self.assertEqual(cap_noisy["description"], "Curated daily homepage serving static passages.")
            self.assertEqual(cap_noisy["purpose"], cap_noisy["description"])
            self.assertEqual(cap_noisy["goal"], "Give readers a calm, zero-tracking daily reading start page.")
            self.assertEqual(
                cap_noisy["what_it_does"],
                "Selects one static passage per day in client-side JavaScript.",
            )
            self.assertEqual(cap_noisy["primary_language"], "JavaScript")
            self.assertEqual(
                cap_noisy["resolved_fields"]["goal"]["provenance"][0]["reason_code"],
                "PROJECT_CATALOG_DECLARATION",
            )
            self.assertEqual(cap_noisy["last_commit"]["state"], "known")
            self.assertEqual(cap_noisy["last_commit"]["sha"], sha_noisy)
            self.assertEqual(cap_noisy["last_commit"]["short_sha"], sha_noisy[:12])
            self.assertEqual(cap_noisy["last_commit"]["subject"], "fix(home): deterministic daily selection")
            self.assertEqual(cap_noisy["last_commit"]["author"], "Matt Schwartz")
            self.assertEqual(cap_noisy["last_commit"]["branch"], "main")

            # 2. Verify Repo 2 uses enriched sidecar override ahead of catalog
            cap_sc = show_payload(state_dir, "mschwar/sidecar-override")
            self.assertEqual(cap_sc["display_name"], "Sidecar Authoritative Name")
            self.assertEqual(cap_sc["description"], "Sidecar concise one-liner.")
            self.assertEqual(cap_sc["goal"], "Sidecar explicit goal explaining why the repository exists.")
            self.assertEqual(
                cap_sc["what_it_does"],
                "Sidecar explicit mechanism describing inputs and outputs.",
            )
            self.assertEqual(cap_sc["primary_language"], "Python")
            self.assertEqual(
                cap_sc["resolved_fields"]["goal"]["provenance"][0]["reason_code"],
                "SIDECAR_DECLARATION",
            )
            self.assertEqual(cap_sc["last_commit"]["sha"], sha_sidecar)
            self.assertEqual(cap_sc["last_commit"]["subject"], "feat(sidecar): declare goal and what_it_does")

            # 3. Verify Repo 3 extracts manifest description + README Goal / What It Does + Python language
            cap_auto = show_payload(state_dir, "mschwar/auto-extracted")
            self.assertEqual(cap_auto["description"], "Manifest-backed CLI for telemetry validation.")
            self.assertEqual(
                cap_auto["goal"],
                "Catch malformed telemetry payloads before they pollute downstream storage.",
            )
            self.assertEqual(
                cap_auto["what_it_does"],
                "Reads JSONL events from stdin, validates Draft 2020-12 schemas, and emits exit codes.",
            )
            self.assertEqual(cap_auto["primary_language"], "Python")
            self.assertEqual(cap_auto["last_commit"]["sha"], sha_auto)
            self.assertEqual(cap_auto["last_commit"]["subject"], "chore: initial validator CLI")

            # 4. Validate JSON schemas & byte budgets on all capsules and portfolio-index.json
            for cap_file in (state_dir / "projects").glob("prj_*.json"):
                raw = cap_file.read_bytes()
                self.assertLessEqual(len(raw), CAPSULE_MAX_BYTES)
                self.assertEqual(validate_json_schema(json.loads(raw), capsule_schema), [])

            port = portfolio_payload(state_dir)
            raw_port = json.loads((state_dir / "portfolio-index.json").read_text(encoding="utf-8"))
            self.assertEqual(validate_json_schema(raw_port, portfolio_schema), [])
            by_key = {p["project_key"]: p for p in port["projects"]}
            self.assertEqual(
                by_key["github.com/mschwar/noisy-home"]["goal"],
                "Give readers a calm, zero-tracking daily reading start page.",
            )
            self.assertEqual(
                by_key["github.com/mschwar/noisy-home"]["last_commit"]["subject"],
                "fix(home): deterministic daily selection",
            )

            # 5. Verify explain_payload, canonical-projects.md, CLI show, and GBrain Reality blocks
            exp = explain_payload(state_dir, "mschwar/noisy-home", decisions_path=dec_path)
            self.assertEqual(exp["goal"], "Give readers a calm, zero-tracking daily reading start page.")
            self.assertEqual(exp["last_commit"]["short_sha"], sha_noisy[:12])

            md_text = (state_dir / "canonical-projects.md").read_text(encoding="utf-8")
            self.assertIn("## Repository Profiles", md_text)
            self.assertIn("- **Goal (Why It Exists)**: Give readers a calm, zero-tracking daily reading start page.", md_text)
            self.assertIn("fix(home): deterministic daily selection", md_text)

            show_buf = io.StringIO()
            with contextlib.redirect_stdout(show_buf):
                self.assertEqual(cli.main(["show", "--state-dir", str(state_dir), "mschwar/noisy-home"]), 0)
            show_out = show_buf.getvalue()
            self.assertIn("goal: Give readers a calm, zero-tracking daily reading start page.", show_out)
            self.assertIn("what it does: Selects one static passage per day", show_out)
            self.assertIn("fix(home): deterministic daily selection", show_out)

            brain_dir = base / "brain"
            sync_res1 = sync_gbrain_pages(state_dir=state_dir, brain_dir=brain_dir)
            self.assertEqual(sync_res1["created_count"], 3)
            gbrain_md = (brain_dir / "projects" / "noisy-home.md").read_text(encoding="utf-8")
            self.assertIn("- **Description:** Curated daily homepage serving static passages.", gbrain_md)
            self.assertIn("- **Goal:** Give readers a calm, zero-tracking daily reading start page.", gbrain_md)
            self.assertIn("- **What It Does:** Selects one static passage per day in client-side JavaScript.", gbrain_md)
            self.assertIn("- **Primary Language:** `JavaScript`", gbrain_md)
            self.assertIn("fix(home): deterministic daily selection (`Matt Schwartz`)", gbrain_md)

            sync_res2 = sync_gbrain_pages(state_dir=state_dir, brain_dir=brain_dir)
            self.assertEqual(sync_res2["updated_count"], 0)
            self.assertEqual(sync_res2["unchanged_count"], 3)

            # 6. Warm refresh reuses cached git_drift (including head_commit_subject) with zero git drift probes
            import ledger.compiler as compiler_mod

            with mock.patch.object(
                compiler_mod,
                "resolve_observation_git_drift",
                side_effect=AssertionError("warm refresh must reuse cached git_drift"),
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(
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
                        ),
                        0,
                    )


if __name__ == "__main__":
    unittest.main()
