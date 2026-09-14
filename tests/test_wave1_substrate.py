from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ledger.compiler import compile_state, load_manifest, orient_payload
from ledger.config import LedgerConfigError, describe_sources, validate_config
from ledger.ids import observation_id_for, source_id_for


class Wave1SubstrateTests(unittest.TestCase):
    def test_source_and_observation_ids_are_stable(self) -> None:
        root = {"path": "/tmp/projects", "label": "projects", "discovery": "children", "machine_name": "prime"}
        first_source = source_id_for(root, index=0)
        second_source = source_id_for(dict(root), index=0)
        self.assertEqual(first_source, second_source)
        entry = {"project_key": "github.com/example/project", "path": "/tmp/projects/project"}
        self.assertEqual(observation_id_for(entry, first_source), observation_id_for(dict(entry), second_source))

    def test_config_validation_rejects_bad_discovery(self) -> None:
        config = {"roots": [{"path": "/tmp", "discovery": "magic"}]}
        with self.assertRaises(LedgerConfigError) as ctx:
            validate_config(config, Path("/"), check_artifacts=False)
        self.assertEqual(ctx.exception.code, "DISCOVERY_UNSUPPORTED")

    def test_inventory_source_fingerprint_changes_with_inventory(self) -> None:
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
                        "discovery": "inventory_policy",
                        "inventory_jsonl": str(inventory),
                        "policy_path": str(policy),
                    }
                ]
            }
            validate_config(config, root)
            first = describe_sources(config, root)[0]["input_fingerprint"]
            inventory.write_text('{"path":"Projects"}\n{"path":"Projects/X"}\n', encoding="utf-8")
            second = describe_sources(config, root)[0]["input_fingerprint"]
            self.assertNotEqual(first, second)

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
                            {"path": str(live), "label": "live", "discovery": "children", "machine_name": "prime"},
                            {"path": str(missing), "label": "missing", "discovery": "children", "machine_name": "prime"},
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
            observations = json.loads((state_dir / "observations.json").read_text(encoding="utf-8"))
            self.assertEqual(observations["observation_count"], 1)
            self.assertTrue(observations["observations"][0]["observation_id"].startswith("obs_"))
            self.assertEqual(observations["observations"][0]["source_resolution"], "resolved")

            loaded = load_manifest(state_dir)
            orientation = orient_payload(loaded)
            self.assertEqual(orientation["health"]["state"], "degraded")
            self.assertIn("typed_observations", orientation["available_capabilities"])
            self.assertIn("project_capsules", orientation["unavailable_capabilities"])

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
            manifest = compile_state(
                config_path=config_path,
                compat_output_path=compat_path,
                state_dir=root / "state",
                generated_at="2026-09-14T06:10:00Z",
            )
            self.assertEqual(manifest["health"]["unresolved_observation_source_count"], 1)
            self.assertEqual(manifest["health"]["state"], "degraded")


if __name__ == "__main__":
    unittest.main()
