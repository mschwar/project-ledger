from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ledger import COMPAT_FLAT_SCHEMA_VERSION
from ledger.compat_contract import (
    COMPAT_FORMAT,
    ENVELOPE_KEYS,
    LedgerCompatError,
    check_compat_schema,
)
from ledger.compiler import compile_state
from ledger.models import CSV_FIELDS

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "compat-contract.json"


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class CompatContractTests(unittest.TestCase):
    def test_csv_fields_frozen_to_golden_fixture(self) -> None:
        """The scanner's flat field list must match the golden fixture exactly.

        A rename, reorder, removal, or unapproved addition to the compatibility
        contract breaks this test, preventing silent field-meaning drift.
        """
        fixture = _load_fixture()
        pinned = [item["field"] for item in fixture["entry_fields"]]
        self.assertEqual(CSV_FIELDS, pinned)

    def test_compat_schema_version_frozen_to_golden_fixture(self) -> None:
        fixture = _load_fixture()
        self.assertEqual(COMPAT_FLAT_SCHEMA_VERSION, fixture["schema_version"])
        self.assertEqual(COMPAT_FORMAT, fixture["format"])

    def test_fixture_role_coverage_is_complete(self) -> None:
        """Every frozen entry field carries a semantic role and none is orphaned."""
        fixture = _load_fixture()
        fields = [item["field"] for item in fixture["entry_fields"]]
        roles = sorted({item["role"] for item in fixture["entry_fields"]})
        self.assertEqual(set(fields), set(CSV_FIELDS))
        self.assertEqual(len(fields), len(set(fields)))
        self.assertIn("identity", roles)
        self.assertIn("provenance", roles)

    def test_envelope_keys_documented_and_known(self) -> None:
        fixture = _load_fixture()
        for key in fixture["envelope_keys"]:
            self.assertIn(key, ENVELOPE_KEYS)

    def test_payload_without_version_marker_is_supported_v0(self) -> None:
        """The current build_ledger.py producer emits no version marker; v0 is accepted."""
        payload = {"generated_at": "2026-09-14T06:00:00Z", "entry_count": 0, "entries": []}
        check_compat_schema(payload)  # must not raise

    def test_unsupported_major_is_rejected_explicitly(self) -> None:
        payload = {"schema_version": "2.0.0", "entries": []}
        with self.assertRaises(LedgerCompatError) as ctx:
            check_compat_schema(payload)
        self.assertEqual(ctx.exception.code, "COMPAT_MAJOR_UNSUPPORTED")

    def test_unsupported_major_from_compile_boundary(self) -> None:
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
            compat_path.write_text(
                json.dumps({"schema_version": "3.1.0", "entries": [{"project_key": "x", "path": "/x"}]}),
                encoding="utf-8",
            )
            with self.assertRaises(LedgerCompatError) as ctx:
                compile_state(
                    config_path=config_path,
                    compat_output_path=compat_path,
                    state_dir=root / "state",
                    generated_at="2026-09-14T06:10:00Z",
                )
            self.assertEqual(ctx.exception.code, "COMPAT_MAJOR_UNSUPPORTED")

    def test_same_major_newer_minor_is_tolerated(self) -> None:
        """Minor bumps within the supported major are backward-compatible additive changes."""
        payload = {"schema_version": "0.9.0", "entries": []}
        check_compat_schema(payload)  # must not raise

    def test_unsupported_format_is_rejected(self) -> None:
        payload = {"format": "some-other-ledger", "entries": []}
        with self.assertRaises(LedgerCompatError) as ctx:
            check_compat_schema(payload)
        self.assertEqual(ctx.exception.code, "COMPAT_FORMAT_UNSUPPORTED")

    def test_malformed_version_is_rejected_not_guessed(self) -> None:
        payload = {"schema_version": "future", "entries": []}
        with self.assertRaises(LedgerCompatError) as ctx:
            check_compat_schema(payload)
        self.assertEqual(ctx.exception.code, "COMPAT_MAJOR_UNSUPPORTED")


if __name__ == "__main__":
    unittest.main()