"""Coverage for P2.5 canonical project IDs and compiler (ledger.identity).

Asserts the two P2.5 deliverables that were previously missing or defective:

1. Distinct canonical_project_id per conceptual project. A split/reject that keeps
   two observations sharing an exact remote in separate projects must NOT produce two
   projects with the same canonical_project_id (the legacy anchor quirk flagged in
   SCHEMA.md §1.8 / CURRENT.md as P2.5-owned). Each project falls back to a
   membership-scoped anchor so IDs are distinct and stable.
2. Resolved-field claim provenance. Each canonical project records, for its resolved
   project_key and display_name, which observation/decision/evidence produced the
   value, so a cold agent can see *why* a canonical value exists.
"""

from __future__ import annotations

import unittest

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
            "repo_name": path.split("/")[-1],
            "remote_url": remote_url,
            "canonical_url": canonical_url,
            "path": path,
        },
    }


EMPTY = {"schema_version": "1.0.0", "decisions": []}


class DistinctCanonicalIdTests(unittest.TestCase):
    def test_split_keeps_same_remote_observations_in_distinct_projects(self) -> None:
        # Two observations share the exact same normalized remote but an explicit split
        # keeps them as separate conceptual projects. They must get DISTINCT canonical
        # project IDs (P2.5: the shared remote can no longer anchor either).
        a = observation(
            "obs-a", project_key="dupe", name="Dupe", path="/a/dupe",
            remote_url="https://github.com/mschwar/dupe.git",
        )
        b = observation(
            "obs-b", project_key="dupe", name="Dupe", path="/b/dupe",
            remote_url="git@github.com:mschwar/dupe.git",
        )
        decisions = {
            "schema_version": "1.0.0",
            "decisions": [
                {
                    "decision_id": "dec-split",
                    "type": "split",
                    "observation_ids": ["obs-a", "obs-b"],
                }
            ],
        }
        canonical, reviews = compile_identity([a, b], decisions)
        self.assertEqual(canonical["canonical_project_count"], 2)
        ids = [p["canonical_project_id"] for p in canonical["projects"]]
        self.assertEqual(len(ids), len(set(ids)), "canonical_project_id must be unique per project")
        # Neither project may anchor on the shared remote (it is not unique to either).
        for project in canonical["projects"]:
            self.assertNotEqual(project["identity_anchor"]["kind"], "normalized_remote")
            self.assertEqual(project["identity_anchor"]["kind"], "observation")
        # The split is surfaced as a bounded review, not silently dropped.
        self.assertTrue(
            any(item["code"] == "AUTO_MATCH_BLOCKED_BY_DECISION" for item in reviews["items"])
        )

    def test_reject_match_keeps_same_remote_observations_in_distinct_projects(self) -> None:
        a = observation(
            "obs-a", project_key="dupe", name="Dupe", path="/a/dupe",
            remote_url="https://github.com/mschwar/dupe.git",
        )
        b = observation(
            "obs-b", project_key="dupe", name="Dupe", path="/b/dupe",
            remote_url="git@github.com:mschwar/dupe.git",
        )
        decisions = {
            "schema_version": "1.0.0",
            "decisions": [
                {
                    "decision_id": "dec-reject",
                    "type": "reject_match",
                    "left_observation_id": "obs-a",
                    "right_observation_id": "obs-b",
                }
            ],
        }
        canonical, _ = compile_identity([a, b], decisions)
        ids = [p["canonical_project_id"] for p in canonical["projects"]]
        self.assertEqual(len(ids), len(set(ids)), "canonical_project_id must be unique per project")

    def test_unique_remote_still_anchors_normalized_remote(self) -> None:
        # A remote unique to one project must keep the normalized_remote anchor (the
        # preferred, stable anchor) — the P2.5 change must not degrade the common case.
        a = observation(
            "obs-a", project_key="osprey", name="Osprey", path="/a/osprey",
            remote_url="https://github.com/mschwar/osprey.git",
        )
        b = observation(
            "obs-b", project_key="osprey", name="Osprey", path="/b/osprey",
            remote_url="git@github.com:mschwar/osprey.git",
        )
        canonical, _ = compile_identity([a, b], EMPTY)
        self.assertEqual(canonical["canonical_project_count"], 1)
        project = canonical["projects"][0]
        self.assertEqual(project["identity_anchor"]["kind"], "normalized_remote")
        self.assertEqual(project["identity_anchor"]["value"], "github.com/mschwar/osprey")

    def test_split_ids_are_stable_across_repeated_compiles(self) -> None:
        a = observation(
            "obs-a", project_key="dupe", name="Dupe", path="/a/dupe",
            remote_url="https://github.com/mschwar/dupe.git",
        )
        b = observation(
            "obs-b", project_key="dupe", name="Dupe", path="/b/dupe",
            remote_url="git@github.com:mschwar/dupe.git",
        )
        decisions = {
            "schema_version": "1.0.0",
            "decisions": [
                {
                    "decision_id": "dec-split",
                    "type": "split",
                    "observation_ids": ["obs-a", "obs-b"],
                }
            ],
        }
        first, _ = compile_identity([a, b], decisions)
        second, _ = compile_identity([a, b], decisions)
        self.assertEqual(
            [p["canonical_project_id"] for p in first["projects"]],
            [p["canonical_project_id"] for p in second["projects"]],
        )


