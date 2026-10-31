"""Verification tests for Strategic Unlocks U1 (Machina continuity projection) and U3 (immutable project lineage)."""

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
from ledger.capsules import load_project_capsule, show_payload
from ledger.explain import explain_payload
from ledger.gbrain_pages import render_reality_block, sync_gbrain_pages
from ledger.ids import stable_id
from ledger.identity import compile_identity, load_canonical_projects, resolve_payload
from ledger.lineage import (
    build_synced_lineage,
    lineage_status_payload,
    load_project_lineage,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _init_git(repo_dir: Path, remote_url: str | None = None) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True, capture_output=True)
    if remote_url:
        subprocess.run(
            ["git", "remote", "add", "origin", remote_url],
            cwd=repo_dir,
            check=True,
            capture_output=True,
        )
    (repo_dir / "README.md").write_text(f"# {repo_dir.name}\n\nTest repo.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True, capture_output=True)


def _run_cli(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.main(argv)
    return rc, buf.getvalue()


class StrategicUnlocksU1AndU3Tests(unittest.TestCase):
    def test_u3_local_to_remote_upgrade_and_github_rename_preserve_immutable_canonical_id_across_state_wipe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repos_dir = root / "repos"
            repos_dir.mkdir()

            # 1. Start with a local-only git repo (no remote) AND a GitHub-backed repo.
            local_only = repos_dir / "alpha-local"
            _init_git(local_only, remote_url=None)

            renamable = repos_dir / "beta-service"
            _init_git(renamable, remote_url="https://github.com/example/beta-old.git")

            config_path = root / "ledger_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "roots": [
                            {
                                "source_id": "test-live",
                                "label": "test-live",
                                "path": str(repos_dir),
                                "discovery": "children",
                            }
                        ]
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            reg_dir = root / "registry"
            reg_dir.mkdir()
            decisions_path = reg_dir / "identity-decisions.json"
            decisions_path.write_text(
                json.dumps({"schema_version": "1.0.0", "decisions": []}),
                encoding="utf-8",
            )
            lineage_path = reg_dir / "project-lineage.json"
            output_dir = root / "output"
            state_dir = root / "state"

            rc, _ = _run_cli(
                [
                    "refresh",
                    "--no-markdown-mirror",
                    "--config",
                    str(config_path),
                    "--output-dir",
                    str(output_dir),
                    "--state-dir",
                    str(state_dir),
                    "--identity-decisions",
                    str(decisions_path),
                ]
            )
            self.assertEqual(rc, 0)

            alpha_initial = resolve_payload(state_dir, str(local_only))
            beta_initial = resolve_payload(state_dir, "github.com/example/beta-old")
            self.assertEqual(alpha_initial["status"], "resolved")
            self.assertEqual(beta_initial["status"], "resolved")
            alpha_pinned_id = alpha_initial["canonical_project_id"]
            beta_pinned_id = beta_initial["canonical_project_id"]

            # Pin durable lineage via `ledger lineage --sync`.
            rc_sync, out_sync = _run_cli(
                [
                    "lineage",
                    "--state-dir",
                    str(state_dir),
                    "--project-lineage",
                    str(lineage_path),
                    "--sync",
                    "--json",
                ]
            )
            self.assertEqual(rc_sync, 0)
            sync_doc = json.loads(out_sync)
            self.assertTrue(sync_doc["in_sync"])
            self.assertEqual(sync_doc["added_count"], 2)

            # 2. Push alpha-local to GitHub for the first time AND rename beta-service's GitHub remote,
            #    AND completely wipe state/ to prove durability does not depend on ephemeral state/runs/.
            subprocess.run(
                ["git", "remote", "add", "origin", "https://github.com/example/alpha-now-remote.git"],
                cwd=local_only,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "remote", "set-url", "origin", "https://github.com/example/beta-renamed.git"],
                cwd=renamable,
                check=True,
                capture_output=True,
            )
            shutil.rmtree(state_dir)
            self.assertFalse(state_dir.exists())

            rc2, _ = _run_cli(
                [
                    "refresh",
                    "--no-markdown-mirror",
                    "--config",
                    str(config_path),
                    "--output-dir",
                    str(output_dir),
                    "--state-dir",
                    str(state_dir),
                    "--identity-decisions",
                    str(decisions_path),
                ]
            )
            self.assertEqual(rc2, 0)

            # Both canonical_project_id values MUST remain 100% immutable!
            alpha_after = resolve_payload(state_dir, "github.com/example/alpha-now-remote")
            self.assertEqual(alpha_after["status"], "resolved")
            self.assertEqual(alpha_after["canonical_project_id"], alpha_pinned_id)

            # And the newly computed remote-anchor ID also resolves as historical_canonical_id.
            alpha_new_anchor_id = stable_id(
                "prj", "normalized_remote", "github.com/example/alpha-now-remote"
            )
            self.assertNotEqual(alpha_new_anchor_id, alpha_pinned_id)
            alpha_by_new_anchor = resolve_payload(state_dir, alpha_new_anchor_id)
            self.assertEqual(alpha_by_new_anchor["status"], "resolved")
            self.assertEqual(alpha_by_new_anchor["canonical_project_id"], alpha_pinned_id)

            beta_after = resolve_payload(state_dir, "github.com/example/beta-renamed")
            self.assertEqual(beta_after["status"], "resolved")
            self.assertEqual(beta_after["canonical_project_id"], beta_pinned_id)
            beta_new_anchor_id = stable_id(
                "prj", "normalized_remote", "github.com/example/beta-renamed"
            )
            self.assertEqual(
                resolve_payload(state_dir, beta_new_anchor_id)["canonical_project_id"],
                beta_pinned_id,
            )

            # Ratchet lineage --sync, then relocate beta-service to a brand-new directory path (new observation_id!)
            # and wipe state/ again: beta_pinned_id STILL survives via the ratcheted normalized_remotes!
            rc_check_dirty, _ = _run_cli(
                [
                    "lineage",
                    "--state-dir",
                    str(state_dir),
                    "--project-lineage",
                    str(lineage_path),
                    "--check",
                ]
            )
            self.assertEqual(rc_check_dirty, 5)
            rc_sync2, _ = _run_cli(
                [
                    "lineage",
                    "--state-dir",
                    str(state_dir),
                    "--project-lineage",
                    str(lineage_path),
                    "--sync",
                ]
            )
            self.assertEqual(rc_sync2, 0)

            relocated = repos_dir / "beta-relocated-dir"
            renamable.rename(relocated)
            shutil.rmtree(state_dir)

            rc3, _ = _run_cli(
                [
                    "refresh",
                    "--no-markdown-mirror",
                    "--config",
                    str(config_path),
                    "--output-dir",
                    str(output_dir),
                    "--state-dir",
                    str(state_dir),
                    "--identity-decisions",
                    str(decisions_path),
                ]
            )
            self.assertEqual(rc3, 0)
            beta_relocated = resolve_payload(state_dir, str(relocated))
            self.assertEqual(beta_relocated["status"], "resolved")
            self.assertEqual(beta_relocated["canonical_project_id"], beta_pinned_id)

    def test_u3_lineage_merge_and_split_preserve_determinism_and_foreign_keys(self) -> None:
        obs_a = {
            "observation_id": "obs_" + "a" * 24,
            "source_id": "src-live",
            "snapshot_id": "snap_1",
            "observed_at": "2026-10-31T20:00:00Z",
            "project_key": "proj-a",
            "location": "/repos/proj-a",
            "compat_entry": {"name": "Proj A", "path": "/repos/proj-a", "git": False},
        }
        obs_b = {
            "observation_id": "obs_" + "b" * 24,
            "source_id": "src-mirror",
            "snapshot_id": "snap_1",
            "observed_at": "2026-10-31T20:00:00Z",
            "project_key": "proj-b",
            "location": "/mirrors/proj-b",
            "compat_entry": {"name": "Proj B", "path": "/mirrors/proj-b", "git": False},
        }
        empty_decisions = {"schema_version": "1.0.0", "decisions": []}
        c0, _ = compile_identity([obs_a, obs_b], empty_decisions)
        self.assertEqual(len(c0["projects"]), 2)
        lineage0 = build_synced_lineage(canonical_payload=c0)
        ids_before = sorted(p["canonical_project_id"] for p in c0["projects"])

        # Merge obs_a and obs_b: winner keeps its pinned canonical_project_id, loser becomes historical_canonical_id.
        merge_decisions = {
            "schema_version": "1.0.0",
            "decisions": [
                {
                    "decision_id": "dec-merge-ab",
                    "type": "merge",
                    "observation_ids": [obs_a["observation_id"], obs_b["observation_id"]],
                    "authority": "operator",
                    "rationale": "Same conceptual project.",
                }
            ],
        }
        c_merged, _ = compile_identity([obs_a, obs_b], merge_decisions, lineage_payload=lineage0)
        self.assertEqual(len(c_merged["projects"]), 1)
        merged_proj = c_merged["projects"][0]
        self.assertEqual(merged_proj["canonical_project_id"], ids_before[0])
        hist_vals = {
            r["value"]
            for r in merged_proj["referents"]
            if r["kind"] == "historical_canonical_id"
        }
        self.assertIn(ids_before[1], hist_vals)

        lineage_merged = build_synced_lineage(
            canonical_payload=c_merged, existing_lineage=lineage0
        )
        self.assertEqual(len(lineage_merged["projects"]), 1)
        self.assertIn(ids_before[1], lineage_merged["projects"][0]["historical_canonical_ids"])

        # Symmetric split (superseding dec-merge-ab): lineage abstains cleanly so the two split components never collide.
        c_split, _ = compile_identity([obs_a, obs_b], empty_decisions, lineage_payload=lineage_merged)
        self.assertEqual(len(c_split["projects"]), 2)
        split_ids = [p["canonical_project_id"] for p in c_split["projects"]]
        self.assertEqual(len(set(split_ids)), 2)

    def test_u3_committed_project_lineage_registry_is_valid(self) -> None:
        loaded = load_project_lineage(REPO_ROOT / "registry" / "project-lineage.json", required=True)
        self.assertGreaterEqual(len(loaded["projects"]), 170)

    def test_u1_end_to_end_machina_continuity_across_capsule_gbrain_explain_and_human_view(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repos_dir = root / "repos"
            repos_dir.mkdir()

            active_repo = repos_dir / "active-machina-app"
            _init_git(active_repo, remote_url="https://github.com/example/active-machina-app.git")
            (active_repo / ".machina").mkdir()
            (active_repo / ".machina" / "state.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "sequence": 18,
                        "slice": 2,
                        "updated_at": "2026-10-31T20:00:00Z",
                        "active_unit": {
                            "id": "APP-UNIT-07",
                            "title": "Deliver continuity projection",
                        },
                        "frontier": {"kind": "operator_approval"},
                        "last_checkpoint": {
                            "slice": 1,
                            "summary": "Checkpointed continuity parser.",
                            "timestamp": "2026-10-31T19:50:00Z",
                        },
                        "last_completed": {
                            "slice": 1,
                            "summary": "Completed APP-UNIT-06.",
                            "timestamp": "2026-10-31T18:00:00Z",
                            "unit": {
                                "id": "APP-UNIT-06",
                                "title": "Bootstrap thin waist",
                            },
                        },
                        "next_action": "Verify GBrain reality block idempotence",
                    }
                ),
                encoding="utf-8",
            )

            idle_repo = repos_dir / "idle-machina-lib"
            _init_git(idle_repo, remote_url="https://github.com/example/idle-machina-lib.git")
            (idle_repo / ".machina").mkdir()
            (idle_repo / ".machina" / "state.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "sequence": 6,
                        "slice": 1,
                        "updated_at": "2026-10-30T15:00:00Z",
                        "active_unit": None,
                        "frontier": None,
                        "last_checkpoint": None,
                        "last_completed": {
                            "slice": 1,
                            "summary": "Verified Consumer Adapter v1.",
                            "timestamp": "2026-10-30T15:00:00Z",
                            "unit": {
                                "id": "LIB-THIN-WAIST-01",
                                "title": "Onboard library onto Machina thin waist",
                            },
                        },
                        "next_action": None,
                    }
                ),
                encoding="utf-8",
            )

            config_path = root / "ledger_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "roots": [
                            {
                                "source_id": "central-active",
                                "label": "central-active",
                                "path": str(repos_dir),
                                "discovery": "children",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            decisions_path = root / "decisions.json"
            decisions_path.write_text(
                json.dumps({"schema_version": "1.0.0", "decisions": []}),
                encoding="utf-8",
            )
            output_dir = root / "output"
            state_dir = root / "state"

            rc, _ = _run_cli(
                [
                    "refresh",
                    "--no-markdown-mirror",
                    "--config",
                    str(config_path),
                    "--output-dir",
                    str(output_dir),
                    "--state-dir",
                    str(state_dir),
                    "--identity-decisions",
                    str(decisions_path),
                ]
            )
            self.assertEqual(rc, 0)

            # 1. Capsule projection check (zero lifecycle vocabulary bleed).
            cap_active = show_payload(state_dir, "github.com/example/active-machina-app")
            mc_active = cap_active["current_state"]["machina_continuity"]
            self.assertEqual(mc_active["state"], "available")
            self.assertEqual(mc_active["sequence"], 18)
            self.assertEqual(mc_active["active_unit_id"], "APP-UNIT-07")
            self.assertEqual(mc_active["active_unit_title"], "Deliver continuity projection")
            self.assertEqual(mc_active["last_completed_unit_id"], "APP-UNIT-06")
            self.assertEqual(mc_active["last_completed_unit_title"], "Bootstrap thin waist")
            self.assertTrue(mc_active["has_checkpoint"])
            self.assertTrue(mc_active["has_frontier"])
            self.assertNotIn("execution_" + "state", json.dumps(cap_active))

            # 2. GBrain Reality Provider projection & zero-churn idempotence check.
            canonical = load_canonical_projects(state_dir)
            proj_by_key = {p["project_key"]: p for p in canonical["projects"]}
            active_proj = proj_by_key["github.com/example/active-machina-app"]
            idle_proj = proj_by_key["github.com/example/idle-machina-lib"]

            active_block = render_reality_block(
                active_proj, load_project_capsule(state_dir, active_proj["canonical_project_id"])
            )
            self.assertIn(
                "- **Machina Continuity:** active `APP-UNIT-07` (Deliver continuity projection) (`frontier`)",
                active_block,
            )
            idle_block = render_reality_block(
                idle_proj, load_project_capsule(state_dir, idle_proj["canonical_project_id"])
            )
            self.assertIn(
                "- **Machina Continuity:** last completed `LIB-THIN-WAIST-01` (Onboard library onto Machina thin waist)",
                idle_block,
            )

            brain_dir = root / "brain"
            (brain_dir / "projects").mkdir(parents=True)
            s1 = sync_gbrain_pages(state_dir=state_dir, brain_dir=brain_dir)
            self.assertEqual(s1["created_count"], 2)
            s2 = sync_gbrain_pages(state_dir=state_dir, brain_dir=brain_dir)
            self.assertEqual(s2["created_count"], 0)
            self.assertEqual(s2["updated_count"], 0)
            self.assertEqual(s2["unchanged_count"], 2)

            # 3. `ledger explain` continuity & historical IDs check.
            exp_active = explain_payload(
                state_dir,
                "github.com/example/active-machina-app",
                decisions_path=decisions_path,
            )
            self.assertEqual(exp_active["status"], "resolved")
            self.assertEqual(
                exp_active["current_state"]["machina_continuity"]["active_unit_id"],
                "APP-UNIT-07",
            )
            self.assertIn("Machina continuity active unit=APP-UNIT-07.", exp_active["explanation"])
            self.assertIsInstance(exp_active["identity"]["historical_canonical_ids"], list)
            self.assertNotIn("execution_" + "state", json.dumps(exp_active))

            # 4. Human canonical ledger Markdown (`state/canonical-projects.md`) check.
            human_md = (state_dir / "canonical-projects.md").read_text(encoding="utf-8")
            self.assertIn("- **Machina-Tracked Projects**: 2", human_md)
            self.assertIn("## Machina Continuity", human_md)
            self.assertIn("`APP-UNIT-07` (Deliver continuity projection)", human_md)
            self.assertIn("`LIB-THIN-WAIST-01` (Onboard library onto Machina thin waist)", human_md)

            # Verify lineage_status_payload helper directly as well.
            lp = root / "lineage.json"
            status_before = lineage_status_payload(state_dir=state_dir, lineage_path=lp, sync=False)
            self.assertFalse(status_before["in_sync"])
            status_synced = lineage_status_payload(state_dir=state_dir, lineage_path=lp, sync=True)
            self.assertTrue(status_synced["in_sync"])


if __name__ == "__main__":
    unittest.main()
