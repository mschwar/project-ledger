"""Tests for P1.7 versioned sidecar declaration validation (`ledger/sidecar.py`)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ledger.compiler import compile_state
from ledger.explain import explain_payload
from ledger.sidecar import validate_sidecar_mapping


class SidecarValidationUnitTests(unittest.TestCase):
    def test_v1_versions_and_legacy_unversioned_are_accepted(self) -> None:
        for raw_ver, expected_status in (
            ("1", "valid"),
            ("1.0", "valid"),
            ("1.0.0", "valid"),
            (1, "valid"),
            (None, "legacy_unversioned"),
        ):
            payload: dict[str, object] = {
                "project_key": "github.com/mschwar/demo",
                "display_name": "Demo",
                "shared": True,
                "tags": ["homelab", "agent"],
                "gbrain_source_slug": "active-repos",
            }
            if raw_ver is not None:
                payload["schema_version"] = raw_ver
            envelope, accepted = validate_sidecar_mapping(payload, sidecar_path="/tmp/.project-ledger.json")
            self.assertEqual(envelope["status"], expected_status, raw_ver)
            self.assertEqual(envelope["normalized_schema_version"], "1.0.0")
            self.assertEqual(envelope["issues"], [])
            self.assertEqual(envelope["declarations"]["project_key"], "github.com/mschwar/demo")
            self.assertEqual(accepted["tags"], ["homelab", "agent"])

    def test_unsupported_schema_version_rejects_entire_sidecar(self) -> None:
        for bad_ver in ("2.0", "2.0.0", "0.9", True, ["1.0"]):
            envelope, accepted = validate_sidecar_mapping(
                {
                    "schema_version": bad_ver,
                    "project_key": "should-not-be-trusted",
                    "display_name": "Ignored",
                },
                sidecar_path="/tmp/bad/.project-ledger.json",
            )
            self.assertEqual(envelope["status"], "invalid")
            self.assertEqual(accepted, {})
            self.assertEqual(envelope["declarations"], {})
            self.assertEqual(envelope["issues"][0]["code"], "SIDECAR_UNSUPPORTED_VERSION")

    def test_forbidden_canonical_project_id_and_bad_field_types_are_stripped_and_flagged(self) -> None:
        envelope, accepted = validate_sidecar_mapping(
            {
                "schema_version": "1.0.0",
                "canonical_project_id": "prj_spoofed123456",
                "project_key": "valid-key",
                "tags": 42,
                "shared": "yes",
                "mystery_field": "unexpected",
            },
            sidecar_path="/tmp/mixed/.project-ledger.json",
        )
        self.assertEqual(envelope["status"], "invalid")
        self.assertEqual(accepted, {"project_key": "valid-key"})
        self.assertEqual(envelope["declarations"], {"project_key": "valid-key"})
        codes_by_field = {issue["field"]: issue["code"] for issue in envelope["issues"]}
        self.assertEqual(codes_by_field["canonical_project_id"], "SIDECAR_FORBIDDEN_FIELD")
        self.assertEqual(codes_by_field["tags"], "SIDECAR_INVALID_FIELD_TYPE")
        self.assertEqual(codes_by_field["shared"], "SIDECAR_INVALID_FIELD_TYPE")
        self.assertEqual(codes_by_field["mystery_field"], "SIDECAR_UNKNOWN_FIELD")


class SidecarCompilerIntegrationTests(unittest.TestCase):
    def test_refresh_and_compile_surface_invalid_sidecars_as_bounded_reviews(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            projects_root = base / "repos"
            good_dir = projects_root / "good-proj"
            bad_ver_dir = projects_root / "bad-version-proj"
            forbidden_dir = projects_root / "forbidden-id-proj"
            malformed_dir = projects_root / "malformed-json-proj"

            for d in (good_dir, bad_ver_dir, forbidden_dir, malformed_dir):
                d.mkdir(parents=True)
                (d / "README.md").write_text(f"# {d.name}\n\nSample description.\n", encoding="utf-8")

            (good_dir / ".project-ledger.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "project_key": "good-proj",
                        "display_name": "Good Project",
                        "gbrain_source_slug": "active-repos",
                        "tags": ["clean"],
                    }
                ),
                encoding="utf-8",
            )
            (bad_ver_dir / ".project-ledger.json").write_text(
                json.dumps(
                    {
                        "schema_version": "2.0.0",
                        "project_key": "poisoned-v2-key",
                        "display_name": "Poisoned Display Name",
                    }
                ),
                encoding="utf-8",
            )
            (forbidden_dir / ".project-ledger.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "canonical_project_id": "prj_000000000000000000000000",
                        "project_key": "forbidden-id-proj",
                    }
                ),
                encoding="utf-8",
            )
            (malformed_dir / ".project-ledger.json").write_text(
                "{not valid json",
                encoding="utf-8",
            )

            cfg_path = base / "ledger_config.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "roots": [
                            {
                                "id": "test-repos",
                                "label": "test-repos",
                                "path": str(projects_root),
                                "discovery": "children",
                                "source_class": "live",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            out_dir = base / "output"
            state_dir = base / "state"
            dec_path = base / "decisions.json"
            dec_path.write_text(json.dumps({"schema_version": "1.0.0", "decisions": []}), encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "ledger",
                    "refresh",
                    "--no-markdown-mirror",
                    "--config",
                    str(cfg_path),
                    "--output-dir",
                    str(out_dir),
                    "--state-dir",
                    str(state_dir),
                    "--identity-decisions",
                    str(dec_path),
                    "--json",
                ],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            manifest = json.loads(proc.stdout)
            self.assertEqual(manifest["capabilities"]["sidecar_declarations"]["state"], "available")

            observations = json.loads((state_dir / "observations.json").read_text(encoding="utf-8"))["observations"]
            by_name = {Path(o["location"]).name: o for o in observations}

            self.assertEqual(by_name["good-proj"]["sidecar"]["status"], "valid")
            self.assertEqual(
                by_name["good-proj"]["sidecar"]["declarations"]["gbrain_source_slug"],
                "active-repos",
            )
            # Unsupported v2 sidecar was NOT trusted for display_name or project_key
            self.assertEqual(by_name["bad-version-proj"]["sidecar"]["status"], "invalid")
            self.assertNotEqual(by_name["bad-version-proj"]["compat_entry"]["name"], "Poisoned Display Name")

            canonical = json.loads((state_dir / "canonical-projects.json").read_text(encoding="utf-8"))["projects"]
            canonical_ids = {p["canonical_project_id"] for p in canonical}
            self.assertNotIn("prj_000000000000000000000000", canonical_ids)

            good_proj = next(p for p in canonical if p["project_key"] == "good-proj")
            self.assertEqual(good_proj["gbrain_source_slug"], "active-repos")

            reviews = json.loads((state_dir / "review-queue.json").read_text(encoding="utf-8"))["items"]
            sidecar_reviews = [r for r in reviews if r["code"] == "SIDECAR_DECLARATION_INVALID"]
            self.assertEqual(len(sidecar_reviews), 3)

            explained = explain_payload(state_dir, sidecar_reviews[0]["review_id"], decisions_path=dec_path)
            self.assertEqual(explained["status"], "resolved")
            self.assertEqual(explained["subject_kind"], "review_item")


if __name__ == "__main__":
    unittest.main()
