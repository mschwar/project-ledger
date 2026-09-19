"""Coverage for the P2.3 identity evidence model (ledger.evidence).

Asserts the records/scoring contract: strong vs weak vs negative classification,
observation-scope evidence extraction, pair scoring, per-canonical-project unifying
evidence summaries, the materialized identity-evidence store, and determinism.
Crucially it proves weak name/semantic similarity never becomes identity authority and
that negative/conflict evidence is recorded as negative.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ledger import IDENTITY_EVIDENCE_SCHEMA_VERSION
from ledger.compiler import compile_state
from ledger.evidence import (
    build_identity_evidence,
    evidence_kinds,
    evidence_strength,
    observation_evidence,
    score_pair,
)
from ledger.identity import compile_identity, materialize_identity


def observation(
    observation_id: str,
    *,
    project_key: str,
    name: str,
    path: str,
    remote_url: str = "",
    canonical_url: str = "",
    description: str = "",
    readme_sha256: str = "",
    source_id: str = "fixture",
) -> dict:
    return {
        "schema_version": "1.0.0",
        "observation_id": observation_id,
        "source_id": source_id,
        "snapshot_id": f"snap-{source_id}",
        "source_resolution": "resolved",
        "observed_at": "2026-09-19T06:00:00Z",
        "project_key": project_key,
        "location": path,
        "compat_entry": {
            "project_key": project_key,
            "name": name,
            "repo_name": Path(path).name,
            "description": description,
            "readme_sha256": readme_sha256,
            "readme_path": f"{path}/README.md" if readme_sha256 else "",
            "remote_url": remote_url,
            "canonical_url": canonical_url,
            "git": bool(remote_url),
            "path": path,
        },
    }


EMPTY_DECISIONS = {"schema_version": "1.0.0", "decisions": []}


class EvidenceTaxonomyTests(unittest.TestCase):
    def test_all_spec_evidence_kinds_are_scored(self) -> None:
        kinds = evidence_kinds()
        # Every P2.3-listed evidence type is modelled.
        for kind in (
            # strong
            "normalized_remote",
            "source_native_identity",
            "explicit_merge_decision",
            # weak referents / declarations
            "explicit_project_key",
            "raw_url",
            "path_locator",
            "path_migration",
            "display_name",
            "readme_compound",
            "name_similarity",
            "semantic_similarity",
            "cross_project_weak_overlap",
            # negative / conflict
            "negative_decision",
            "conflicting_decision",
        ):
            self.assertIn(kind, kinds, msg=f"evidence kind {kind} missing")

    def test_weak_similarity_is_explicitly_weak_and_referent_only(self) -> None:
        for kind in (
            "name_similarity",
            "semantic_similarity",
            "cross_project_weak_overlap",
        ):
            self.assertEqual(evidence_strength(kind), "weak", msg=kind)
            self.assertTrue(evidence_kinds()[kind]["referent_only"], msg=kind)

    def test_strong_and_negative_are_distinct(self) -> None:
        self.assertEqual(evidence_strength("normalized_remote"), "strong")
        self.assertEqual(evidence_strength("source_native_identity"), "strong")
        self.assertEqual(evidence_strength("negative_decision"), "negative")
        self.assertEqual(evidence_strength("conflicting_decision"), "negative")


class ObservationEvidenceTests(unittest.TestCase):
    def test_observation_records_strong_and_weak_kinds(self) -> None:
        obs = observation(
            "obs-a",
            project_key="homelab",
            name="Homelab Agent Platform",
            path="/repos/homelab",
            remote_url="https://github.com/mschwar/homelab.git",
            description="Fleet control plane.",
            readme_sha256="abc123",
        )
        records = observation_evidence(obs)
        by_kind = {r["kind"]: r for r in records}

        # Strong identity signals present and scored strong.
        self.assertEqual(by_kind["normalized_remote"]["strength"], "strong")
        self.assertEqual(
            by_kind["normalized_remote"]["value"], "github.com/mschwar/homelab"
        )
        self.assertEqual(by_kind["source_native_identity"]["strength"], "strong")
        self.assertIn("fixture", by_kind["source_native_identity"]["detail"])

        # Weak declaration / referent evidence present and scored weak.
        self.assertEqual(by_kind["explicit_project_key"]["strength"], "weak")
        self.assertEqual(by_kind["explicit_project_key"]["value"], "homelab")
        self.assertEqual(by_kind["path_locator"]["strength"], "weak")
        self.assertEqual(by_kind["display_name"]["strength"], "weak")
        self.assertEqual(by_kind["readme_compound"]["strength"], "weak")
        self.assertIn(
            "readme content hash present", by_kind["readme_compound"]["detail"]
        )

    def test_every_record_carries_a_stable_evidence_id(self) -> None:
        obs = observation(
            "obs-a",
            project_key="k",
            name="N",
            path="/p",
            remote_url="https://github.com/x/y.git",
        )
        first = observation_evidence(obs)
        second = observation_evidence(obs)
        self.assertEqual(
            [r["identity_evidence_id"] for r in first],
            [r["identity_evidence_id"] for r in second],
        )


class PairScoringTests(unittest.TestCase):
    def test_shared_exact_remote_scores_strong_match(self) -> None:
        left = observation(
            "obs-live",
            project_key="homelab",
            name="Homelab",
            path="/a/homelab",
            remote_url="https://github.com/mschwar/homelab.git",
        )
        right = observation(
            "obs-mirror",
            project_key="homelab",
            name="Homelab",
            path="/b/homelab",
            remote_url="git@github.com:mschwar/homelab.git",
        )
        result = score_pair(left, right)
        self.assertEqual(result["verdict"], "strong_match")
        self.assertEqual(result["shared_strong"], ["normalized_remote"])
        # The unifying remote evidence is strong; name overlap coexists but stays weak
        # and never masks the strong signal.
        self.assertIn("normalized_remote", [r["kind"] for r in result["evidence"]])
        strong = [r for r in result["evidence"] if r["strength"] == "strong"]
        self.assertTrue(strong)
        self.assertTrue(all(r["kind"] == "normalized_remote" for r in strong))

    def test_name_only_overlap_is_weak_and_never_strong(self) -> None:
        left = observation(
            "obs-a",
            project_key="a",
            name="Shared Website",
            path="/x/a",
            remote_url="https://github.com/one/a.git",
        )
        right = observation(
            "obs-b",
            project_key="b",
            name="Shared Website",
            path="/y/b",
            remote_url="https://github.com/two/b.git",
        )
        result = score_pair(left, right)
        self.assertEqual(result["verdict"], "weak")
        self.assertEqual(result["shared_strong"], [])
        self.assertIn("name_similarity", result["shared_weak"])
        # Every piece of evidence for the overlap is clearly weak.
        self.assertTrue(all(r["strength"] == "weak" for r in result["evidence"]))
        self.assertTrue(
            all(r["kind"] in {"name_similarity"} for r in result["evidence"])
        )

    def test_unrelated_observations_score_none(self) -> None:
        left = observation(
            "obs-a",
            project_key="a",
            name="Alpha",
            path="/x/a",
            remote_url="https://github.com/o/a.git",
        )
        right = observation(
            "obs-b",
            project_key="b",
            name="Beta",
            path="/y/b",
            remote_url="https://github.com/o/b.git",
        )
        self.assertEqual(score_pair(left, right)["verdict"], "none")


class ProjectEvidenceTests(unittest.TestCase):
    def test_exact_remote_auto_merge_records_strong_unifying_evidence(self) -> None:
        obs = [
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
        canonical, _ = compile_identity(obs, EMPTY_DECISIONS)
        project = canonical["projects"][0]
        self.assertEqual(len(project["observation_ids"]), 2)
        summary = project["identity_evidence_summary"]
        self.assertEqual(summary["unifying_authority"], "exact_normalized_remote")
        self.assertTrue(summary["automatic"])
        self.assertEqual(summary["strong_reason_code"], "EXACT_NORMALIZED_REMOTE")
        # Unifying strong evidence attributed to the whole project.
        unifying = [
            r
            for r in project["identity_evidence"]
            if set(r["observation_ids"]) == {"obs-live", "obs-mirror"}
        ]
        self.assertTrue(unifying)
        self.assertTrue(
            all(
                r["strength"] == "strong" and r["kind"] == "normalized_remote"
                for r in unifying
            )
        )

    def test_weak_similarity_does_not_merge_two_projects(self) -> None:
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
        canonical, reviews = compile_identity(obs, EMPTY_DECISIONS)
        # Same name + same key, but different remotes: must NOT auto-merge.
        self.assertEqual(canonical["canonical_project_count"], 2)
        self.assertTrue(
            any(r["code"] == "PROJECT_KEY_AMBIGUOUS" for r in reviews["items"])
        )

        store = build_identity_evidence(obs, canonical, EMPTY_DECISIONS)
        overlaps = store["cross_project_weak_overlaps"]
        self.assertEqual(len(overlaps), 1)
        overlap = overlaps[0]
        self.assertEqual(overlap["verdict"], "weak")
        self.assertTrue(all(r["strength"] == "weak" for r in overlap["evidence"]))

    def test_negative_decision_is_recorded_as_negative_evidence(self) -> None:
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
                    "rationale": "Separate conceptual projects despite shared remote.",
                }
            ],
        }
        canonical, _ = compile_identity(obs, decisions)
        self.assertEqual(canonical["canonical_project_count"], 2)
        store = build_identity_evidence(obs, canonical, decisions)
        negatives = [
            item
            for item in store["cross_project_weak_overlaps"]
            if any(r["kind"] == "negative_decision" for r in item["evidence"])
        ]
        self.assertEqual(len(negatives), 1)
        negative_record = next(
            r for r in negatives[0]["evidence"] if r["kind"] == "negative_decision"
        )
        self.assertEqual(negative_record["strength"], "negative")
        self.assertEqual(negative_record["value"], "dec-keep-separate")


class EvidenceStoreAndMaterializeTests(unittest.TestCase):
    def test_compile_state_writes_identity_evidence_artifact_and_registers_it(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live"
            live.mkdir()
            config_path = root / "ledger_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "roots": [
                            {
                                "path": str(live),
                                "label": "live",
                                "source_id": "live-primary",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            compat_path = root / "projects.json"
            compat_path.write_text(
                json.dumps(
                    {
                        "generated_at": "2026-09-19T06:00:00+00:00",
                        "entries": [
                            {
                                "project_key": "example",
                                "source_label": "live",
                                "path": str(live / "example"),
                                "name": "Example",
                                "remote_url": "https://github.com/mschwar/example.git",
                                "description": "Example project.",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            state_dir = root / "state"
            manifest = compile_state(
                config_path=config_path,
                compat_output_path=compat_path,
                state_dir=state_dir,
                generated_at="2026-09-19T06:10:00Z",
            )
            evidence_path = state_dir / "identity-evidence.json"
            self.assertTrue(evidence_path.is_file())
            self.assertEqual(
                manifest["capabilities"]["identity_evidence"]["state"], "available"
            )
            self.assertEqual(
                manifest["artifacts"]["identity_evidence"], str(evidence_path.resolve())
            )
            self.assertGreater(manifest["counts"]["identity_evidence"], 0)

            payload = json.loads(evidence_path.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["schema_version"], IDENTITY_EVIDENCE_SCHEMA_VERSION
            )
            self.assertGreater(payload["identity_evidence_count"], 0)
            self.assertTrue(payload["by_project"])
            sum_ = next(iter(payload["by_project"].values()))["summary"]
            self.assertEqual(sum_["unifying_authority"], "singleton")

    def test_materialize_identity_evidence_store_is_deterministic(self) -> None:
        obs = [
            observation(
                "obs-a",
                project_key="homelab",
                name="Homelab",
                path="/a",
                remote_url="https://github.com/mschwar/homelab.git",
            ),
            observation(
                "obs-b",
                project_key="homelab",
                name="Homelab",
                path="/b",
                remote_url="git@github.com:mschwar/homelab.git",
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            dir_a = Path(tmp) / "a"
            dir_a.mkdir()
            canonical_a, _ = materialize_identity(
                observations=obs,
                decisions_path=None,
                state_dir=dir_a,
                run_id="run-a",
                compiled_at="2026-09-19T06:10:00Z",
            )
            evidence_a = json.loads(
                (dir_a / "identity-evidence.json").read_text(encoding="utf-8")
            )

            dir_b = Path(tmp) / "b"
            dir_b.mkdir()
            materialize_identity(
                observations=obs[:],
                decisions_path=None,
                state_dir=dir_b,
                run_id="run-b",
                compiled_at="2026-09-19T06:20:00Z",
            )
            evidence_b = json.loads(
                (dir_b / "identity-evidence.json").read_text(encoding="utf-8")
            )

            self.assertEqual(evidence_a["by_project"], evidence_b["by_project"])
            self.assertEqual(
                evidence_a["cross_project_weak_overlaps"],
                evidence_b["cross_project_weak_overlaps"],
            )
            # The evidence store's first project resolves to the same canonical ID the
            # compiler assigned, confirming the membership key maps correctly.
            first_entry = next(iter(evidence_a["by_project"].values()))
            self.assertEqual(
                first_entry["canonical_project_id"],
                canonical_a["projects"][0]["canonical_project_id"],
            )


if __name__ == "__main__":
    unittest.main()
