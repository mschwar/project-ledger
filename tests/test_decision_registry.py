"""Coverage for the P2.4 decision registry (ledger.identity + ledger.evidence).

Asserts the durable decision-input contract beyond the walking skeleton: canonical
human key assignment, supersession (append/supersede oriented, never silent rewrite),
enriched decision fields (authority, decided_at, rationale, evidence refs), and that
superseded negative decisions no longer constrain compilation or evidence.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ledger.identity import compile_identity, load_identity_decisions, materialize_identity
from ledger.evidence import build_identity_evidence


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
        "observed_at": "2026-09-19T06:00:00Z",
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


EMPTY_DECISIONS = {"schema_version": "1.0.0", "decisions": []}


class DecisionValidationTests(unittest.TestCase):
    def test_canonical_key_requires_observation_and_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "identity-decisions.json"
            registry.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "decisions": [
                            {
                                "decision_id": "dec-key",
                                "type": "canonical_key",
                                "observation_id": "obs-a",
                                "key": "stable-key",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            payload = load_identity_decisions(registry)
            self.assertEqual(payload["decisions"][0]["type"], "canonical_key")

    def test_canonical_key_missing_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "identity-decisions.json"
            registry.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "decisions": [
                            {
                                "decision_id": "dec-key",
                                "type": "canonical_key",
                                "observation_id": "obs-a",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_identity_decisions(registry)

    def test_supersede_requires_target_and_cannot_self_supersede(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "identity-decisions.json"
            registry.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "decisions": [
                            {
                                "decision_id": "dec-sup",
                                "type": "supersede",
                                "supersedes_decision_id": "dec-old",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            load_identity_decisions(registry)  # valid

            registry.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "decisions": [
                            {
                                "decision_id": "dec-sup",
                                "type": "supersede",
                                "supersedes_decision_id": "dec-sup",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_identity_decisions(registry)

    def test_authority_must_be_a_known_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "identity-decisions.json"
            registry.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "decisions": [
                            {
                                "decision_id": "dec-merge",
                                "type": "merge",
                                "observation_ids": ["obs-a", "obs-b"],
                                "authority": "operator",
                                "decided_at": "2026-09-19T07:00:00Z",
                                "rationale": "Reviewed migration.",
                                "evidence": [{"kind": "normalized_remote", "value": "github.com/x/y"}],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            load_identity_decisions(registry)

            registry.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "decisions": [
                            {
                                "decision_id": "dec-merge",
                                "type": "merge",
                                "observation_ids": ["obs-a", "obs-b"],
                                "authority": "not-a-real-authority",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_identity_decisions(registry)


class CanonicalKeyTests(unittest.TestCase):
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

    def test_canonical_key_overrides_auto_derived_project_key(self) -> None:
        decisions = {
            "schema_version": "1.0.0",
            "decisions": [
                {
                    "decision_id": "dec-key",
                    "type": "canonical_key",
                    "observation_id": "obs-live",
                    "key": "homelab-platform",
                    "authority": "operator",
                    "decided_at": "2026-09-19T07:00:00Z",
                    "rationale": "Operator-approved stable key.",
                }
            ],
        }
        canonical, reviews = compile_identity([self.live, self.mirror], decisions)
        self.assertEqual(canonical["canonical_project_count"], 1)
        project = canonical["projects"][0]
        self.assertEqual(project["project_key"], "homelab-platform")
        self.assertEqual(project["canonical_key_decision_id"], "dec-key")
        # Identity authority is unchanged: the exact remote still merges both.
        self.assertEqual(project["identity_anchor"]["kind"], "normalized_remote")
        self.assertEqual(reviews["review_count"], 0)

    def test_canonical_key_does_not_change_membership(self) -> None:
        decisions = {
            "schema_version": "1.0.0",
            "decisions": [
                {
                    "decision_id": "dec-key",
                    "type": "canonical_key",
                    "observation_id": "obs-live",
                    "key": "renamed",
                }
            ],
        }
        canonical, _ = compile_identity([self.live, self.mirror], decisions)
        project = canonical["projects"][0]
        self.assertEqual(set(project["observation_ids"]), {"obs-live", "obs-mirror"})
        # The canonical key is a stable referent.
        self.assertTrue(
            any(
                ref["kind"] == "project_key" and ref["value"] == "renamed"
                for ref in project["referents"]
            )
        )


class SupersedeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.live = observation(
            "obs-live",
            project_key="homelab",
            name="Homelab",
            path="/a/homelab",
            remote_url="https://github.com/mschwar/homelab.git",
        )
        self.mirror = observation(
            "obs-mirror",
            project_key="homelab",
            name="Homelab",
            path="/b/homelab",
            remote_url="git@github.com:mschwar/homelab.git",
        )

    def test_superseded_reject_match_no_longer_blocks_auto_merge(self) -> None:
        decisions = {
            "schema_version": "1.0.0",
            "decisions": [
                {
                    "decision_id": "dec-keep-separate",
                    "type": "reject_match",
                    "left_observation_id": "obs-live",
                    "right_observation_id": "obs-mirror",
                    "rationale": "Originally kept separate.",
                },
                {
                    "decision_id": "dec-supersede",
                    "type": "supersede",
                    "supersedes_decision_id": "dec-keep-separate",
                    "rationale": "Reconsidered: same project after all.",
                },
            ],
        }
        canonical, reviews = compile_identity([self.live, self.mirror], decisions)
        # The superseded negative decision no longer blocks the exact-remote merge.
        self.assertEqual(canonical["canonical_project_count"], 1)
        self.assertEqual(
            set(canonical["projects"][0]["observation_ids"]), {"obs-live", "obs-mirror"}
        )
        self.assertFalse(
            any(item["code"] == "AUTO_MATCH_BLOCKED_BY_DECISION" for item in reviews["items"])
        )

    def test_superseded_negative_decision_is_not_negative_evidence(self) -> None:
        decisions = {
            "schema_version": "1.0.0",
            "decisions": [
                {
                    "decision_id": "dec-keep-separate",
                    "type": "reject_match",
                    "left_observation_id": "obs-live",
                    "right_observation_id": "obs-mirror",
                },
                {
                    "decision_id": "dec-supersede",
                    "type": "supersede",
                    "supersedes_decision_id": "dec-keep-separate",
                },
            ],
        }
        canonical, _ = compile_identity([self.live, self.mirror], decisions)
        store = build_identity_evidence([self.live, self.mirror], canonical, decisions)
        negatives = [
            item
            for item in store["cross_project_weak_overlaps"]
            if any(r["kind"] == "negative_decision" for r in item["evidence"])
        ]
        self.assertEqual(len(negatives), 0)

    def test_supersede_of_unknown_decision_is_bounded_review(self) -> None:
        decisions = {
            "schema_version": "1.0.0",
            "decisions": [
                {
                    "decision_id": "dec-sup",
                    "type": "supersede",
                    "supersedes_decision_id": "dec-does-not-exist",
                }
            ],
        }
        canonical, reviews = compile_identity([self.live], decisions)
        self.assertEqual(canonical["canonical_project_count"], 1)
        self.assertEqual(reviews["review_count"], 1)
        self.assertEqual(reviews["items"][0]["code"], "DECISION_SUPERSEDE_UNKNOWN")


class MaterializeDecisionRegistryTests(unittest.TestCase):
    def test_materialize_identity_accepts_canonical_key_and_supersede(self) -> None:
        live = observation(
            "obs-live",
            project_key="homelab",
            name="Homelab",
            path="/a/homelab",
            remote_url="https://github.com/mschwar/homelab.git",
        )
        mirror = observation(
            "obs-mirror",
            project_key="homelab",
            name="Homelab",
            path="/b/homelab",
            remote_url="git@github.com:mschwar/homelab.git",
        )
        decisions = {
            "schema_version": "1.0.0",
            "decisions": [
                {
                    "decision_id": "dec-key",
                    "type": "canonical_key",
                    "observation_id": "obs-live",
                    "key": "homelab-platform",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = root / "identity-decisions.json"
            registry.write_text(json.dumps(decisions), encoding="utf-8")
            canonical, _ = materialize_identity(
                observations=[live, mirror],
                decisions_path=registry,
                state_dir=root,
                run_id="run-fixture",
                compiled_at="2026-09-19T07:10:00Z",
            )
            self.assertEqual(canonical["projects"][0]["project_key"], "homelab-platform")


if __name__ == "__main__":
    unittest.main()
