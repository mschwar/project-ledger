from __future__ import annotations

import json
import unittest
from pathlib import Path

from ledger.identity import compile_identity

FIXTURE = Path(__file__).parent / "fixtures" / "r0-reality-cases.json"


class R0RealityRegressions(unittest.TestCase):
    """Ratchet the R0 live-provider-proof identity cases into durable regression tests.

    Cases captured from docs/work/R0-LIVE-PROVIDER-PROOF-20260919.md (run
    run_4c8e9084d4ca23637eac9267). The fixture reproduces the exact canonical
    project IDs and the single ambiguity review ID documented in that production
    proof, so the identity compiler cannot silently drift from demonstrated behavior.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls._compiled: tuple[dict, dict] | None = None

    def _compile(self) -> tuple[dict, dict]:
        if self._compiled is None:
            observations = self.fixture["observations"]
            self._compiled = compile_identity(
                observations,
                {"schema_version": "1.0.0", "decisions": []},
            )
        return self._compiled

    def test_fixture_reproduces_root_canonical_ids_from_production_proof(self) -> None:
        canonical, _ = self._compile()
        self.assertEqual(
            canonical["canonical_project_count"],
            self.fixture["expected"]["canonical_project_count"],
        )
        by_key = {p["project_key"]: p["canonical_project_id"] for p in canonical["projects"]}
        expected = self.fixture["expected"]["canonical_projects"]
        for key, canonical_id in expected.items():
            self.assertEqual(by_key[key], canonical_id, msg=f"canonical ID drift for {key}")

    def test_multi_manifestation_same_project_merges_by_exact_remote(self) -> None:
        canonical, _ = self._compile()
        homelab = next(p for p in canonical["projects"] if p["project_key"] == "homelab")
        self.assertEqual(
            homelab["observation_ids"],
            [
                "obs_cc508b56483db8d8e146107a",
                "obs_f0c43e37fa8e90f8dafd9d52",
            ],
        )
        white_rabbit = next(p for p in canonical["projects"] if p["project_key"] == "white-rabbit")
        self.assertEqual(
            white_rabbit["observation_ids"],
            [
                "obs_128ca37a8e8f71df0611b79b",
                "obs_9ddd0040fce7668f9c3aa442",
            ],
        )
        reality = next(p for p in canonical["projects"] if p["project_key"] == "reality-ledger")
        self.assertEqual(
            reality["observation_ids"],
            [
                "obs_d04229543f0f27e9cda304c7",
                "obs_e78c1dfefc3786c34da9bf7a",
            ],
        )

    def test_same_name_ambiguous_project_stays_split_and_opens_review(self) -> None:
        canonical, reviews = self._compile()
        garden = [p for p in canonical["projects"] if p["project_key"] == "the-garden"]
        self.assertEqual(len(garden), self.fixture["expected"]["the-garden_split_count"])
        self.assertTrue(all(len(p["observation_ids"]) == 1 for p in garden))

        self.assertEqual(reviews["review_count"], 1)
        item = reviews["items"][0]
        self.assertEqual(item["review_id"], self.fixture["expected"]["project_key_ambiguous_review_id"])
        self.assertEqual(item["code"], "PROJECT_KEY_AMBIGUOUS")
        self.assertEqual(item["state"], "open")
        self.assertEqual(
            item["affected_observation_ids"],
            self.fixture["expected"]["project_key_ambiguous_observations"],
        )

    def test_compile_is_deterministic_on_repeated_runs(self) -> None:
        first, first_reviews = self._compile()
        second, second_reviews = compile_identity(
            self.fixture["observations"],
            {"schema_version": "1.0.0", "decisions": []},
        )
        self.assertEqual(first["projects"], second["projects"])
        self.assertEqual(first_reviews["items"], second_reviews["items"])

    def test_fixture_observations_have_unique_ids(self) -> None:
        ids = [o["observation_id"] for o in self.fixture["observations"]]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()