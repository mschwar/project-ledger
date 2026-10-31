"""Executable Draft 2020-12 JSON Schema & packaging surface contract tests (PL-GAP-CLOSEOUT-03)."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import re
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
from typing import Any

import build_ledger
from ledger.capsules import CAPSULE_MAX_BYTES
from ledger.cli import main as ledger_main

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = REPO_ROOT / "schemas"


def _matches_json_type(instance: Any, expected_type: str) -> bool:
    if expected_type == "null":
        return instance is None
    if expected_type == "boolean":
        return isinstance(instance, bool)
    if expected_type == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected_type == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if expected_type == "string":
        return isinstance(instance, str)
    if expected_type == "array":
        return isinstance(instance, list)
    if expected_type == "object":
        return isinstance(instance, dict)
    raise ValueError(f"Unsupported JSON Schema type: {expected_type!r}")


def _resolve_ref(root_schema: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"Only local JSON pointer $ref values are supported: {ref!r}")
    node: Any = root_schema
    for part in ref[2:].split("/"):
        node = node[part]
    if not isinstance(node, dict):
        raise ValueError(f"Resolved $ref {ref!r} is not a schema object.")
    return node


def validate_json_schema(
    instance: Any,
    schema: dict[str, Any],
    *,
    root_schema: dict[str, Any] | None = None,
    path: str = "$",
) -> list[str]:
    """Deterministic stdlib validator for Project Ledger's Draft 2020-12 schema subset."""
    root = root_schema if root_schema is not None else schema
    if "$ref" in schema:
        target = _resolve_ref(root, schema["$ref"])
        return validate_json_schema(instance, target, root_schema=root, path=path)

    errors: list[str] = []

    if "oneOf" in schema:
        matches = sum(
            1
            for branch in schema["oneOf"]
            if not validate_json_schema(instance, branch, root_schema=root, path=path)
        )
        if matches != 1:
            errors.append(f"{path}: expected exactly 1 matching oneOf branch, got {matches}")
        return errors

    if "not" in schema:
        if not validate_json_schema(instance, schema["not"], root_schema=root, path=path):
            errors.append(f"{path}: matched forbidden 'not' schema")

    if "type" in schema:
        raw_type = schema["type"]
        allowed_types = [raw_type] if isinstance(raw_type, str) else list(raw_type)
        if not any(_matches_json_type(instance, t) for t in allowed_types):
            errors.append(f"{path}: value {instance!r} does not match type {raw_type!r}")
            return errors

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}, got {instance!r}")

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: value {instance!r} not in enum {schema['enum']!r}")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < int(schema["minLength"]):
            errors.append(f"{path}: string length {len(instance)} < minLength {schema['minLength']}")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            errors.append(f"{path}: string {instance!r} does not match pattern {schema['pattern']!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: number {instance} < minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: number {instance} > maximum {schema['maximum']}")

    if isinstance(instance, list) and "items" in schema:
        item_schema = schema["items"]
        for idx, item in enumerate(instance):
            errors.extend(
                validate_json_schema(item, item_schema, root_schema=root, path=f"{path}[{idx}]")
            )

    if isinstance(instance, dict):
        for req_key in schema.get("required", []):
            if req_key not in instance:
                errors.append(f"{path}: missing required key {req_key!r}")
        props = schema.get("properties", {})
        add_props = schema.get("additionalProperties", True)
        for key, val in instance.items():
            child_path = f"{path}.{key}"
            if key in props:
                errors.extend(
                    validate_json_schema(val, props[key], root_schema=root, path=child_path)
                )
            elif add_props is False:
                errors.append(f"{path}: unexpected additional property {key!r}")
            elif isinstance(add_props, dict):
                errors.extend(
                    validate_json_schema(val, add_props, root_schema=root, path=child_path)
                )

    return errors


def _init_git_repo(repo_dir: Path, remote_url: str) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "remote", "add", "origin", remote_url],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )


