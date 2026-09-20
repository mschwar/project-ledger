"""Coverage for P1.4 — minimal epistemic claim/evidence contract.

P1.4 delivers only what canonical identity needs immediately: a typed
``declared``/``observed``/``inferred`` claim surface (``ledger/claims.py``), and the
``DIVERGENT_SIDECAR_DECLARATIONS`` review that surfaces when two observations merged
into one canonical project carry conflicting sidecar ``project_key`` declarations.

The old behavior (pinned by the ``estate-5`` fixture before this programme) let exact
remote authority silently absorb the conflicting declarations with no review — a
violation of SCHEMA.md §6 ("emit claims/conflict/review; do not use last-write-wins").
This file proves the new behavior and the ratchet:

- a merge of two same-remote observations with conflicting ``project_key`` sidecars
  opens a ``DIVERGENT_SIDECAR_DECLARATIONS`` review referencing both observations;
- the remote-based merge is left intact (advisory, not a blocker);
- the conflicting values are carried as typed ``declared`` claims in the review evidence;
- a ``canonical_key`` decision (operator-chosen authoritative key) resolves the review
  and it does not recur on unchanged inputs (the P2.8-style ratchet);
- observations that declare the SAME key, or that declare nothing conflicting, do not
  open the review.
"""

from __future__ import annotations

import unittest

from ledger.claims import (
    CLAIM_KIND_DECLARED,
    divergent_declarations,
    sidecar_declaration_claim,
)
from ledger.identity import compile_identity

SCHEMA = "1.0.0"
EMPTY_DECISIONS = {"schema_version": SCHEMA, "decisions": []}


def observation(
    observation_id: str,
    *,
    project_key: str = "",
    name: str,
    path: str,
    remote_url: str,
) -> dict:
    return {
        "schema_version": SCHEMA,
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
            "canonical_url": remote_url,
            "path": path,
        },
    }


def decision(decision_id: str, **fields: object) -> dict:
    return {"decision_id": decision_id, **fields}


def review_codes(reviews: dict) -> list[str]:
    return sorted(item["code"] for item in reviews["items"])


class ClaimContractTests(unittest.TestCase):
    """The minimal typed claim surface (P1.4)."""

    def test_sidecar_declaration_claim_is_typed_and_provenanced(self) -> None:
        obs = observation(
            "obs-north",
            project_key="north",
            name="Meridian",
            path="/a/north",
            remote_url="https://github.com/mschwar/north.git",
        )
        claim = sidecar_declaration_claim(obs)
        self.assertIsNotNone(claim)
        assert claim is not None
        self.assertEqual(claim["claim_kind"], CLAIM_KIND_DECLARED)
        self.assertEqual(claim["subject_type"], "observation")
        self.assertEqual(claim["subject_id"], "obs-north")
        self.assertEqual(claim["field"], "project_key")
        self.assertEqual(claim["value"], "north")
        self.assertEqual(claim["source_id"], "fixture")
        self.assertTrue(claim["claim_id"].startswith("claim_"))

    def test_no_declaration_yields_no_claim(self) -> None:
        obs = observation("obs-blank", name="B", path="/a/b", remote_url="https://github.com/x/b.git")
        self.assertIsNone(sidecar_declaration_claim(obs))

    def test_divergent_declarations_detected(self) -> None:
        obs = [
            observation("o1", project_key="a", name="X", path="/p/1", remote_url="https://g/x.git"),
            observation("o2", project_key="b", name="X", path="/p/2", remote_url="https://g/x.git"),
        ]
        claims = divergent_declarations(obs)
        self.assertEqual(len(claims), 2)
        self.assertEqual({c["value"] for c in claims}, {"a", "b"})
        self.assertTrue(all(c["claim_kind"] == CLAIM_KIND_DECLARED for c in claims))

    def test_agreeing_declarations_are_not_divergent(self) -> None:
        obs = [
            observation("o1", project_key="same", name="X", path="/p/1", remote_url="https://g/x.git"),
            observation("o2", project_key="same", name="X", path="/p/2", remote_url="https://g/x.git"),
        ]
        self.assertEqual(divergent_declarations(obs), [])


