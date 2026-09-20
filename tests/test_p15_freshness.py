"""P1.5 — Tighten freshness/result semantics.

Covers:
- unavailable vs successfully-observed-empty vs observed source outcomes in the
  materialized manifest sources[] records;
- stronger source content `as_of` semantics only where an upstream source actually
  supplies authoritative timestamp evidence (git remote-ref / HEAD commit / last touch);
- no invented freshness: when no authoritative upstream timestamp exists, the source
  freshness stays "unknown" (or "unavailable" for an unprobeable source) rather than
  being guessed from compile time or path mtime.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ledger.compiler import compile_state


def _write_config(tmp: Path, roots: list[dict]) -> Path:
    config_path = tmp / "ledger_config.json"
    config_path.write_text(json.dumps({"roots": roots}), encoding="utf-8")
    return config_path


def _write_compat(tmp: Path, entries: list[dict], *, generated_at: str = "2026-09-14T06:00:00+00:00") -> Path:
    path = tmp / "projects.json"
    path.write_text(json.dumps({"generated_at": generated_at, "entries": entries}), encoding="utf-8")
    return path


def _compile(tmp: Path, roots: list[dict], entries: list[dict]) -> dict:
    return compile_state(
        config_path=_write_config(tmp, roots),
        compat_output_path=_write_compat(tmp, entries),
        state_dir=tmp / "state",
        generated_at="2026-09-14T06:10:00Z",
    )


class P15FreshnessSemanticsTests(unittest.TestCase):
    def test_observed_source_reports_result_state_observed_and_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live"
            live.mkdir()
            manifest = _compile(
                root,
                [{"path": str(live), "label": "live", "source_id": "live-src"}],
                [{"project_key": "a", "source_label": "live", "path": str(live / "a"), "name": "A"}],
            )
            source = manifest["sources"][0]
            self.assertEqual(source["result_state"], "observed")
            self.assertEqual(source["observation_count"], 1)
            self.assertEqual(manifest["health"]["observed_empty_source_count"], 0)
            self.assertEqual(source["status"], "available")

    def test_available_but_empty_source_is_observed_empty_not_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live"
            live.mkdir()
            manifest = _compile(
                root,
                [{"path": str(live), "label": "live", "source_id": "live-src"}],
                [],  # probe succeeds but the source contributes zero observations
            )
            source = manifest["sources"][0]
            # P1.5 distinction: the source is healthy and was probed successfully,
            # and it simply yielded nothing. It is NOT "unavailable".
            self.assertEqual(source["status"], "available")
            self.assertEqual(source["result_state"], "observed_empty")
            self.assertEqual(source["observation_count"], 0)
            self.assertEqual(manifest["health"]["observed_empty_source_count"], 1)

    def test_unavailable_source_is_helper_distinct_from_observed_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing"
            manifest = _compile(
                root,
                [{"path": str(missing), "label": "missing", "source_id": "missing-src"}],
                [],
            )
            source = manifest["sources"][0]
            self.assertEqual(source["status"], "unavailable")
            self.assertEqual(source["result_state"], "unavailable")
            self.assertEqual(source["observation_count"], 0)
            self.assertEqual(manifest["health"]["source_unavailable_count"], 1)
            self.assertEqual(manifest["health"]["observed_empty_source_count"], 0)

    def test_git_remote_ref_evidence_yields_known_freshness_with_strong_as_of(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live"
            live.mkdir()
            manifest = _compile(
                root,
                [{"path": str(live), "label": "live", "source_id": "live-src"}],
                [
                    {
                        "project_key": "a",
                        "source_label": "live",
                        "path": str(live / "a"),
                        "name": "A",
                        "last_remote_ref_at": "2026-09-13T10:00:00+00:00",
                    },
                    {
                        "project_key": "b",
                        "source_label": "live",
                        "path": str(live / "b"),
                        "name": "B",
                        "last_remote_ref_at": "2026-09-13T11:30:00+00:00",
                    },
                ],
            )
            source = manifest["sources"][0]
            self.assertEqual(source["result_state"], "observed")
            self.assertEqual(source["freshness_state"], "known")
            # Strongest authoritative evidence (latest remote-ref committer date) wins.
            self.assertEqual(source["content_as_of"], "2026-09-13T11:30:00+00:00")
            self.assertEqual(source["content_as_of_basis"], "compat.entry.last_remote_ref_at")

    def test_head_commit_is_used_when_no_remote_ref_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live"
            live.mkdir()
            manifest = _compile(
                root,
                [{"path": str(live), "label": "live", "source_id": "live-src"}],
                [
                    {
                        "project_key": "a",
                        "source_label": "live",
                        "path": str(live / "a"),
                        "name": "A",
                        "head_commit_at": "2026-09-12T08:00:00+00:00",
                    }
                ],
            )
            source = manifest["sources"][0]
            self.assertEqual(source["freshness_state"], "known")
            self.assertEqual(source["content_as_of"], "2026-09-12T08:00:00+00:00")
            self.assertEqual(source["content_as_of_basis"], "compat.entry.head_commit_at")

    def test_content_as_of_compares_by_instant_not_string_form(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live"
            live.mkdir()
            # 08:00-0500 is instant 13:00Z — chronologically later than 12:00Z, but
            # lexically smaller ("08:00:00-0500" < "12:00:00Z"). Instant comparison
            # must pick the -0500 form.
            manifest = _compile(
                root,
                [{"path": str(live), "label": "live", "source_id": "live-src"}],
                [
                    {
                        "project_key": "a",
                        "source_label": "live",
                        "path": str(live / "a"),
                        "name": "A",
                        "head_commit_at": "2026-09-13T08:00:00-0500",
                    },
                    {
                        "project_key": "b",
                        "source_label": "live",
                        "path": str(live / "b"),
                        "name": "B",
                        "head_commit_at": "2026-09-13T12:00:00Z",
                    },
                ],
            )
            source = manifest["sources"][0]
            self.assertEqual(source["freshness_state"], "known")
            self.assertEqual(source["content_as_of"], "2026-09-13T08:00:00-0500")

    def test_naive_upstream_timestamp_is_ignored_instead_of_inventing_timezone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live"
            live.mkdir()
            # A timezone-naive timestamp carries no instant; we must not assume UTC.
            manifest = _compile(
                root,
                [{"path": str(live), "label": "live", "source_id": "live-src"}],
                [
                    {
                        "project_key": "a",
                        "source_label": "live",
                        "path": str(live / "a"),
                        "name": "A",
                        "head_commit_at": "2026-09-14T00:00:00",  # naive, no offset
                    }
                ],
            )
            source = manifest["sources"][0]
            self.assertEqual(source["freshness_state"], "unknown")
            self.assertIsNone(source["content_as_of"])
            self.assertIsNone(source["content_as_of_basis"])

    def test_bad_upstream_timestamp_does_not_poison_source_freshness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live"
            live.mkdir()
            manifest = _compile(
                root,
                [{"path": str(live), "label": "live", "source_id": "live-src"}],
                [
                    {
                        "project_key": "a",
                        "source_label": "live",
                        "path": str(live / "a"),
                        "name": "A",
                        "head_commit_at": "not-a-timestamp",
                    },
                    {
                        "project_key": "b",
                        "source_label": "live",
                        "path": str(live / "b"),
                        "name": "B",
                        "head_commit_at": "2026-09-13T08:00:00+00:00",
                    },
                ],
            )
            source = manifest["sources"][0]
            # The malformed timestamp is ignored; the valid one supplies freshness.
            self.assertEqual(source["freshness_state"], "known")
            self.assertEqual(source["content_as_of"], "2026-09-13T08:00:00+00:00")

    def test_no_upstream_timestamp_means_no_invented_freshness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live"
            live.mkdir()
            # Entries carry no authoritative timestamp evidence at all.
            manifest = _compile(
                root,
                [{"path": str(live), "label": "live", "source_id": "live-src"}],
                [{"project_key": "a", "source_label": "live", "path": str(live / "a"), "name": "A"}],
            )
            source = manifest["sources"][0]
            self.assertEqual(source["result_state"], "observed")
            # No invented freshness: content_as_of stays empty, freshness_state unknown.
            self.assertEqual(source["freshness_state"], "unknown")
            self.assertEqual(source["content_as_of"], None)
            self.assertEqual(source["content_as_of_basis"], None)

    def test_last_touch_is_weakest_and_only_used_when_only_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live"
            live.mkdir()
            manifest = _compile(
                root,
                [{"path": str(live), "label": "live", "source_id": "live-src"}],
                [
                    {
                        "project_key": "a",
                        "source_label": "live",
                        "path": str(live / "a"),
                        "name": "A",
                        "last_touch_at": "2026-09-11T05:00:00+00:00",
                        # remote_ref/head_commit absent, so fall back to filesystem evidence
                    }
                ],
            )
            source = manifest["sources"][0]
            self.assertEqual(source["freshness_state"], "known")
            self.assertEqual(source["content_as_of"], "2026-09-11T05:00:00+00:00")
            self.assertEqual(source["content_as_of_basis"], "compat.entry.last_touch_at")


if __name__ == "__main__":
    unittest.main()