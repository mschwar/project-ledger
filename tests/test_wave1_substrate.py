from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ledger import cli
from ledger.compiler import compile_state, load_manifest, orient_payload
from ledger.config import LedgerConfigError, describe_sources, validate_config
from ledger.ids import observation_id_for, source_id_for


class Wave1SubstrateTests(unittest.TestCase):
    def test_source_and_observation_ids_are_stable(self) -> None:
        root = {"path": "/tmp/projects", "label": "projects", "discovery": "children", "machine_name": "prime"}
        first_source = source_id_for(root, index=0)
        second_source = source_id_for(dict(root), index=99)
        self.assertEqual(first_source, second_source)

        entry = {"project_key": "github.com/example/project", "path": "/tmp/projects/project"}
        renamed_claim = dict(entry, project_key="renamed-project-key")
        self.assertEqual(
            observation_id_for(entry, first_source),
            observation_id_for(renamed_claim, second_source),
        )

    def test_config_validation_rejects_bad_discovery(self) -> None:
        config = {"roots": [{"path": "/tmp", "discovery": "magic"}]}
        with self.assertRaises(LedgerConfigError) as ctx:
            validate_config(config, Path("/"), check_artifacts=False)
        self.assertEqual(ctx.exception.code, "DISCOVERY_UNSUPPORTED")

    def test_config_validation_rejects_duplicate_source_ids(self) -> None:
        config = {
            "roots": [
                {"path": "/tmp/a", "source_id": "primary"},
                {"path": "/tmp/b", "source_id": "primary"},
            ]
        }
        with self.assertRaises(LedgerConfigError) as ctx:
            validate_config(config, Path("/"), check_artifacts=False)
        self.assertEqual(ctx.exception.code, "SOURCE_ID_DUPLICATE")

    def test_inventory_probe_fingerprint_changes_with_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mount = root / "drive"
            mount.mkdir()
            inventory = root / "inventory.jsonl"
            inventory.write_text('{"path":"Projects"}\n', encoding="utf-8")
            policy = root / "policy.json"
            policy.write_text('{"roots":{}}', encoding="utf-8")
            config = {
                "roots": [
                    {
                        "path": str(mount),
                        "label": "drive",
                        "source_id": "google-drive",
                        "discovery": "inventory_policy",
                        "inventory_jsonl": str(inventory),
                        "policy_path": str(policy),
                    }
                ]
            }
            validate_config(config, root)
            first_source = describe_sources(config, root)[0]
            first_fingerprint = first_source["probe_fingerprint"]
            inventory.write_text('{"path":"Projects"}\n{"path":"Projects/X"}\n', encoding="utf-8")
            second_source = describe_sources(config, root)[0]
            self.assertNotEqual(first_fingerprint, second_source["probe_fingerprint"])
            self.assertEqual(first_source["source_id"], second_source["source_id"])

    def test_compile_emits_manifest_and_typed_observations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live"
            live.mkdir()
            missing = root / "missing"
            config_path = root / "ledger_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "roots": [
                            {
                                "path": str(live),
                                "label": "live",
                                "source_id": "live-primary",
                                "discovery": "children",
                                "machine_name": "prime",
                            },
                            {
                                "path": str(missing),
                                "label": "missing",
                                "source_id": "missing-source",
                                "discovery": "children",
                                "machine_name": "prime",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            compat_path = root / "projects.json"
            compat_path.write_text(
                json.dumps(
                    {
                        "generated_at": "2026-09-14T06:00:00+00:00",
                        "entries": [
                            {
                                "project_key": "example",
                                "source_label": "live",
                                "path": str(live / "example"),
                                "name": "Example",
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
                generated_at="2026-09-14T06:10:00Z",
            )
            self.assertEqual(manifest["schema_version"], "1.0.0")
            self.assertEqual(manifest["health"]["state"], "degraded")
            self.assertEqual(manifest["counts"]["observations"], 1)
            self.assertIsNone(manifest["counts"]["canonical_projects"])
            self.assertEqual(manifest["capabilities"]["canonical_projects"]["state"], "unavailable")
            self.assertTrue((state_dir / "system-manifest.json").is_file())

            live_source = next(item for item in manifest["sources"] if item["source_id"] == "live-primary")
            self.assertTrue(live_source["compat_snapshot_id"].startswith("snap_"))
            self.assertEqual(live_source["compat_snapshot_as_of"], "2026-09-14T06:00:00+00:00")

            observations = json.loads((state_dir / "observations.json").read_text(encoding="utf-8"))
            self.assertEqual(observations["observation_count"], 1)
            observation = observations["observations"][0]
            self.assertTrue(observation["observation_id"].startswith("obs_"))
            self.assertEqual(observation["source_id"], "live-primary")
            self.assertEqual(observation["snapshot_id"], live_source["compat_snapshot_id"])
            self.assertEqual(observation["source_resolution"], "resolved")

            loaded = load_manifest(state_dir)
            orientation = orient_payload(loaded)
            self.assertEqual(orientation["health"]["state"], "degraded")
            self.assertIn("typed_observations", orientation["available_capabilities"])
            self.assertIn("project_capsules", orientation["unavailable_capabilities"])

    def test_compat_snapshot_changes_when_observation_run_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live"
            live.mkdir()
            config_path = root / "ledger_config.json"
            config_path.write_text(
                json.dumps({"roots": [{"path": str(live), "label": "live", "source_id": "live-primary"}]}),
                encoding="utf-8",
            )
            compat_path = root / "projects.json"
            compat_path.write_text(json.dumps({"generated_at": "2026-09-14T06:00:00Z", "entries": []}), encoding="utf-8")
            first = compile_state(
                config_path=config_path,
                compat_output_path=compat_path,
                state_dir=root / "state-a",
                generated_at="2026-09-14T06:10:00Z",
            )
            compat_path.write_text(json.dumps({"generated_at": "2026-09-14T07:00:00Z", "entries": []}), encoding="utf-8")
            second = compile_state(
                config_path=config_path,
                compat_output_path=compat_path,
                state_dir=root / "state-b",
                generated_at="2026-09-14T07:10:00Z",
            )
            self.assertNotEqual(first["sources"][0]["compat_snapshot_id"], second["sources"][0]["compat_snapshot_id"])

    def test_compile_survives_missing_inventory_artifact_and_reports_degraded_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mount = root / "drive"
            mount.mkdir()
            missing_inventory = root / "missing.jsonl"
            policy = root / "policy.json"
            policy.write_text('{"roots":{}}', encoding="utf-8")
            config_path = root / "ledger_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "roots": [
                            {
                                "path": str(mount),
                                "label": "drive",
                                "source_id": "drive-inventory",
                                "discovery": "inventory_policy",
                                "inventory_jsonl": str(missing_inventory),
                                "policy_path": str(policy),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            compat_path = root / "projects.json"
            compat_path.write_text(json.dumps({"entries": []}), encoding="utf-8")

            with self.assertRaises(LedgerConfigError):
                config = json.loads(config_path.read_text(encoding="utf-8"))
                validate_config(config, root, check_artifacts=True)

            manifest = compile_state(
                config_path=config_path,
                compat_output_path=compat_path,
                state_dir=root / "state",
                generated_at="2026-09-14T06:10:00Z",
            )
            self.assertEqual(manifest["health"]["state"], "degraded")
            self.assertEqual(manifest["health"]["source_unavailable_count"], 1)
            self.assertEqual(manifest["sources"][0]["status"], "unavailable")
            self.assertEqual(manifest["sources"][0]["artifacts"][0]["state"], "unavailable")

    def test_compile_surfaces_unresolved_source_instead_of_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live"
            live.mkdir()
            config_path = root / "ledger_config.json"
            config_path.write_text(json.dumps({"roots": [{"path": str(live), "label": "live"}]}), encoding="utf-8")
            compat_path = root / "projects.json"
            compat_path.write_text(
                json.dumps({"entries": [{"project_key": "x", "source_label": "mystery", "path": "/x"}]}),
                encoding="utf-8",
            )
            state_dir = root / "state"
            manifest = compile_state(
                config_path=config_path,
                compat_output_path=compat_path,
                state_dir=state_dir,
                generated_at="2026-09-14T06:10:00Z",
            )
            self.assertEqual(manifest["health"]["unresolved_observation_source_count"], 1)
            self.assertEqual(manifest["health"]["state"], "degraded")
            observations = json.loads((state_dir / "observations.json").read_text(encoding="utf-8"))
            unresolved = observations["observations"][0]
            self.assertEqual(unresolved["source_resolution"], "unresolved")
            self.assertTrue(unresolved["source_id"].startswith("src_"))
            self.assertTrue(unresolved["snapshot_id"].startswith("snap_"))

    def test_refresh_orchestrates_scan_then_compile_without_mutating_repo_fixture(self) -> None:
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
                                "source_id": "refresh-fixture",
                                "path": str(live),
                                "label": "refresh-fixture",
                                "discovery": "children",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            output_dir = root / "output"
            state_dir = root / "state"

            def fake_scan(*_args, **_kwargs):
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / "projects.json").write_text(
                    json.dumps(
                        {
                            "generated_at": "2026-09-14T06:00:00Z",
                            "entries": [
                                {
                                    "project_key": "sample-project",
                                    "source_label": "refresh-fixture",
                                    "path": str(live / "sample-project"),
                                    "name": "Sample Project",
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                return mock.Mock(returncode=0)

            with mock.patch.object(cli.subprocess, "run", side_effect=fake_scan) as run_scan:
                result = cli.main(
                    [
                        "refresh",
                        "--config",
                        str(config_path),
                        "--output-dir",
                        str(output_dir),
                        "--state-dir",
                        str(state_dir),
                    ]
                )

            self.assertEqual(result, 0)
            run_scan.assert_called_once()
            self.assertTrue((state_dir / "system-manifest.json").is_file())
            manifest = load_manifest(state_dir)
            self.assertEqual(manifest["counts"]["observations"], 1)
            self.assertEqual(manifest["sources"][0]["source_id"], "refresh-fixture")


if __name__ == "__main__":
    unittest.main()
