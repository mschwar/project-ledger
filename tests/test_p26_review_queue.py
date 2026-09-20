"""Coverage for the P2.6 identity review queue (ledger.identity).

Asserts the first-class review-item contract: every review carries severity,
reason_automation_stopped, resolution_actions, and evidence; decision-scoped
reviews (DECISION_SUPERSEDE_UNKNOWN) carry a non-empty affected_decision_ids
referent and a distinct review_id per distinct unknown target (the data-loss
collision fixed in P2.6); and observation-scoped review IDs are unchanged
(pinned fixtures preserved).
"""

from __future__ import annotations

import unittest

from ledger.ids import stable_id
from ledger.identity import compile_identity


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
            "repo_name": path.rsplit("/", 1)[-1],
            "remote_url": remote_url,
            "canonical_url": canonical_url,
            "path": path,
        },
    }


EMPTY_DECISIONS = {"schema_version": "1.0.0", "decisions": []}


class ReviewEnvelopeTests(unittest.TestCase):
    def test_every_review_carries_first_class_fields(self) -> None:
        # PROJECT_KEY_AMBIGUOUS: two same-name/key observations at different remotes.
        obs = [
            observation(
                "obs-a",
                project_key="garden",
                name="The Garden",
                path="/p/a",
                remote_url="https://github.com/x/a.git",
            ),
            observation(
                "obs-b",
                project_key="garden",
                name="The Garden",
                path="/p/b",
                remote_url="https://github.com/x/b.git",
            ),
        ]
        _, reviews = compile_identity(obs, EMPTY_DECISIONS)
        self.assertEqual(reviews["review_count"], 1)
        item = reviews["items"][0]
        self.assertEqual(item["code"], "PROJECT_KEY_AMBIGUOUS")
        self.assertEqual(item["state"], "open")
        self.assertEqual(item["severity"], "warning")
        self.assertTrue(item["reason_automation_stopped"])
        self.assertTrue(item["resolution_actions"])
        self.assertTrue(item["evidence"])
        # Observation-scoped review: affected_decision_ids present but empty.
        self.assertEqual(item["affected_decision_ids"], [])
        self.assertEqual(
            item["affected_observation_ids"], ["obs-a", "obs-b"]
        )

    def test_decision_conflict_review_is_error_severity(self) -> None:
        obs = [
            observation(
                "obs-live",
                project_key="homelab",
                name="Homelab",
                path="/a",
                remote_url="https://github.com/mschwar/homelab.git",
            ),
            observation(
                "obs-mirror",
                project_key="homelab",
                name="Homelab",
                path="/b",
                remote_url="git@github.com:mschwar/homelab.git",
            ),
        ]
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
                    "decision_id": "dec-merge",
                    "type": "merge",
                    "observation_ids": ["obs-live", "obs-mirror"],
                },
            ],
        }
        _, reviews = compile_identity(obs, decisions)
        conflicts = [
            item for item in reviews["items"] if item["code"] == "DECISION_CONFLICT"
        ]
        self.assertTrue(conflicts)
        for item in conflicts:
            self.assertEqual(item["severity"], "error")
            self.assertTrue(item["reason_automation_stopped"])
            self.assertTrue(item["resolution_actions"])


class DecisionScopedReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.obs = [
            observation(
                "obs-live",
                project_key="homelab",
                name="Homelab",
                path="/a/homelab",
                remote_url="https://github.com/mschwar/homelab.git",
            )
        ]

    def test_supersede_unknown_review_carries_decision_referent(self) -> None:
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
        _, reviews = compile_identity(self.obs, decisions)
        self.assertEqual(reviews["review_count"], 1)
        item = reviews["items"][0]
        self.assertEqual(item["code"], "DECISION_SUPERSEDE_UNKNOWN")
        # The review is about the superseding decision, not observations.
        self.assertEqual(item["affected_decision_ids"], ["dec-sup"])
        self.assertEqual(item["affected_observation_ids"], [])
        self.assertEqual(item["severity"], "warning")
        self.assertTrue(item["reason_automation_stopped"])
        self.assertTrue(item["resolution_actions"])

    def test_two_distinct_unknown_targets_produce_distinct_review_ids(self) -> None:
        # Regression for the P2.6 data-loss bug: two supersede decisions referencing
        # two different unknown decisions previously collapsed to one review_id and the
        # first review was silently dropped.
        decisions = {
            "schema_version": "1.0.0",
            "decisions": [
                {
                    "decision_id": "dec-sup1",
                    "type": "supersede",
                    "supersedes_decision_id": "dec-ghost-a",
                },
                {
                    "decision_id": "dec-sup2",
                    "type": "supersede",
                    "supersedes_decision_id": "dec-ghost-b",
                },
            ],
        }
        _, reviews = compile_identity(self.obs, decisions)
        self.assertEqual(reviews["review_count"], 2)
        ids = [item["review_id"] for item in reviews["items"]]
        self.assertEqual(len(ids), len(set(ids)), "review_ids must be distinct")
        decision_refs = sorted(
            item["affected_decision_ids"][0] for item in reviews["items"]
        )
        self.assertEqual(decision_refs, ["dec-sup1", "dec-sup2"])

    def test_observation_scoped_review_id_is_unchanged(self) -> None:
        # The decision referent must only enter the stable_id material when present,
        # so observation-scoped review IDs are unchanged by the P2.6 fix. Assert the
        # review_id equals stable_id("rev", code, ids) with no decision material.
        obs = [
            observation(
                "obs-a",
                project_key="wren",
                name="Wren",
                path="/p/a",
                remote_url="https://github.com/example/wren.git",
            ),
            observation(
                "obs-b",
                project_key="wren",
                name="Wren",
                path="/p/b",
                remote_url="https://github.com/example-org/wren.git",
            ),
        ]
        _, reviews = compile_identity(obs, EMPTY_DECISIONS)
        item = reviews["items"][0]
        self.assertEqual(item["code"], "PROJECT_KEY_AMBIGUOUS")
        self.assertEqual(
            item["review_id"],
            stable_id("rev", "PROJECT_KEY_AMBIGUOUS", ["obs-a", "obs-b"]),
        )


if __name__ == "__main__":
    unittest.main()
