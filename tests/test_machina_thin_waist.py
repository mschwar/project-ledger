"""Verification tests for Project Ledger's Machina thin-waist onboarding (Consumer Adapter v1)."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from ledger.identity import IDENTITY_DECISION_SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "scripts" / "machina_adapter.sh"
SPECS_DIR = ROOT / ".machina" / "specs"

REQUIRED_SPEC_KEYS = {
    "id",
    "title",
    "objective",
    "authority",
    "initial_next_action",
    "completion",
}

REQUIRED_ORIENT_KEYS = {
    "result",
    "schema_version",
    "generated_at",
    "state_updated_at",
    "state_sequence",
    "execution_state",
    "slice",
    "attempt_id",
    "active_unit",
    "situation",
    "next_transition",
    "uncertainty",
}


def _populate_minimal_ledger_root(target: Path) -> None:
    sample_source = target / "sources" / "sample"
    sample_source.mkdir(parents=True, exist_ok=True)
    (target / "registry").mkdir(parents=True, exist_ok=True)
    (target / "ledger_config.json").write_text(
        json.dumps(
            {
                "defaults": {"min_score": 2},
                "roots": [
                    {
                        "source_id": "sample-live",
                        "path": str(sample_source),
                        "label": "sample-live",
                        "discovery": "children",
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (target / "registry" / "identity-decisions.json").write_text(
        json.dumps(
            {
                "schema_version": IDENTITY_DECISION_SCHEMA_VERSION,
                "decisions": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    for doc in ("CURRENT.md", "SYSTEM.md", "SCHEMA.md"):
        (target / doc).write_text("# Doc\n", encoding="utf-8")


class MachinaThinWaistTests(unittest.TestCase):
    def test_work_unit_specs_conform_to_v1_and_have_no_em_dashes(self) -> None:
        specs = sorted(SPECS_DIR.glob("*.json"))
        self.assertGreaterEqual(len(specs), 1)
        for spec_path in specs:
            raw = spec_path.read_text(encoding="utf-8")
            self.assertNotIn("\u2014", raw, f"em dash found in {spec_path.name}")
            data = json.loads(raw)
            self.assertTrue(REQUIRED_SPEC_KEYS.issubset(data.keys()), spec_path.name)
            for key in ("id", "title", "objective", "initial_next_action", "completion"):
                self.assertIsInstance(data[key], str)
                self.assertTrue(data[key].strip())
            auth = data["authority"]
            self.assertIsInstance(auth, dict)
            self.assertIsInstance(auth.get("allowed"), list)
            self.assertIsInstance(auth.get("forbidden"), list)
            self.assertTrue(auth["allowed"])
            self.assertTrue(auth["forbidden"])

    def test_adapter_and_test_files_contain_no_em_dashes(self) -> None:
        for path in (ADAPTER, Path(__file__).resolve()):
            self.assertNotIn("\u2014", path.read_text(encoding="utf-8"), path.name)

    def test_adapter_orient_returns_valid_orient_packet_v1(self) -> None:
        proc = subprocess.run(
            ["bash", str(ADAPTER), "orient"],
            check=True,
            capture_output=True,
            text=True,
        )
        packet = json.loads(proc.stdout)
        self.assertEqual(packet["result"], "ORIENT")
        self.assertTrue(REQUIRED_ORIENT_KEYS.issubset(packet.keys()))
        self.assertIn(
            packet["execution_state"],
            {"idle", "work_ready", "running", "frontier"},
        )
        self.assertGreaterEqual(int(packet["state_sequence"]), 6)
        self.assertIsNotNone(packet.get("last_completed"))
        self.assertTrue(str(packet["last_completed"].get("unit_id") or "").strip())

    def test_adapter_map_projects_ledger_domain_truth_without_execution_state_bleed(self) -> None:
        env = dict(os.environ, LEDGER_ADAPTER_SKIP_GIT_CHECK="1")
        proc = subprocess.run(
            ["bash", str(ADAPTER), "map"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        kv = dict(line.split("=", 1) for line in proc.stdout.strip().splitlines())
        self.assertEqual(kv["ledger_has_execution_state_field"], "no")
        self.assertEqual(kv["canonical_config"], "ledger_config.json")
        self.assertEqual(kv["validate_status"], "ok")
        self.assertGreaterEqual(int(kv["configured_source_count"]), 1)
        self.assertGreaterEqual(int(kv["identity_decision_count"]), 60)
        if (ROOT / "state" / "system-manifest.json").exists():
            self.assertEqual(kv["manifest_state"], "present")
            self.assertEqual(kv["manifest_health"], "ok")
            self.assertGreaterEqual(int(kv["source_count"]), 1)
            self.assertGreaterEqual(int(kv["observation_count"]), 1)
            self.assertGreaterEqual(int(kv["canonical_project_count"]), 1)
            self.assertGreaterEqual(int(kv["review_item_count"]), 0)
        else:
            self.assertEqual(kv["manifest_state"], "absent")
            self.assertEqual(kv["manifest_health"], "uncompiled")

    def test_adapter_map_fails_closed_when_source_missing_config_invalid_or_manifest_degraded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_root = Path(tmp)
            _populate_minimal_ledger_root(fake_root)
            env = dict(
                os.environ,
                LEDGER_ROOT_OVERRIDE=str(fake_root),
                LEDGER_ADAPTER_SKIP_GIT_CHECK="1",
            )

            # 1. Clean uncompiled minimal root succeeds.
            ok_uncompiled = subprocess.run(
                ["bash", str(ADAPTER), "map"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(ok_uncompiled.returncode, 0, ok_uncompiled.stderr)

            # 2. Missing canonical document fails closed.
            (fake_root / "SCHEMA.md").unlink()
            missing = subprocess.run(
                ["bash", str(ADAPTER), "map"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("missing canonical Project Ledger source", missing.stderr)
            (fake_root / "SCHEMA.md").write_text("# Schema\n", encoding="utf-8")

            # 3. Invalid ledger_config.json fails closed via ledger validate.
            good_cfg = (fake_root / "ledger_config.json").read_text(encoding="utf-8")
            (fake_root / "ledger_config.json").write_text('{"roots": []}\n', encoding="utf-8")
            bad_cfg = subprocess.run(
                ["bash", str(ADAPTER), "map"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertNotEqual(bad_cfg.returncode, 0)
            self.assertIn("ledger validate failed", bad_cfg.stderr)
            (fake_root / "ledger_config.json").write_text(good_cfg, encoding="utf-8")

            # 4a. Partial-source degraded state/system-manifest.json is contained per Law 11.
            (fake_root / "state").mkdir(parents=True, exist_ok=True)
            (fake_root / "state" / "system-manifest.json").write_text(
                json.dumps(
                    {
                        "health": {
                            "state": "degraded",
                            "available_source_count": 1,
                            "unavailable_source_count": 1,
                        },
                        "counts": {
                            "sources": 2,
                            "observations": 1,
                            "canonical_projects": 1,
                            "review_items": 0,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            partial_degraded = subprocess.run(
                ["bash", str(ADAPTER), "map"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(partial_degraded.returncode, 0, partial_degraded.stderr)
            partial_kv = dict(
                line.split("=", 1) for line in partial_degraded.stdout.strip().splitlines()
            )
            self.assertEqual(partial_kv["manifest_health"], "degraded")

            # 4b. Total-outage degraded manifest (zero available sources) or invalid health fails closed.
            (fake_root / "state" / "system-manifest.json").write_text(
                json.dumps(
                    {
                        "health": {
                            "state": "degraded",
                            "available_source_count": 0,
                            "unavailable_source_count": 1,
                        },
                        "counts": {
                            "sources": 1,
                            "observations": 0,
                            "canonical_projects": 0,
                            "review_items": 0,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            total_outage = subprocess.run(
                ["bash", str(ADAPTER), "map"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertNotEqual(total_outage.returncode, 0)
            self.assertIn("zero available sources", total_outage.stderr)

            (fake_root / "state" / "system-manifest.json").write_text(
                json.dumps(
                    {
                        "health": {"state": "corrupt"},
                        "counts": {
                            "sources": 1,
                            "observations": 1,
                            "canonical_projects": 1,
                            "review_items": 0,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            invalid_health = subprocess.run(
                ["bash", str(ADAPTER), "map"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertNotEqual(invalid_health.returncode, 0)
            self.assertIn("invalid system-manifest health", invalid_health.stderr)

    def test_adapter_map_fails_closed_when_head_is_behind_origin_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            remote = base / "remote.git"
            clone = base / "clone"
            subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "init", "-b", "main", str(clone)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(clone), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(clone), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(clone), "remote", "add", "origin", str(remote)], check=True)

            _populate_minimal_ledger_root(clone)
            (clone / "sources" / "sample" / "README.md").write_text("# Sample\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(clone), "add", "."], check=True)
            subprocess.run(["git", "-C", str(clone), "commit", "-m", "initial"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(clone), "push", "-u", "origin", "main"], check=True, capture_output=True)

            env = {k: v for k, v in os.environ.items() if k != "LEDGER_ADAPTER_SKIP_GIT_CHECK"}
            env["LEDGER_ROOT_OVERRIDE"] = str(clone)

            synced = subprocess.run(
                ["bash", str(ADAPTER), "map"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(synced.returncode, 0, synced.stderr)

            (clone / "CURRENT.md").write_text("# Updated Current\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(clone), "add", "CURRENT.md"], check=True)
            subprocess.run(["git", "-C", str(clone), "commit", "-m", "upstream"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(clone), "push", "origin", "main"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(clone), "checkout", "HEAD~1"], check=True, capture_output=True)

            behind = subprocess.run(
                ["bash", str(ADAPTER), "map"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertNotEqual(behind.returncode, 0)
            self.assertIn("stale checkout: HEAD is 1 commit(s) behind origin/main", behind.stderr)


if __name__ == "__main__":
    unittest.main()
