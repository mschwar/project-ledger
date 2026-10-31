"""Tests for P1.7 versioned sidecar schema and HEAD-drift validation (`ledger/sidecar.py`)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ledger.capsules import show_payload
from ledger.explain import explain_payload
from ledger.sidecar import (
    FORBIDDEN_SIDECAR_FIELDS,
    KNOWN_GIT_OBJECT_FIELDS,
    KNOWN_SIDECAR_FIELDS,
    SIDECAR_SCHEMA_PATH,
    load_sidecar_schema,
    validate_sidecar_mapping,
)
from ledger.sources.common import load_sidecar


def _git_init_and_commit(repo_dir: Path, message: str = "initial commit") -> str:
    repo_dir.mkdir(parents=True, exist_ok=True)
    if not (repo_dir / ".git").exists():
        subprocess.run(["git", "-C", str(repo_dir), "init", "-q", "-b", "main"], check=True)
        subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(repo_dir), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(
            ["git", "-C", str(repo_dir), "remote", "add", "origin", f"https://github.com/mschwar/{repo_dir.name}.git"],
            check=True,
        )
    subprocess.run(["git", "-C", str(repo_dir), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "commit", "-q", "-m", message], check=True)
    return subprocess.check_output(
        ["git", "-C", str(repo_dir), "rev-parse", "HEAD"], text=True
    ).strip()


class SidecarValidationUnitTests(unittest.TestCase):
    def test_json_schema_file_exists_and_matches_validator_constants(self) -> None:
        self.assertTrue(SIDECAR_SCHEMA_PATH.is_file(), f"Missing {SIDECAR_SCHEMA_PATH}")
        schema = load_sidecar_schema()
        self.assertEqual(schema["type"], "object")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["x-forbidden-fields"]), set(FORBIDDEN_SIDECAR_FIELDS))
        self.assertEqual(set(schema["properties"].keys()), set(KNOWN_SIDECAR_FIELDS))
        git_props = schema["properties"]["git"]["properties"]
        self.assertEqual(set(git_props.keys()), set(KNOWN_GIT_OBJECT_FIELDS))

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
            self.assertFalse(envelope["stale"])
            self.assertEqual(envelope["warnings"], [])
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
                "git": {
                    "commit": "abc1234",
                    "dirty": "not-a-bool",
                    "unexpected_git_key": 1,
                },
            },
            sidecar_path="/tmp/mixed/.project-ledger.json",
        )
        self.assertEqual(envelope["status"], "invalid")
        self.assertEqual(accepted["project_key"], "valid-key")
        self.assertEqual(accepted["git"], {"commit": "abc1234"})
        codes_by_field = {issue["field"]: issue["code"] for issue in envelope["issues"]}
        self.assertEqual(codes_by_field["canonical_project_id"], "SIDECAR_FORBIDDEN_FIELD")
        self.assertEqual(codes_by_field["tags"], "SIDECAR_INVALID_FIELD_TYPE")
        self.assertEqual(codes_by_field["shared"], "SIDECAR_INVALID_FIELD_TYPE")
        self.assertEqual(codes_by_field["mystery_field"], "SIDECAR_UNKNOWN_FIELD")
        self.assertEqual(codes_by_field["git.dirty"], "SIDECAR_INVALID_FIELD_TYPE")
        self.assertEqual(codes_by_field["git.unexpected_git_key"], "SIDECAR_UNKNOWN_FIELD")

    def test_load_sidecar_emits_stale_sidecar_when_declared_commit_or_timestamp_lags_head(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            # 1. Garden-of-Wisdom pattern: May 19 sidecar declares old git.commit while live HEAD advanced.
            gow = root / "Garden-of-Wisdom"
            gow.mkdir()
            (gow / "README.md").write_text("# A Garden of Wisdom\n", encoding="utf-8")
            old_sha = _git_init_and_commit(gow, "may-19 baseline")
            (gow / ".project-ledger.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "project_key": "garden-of-wisdom",
                        "display_name": "A Garden of Wisdom",
                        "git": {
                            "branch": "main",
                            "commit": old_sha[:7],
                            "dirty": False,
                            "dirty_count": 0,
                            "is_git": True,
                        },
                        "updated_at": "2026-05-19T00:19:51.746045+00:00",
                    }
                ),
                encoding="utf-8",
            )
            # Current at old_sha -> NOT stale yet.
            fresh_check = load_sidecar(gow / ".project-ledger.json")
            self.assertFalse(fresh_check.get("_stale_sidecar"))

            # Advance HEAD without updating sidecar -> stale_sidecar!
            (gow / "README.md").write_text("# A Garden of Wisdom\n\nUpdated.\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(gow), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(gow), "commit", "-q", "-m", "oct-31 advance"], check=True)

            stale_gow = load_sidecar(gow / ".project-ledger.json")
            self.assertTrue(stale_gow.get("_stale_sidecar"))
            self.assertIsNone(stale_gow.get("_sidecar_error"))
            self.assertTrue(
                any("stale_sidecar" in msg for msg in stale_gow.get("_sidecar_warnings", []))
            )

            # 2. Fresh sidecar committed IN HEAD (even if last_session_at was written seconds before commit)
            # must NOT false-positive as stale_sidecar.
            fresh_repo = root / "fresh-committed-sidecar"
            fresh_repo.mkdir()
            (fresh_repo / "README.md").write_text("# Fresh\n", encoding="utf-8")
            (fresh_repo / ".project-ledger.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "project_key": "fresh-repo",
                        "last_session_at": "2020-01-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            _git_init_and_commit(fresh_repo, "commit sidecar in HEAD")
            loaded_fresh = load_sidecar(fresh_repo / ".project-ledger.json")
            self.assertFalse(loaded_fresh.get("_stale_sidecar"))

            # 3. PR branch modifying .project-ledger.json and merged via `--no-ff` merge commit
            # must also treat HEAD as fresh (`git log -1 -m`).
            subprocess.run(["git", "-C", str(fresh_repo), "checkout", "-q", "-b", "feat/update-sidecar"], check=True)
            (fresh_repo / ".project-ledger.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "project_key": "fresh-repo",
                        "last_session_at": "2020-01-02T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            subprocess.run(["git", "-C", str(fresh_repo), "add", ".project-ledger.json"], check=True)
            subprocess.run(["git", "-C", str(fresh_repo), "commit", "-q", "-m", "update sidecar on PR"], check=True)
            subprocess.run(["git", "-C", str(fresh_repo), "checkout", "-q", "main"], check=True)
            subprocess.run(
                ["git", "-C", str(fresh_repo), "merge", "--no-ff", "-q", "-m", "Merge PR #1", "feat/update-sidecar"],
                check=True,
            )
            loaded_merged = load_sidecar(fresh_repo / ".project-ledger.json")
            self.assertFalse(loaded_merged.get("_stale_sidecar"))


class SidecarCompilerIntegrationTests(unittest.TestCase):
    def test_refresh_and_compile_surface_invalid_sidecars_and_stale_sidecar_warnings(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            projects_root = base / "repos"
            good_dir = projects_root / "good-proj"
            bad_ver_dir = projects_root / "bad-version-proj"
            forbidden_dir = projects_root / "forbidden-id-proj"
            malformed_dir = projects_root / "malformed-json-proj"
            stale_dir = projects_root / "stale-may19-proj"

            for d in (good_dir, bad_ver_dir, forbidden_dir, malformed_dir):
                d.mkdir(parents=True)
                (d / "README.md").write_text(f"# {d.name}\n\nSample description.\n", encoding="utf-8")

            stale_dir.mkdir(parents=True)
            (stale_dir / "README.md").write_text("# Stale May 19 Repo\n", encoding="utf-8")
            may_sha = _git_init_and_commit(stale_dir, "may commit")
            (stale_dir / ".project-ledger.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "project_key": "stale-may19-proj",
                        "display_name": "Stale May 19 Project",
                        "git": {"branch": "main", "commit": may_sha[:7], "is_git": True},
                        "updated_at": "2026-05-19T00:19:51+00:00",
                    }
                ),
                encoding="utf-8",
            )
            (stale_dir / "README.md").write_text("# Stale May 19 Repo\n\nNewer HEAD.\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(stale_dir), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(stale_dir), "commit", "-q", "-m", "advance HEAD"], check=True)

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
            self.assertFalse(by_name["good-proj"]["sidecar"]["stale"])
            self.assertEqual(
                by_name["good-proj"]["sidecar"]["declarations"]["gbrain_source_slug"],
                "active-repos",
            )

            # Stale May 19 sidecar remains schema-valid (0 false error reviews) while emitting stale_sidecar warning.
            self.assertEqual(by_name["stale-may19-proj"]["sidecar"]["status"], "valid")
            self.assertTrue(by_name["stale-may19-proj"]["sidecar"]["stale"])
            self.assertEqual(
                by_name["stale-may19-proj"]["sidecar"]["warnings"][0]["code"],
                "stale_sidecar",
            )
            self.assertIn("stale_sidecar", by_name["stale-may19-proj"]["compat_entry"]["include_reason"])
            shown_stale = show_payload(state_dir, "stale-may19-proj")
            self.assertEqual(shown_stale["status"], "resolved")
            self.assertIn("stale_sidecar", shown_stale["preferred_location"]["warning_codes"])
            self.assertTrue(any("stale_sidecar" in w for w in shown_stale["warnings"]))

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
