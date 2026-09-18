from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from ledger import cli
from ledger.identity import compile_identity, materialize_identity, resolve_payload


def observation(
    observation_id: str,
    *,
    project_key: str,
    name: str,
    path: str,
    remote_url: str = "",
    canonical_url: str = "",
) -> dict:
    return {
        "schema_version": "1.0.0",
        "observation_id": observation_id,
        "source_id": "fixture",
        "snapshot_id": "snap-fixture",
        "source_resolution": "resolved",
        "observed_at": "2026-09-18T06:00:00Z",
        "project_key": project_key,
        "location": path,
        "compat_entry": {
            "project_key": project_key,
            "name": name,
            "repo_name": Path(path).name,
            "remote_url": remote_url,
            "canonical_url": canonical_url,
            "path": path,
        },
    }


class IdentityWalkingSkeletonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.live = observation(
            "obs-live",
            project_key="homelab",
            name="Homelab Agent Platform",
            path="/central/repos/active/homelab",
            remote_url="https://github.com/mschwar/homelab.git",
        )
        self.mirror = observation(
            "obs-mirror",
            project_key="homelab",
            name="Homelab Agent Platform",
            path="/central/registry/mirrors/mac/homelab",
            remote_url="git@github.com:mschwar/homelab.git",
        )
        self.backup = observation(
            "obs-backup",
            project_key="homelab",
            name="Homelab Agent Platform",
            path="/central/archive/backups/homelab",
            canonical_url="https://github.com/mschwar/homelab",
        )
        self.unrelated = observation(
            "obs-unrelated",
            project_key="homelab",
            name="Homelab Agent Platform",
            path="/central/repos/apps/example-homelab",
            remote_url="https://github.com/example/homelab.git",
        )

    def test_exact_remote_merges_manifestations_but_same_name_unrelated_project_stays_separate(self) -> None:
        canonical, reviews = compile_identity(
            [self.live, self.mirror, self.backup, self.unrelated],
            {"schema_version": "1.0.0", "decisions": []},
        )

        self.assertEqual(canonical["canonical_project_count"], 2)
        grouped = [
            project
            for project in canonical["projects"]
            if "github.com/mschwar/homelab" in project["normalized_remotes"]
        ]
        self.assertEqual(len(grouped), 1)
        self.assertEqual(
            grouped[0]["observation_ids"],
            ["obs-backup", "obs-live", "obs-mirror"],
        )
        self.assertEqual(reviews["review_count"], 1)
        self.assertEqual(reviews["items"][0]["code"], "PROJECT_KEY_AMBIGUOUS")

    def test_project_key_and_arbitrary_website_do_not_become_auto_identity(self) -> None:
        key_only = observation(
            "obs-key-only",
            project_key="github.com/mschwar/homelab",
            name="Homelab Agent Platform",
            path="/notes/homelab",
        )
        website_a = observation(
            "obs-site-a",
            project_key="site-a",
            name="Shared Website A",
            path="/projects/site-a",
            canonical_url="https://example.com/shared-product",
        )
        website_b = observation(
            "obs-site-b",
            project_key="site-b",
            name="Shared Website B",
            path="/projects/site-b",
            canonical_url="https://example.com/shared-product",
        )

        canonical, _ = compile_identity(
            [self.live, key_only, website_a, website_b],
            {"schema_version": "1.0.0", "decisions": []},
        )

        self.assertEqual(canonical["canonical_project_count"], 4)

    def test_canonical_id_is_stable_when_an_exact_remote_mirror_is_added(self) -> None:
        first, _ = compile_identity(
            [self.live],
            {"schema_version": "1.0.0", "decisions": []},
        )
        second, _ = compile_identity(
            [self.live, self.mirror],
            {"schema_version": "1.0.0", "decisions": []},
        )
        self.assertEqual(
            first["projects"][0]["canonical_project_id"],
            second["projects"][0]["canonical_project_id"],
        )

    def test_exact_resolve_handles_remote_path_id_and_preserves_name_ambiguity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            canonical, _ = materialize_identity(
                observations=[self.live, self.mirror, self.unrelated],
                decisions_path=None,
                state_dir=state_dir,
                run_id="run-fixture",
                compiled_at="2026-09-18T06:10:00Z",
            )
            target = next(
                project
                for project in canonical["projects"]
                if "github.com/mschwar/homelab" in project["normalized_remotes"]
            )

            remote = resolve_payload(state_dir, "https://github.com/mschwar/homelab.git")
            self.assertEqual(remote["status"], "resolved")
            self.assertEqual(remote["canonical_project_id"], target["canonical_project_id"])

            path = resolve_payload(state_dir, "/central/registry/mirrors/mac/homelab")
            self.assertEqual(path["status"], "resolved")
            self.assertEqual(path["canonical_project_id"], target["canonical_project_id"])

            canonical_id = resolve_payload(state_dir, target["canonical_project_id"])
            self.assertEqual(canonical_id["status"], "resolved")

            ambiguous = resolve_payload(state_dir, "Homelab Agent Platform")
            self.assertEqual(ambiguous["status"], "ambiguous")
            self.assertEqual(ambiguous["candidate_count"], 2)

            missing = resolve_payload(state_dir, "does-not-exist")
            self.assertEqual(missing["status"], "unresolved")

    def test_negative_decision_blocks_exact_remote_auto_merge(self) -> None:
        decisions = {
            "schema_version": "1.0.0",
            "decisions": [
                {
                    "decision_id": "dec-keep-separate",
                    "type": "reject_match",
                    "left_observation_id": "obs-live",
                    "right_observation_id": "obs-mirror",
                    "rationale": "Fixture: same remote intentionally represents separate conceptual projects.",
                }
            ],
        }
        canonical, reviews = compile_identity([self.live, self.mirror], decisions)
        self.assertEqual(canonical["canonical_project_count"], 2)
        self.assertTrue(
            any(item["code"] == "AUTO_MATCH_BLOCKED_BY_DECISION" for item in reviews["items"])
        )

    def test_explicit_merge_decision_and_alias_ratchet_ambiguity(self) -> None:
        decisions = {
            "schema_version": "1.0.0",
            "decisions": [
                {
                    "decision_id": "dec-merge-two-remotes",
                    "type": "merge",
                    "observation_ids": ["obs-live", "obs-unrelated"],
                    "rationale": "Operator-reviewed repository migration fixture.",
                },
                {
                    "decision_id": "dec-alias-old-name",
                    "type": "alias",
                    "observation_id": "obs-live",
                    "alias": "old-homelab",
                    "rationale": "Historical operator referent.",
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = root / "identity-decisions.json"
            registry.write_text(json.dumps(decisions), encoding="utf-8")
            canonical, reviews = materialize_identity(
                observations=[self.live, self.unrelated],
                decisions_path=registry,
                state_dir=root,
                run_id="run-fixture",
                compiled_at="2026-09-18T06:10:00Z",
            )
            self.assertEqual(canonical["canonical_project_count"], 1)
            self.assertEqual(reviews["review_count"], 0)
            project = canonical["projects"][0]
            self.assertEqual(project["identity_anchor"]["kind"], "merge_decision")
            self.assertIn("dec-merge-two-remotes", project["merge_decision_ids"])

            alias = resolve_payload(root, "old-homelab")
            self.assertEqual(alias["status"], "resolved")
            self.assertEqual(alias["canonical_project_id"], project["canonical_project_id"])

    def test_cli_resolve_exit_codes_are_machine_usable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            canonical, _ = materialize_identity(
                observations=[self.live, self.unrelated],
                decisions_path=None,
                state_dir=state_dir,
                run_id="run-fixture",
                compiled_at="2026-09-18T06:10:00Z",
            )
            target = next(
                project
                for project in canonical["projects"]
                if "github.com/mschwar/homelab" in project["normalized_remotes"]
            )

            with redirect_stdout(StringIO()):
                self.assertEqual(
                    cli.main(["resolve", target["canonical_project_id"], "--state-dir", str(state_dir), "--json"]),
                    0,
                )
                self.assertEqual(
                    cli.main(["resolve", "Homelab Agent Platform", "--state-dir", str(state_dir), "--json"]),
                    3,
                )
                self.assertEqual(
                    cli.main(["resolve", "does-not-exist", "--state-dir", str(state_dir), "--json"]),
                    4,
                )


if __name__ == "__main__":
    unittest.main()
