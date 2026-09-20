"""Coverage for the P2.7 resolution ratchet (ledger.identity).

For each first-class review type that a durable decision can resolve, asserts the
review-resolution loop works end to end:

    review opens -> resolve once -> persist a durable decision into the registry
    -> rerun with IDENTICAL observations -> the review does not recur
    -> the durable decision that resolved it is discoverable in the state a
      future `ledger explain` (P3.6) will read.

Review types covered and the durable decision that resolves each:

- ``PROJECT_KEY_AMBIGUOUS``          -> a merge decision (the two same-key projects
  unite, so the compatibility key no longer spans multiple canonical projects).
- ``DECISION_CONFLICT``              -> supersede the conflicting negative decision
  (the remaining positive merge then applies without conflict).
- ``AUTO_MATCH_BLOCKED_BY_DECISION`` -> supersede the blocking negative decision (the
  exact normalized remote then auto-merges the observations).
- ``DECISION_REFERENCE_UNAVAILABLE`` -> supersede the stale decision that referenced
  observations not present in the compile.
- ``DECISION_SUPERSEDE_UNKNOWN``     -> add the referenced decision to the registry so
  the earlier supersede resolves to a known target.

Gate C requires "a resolved identity question does not recur on unchanged inputs";
this file pins that guarantee for every review-resolution type.
"""

from __future__ import annotations

import unittest

from ledger.identity import compile_identity


SCHEMA = "1.0.0"
EMPTY_DECISIONS = {"schema_version": SCHEMA, "decisions": []}


