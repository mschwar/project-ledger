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

import tempfile
import unittest
from pathlib import Path

from ledger.explain import explain_payload
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

    def test_split_same_remote_projects_auto_disambiguate_project_key_and_resolve_individually(
        self,
    ) -> None:
        a = observation(
            "obs-a",
            project_key="dupe",
            name="Dupe",
            path="/a/dupe",
            remote_url="https://github.com/mschwar/dupe.git",
        )
        b = observation(
            "obs-b",
            project_key="dupe",
            name="Dupe",
            path="/b/dupe",
            remote_url="git@github.com:mschwar/dupe.git",
        )
        decisions_doc = {
            "schema_version": "1.0.0",
            "decisions": [
                {
                    "decision_id": "dec-split",
                    "type": "split",
                    "observation_ids": ["obs-a", "obs-b"],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            decisions_path = Path(tmp) / "decisions.json"
            import json as _json

            decisions_path.write_text(_json.dumps(decisions_doc), encoding="utf-8")
            canonical, reviews = materialize_identity(
                observations=[a, b],
                decisions_path=decisions_path,
                state_dir=state_dir,
                run_id="run_000000000000000000000001",
                compiled_at="2026-11-01T02:30:00Z",
            )
            self.assertEqual(canonical["canonical_project_count"], 2)
            projs = sorted(canonical["projects"], key=lambda p: p["canonical_project_id"])
            p0, p1 = projs[0], projs[1]
            expected_key_0 = f"dupe-{p0['canonical_project_id'].removeprefix('prj_')[:8]}"
            expected_key_1 = f"dupe-{p1['canonical_project_id'].removeprefix('prj_')[:8]}"
            self.assertEqual(p0["project_key"], expected_key_0)
            self.assertEqual(p1["project_key"], expected_key_1)
            self.assertNotEqual(p0["project_key"], p1["project_key"])

            # Raw key hint remains on both projects and PROJECT_KEY_AMBIGUOUS stays open.
            for proj in (p0, p1):
                self.assertEqual(proj["project_key_hints"], ["dupe"])
                self.assertTrue(
                    any(
                        ref["kind"] == "project_key_hint" and ref["value"] == "dupe"
                        for ref in proj["referents"]
                    )
                )
                self.assertEqual(
                    proj["resolved_fields"]["project_key"]["provenance"][-1]["reason_code"],
                    "DISAMBIGUATED_SPLIT_PROJECT_KEY",
                )
            review_codes = {item["code"] for item in reviews["items"]}
            self.assertIn("AUTO_MATCH_BLOCKED_BY_DECISION", review_codes)
            self.assertIn("PROJECT_KEY_AMBIGUOUS", review_codes)

            # Each disambiguated key resolves individually; raw 'dupe' returns ambiguous with distinct keys.
            res0 = resolve_payload(state_dir, expected_key_0)
            self.assertEqual(res0["status"], "resolved")
            self.assertEqual(res0["canonical_project_id"], p0["canonical_project_id"])

            res1 = resolve_payload(state_dir, expected_key_1)
            self.assertEqual(res1["status"], "resolved")
            self.assertEqual(res1["canonical_project_id"], p1["canonical_project_id"])

            res_raw = resolve_payload(state_dir, "dupe")
            self.assertEqual(res_raw["status"], "ambiguous")
            cand_keys = sorted(c["project_key"] for c in res_raw["candidates"])
            self.assertEqual(cand_keys, sorted([expected_key_0, expected_key_1]))


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


class HistoricalCanonicalIdLineageTests(unittest.TestCase):
    def test_local_to_remote_upgrade_and_remote_rename_retain_historical_canonical_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            # Run 1: local repo with no git remote yet -> observation-anchored prj_* ID.
            local_obs = observation(
                "obs-local",
                project_key="sprout",
                name="Sprout",
                path="/central/repos/active/sprout",
            )
            c1, _ = materialize_identity(
                observations=[local_obs],
                decisions_path=None,
                state_dir=state_dir,
                run_id="run-1",
                compiled_at="2026-10-31T10:00:00Z",
            )
            pid_local = c1["projects"][0]["canonical_project_id"]
            self.assertEqual(c1["projects"][0]["identity_anchor"]["kind"], "observation")

            # Run 2: git origin remote is added -> re-anchors to normalized_remote prj_* ID.
            remote_v1 = observation(
                "obs-local",
                project_key="sprout",
                name="Sprout",
                path="/central/repos/active/sprout",
                remote_url="https://github.com/mschwar/sprout.git",
            )
            c2, _ = materialize_identity(
                observations=[remote_v1],
                decisions_path=None,
                state_dir=state_dir,
                run_id="run-2",
                compiled_at="2026-10-31T11:00:00Z",
            )
            pid_remote_v1 = c2["projects"][0]["canonical_project_id"]
            self.assertNotEqual(pid_local, pid_remote_v1)

            res_from_local = resolve_payload(state_dir, pid_local)
            self.assertEqual(res_from_local["status"], "resolved")
            self.assertEqual(res_from_local["canonical_project_id"], pid_remote_v1)
            self.assertEqual(res_from_local["matched_by"][0]["kind"], "historical_canonical_id")

            # Run 3: remote repository is renamed on GitHub -> re-anchors again; BOTH prior IDs resolve!
            remote_v2 = observation(
                "obs-local",
                project_key="sprout-core",
                name="Sprout Core",
                path="/central/repos/active/sprout",
                remote_url="https://github.com/mschwar/sprout-core.git",
            )
            c3, _ = materialize_identity(
                observations=[remote_v2],
                decisions_path=None,
                state_dir=state_dir,
                run_id="run-3",
                compiled_at="2026-10-31T12:00:00Z",
            )
            pid_remote_v2 = c3["projects"][0]["canonical_project_id"]
            self.assertNotEqual(pid_remote_v1, pid_remote_v2)

            for old_pid in (pid_local, pid_remote_v1):
                resolved = resolve_payload(state_dir, old_pid)
                self.assertEqual(resolved["status"], "resolved")
                self.assertEqual(resolved["canonical_project_id"], pid_remote_v2)
                explained = explain_payload(state_dir, old_pid)
                self.assertEqual(explained["status"], "resolved")
                self.assertEqual(explained["canonical_project_id"], pid_remote_v2)


if __name__ == "__main__":
    unittest.main()