class ResolvedFieldProvenanceTests(unittest.TestCase):
    def test_project_key_from_single_declaration_has_observation_provenance(self) -> None:
        a = observation(
            "obs-a", project_key="dupe", name="Dupe", path="/a/dupe",
            remote_url="https://github.com/mschwar/dupe.git",
        )
        canonical, _ = compile_identity([a], EMPTY)
        project = canonical["projects"][0]
        self.assertIn("resolved_fields", project)
        key_prov = project["resolved_fields"]["project_key"]["provenance"]
        self.assertEqual(key_prov[0]["kind"], "declaration")
        self.assertEqual(key_prov[0]["observation_id"], "obs-a")
        self.assertEqual(key_prov[0]["reason_code"], "PROJECT_KEY_DECLARATION")

    def test_project_key_from_canonical_key_decision_has_decision_provenance(self) -> None:
        a = observation(
            "obs-a", project_key="dupe", name="Dupe", path="/a/dupe",
            remote_url="https://github.com/mschwar/dupe.git",
        )
        decisions = {
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
        canonical, _ = compile_identity([a], decisions)
        project = canonical["projects"][0]
        self.assertEqual(project["project_key"], "stable-key")
        key_prov = project["resolved_fields"]["project_key"]["provenance"]
        self.assertEqual(key_prov[0]["kind"], "decision")
        self.assertEqual(key_prov[0]["decision_id"], "dec-key")
        self.assertEqual(key_prov[0]["reason_code"], "CANONICAL_KEY_DECISION")

    def test_project_key_from_unique_remote_has_observed_provenance(self) -> None:
        # Two observations with the same project_key but a unique shared remote: the
        # remote is the stable key, and provenance points at the observed remote.
        a = observation(
            "obs-a", project_key="north", name="Meridian", path="/a/north",
            remote_url="https://github.com/mschwar/north.git",
        )
        b = observation(
            "obs-b", project_key="south", name="Meridian", path="/b/south",
            remote_url="git@github.com:mschwar/north.git",
        )
        canonical, _ = compile_identity([a, b], EMPTY)
        project = canonical["projects"][0]
        self.assertEqual(project["project_key"], "github.com/mschwar/north")
        key_prov = project["resolved_fields"]["project_key"]["provenance"]
        self.assertEqual(key_prov[0]["kind"], "observed")
        self.assertEqual(key_prov[0]["value"], "github.com/mschwar/north")
        self.assertEqual(key_prov[0]["reason_code"], "EXACT_NORMALIZED_REMOTE")

    def test_display_name_has_observation_provenance(self) -> None:
        a = observation(
            "obs-a", project_key="dupe", name="Dupe", path="/a/dupe",
            remote_url="https://github.com/mschwar/dupe.git",
        )
        canonical, _ = compile_identity([a], EMPTY)
        project = canonical["projects"][0]
        name_prov = project["resolved_fields"]["display_name"]["provenance"]
        self.assertEqual(name_prov[0]["kind"], "observed")
        self.assertEqual(name_prov[0]["observation_id"], "obs-a")
        self.assertEqual(name_prov[0]["reason_code"], "DISPLAY_NAME")


if __name__ == "__main__":
    unittest.main()