def observation(
    observation_id: str,
    *,
    project_key: str,
    name: str,
    path: str,
    remote_url: str = "",
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


def assert_decision_reference(test: unittest.TestCase, project: dict, decision_id: str) -> None:
    """The resolution decision must be discoverable in the compiled project.

    This is the durable "explain path": a future ``ledger explain`` (P3.6) will read
    the same materialized decision references (authority / provenance) to point an
    operator at the decision that resolved a review. Assert the strongest surface the
    compiler currently exposes before falling back to the persisted registry.
    """
    evidence_values = {
        str(record.get("value"))
        for record in project.get("identity_evidence", [])
    }
    provenance_decision_ids = set()
    for field in project.get("resolved_fields", {}).values():
        for claim in field.get("provenance", []):
            if claim.get("decision_id"):
                provenance_decision_ids.add(str(claim["decision_id"]))
    merge_ids = set(project.get("merge_decision_ids", []))
    canonical_key_id = project.get("canonical_key_decision_id")
    if canonical_key_id:
        merge_ids.add(str(canonical_key_id))
    found = (
        decision_id in evidence_values
        or decision_id in provenance_decision_ids
        or decision_id in merge_ids
    )
    test.assertTrue(
        found,
        f"decision {decision_id!r} is not discoverable in the compiled project state",
    )


class ProjectKeyAmbiguousRatchetTests(unittest.TestCase):
    """PROJECT_KEY_AMBIGUOUS is resolved by a merge decision (P3.6 explain reads the
    explicit_merge_decision evidence)."""

    def setUp(self) -> None:
        # Two distinct-remote projects that share a compatibility project_key. They
        # stay separate under automatic identity (keys are referents, not authority)
        # and open a PROJECT_KEY_AMBIGUOUS review.
        self.obs = [
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

    def test_merge_decision_resolves_without_recurrence(self) -> None:
        _, before = compile_identity(self.obs, EMPTY_DECISIONS)
        self.assertIn("PROJECT_KEY_AMBIGUOUS", review_codes(before))

        # Resolve once and persist a durable merge decision.
        resolved = {
            "schema_version": SCHEMA,
            "decisions": [
                decision(
                    "dec-garden-merge",
                    type="merge",
                    observation_ids=["obs-a", "obs-b"],
                    authority="operator",
                )
            ],
        }
        can, after = compile_identity(self.obs, resolved)
        self.assertNotIn("PROJECT_KEY_AMBIGUOUS", review_codes(after))
        self.assertEqual(after["review_count"], 0)
        # Identical observations, so a re-resolve does not recur.
        _, third = compile_identity(self.obs, resolved)
        self.assertEqual(third["review_count"], 0)

        # Explain path identifies the merge decision (authority for the union).
        self.assertEqual(len(can["projects"]), 1)
        project = can["projects"][0]
        self.assertEqual(project["merge_decision_ids"], ["dec-garden-merge"])
        assert_decision_reference(self, project, "dec-garden-merge")

    def test_canonical_key_decision_resolves_by_disambiguating(self) -> None:
        # Option A (t_92cca8a6): the projected_key ambiguity is computed on each
        # project's RESOLVED project_key, so an operator who assigns a distinct
        # canonical_key to one of two same-key projects disambiguates and clears the
        # review — the advertised resolution_actions now match the compiler.
        _, before = compile_identity(self.obs, EMPTY_DECISIONS)
        self.assertIn("PROJECT_KEY_AMBIGUOUS", review_codes(before))

        # Resolve once: assign a distinct stable key to one of the two projects.
        resolved = {
            "schema_version": SCHEMA,
            "decisions": [
                decision(
                    "dec-garden-key",
                    type="canonical_key",
                    observation_id="obs-a",
                    key="garden-a",
                    authority="operator",
                )
            ],
        }
        can, after = compile_identity(self.obs, resolved)
        self.assertNotIn("PROJECT_KEY_AMBIGUOUS", review_codes(after))
        self.assertEqual(after["review_count"], 0)

        # The two projects stay distinct (canonical_key never changes membership),
        # each with its own resolved key; the raw hint survives as a referent.
        self.assertEqual(len(can["projects"]), 2)
        keys = {p["project_key"]: p for p in can["projects"]}
        self.assertEqual(set(keys), {"garden", "garden-a"})
        for key in ("garden", "garden-a"):
            hints = set(keys[key]["project_key_hints"])
            self.assertIn("garden", hints, "raw hint must remain a project_key_hint")

        # Identical observations do not re-open the review.
        _, third = compile_identity(self.obs, resolved)
        self.assertEqual(third["review_count"], 0)

        # Explain path: the canonical_key decision is discoverable in state.
        keyed = keys["garden-a"]
        assert_decision_reference(self, keyed, "dec-garden-key")
        self.assertEqual(keyed["canonical_key_decision_id"], "dec-garden-key")

    def test_reject_match_does_not_clear_shared_key_warning(self) -> None:
        # A reject_match confirms the two projects are genuinely distinct but leaves
        # the human key shared across them, so the shared-key warning must stay open.
        # This pins that the (now corrected) resolution_actions exclude reject_match.
        _, before = compile_identity(self.obs, EMPTY_DECISIONS)
        self.assertIn("PROJECT_KEY_AMBIGUOUS", review_codes(before))
        original_id = next(i["review_id"] for i in before["items"] if i["code"] == "PROJECT_KEY_AMBIGUOUS")

        decided = {
            "schema_version": SCHEMA,
            "decisions": [
                decision(
                    "dec-garden-reject",
                    type="reject_match",
                    left_observation_id="obs-a",
                    right_observation_id="obs-b",
                    authority="operator",
                )
            ],
        }
        _, after = compile_identity(self.obs, decided)
        self.assertIn("PROJECT_KEY_AMBIGUOUS", review_codes(after))
        keep = next(i for i in after["items"] if i["code"] == "PROJECT_KEY_AMBIGUOUS")
        self.assertEqual(keep["review_id"], original_id)
        # The advertised actions no longer promise reject_match resolves it.
        self.assertNotIn(
            "Add a reject_match decision if they are genuinely distinct projects.",
            [str(a) for a in keep["resolution_actions"]],
        )


class DecisionConflictRatchetTests(unittest.TestCase):
    """DECISION_CONFLICT (positive vs negative decision) is resolved by superseding
    the conflicting negative decision so the positive merge can apply."""

    def setUp(self) -> None:
        self.obs = [
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
        self.conflicting = {
            "schema_version": SCHEMA,
            "decisions": [
                decision(
                    "dec-reject",
                    type="reject_match",
                    left_observation_id="obs-live",
                    right_observation_id="obs-mirror",
                ),
                decision(
                    "dec-merge",
                    type="merge",
                    observation_ids=["obs-live", "obs-mirror"],
                ),
            ],
        }

    def test_superseding_negative_decision_resolves(self) -> None:
        _, before = compile_identity(self.obs, self.conflicting)
        self.assertIn("DECISION_CONFLICT", review_codes(before))

        # Resolve once: supersede the negative decision; the merge then applies.
        resolved = {
            "schema_version": SCHEMA,
            "decisions": self.conflicting["decisions"]
            + [decision("dec-sup-reject", type="supersede", supersedes_decision_id="dec-reject")],
        }
        can, after = compile_identity(self.obs, resolved)
        self.assertNotIn("DECISION_CONFLICT", review_codes(after))
        # The auto-match conflict and same-key warning also clear once unified.
        self.assertEqual(after["review_count"], 0)

        # Identical observations do not re-open the conflict.
        _, third = compile_identity(self.obs, resolved)
        self.assertEqual(third["review_count"], 0)

        # Explain path: the positive merge is the durable authority for the union.
        self.assertEqual(len(can["projects"]), 1)
        project = can["projects"][0]
        self.assertEqual(project["merge_decision_ids"], ["dec-merge"])
        assert_decision_reference(self, project, "dec-merge")


class AutoMatchBlockedRatchetTests(unittest.TestCase):
    """AUTO_MATCH_BLOCKED_BY_DECISION (negative decision holds apart two obs that an
    exact remote would otherwise auto-merge) is resolved by superseding the negative
    decision so the exact-normalized-remote automatic merge can proceed."""

    def setUp(self) -> None:
        self.obs = [
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
        self.split = {
            "schema_version": SCHEMA,
            "decisions": [
                decision(
                    "dec-split",
                    type="reject_match",
                    left_observation_id="obs-live",
                    right_observation_id="obs-mirror",
                )
            ],
        }

    def test_superseding_blocking_decision_resolves(self) -> None:
        _, before = compile_identity(self.obs, self.split)
        self.assertIn("AUTO_MATCH_BLOCKED_BY_DECISION", review_codes(before))

        # Resolve once: supersede the blocking negative decision.
        resolved = {
            "schema_version": SCHEMA,
            "decisions": self.split["decisions"]
            + [decision("dec-sup-split", type="supersede", supersedes_decision_id="dec-split")],
        }
        can, after = compile_identity(self.obs, resolved)
        self.assertNotIn("AUTO_MATCH_BLOCKED_BY_DECISION", review_codes(after))
        self.assertEqual(after["review_count"], 0)

        # Sanity: the exact remote now auto-merges the observations.
        self.assertEqual(len(can["projects"]), 1)
        project = can["projects"][0]
        self.assertEqual(project["identity_anchor"]["kind"], "normalized_remote")
        self.assertFalse(project["merge_decision_ids"])
        unifying = [
            record
            for record in project["identity_evidence"]
            if record["kind"] == "normalized_remote"
        ]
        self.assertTrue(unifying, "expected a unifying normalized_remote evidence record")

        # Identical inputs do not re-open the review.
        _, third = compile_identity(self.obs, resolved)
        self.assertEqual(third["review_count"], 0)


class DecisionReferenceUnavailableRatchetTests(unittest.TestCase):
    """DECISION_REFERENCE_UNAVAILABLE (a decision references observations not present,
    e.g. an unavailable source) is resolved by superseding the stale decision so a
    missing source no longer blocks the present observations."""

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
        self.stale = {
            "schema_version": SCHEMA,
            "decisions": [
                decision(
                    "dec-merge-missing",
                    type="merge",
                    observation_ids=["obs-live", "obs-ghost"],
                )
            ],
        }

    def test_superseding_stale_decision_resolves(self) -> None:
        _, before = compile_identity(self.obs, self.stale)
        self.assertIn("DECISION_REFERENCE_UNAVAILABLE", review_codes(before))

        resolved = {
            "schema_version": SCHEMA,
            "decisions": self.stale["decisions"]
            + [
                decision(
                    "dec-sup-stale",
                    type="supersede",
                    supersedes_decision_id="dec-merge-missing",
                )
            ],
        }
        can, after = compile_identity(self.obs, resolved)
        self.assertNotIn("DECISION_REFERENCE_UNAVAILABLE", review_codes(after))
        self.assertEqual(after["review_count"], 0)
        # The present observation still compiles as a valid project.
        self.assertEqual(len(can["projects"]), 1)

        _, third = compile_identity(self.obs, resolved)
        self.assertEqual(third["review_count"], 0)


class DecisionSupersedeUnknownRatchetTests(unittest.TestCase):
    """DECISION_SUPERSEDE_UNKNOWN (a supersede references a decision missing from the
    registry) is resolved by adding the referenced decision so the supersede has a
    known target."""

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
        self.dangling = {
            "schema_version": SCHEMA,
            "decisions": [
                decision("dec-sup-ghost", type="supersede", supersedes_decision_id="dec-ghost")
            ],
        }

    def test_adding_referenced_decision_resolves(self) -> None:
        _, before = compile_identity(self.obs, self.dangling)
        self.assertIn("DECISION_SUPERSEDE_UNKNOWN", review_codes(before))

        # Resolve once: persist the decision the supersede referenced (it is then
        # marked superseded/inactive, which is the point of the earlier supersede).
        resolved = {
            "schema_version": SCHEMA,
            "decisions": self.dangling["decisions"]
            + [
                decision(
                    "dec-ghost",
                    type="canonical_key",
                    observation_id="obs-live",
                    key="homelab",
                    authority="operator",
                )
            ],
        }
        can, after = compile_identity(self.obs, resolved)
        self.assertNotIn("DECISION_SUPERSEDE_UNKNOWN", review_codes(after))
        self.assertEqual(after["review_count"], 0)
        self.assertEqual(len(can["projects"]), 1)

        # Identical observations do not re-open the review.
        _, third = compile_identity(self.obs, resolved)
        self.assertEqual(third["review_count"], 0)


if __name__ == "__main__":
    unittest.main()