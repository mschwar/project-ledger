"""Tests for the Canonical Reality Provider (machina/homelab/gbrain) and Wave 3 closeout.

Covers:
1. Git upstream ahead/behind/dirty working-tree drift warnings in `ledger resolve`,
   `ledger locate`, and `ledger show` (both fixture-driven and live git repositories).
2. GBrain source & project-page cross-linking (`gbrain_source_slug`, `gbrain.page_key`)
   across registered ai-server sources, mirror exclusions, and sidecar overrides.
3. `ledger explain <referent>` (P3.6) across canonical projects, observations, review
   items, and identity decisions.
4. `ledger review` + frontier zero-shot weak-overlap triage candidate approval
   (`--approve-candidates`).
5. Human canonical ledger Markdown view (`state/canonical-projects.md`, P3.7).
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from ledger import cli
from ledger.compiler import compile_state
from ledger.explain import explain_payload
from ledger.gbrain import normalize_gbrain_slug
from ledger.identity import compile_identity, resolve_payload
from ledger.sources.git import inspect_git_worktree_state
from ledger.triage import approve_candidate_decisions, review_payload


def _make_obs(
    obs_id: str,
    *,
    source_id: str = "central-active",
    source_label: str = "central-active",
    project_key: str = "machina",
    name: str = "Machina",
    path: str = "/central/repos/active/machina",
    remote_url: str = "https://github.com/mschwar/machina.git",
    git: bool = True,
    head_branch: str = "main",
    head_commit: str = "abc1234",
    behind_count: int | None = None,
    ahead_count: int | None = None,
    dirty: bool | None = None,
    gbrain_source_slug: str = "",
) -> dict:
    compat: dict[str, object] = {
        "project_key": project_key,
        "name": name,
        "repo_name": Path(path).name if path.startswith("/") else project_key,
        "source_label": source_label,
        "machine_name": "ai-server",
        "path": path,
        "remote_url": remote_url,
        "canonical_url": remote_url,
        "git": git,
        "head_branch": head_branch,
        "head_commit": head_commit,
        "head_commit_at": "2026-10-29T01:00:00+00:00",
        "last_remote_ref_at": "2026-10-29T01:00:00+00:00",
        "description": f"{name} description.",
        "status": "active",
    }
    if behind_count is not None:
        compat["behind_count"] = behind_count
    if ahead_count is not None:
        compat["ahead_count"] = ahead_count
    if dirty is not None:
        compat["dirty"] = dirty
    if gbrain_source_slug:
        compat["gbrain_source_slug"] = gbrain_source_slug
    return {
        "schema_version": "1.0.0",
        "observation_id": obs_id,
        "source_id": source_id,
        "snapshot_id": f"snap_{source_id}",
        "source_resolution": "resolved",
        "observed_at": "2026-10-29T02:00:00Z",
        "project_key": project_key,
        "location": path,
        "compat_entry": compat,
    }


class GBrainCrossLinkTests(unittest.TestCase):
    def test_slug_normalization_enforces_gbrain_grammar(self) -> None:
        self.assertEqual(normalize_gbrain_slug("github.com/mschwar/machina"), "machina")
        self.assertEqual(normalize_gbrain_slug("google-drive:2026.TSLE.TEAM_internal-MS"), "2026-tsle-team-internal-ms")
        self.assertEqual(
            normalize_gbrain_slug("google-drive:projects/very_long_project_name_exceeding_thirty_two_chars"),
            "very-long-project-name-exceeding",
        )
        self.assertLessEqual(
            len(normalize_gbrain_slug("google-drive:projects/very_long_project_name_exceeding_thirty_two_chars")),
            32,
        )

    def test_maps_registered_corpus_paths_and_excludes_registry_mirrors(self) -> None:
        active = _make_obs(
            "obs-active",
            path="/central/repos/active/machina",
            project_key="github.com/mschwar/machina",
            remote_url="https://github.com/mschwar/machina.git",
        )
        garden = _make_obs(
            "obs-garden",
            source_id="git-the-garden",
            path="/central/git/the_garden",
            project_key="the-garden",
            remote_url="",
        )
        notes = _make_obs(
            "obs-notes",
            source_id="central-projects",
            path="/central/projects/notes",
            project_key="notes",
            remote_url="",
        )
        mirror = _make_obs(
            "obs-mirror",
            source_id="mac-mirror",
            path="/central/registry/mirrors/matthews-macbook-air-2/Documents/homelab",
            project_key="homelab",
            remote_url="https://github.com/mschwar/homelab.git",
        )
        sidecar_override = _make_obs(
            "obs-override",
            source_id="central-infra",
            path="/central/repos/infra/custom",
            project_key="custom",
            remote_url="",
            gbrain_source_slug="central-knowledge",
        )

        canonical, _ = compile_identity(
            [active, garden, notes, mirror, sidecar_override],
            {"schema_version": "1.0.0", "decisions": []},
        )
        by_key = {p["project_key"]: p for p in canonical["projects"]}

        self.assertEqual(by_key["github.com/mschwar/machina"]["gbrain_source_slug"], "active-repos")
        self.assertEqual(by_key["github.com/mschwar/machina"]["gbrain"]["page_key"], "projects/machina")
        self.assertEqual(by_key["the-garden"]["gbrain_source_slug"], "the-garden")
        self.assertEqual(by_key["notes"]["gbrain_source_slug"], "central-notes")
        # Mirror under /central/registry/mirrors/ must NOT map to atlas-registry.
        self.assertEqual(by_key["homelab"]["gbrain_source_slug"], "default")
        self.assertEqual(by_key["homelab"]["gbrain"]["page_key"], "projects/homelab")
        self.assertEqual(by_key["custom"]["gbrain_source_slug"], "central-knowledge")
        self.assertTrue(by_key["custom"]["gbrain"]["match_basis"].startswith("declared_sidecar"))


class GitDriftRealityProviderTests(unittest.TestCase):
    def test_real_git_repo_behind_origin_main_and_dirty_emits_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "worker-repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True, capture_output=True)
            (repo / "README.md").write_text("# Worker Repo\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
            base_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
            ).stdout.strip()

            for i in range(1, 21):
                (repo / "README.md").write_text(f"# Worker Repo {i}\n", encoding="utf-8")
                subprocess.run(["git", "commit", "-am", f"c{i}"], cwd=repo, check=True, capture_output=True)
            remote_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
            ).stdout.strip()

            # Point refs/remotes/origin/main to remote_sha and reset local main 20 commits back.
            subprocess.run(
                ["git", "update-ref", "refs/remotes/origin/main", remote_sha],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            subprocess.run(["git", "reset", "--hard", base_sha], cwd=repo, check=True, capture_output=True)
            # Dirty the working tree.
            (repo / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")

            drift = inspect_git_worktree_state(repo)
            self.assertEqual(drift["state"], "behind")
            self.assertEqual(drift["head_branch"], "main")
            self.assertEqual(drift["upstream_ref"], "origin/main")
            self.assertEqual(drift["behind_count"], 20)
            self.assertEqual(drift["ahead_count"], 0)
            self.assertTrue(drift["dirty"])
            self.assertIn("GIT_BEHIND_UPSTREAM", drift["warning_codes"])
            self.assertIn("GIT_DIRTY_WORKTREE", drift["warning_codes"])
            self.assertIn("warning: local main is 20 commits behind origin/main", drift["warnings"])


class ExplainAndReviewCloseoutTests(unittest.TestCase):
    def test_full_compile_resolve_locate_explain_review_and_approve_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live"
            inv = root / "inv"
            live.mkdir()
            inv.mkdir()

            config_path = root / "ledger_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "roots": [
                            {
                                "source_id": "central-active",
                                "path": str(live),
                                "label": "central-active",
                                "discovery": "children",
                            },
                            {
                                "source_id": "matty-pc-inventory",
                                "path": str(inv),
                                "label": "matty-pc",
                                "discovery": "children",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            compat_path = root / "projects.json"
            compat_path.write_text(
                json.dumps(
                    {
                        "generated_at": "2026-10-29T03:00:00+00:00",
                        "entries": [
                            {
                                "project_key": "github.com/mschwar/machina",
                                "source_label": "central-active",
                                "path": "/central/repos/active/machina",
                                "name": "Machina",
                                "remote_url": "https://github.com/mschwar/machina.git",
                                "canonical_url": "https://github.com/mschwar/machina.git",
                                "git": True,
                                "head_branch": "main",
                                "head_commit": "aaa111",
                                "behind_count": 20,
                                "ahead_count": 0,
                                "upstream_ref": "origin/main",
                                "description": "Work Unit kernel.",
                            },
                            {
                                "project_key": "google-drive:machina",
                                "source_label": "matty-pc",
                                "path": "matty-pc:machina",
                                "name": "Machina",
                                "remote_url": "",
                                "canonical_url": "",
                                "git": False,
                                "description": "Inventory copy of Machina.",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            decisions_path = root / "identity-decisions.json"
            decisions_path.write_text(
                json.dumps({"schema_version": "1.0.0", "decisions": []}),
                encoding="utf-8",
            )
            state_dir = root / "state"
            manifest = compile_state(
                config_path=config_path,
                compat_output_path=compat_path,
                state_dir=state_dir,
                identity_decisions_path=decisions_path,
                generated_at="2026-10-29T03:10:00Z",
            )

            # P3.7: canonical-projects.md materialized and registered as a portable relative path.
            self.assertEqual(manifest["artifacts"]["canonical_markdown"], "state/canonical-projects.md")
            md_path = state_dir.parent / manifest["artifacts"]["canonical_markdown"]
            self.assertTrue(md_path.is_file())
            md_text = md_path.read_text(encoding="utf-8")
            self.assertIn("# Canonical Project Ledger", md_text)
            self.assertIn("warning: local main is 20 commits behind origin/main", md_text)

            # Resolve & locate surface canonical_project_id, checkout path, gbrain slug, and drift warning.
            resolved = resolve_payload(state_dir, "github.com/mschwar/machina")
            self.assertEqual(resolved["status"], "resolved")
            self.assertEqual(resolved["gbrain_source_slug"], "active-repos")
            self.assertEqual(resolved["gbrain"]["page_key"], "projects/machina")
            self.assertEqual(resolved["preferred_location"]["location"], "/central/repos/active/machina")
            self.assertIn("warning: local main is 20 commits behind origin/main", resolved["warnings"])

            # Review surfaces the weak overlap between the live and inventory Machina entries.
            rev_before = review_payload(state_dir, decisions_path=decisions_path)
            self.assertEqual(rev_before["untriaged_weak_overlap_count"], 1)
            overlap = rev_before["untriaged_weak_overlaps"][0]
            obs_ids = sorted([overlap["left_observation_id"], overlap["right_observation_id"]])

            candidates_path = root / "candidates.json"
            candidates_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "candidates": [
                            {
                                "decision_id": "dec-merge-machina",
                                "type": "merge",
                                "observation_ids": obs_ids,
                                "authority": "reviewed_agent",
                                "rationale": "Same Machina project across ai-server and matty-pc.",
                            },
                            {
                                "decision_id": "dec-key-machina",
                                "type": "canonical_key",
                                "observation_id": obs_ids[0],
                                "key": "github.com/mschwar/machina",
                                "authority": "reviewed_agent",
                                "rationale": "Select repository key.",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            approval = approve_candidate_decisions(
                candidates_path=candidates_path,
                decisions_path=decisions_path,
            )
            self.assertEqual(approval["approved_count"], 2)

            # Recompile after one-click approval: 1 canonical project, 0 reviews, 0 untriaged weak overlaps.
            compile_state(
                config_path=config_path,
                compat_output_path=compat_path,
                state_dir=state_dir,
                identity_decisions_path=decisions_path,
                generated_at="2026-10-29T03:15:00Z",
            )
            rev_after = review_payload(
                state_dir,
                candidates_path=candidates_path,
                decisions_path=decisions_path,
            )
            self.assertEqual(rev_after["review_count"], 0)
            self.assertEqual(rev_after["untriaged_weak_overlap_count"], 0)
            self.assertEqual(rev_after["pending_candidate_count"], 0)
            self.assertEqual(rev_after["approved_candidate_count"], 2)

            # Explain canonical project, decision, and observation.
            exp_proj = explain_payload(state_dir, "machina", decisions_path=decisions_path)
            self.assertEqual(exp_proj["status"], "resolved")
            self.assertEqual(exp_proj["subject_kind"], "canonical_project")
            self.assertEqual(len(exp_proj["decisions"]), 2)
            self.assertIn("gbrain_source_slug", exp_proj["resolved_fields"])

            exp_dec = explain_payload(state_dir, "dec-merge-machina", decisions_path=decisions_path)
            self.assertEqual(exp_dec["status"], "resolved")
            self.assertEqual(exp_dec["subject_kind"], "identity_decision")
            self.assertTrue(exp_dec["active"])

            exp_obs = explain_payload(state_dir, obs_ids[0], decisions_path=decisions_path)
            self.assertEqual(exp_obs["status"], "resolved")
            self.assertEqual(exp_obs["subject_kind"], "observation")

            # CLI coverage for explain and review.
            buf = StringIO()
            with redirect_stdout(buf):
                self.assertEqual(
                    cli.main(
                        [
                            "explain",
                            "machina",
                            "--state-dir",
                            str(state_dir),
                            "--identity-decisions",
                            str(decisions_path),
                        ]
                    ),
                    0,
                )
                self.assertEqual(
                    cli.main(
                        [
                            "review",
                            "--state-dir",
                            str(state_dir),
                            "--candidates",
                            str(candidates_path),
                            "--identity-decisions",
                            str(decisions_path),
                        ]
                    ),
                    0,
                )


if __name__ == "__main__":
    unittest.main()