class DivergentSidecarReviewTests(unittest.TestCase):
    """DIVERGENT_SIDECAR_DECLARATIONS behavior in the identity compiler."""

    def setUp(self) -> None:
        # Two observations of the SAME project (identical exact remote), each carrying
        # a conflicting sidecar project_key declaration (north vs south). This mirrors
        # the production estate-5 case.
        self.obs = [
            observation(
                "obs-north",
                project_key="north",
                name="Meridian",
                path="/central/repos/active/north",
                remote_url="https://github.com/mschwar/north.git",
            ),
            observation(
                "obs-south",
                project_key="south",
                name="Meridian",
                path="/central/registry/mirrors/mac/south",
                remote_url="git@github.com:mschwar/north.git",
            ),
        ]

    def test_conflicting_declarations_open_review_but_keep_merge(self) -> None:
        canonical, reviews = compile_identity(self.obs, EMPTY_DECISIONS)
        # The merge stands on exact-remote authority: one project.
        self.assertEqual(canonical["canonical_project_count"], 1)
        project = canonical["projects"][0]
        self.assertEqual(project["identity_anchor"]["kind"], "normalized_remote")
        self.assertEqual(project["project_key"], "github.com/mschwar/north")
        self.assertEqual(set(project["project_key_hints"]), {"north", "south"})

        # ... but the conflicting declarations are surfaced, not absorbed.
        self.assertIn("DIVERGENT_SIDECAR_DECLARATIONS", review_codes(reviews))
        item = next(i for i in reviews["items"] if i["code"] == "DIVERGENT_SIDECAR_DECLARATIONS")
        self.assertEqual(item["severity"], "warning")
        self.assertEqual(set(item["affected_observation_ids"]), {"obs-north", "obs-south"})
        # Both conflicting values carried as typed declared claims.
        values = {
            (e["field"], e["value"])
            for e in item["evidence"]
            if e["kind"] == "sidecar_declaration"
        }
        self.assertEqual(values, {("project_key", "north"), ("project_key", "south")})

    def test_agreeing_declarations_do_not_open_review(self) -> None:
        same = [
            observation(
                "obs-live",
                project_key="homelab",
                name="Homelab",
                path="/a/homelab",
                remote_url="https://github.com/mschwar/homelab.git",
            ),
            observation(
                "obs-mirror",
                project_key="homelab",
                name="Homelab",
                path="/b/homelab",
                remote_url="git@github.com:mschwar/homelab.git",
            ),
        ]
        _, reviews = compile_identity(same, EMPTY_DECISIONS)
        self.assertNotIn("DIVERGENT_SIDECAR_DECLARATIONS", review_codes(reviews))
        self.assertEqual(reviews["review_count"], 0)

    def test_canonical_key_decision_resolves_without_recurrence(self) -> None:
        # Before: the divergent-declaration review is open.
        _, before = compile_identity(self.obs, EMPTY_DECISIONS)
        self.assertIn("DIVERGENT_SIDECAR_DECLARATIONS", review_codes(before))

        # Resolve once: the operator chooses the authoritative project key.
        resolved = {
            "schema_version": SCHEMA,
            "decisions": [
                decision(
                    "dec-north-key",
                    type="canonical_key",
                    observation_id="obs-north",
                    key="north",
                    authority="operator",
                )
            ],
        }
        can, after = compile_identity(self.obs, resolved)
        self.assertNotIn("DIVERGENT_SIDECAR_DECLARATIONS", review_codes(after))
        self.assertEqual(after["review_count"], 0)

        # Identical observations do not re-open the review.
        _, third = compile_identity(self.obs, resolved)
        self.assertEqual(third["review_count"], 0)

        # Explain path: the canonical_key decision is discoverable and the key chosen.
        self.assertEqual(len(can["projects"]), 1)
        project = can["projects"][0]
        self.assertEqual(project["project_key"], "north")
        self.assertEqual(project["canonical_key_decision_id"], "dec-north-key")


class DeterminismTests(unittest.TestCase):
    """The new review is deterministic; no churn on repeated compiles."""

    def test_repeated_compiles_are_stable(self) -> None:
        obs = [
            observation("o1", project_key="a", name="X", path="/p/1", remote_url="https://g/x.git"),
            observation("o2", project_key="b", name="X", path="/p/2", remote_url="https://g/x.git"),
        ]
        first, first_reviews = compile_identity(obs, EMPTY_DECISIONS)
        second, second_reviews = compile_identity(obs, EMPTY_DECISIONS)
        self.assertEqual(first["projects"], second["projects"])
        self.assertEqual(first_reviews["items"], second_reviews["items"])
        self.assertEqual(
            first_reviews["items"][0]["review_id"],
            second_reviews["items"][0]["review_id"],
        )


if __name__ == "__main__":
    unittest.main()