class StateSchemasAndPackagingTests(unittest.TestCase):
    def _load_schema(self, filename: str) -> dict[str, Any]:
        schema_path = SCHEMAS_DIR / filename
        self.assertTrue(schema_path.is_file(), f"Missing schema file: {schema_path}")
        payload = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(payload.get("$schema"), "https://json-schema.org/draft/2020-12/schema")
        return payload

    def test_pyproject_and_build_ledger_reexport_surface(self) -> None:
        pyproject_path = REPO_ROOT / "pyproject.toml"
        self.assertTrue(pyproject_path.is_file())
        parsed = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        self.assertEqual(parsed["project"]["name"], "project-ledger")
        self.assertEqual(parsed["project"]["scripts"]["ledger"], "ledger.cli:main")
        self.assertEqual(parsed["project"]["scripts"]["ledger-mcp"], "ledger.mcp_server:main")
        self.assertEqual(parsed["tool"]["setuptools"]["py-modules"], ["build_ledger"])
        self.assertIn("F", parsed["tool"]["ruff"]["lint"]["select"])

        exported = getattr(build_ledger, "__all__", None)
        self.assertIsInstance(exported, list)
        self.assertGreaterEqual(len(exported), 50)
        for symbol in exported:
            self.assertTrue(
                hasattr(build_ledger, symbol),
                f"build_ledger.__all__ symbol {symbol!r} missing from module attributes",
            )

    def test_compiled_state_artifacts_conform_to_published_json_schemas(self) -> None:
        manifest_schema = self._load_schema("system-manifest.schema.json")
        canonical_schema = self._load_schema("canonical-projects.schema.json")
        capsule_schema = self._load_schema("project-capsule.schema.json")
        review_schema = self._load_schema("review-queue.schema.json")
        changes_schema = self._load_schema("changes.schema.json")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live_root = root / "active"
            live_root.mkdir()

            # Project 1: Git repo with valid sidecar, .ctx/skeleton.json, and .machina/state.json.
            alpha = live_root / "alpha"
            _init_git_repo(alpha, "https://github.com/example/alpha.git")
            (alpha / "README.md").write_text("# Alpha\n\nAlpha service substrate.\n", encoding="utf-8")
            (alpha / ".project-ledger.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "project_key": "github.com/example/alpha",
                        "display_name": "Alpha",
                        "status": "active",
                        "tags": ["core", "service"],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            (alpha / ".ctx").mkdir()
            (alpha / ".ctx" / "skeleton.json").write_text(
                json.dumps(
                    {
                        "repo_id": "alpha",
                        "structural_hash": "abc123",
                        "boundary_doc_hash": "def456",
                        "total_files": 4,
                        "languages": {"Python": 3, "Markdown": 1},
                        "entrypoints": ["alpha/cli.py"],
                        "boundary_docs": [
                            {
                                "path": "README.md",
                                "kind": "overview",
                                "headings": ["Alpha"],
                                "summary": "Alpha service substrate.",
                            }
                        ],
                        "subsystems": [
                            {
                                "path": "alpha",
                                "file_count": 3,
                                "languages": ["Python"],
                                "key_symbols": ["main"],
                                "internal_deps": [],
                                "external_deps": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (alpha / ".machina").mkdir()
            (alpha / ".machina" / "state.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "updated_at": "2026-10-31T19:00:00Z",
                        "active_unit": None,
                        "last_completed": {
                            "timestamp": "2026-10-31T19:00:00Z",
                            "summary": "Completed alpha verification slice.",
                            "unit": {"id": "ALPHA-01", "title": "Alpha verification"},
                        },
                        "next_action": "Monitor alpha health.",
                    }
                ),
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "."], cwd=alpha, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "init alpha"],
                cwd=alpha,
                check=True,
                capture_output=True,
            )

            # Mirror root with explicit slug source_id and second checkout of alpha (exact_normalized_remote).
            mirror_root = root / "mirrors" / "alpha-mirror"
            _init_git_repo(mirror_root, "https://github.com/example/alpha.git")
            (mirror_root / "README.md").write_text("# Alpha Mirror\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=mirror_root, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "init alpha mirror"],
                cwd=mirror_root,
                check=True,
                capture_output=True,
            )

            # Project 2: Non-git directory with invalid sidecar to populate review-queue.json too.
            beta = live_root / "beta"
            beta.mkdir()
            (beta / "README.md").write_text("# Beta\n\nBeta prototype.\n", encoding="utf-8")
            (beta / ".project-ledger.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "canonical_project_id": "prj_000000000000000000000000",
                    }
                ),
                encoding="utf-8",
            )

            config_path = root / "ledger_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "roots": [
                            {
                                "label": "active",
                                "path": str(live_root),
                                "discovery": "children",
                                "storage_scope": "shared",
                            },
                            {
                                "source_id": "central-mirror-alpha",
                                "label": "alpha-mirror",
                                "path": str(mirror_root),
                                "discovery": "self",
                                "storage_scope": "mirror",
                            },
                        ]
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            state_dir = root / "state"
            decisions_path = root / "decisions.json"
            decisions_path.write_text(
                json.dumps({"schema_version": "1.0.0", "decisions": []}) + "\n",
                encoding="utf-8",
            )

            # Run 1: initial_baseline compile via `ledger refresh --no-markdown-mirror`.
            with contextlib.redirect_stdout(io.StringIO()):
                rc1 = ledger_main(
                    [
                        "refresh",
                        "--no-markdown-mirror",
                        "--config",
                        str(config_path),
                        "--state-dir",
                        str(state_dir),
                        "--identity-decisions",
                        str(decisions_path),
                    ]
                )
            self.assertEqual(rc1, 0)

            initial_changes = json.loads((state_dir / "changes.json").read_text(encoding="utf-8"))
            self.assertEqual(initial_changes["baseline_state"], "initial_baseline")
            self.assertEqual(validate_json_schema(initial_changes, changes_schema), [])

            # Mutate estate (add gamma project) and run Run 2 so `changes.json` has `compared` events.
            gamma = live_root / "gamma"
            gamma.mkdir()
            (gamma / "README.md").write_text("# Gamma\n\nGamma tool.\n", encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()):
                rc2 = ledger_main(
                    [
                        "refresh",
                        "--no-markdown-mirror",
                        "--config",
                        str(config_path),
                        "--state-dir",
                        str(state_dir),
                        "--identity-decisions",
                        str(decisions_path),
                    ]
                )
            self.assertEqual(rc2, 0)

            manifest_doc = json.loads((state_dir / "system-manifest.json").read_text(encoding="utf-8"))
            canonical_doc = json.loads((state_dir / "canonical-projects.json").read_text(encoding="utf-8"))
            review_doc = json.loads((state_dir / "review-queue.json").read_text(encoding="utf-8"))
            changes_doc = json.loads((state_dir / "changes.json").read_text(encoding="utf-8"))

            self.assertEqual(validate_json_schema(manifest_doc, manifest_schema), [])
            self.assertEqual(validate_json_schema(canonical_doc, canonical_schema), [])
            self.assertGreaterEqual(review_doc["review_count"], 1)
            self.assertEqual(validate_json_schema(review_doc, review_schema), [])
            self.assertEqual(changes_doc["baseline_state"], "compared")
            self.assertGreaterEqual(changes_doc["change_count"], 1)
            self.assertEqual(validate_json_schema(changes_doc, changes_schema), [])

            capsule_files = sorted((state_dir / "projects").glob("prj_*.json"))
            self.assertEqual(len(capsule_files), 3)
            for cap_path in capsule_files:
                raw_bytes = cap_path.read_bytes()
                self.assertLessEqual(len(raw_bytes), CAPSULE_MAX_BYTES)
                cap_doc = json.loads(raw_bytes.decode("utf-8"))
                self.assertEqual(
                    validate_json_schema(cap_doc, capsule_schema),
                    [],
                    f"Capsule failed schema validation: {cap_path.name}",
                )

            # Negative validation checks: missing required fields, invalid enums, and unexpected properties fail.
            bad_manifest = copy.deepcopy(manifest_doc)
            del bad_manifest["health"]
            self.assertTrue(validate_json_schema(bad_manifest, manifest_schema))

            bad_manifest_extra = copy.deepcopy(manifest_doc)
            bad_manifest_extra["unexpected_field"] = 123
            self.assertTrue(validate_json_schema(bad_manifest_extra, manifest_schema))

            sample_capsule = json.loads(capsule_files[0].read_text(encoding="utf-8"))
            bad_capsule = copy.deepcopy(sample_capsule)
            bad_capsule["preferred_location"]["state"] = "bogus_state"
            self.assertTrue(validate_json_schema(bad_capsule, capsule_schema))

    def test_project_lineage_registry_conforms_to_published_json_schema(self) -> None:
        lineage_schema = self._load_schema("project-lineage.schema.json")
        lineage_path = REPO_ROOT / "registry" / "project-lineage.json"
        self.assertTrue(lineage_path.is_file(), f"Missing lineage registry: {lineage_path}")
        lineage_doc = json.loads(lineage_path.read_text(encoding="utf-8"))
        self.assertEqual(validate_json_schema(lineage_doc, lineage_schema), [])
        self.assertGreaterEqual(len(lineage_doc["projects"]), 170)

        bad_lineage = copy.deepcopy(lineage_doc)
        bad_lineage["projects"][0]["canonical_project_id"] = "invalid_id"
        self.assertTrue(validate_json_schema(bad_lineage, lineage_schema))


if __name__ == "__main__":
    unittest.main()
