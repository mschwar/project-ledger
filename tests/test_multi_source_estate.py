from __future__ import annotations

import json
import unittest
from pathlib import Path

from ledger.identity import compile_identity

FIXTURE = Path(__file__).parent / "fixtures" / "multi-source-estate.json"


class MultiSourceEstateAcceptance(unittest.TestCase):
    """Prove the canonical-identity compiler against the full multi-source estate matrix.

    The fixture pins the exact deterministic output (canonical IDs, observation
    memberships, review IDs) the compiler currently produces for each estate column
    (live+mirror+backup, renamed/moved, same-name unrelated, missing remote, divergent
    sidecars, inventory-only, inaccessible source). Any future behavior drift that makes
    these cases behave differently fails here, so canonical identity cannot silently
    diverge from demonstrated estate behavior.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.cases = cls.fixture["cases"]

    def _compile_case(self, case: dict) -> tuple[dict, dict]:
        decisions = {"schema_version": "1.0.0", "decisions": case.get("decisions", [])}
        return compile_identity(case["observations"], decisions)

    def _pinned_by_id(self, expected: dict) -> dict:
        return {
            entry["canonical_project_id"]: entry
            for entries in expected["by_project_key"].values()
            for entry in entries
        }

    def test_every_case_reproduces_its_pinned_canonical_id(self) -> None:
        for case in self.cases:
            canonical, _ = self._compile_case(case)
            expected = case["expected"]
            self.assertEqual(
                canonical["canonical_project_count"],
                expected["canonical_project_count"],
                msg=f"{case['case_id']}: canonical project count drifted",
            )
            pinned_by_id = self._pinned_by_id(expected)
            for project in canonical["projects"]:
                self.assertIn(
                    project["canonical_project_id"],
                    pinned_by_id,
                    msg=f"{case['case_id']}: canonical ID {project['canonical_project_id']} not pinned",
                )

    def test_every_case_reproduces_pinned_observation_membership(self) -> None:
        for case in self.cases:
            canonical, _ = self._compile_case(case)
            expected = self._pinned_by_id(case["expected"])
            for project in canonical["projects"]:
                pinned = expected[project["canonical_project_id"]]
                self.assertEqual(
                    project["observation_ids"],
                    pinned["observation_ids"],
                    msg=f"{case['case_id']}: observation membership drifted for {pinned['canonical_project_id']}",
                )
                self.assertEqual(
                    project["normalized_remotes"],
                    pinned["normalized_remotes"],
                    msg=f"{case['case_id']}: normalized remotes drifted for {pinned['canonical_project_id']}",
                )
                self.assertEqual(
                    project["project_key_hints"],
                    pinned["project_key_hints"],
                    msg=f"{case['case_id']}: project key hints drifted for {pinned['canonical_project_id']}",
                )

    def test_every_case_reproduces_pinned_reviews(self) -> None:
        for case in self.cases:
            canonical, reviews = self._compile_case(case)
            expected = case["expected"]
            self.assertEqual(reviews["review_count"], expected["review_count"], msg=case["case_id"])
            self.assertEqual(
                [(i["code"], i["review_id"], i["affected_observation_ids"], i["state"]) for i in reviews["items"]],
                [(i["code"], i["review_id"], i["affected_observation_ids"], i["state"]) for i in expected["reviews"]],
                msg=f"{case['case_id']}: review queue drifted",
            )

    def test_cases_are_deterministic_on_repeated_compiles(self) -> None:
        for case in self.cases:
            first, first_reviews = self._compile_case(case)
            second, second_reviews = self._compile_case(case)
            self.assertEqual(first["projects"], second["projects"], msg=f"{case['case_id']}: canonical non-determinism")
            self.assertEqual(
                first_reviews["items"], second_reviews["items"], msg=f"{case['case_id']}: review non-determinism"
            )

    def test_estate_1_live_mirror_backup_merge_has_single_remote_anchor_and_no_review(self) -> None:
        case = next(c for c in self.cases if c["case_id"] == "estate-1")
        canonical, reviews = self._compile_case(case)
        self.assertEqual(canonical["canonical_project_count"], 1)
        project = canonical["projects"][0]
        self.assertEqual(project["identity_anchor"]["kind"], "normalized_remote")
        self.assertEqual(project["identity_anchor"]["value"], "github.com/mschwar/osprey")
        self.assertEqual(reviews["review_count"], 0)

    def test_estate_5_divergent_sidecar_keeps_remote_key_and_both_hints_without_review(self) -> None:
        case = next(c for c in self.cases if c["case_id"] == "estate-5")
        canonical, reviews = self._compile_case(case)
        self.assertEqual(canonical["canonical_project_count"], 1)
        project = canonical["projects"][0]
        # Remote is the stable key, not either conflicting sidecar claim.
        self.assertEqual(project["project_key"], "github.com/mschwar/north")
        self.assertEqual(set(project["project_key_hints"]), {"north", "south"})
        self.assertEqual(reviews["review_count"], 0)

    def test_estate_2_renamed_project_resolves_by_both_paths(self) -> None:
        # The fixture pins one canonical project for the moved path pair; confirm both
        # observations are members so a moved working copy does not split identity.
        case = next(c for c in self.cases if c["case_id"] == "estate-2")
        canonical, _ = self._compile_case(case)
        project = canonical["projects"][0]
        self.assertEqual(set(project["observation_ids"]), {"m2-new", "m2-old"})
        self.assertEqual(project["canonical_project_id"], "prj_e7d560d6730f180262ea80bf")

    def test_estate_7_unavailable_source_decision_is_bounded_review(self) -> None:
        case = next(c for c in self.cases if c["case_id"] == "estate-7")
        canonical, reviews = self._compile_case(case)
        # Present observations still compile.
        self.assertEqual(canonical["canonical_project_count"], 1)
        self.assertEqual(reviews["review_count"], 1)
        item = reviews["items"][0]
        self.assertEqual(item["code"], "DECISION_REFERENCE_UNAVAILABLE")
        self.assertIn("obs-today-unavailable", item["affected_observation_ids"])


if __name__ == "__main__":
    unittest.main()