"""Tests for Wave 4 semantic change feed (`state/changes.json`, `ledger changes`, P4.5)."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from ledger import CHANGE_FEED_SCHEMA_VERSION, cli
from ledger.changes import changes_payload, load_changes, materialize_changes
from ledger.compiler import compile_state, load_manifest, orient_payload
from ledger.explain import explain_payload


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class SemanticChangeFeedTests(unittest.TestCase):
    def test_initial_baseline_and_idempotent_recompile_have_zero_churn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live"
            live.mkdir()
            config_path = root / "ledger_config.json"
            _write_json(
                config_path,
                {
                    "roots": [
                        {
                            "source_id": "central-active",
                            "path": str(live),
                            "label": "central-active",
                            "discovery": "children",
                        }
                    ]
                },
            )
            compat_path = root / "projects.json"
            _write_json(
                compat_path,
                {
                    "generated_at": "2026-10-30T01:00:00+00:00",
                    "entries": [
                        {
                            "project_key": "github.com/mschwar/machina",
                            "source_label": "central-active",
                            "path": "/central/repos/active/machina",
                            "name": "Machina",
                            "remote_url": "https://github.com/mschwar/machina.git",
                            "canonical_url": "https://github.com/mschwar/machina.git",
                            "git": True,
                            "head_branch": "main",
                            "head_commit": "111aaaa",
                            "dirty": True,
                            "main_behind_count": 0,
                        }
                    ],
                },
            )
            state_dir = root / "state"

            m1 = compile_state(
                config_path=config_path,
                compat_output_path=compat_path,
                state_dir=state_dir,
                generated_at="2026-10-30T01:05:00Z",
            )
            self.assertEqual(m1["capabilities"]["change_feed"]["state"], "available")
            self.assertEqual(m1["counts"]["change_events"], 0)
            self.assertEqual(m1["changes"]["baseline_state"], "initial_baseline")
            self.assertIsNone(m1["changes"]["previous_run_id"])

            c1 = load_changes(state_dir)
            self.assertEqual(c1["schema_version"], CHANGE_FEED_SCHEMA_VERSION)
            self.assertEqual(c1["baseline_state"], "initial_baseline")
            self.assertEqual(c1["change_count"], 0)
            self.assertEqual(c1["changes"], [])
            self.assertIn(m1["run_id"], c1["available_run_ids"])

            # Exact same compile timestamp + inputs -> idempotent initial_baseline (never diffs self).
            m1_repeat = compile_state(
                config_path=config_path,
                compat_output_path=compat_path,
                state_dir=state_dir,
                generated_at="2026-10-30T01:05:00Z",
            )
            self.assertEqual(m1_repeat["run_id"], m1["run_id"])
            c1_repeat = load_changes(state_dir)
            self.assertEqual(c1_repeat["baseline_state"], "initial_baseline")
            self.assertEqual(c1_repeat["change_count"], 0)

            # Advance scanner generated_at and compile generated_at without changing estate facts.
            _write_json(
                compat_path,
                {
                    "generated_at": "2026-10-30T02:00:00+00:00",
                    "entries": [
                        {
                            "project_key": "github.com/mschwar/machina",
                            "source_label": "central-active",
                            "path": "/central/repos/active/machina",
                            "name": "Machina",
                            "remote_url": "https://github.com/mschwar/machina.git",
                            "canonical_url": "https://github.com/mschwar/machina.git",
                            "git": True,
                            "head_branch": "main",
                            "head_commit": "111aaaa",
                            "dirty": True,
                            "main_behind_count": None,
                        }
                    ],
                },
            )
            m2 = compile_state(
                config_path=config_path,
                compat_output_path=compat_path,
                state_dir=state_dir,
                generated_at="2026-10-30T02:05:00Z",
            )
            self.assertNotEqual(m2["run_id"], m1["run_id"])
            c2 = load_changes(state_dir)
            self.assertEqual(c2["baseline_state"], "compared")
            self.assertEqual(c2["previous_run_id"], m1["run_id"])
            self.assertEqual(c2["change_count"], 0)
            self.assertEqual(c2["changes"], [])

            # Re-running the second compile identically preserves previous_run_id == m1["run_id"].
            compile_state(
                config_path=config_path,
                compat_output_path=compat_path,
                state_dir=state_dir,
                generated_at="2026-10-30T02:05:00Z",
            )
            c2_repeat = load_changes(state_dir)
            self.assertEqual(c2_repeat["previous_run_id"], m1["run_id"])
            self.assertEqual(c2_repeat["change_count"], 0)

    def test_unavailable_source_emits_unavailable_never_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live_a = root / "live-a"
            live_b = root / "live-b"
            mirror_c = root / "mirror-c"
            live_a.mkdir()
            live_b.mkdir()
            mirror_c.mkdir()

            config_path = root / "ledger_config.json"
            _write_json(
                config_path,
                {
                    "roots": [
                        {
                            "source_id": "live-a",
                            "path": str(live_a),
                            "label": "live-a",
                            "source_class": "live",
                            "discovery": "children",
                        },
                        {
                            "source_id": "live-b",
                            "path": str(live_b),
                            "label": "live-b",
                            "source_class": "live",
                            "discovery": "children",
                        },
                        {
                            "source_id": "mirror-c",
                            "path": str(mirror_c),
                            "label": "mirror-c",
                            "source_class": "mirror",
                            "discovery": "children",
                        },
                    ]
                },
            )
            compat_path = root / "projects.json"
            _write_json(
                compat_path,
                {
                    "generated_at": "2026-10-30T01:00:00+00:00",
                    "entries": [
                        {
                            "project_key": "proj-a",
                            "source_label": "live-a",
                            "path": str(live_a / "proj-a"),
                            "name": "Proj A",
                        },
                        {
                            "project_key": "laptop-only",
                            "source_label": "live-b",
                            "path": str(live_b / "laptop-only"),
                            "name": "Laptop Only",
                        },
                        {
                            "project_key": "homelab",
                            "source_label": "live-b",
                            "path": str(live_b / "homelab"),
                            "name": "Homelab",
                            "remote_url": "https://github.com/mschwar/homelab.git",
                            "git": True,
                            "head_branch": "main",
                            "head_commit": "aaa111",
                        },
                        {
                            "project_key": "homelab",
                            "source_label": "mirror-c",
                            "path": str(mirror_c / "homelab"),
                            "name": "Homelab",
                            "remote_url": "https://github.com/mschwar/homelab.git",
                            "git": True,
                            "head_branch": "main",
                            "head_commit": "aaa111",
                        },
                    ],
                },
            )
            state_dir = root / "state"
            compile_state(
                config_path=config_path,
                compat_output_path=compat_path,
                state_dir=state_dir,
                generated_at="2026-10-30T01:05:00Z",
            )

            # Take live-b offline (unmounted/inaccessible).
            shutil.rmtree(live_b)
            _write_json(
                compat_path,
                {
                    "generated_at": "2026-10-30T02:00:00+00:00",
                    "entries": [
                        {
                            "project_key": "proj-a",
                            "source_label": "live-a",
                            "path": str(live_a / "proj-a"),
                            "name": "Proj A",
                        },
                        {
                            "project_key": "homelab",
                            "source_label": "mirror-c",
                            "path": str(mirror_c / "homelab"),
                            "name": "Homelab",
                            "remote_url": "https://github.com/mschwar/homelab.git",
                            "git": True,
                            "head_branch": "main",
                            "head_commit": "aaa111",
                        },
                    ],
                },
            )
            compile_state(
                config_path=config_path,
                compat_output_path=compat_path,
                state_dir=state_dir,
                generated_at="2026-10-30T02:05:00Z",
            )

            feed = load_changes(state_dir)
            change_types = [ev["change_type"] for ev in feed["changes"]]
            self.assertIn("SOURCE_STATUS_CHANGED", change_types)
            self.assertIn("PROJECT_UNAVAILABLE", change_types)
            self.assertIn("OBSERVATION_UNAVAILABLE", change_types)
            self.assertNotIn("PROJECT_REMOVED", change_types)
            self.assertNotIn("OBSERVATION_REMOVED", change_types)
            # Homelab fell back from live-b to mirror-c without being marked unavailable or removed.
            self.assertIn("PROJECT_PREFERRED_LOCATION_CHANGED", change_types)
            self.assertIn("PROJECT_MEMBERSHIP_CHANGED", change_types)

            unavail_proj = next(
                ev for ev in feed["changes"] if ev["change_type"] == "PROJECT_UNAVAILABLE"
            )
            self.assertEqual(unavail_proj["project_key"], "laptop-only")
            self.assertEqual(unavail_proj["after"]["reason_code"], "SOURCE_UNAVAILABLE")

    def test_project_added_removed_moved_and_reanchored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live"
            live.mkdir()
            config_path = root / "ledger_config.json"
            _write_json(
                config_path,
                {
                    "roots": [
                        {
                            "source_id": "central-active",
                            "path": str(live),
                            "label": "central-active",
                            "source_class": "live",
                            "discovery": "children",
                        }
                    ]
                },
            )
            compat_path = root / "projects.json"
            _write_json(
                compat_path,
                {
                    "generated_at": "2026-10-30T01:00:00+00:00",
                    "entries": [
                        {
                            "project_key": "local-proto",
                            "source_label": "central-active",
                            "path": "/central/repos/active/local-proto",
                            "name": "Local Proto",
                            "remote_url": "",
                        },
                        {
                            "project_key": "retired-tool",
                            "source_label": "central-active",
                            "path": "/central/repos/active/retired-tool",
                            "name": "Retired Tool",
                        },
                        {
                            "project_key": "github.com/example/movable",
                            "source_label": "central-active",
                            "path": "/central/repos/active/movable-old",
                            "name": "Movable",
                            "remote_url": "https://github.com/example/movable.git",
                        },
                    ],
                },
            )
            state_dir = root / "state"
            compile_state(
                config_path=config_path,
                compat_output_path=compat_path,
                state_dir=state_dir,
                generated_at="2026-10-30T01:05:00Z",
            )

            # Run 2:
            # - local-proto gains git remote (1-to-1 re-anchor)
            # - retired-tool is removed from available source
            # - movable moves directory while keeping exact remote (continuing canonical_project_id)
            # - brand-new appears
            _write_json(
                compat_path,
                {
                    "generated_at": "2026-10-30T02:00:00+00:00",
                    "entries": [
                        {
                            "project_key": "github.com/example/local-proto",
                            "source_label": "central-active",
                            "path": "/central/repos/active/local-proto",
                            "name": "Local Proto",
                            "remote_url": "https://github.com/example/local-proto.git",
                            "git": True,
                            "head_branch": "main",
                            "head_commit": "1234567",
                        },
                        {
                            "project_key": "github.com/example/movable",
                            "source_label": "central-active",
                            "path": "/central/repos/active/movable-new",
                            "name": "Movable",
                            "remote_url": "https://github.com/example/movable.git",
                        },
                        {
                            "project_key": "brand-new",
                            "source_label": "central-active",
                            "path": "/central/repos/active/brand-new",
                            "name": "Brand New",
                        },
                    ],
                },
            )
            compile_state(
                config_path=config_path,
                compat_output_path=compat_path,
                state_dir=state_dir,
                generated_at="2026-10-30T02:05:00Z",
            )

            feed = load_changes(state_dir)
            by_type: dict[str, list[dict]] = {}
            for ev in feed["changes"]:
                by_type.setdefault(ev["change_type"], []).append(ev)

            self.assertEqual(len(by_type.get("PROJECT_REANCHORED", [])), 1)
            self.assertEqual(
                by_type["PROJECT_REANCHORED"][0]["project_key"],
                "github.com/example/local-proto",
            )
            self.assertEqual(len(by_type.get("PROJECT_REMOVED", [])), 1)
            self.assertEqual(by_type["PROJECT_REMOVED"][0]["project_key"], "retired-tool")
            self.assertEqual(len(by_type.get("PROJECT_ADDED", [])), 1)
            self.assertEqual(by_type["PROJECT_ADDED"][0]["project_key"], "brand-new")

            # Movable kept its canonical_project_id and updated membership + preferred_location.
            movable_events = [
                ev
                for ev in feed["changes"]
                if ev.get("project_key") == "github.com/example/movable"
                and ev["entity_kind"] == "canonical_project"
            ]
            movable_types = {ev["change_type"] for ev in movable_events}
            self.assertIn("PROJECT_MEMBERSHIP_CHANGED", movable_types)
            self.assertIn("PROJECT_PREFERRED_LOCATION_CHANGED", movable_types)
            self.assertNotIn("PROJECT_ADDED", movable_types)
            self.assertNotIn("PROJECT_REMOVED", movable_types)

    def test_merge_split_reviews_negative_decisions_since_cli_and_explain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live"
            inv = root / "inv"
            live.mkdir()
            inv.mkdir()

            config_path = root / "ledger_config.json"
            _write_json(
                config_path,
                {
                    "roots": [
                        {
                            "source_id": "central-active",
                            "path": str(live),
                            "label": "central-active",
                            "source_class": "live",
                            "discovery": "children",
                        },
                        {
                            "source_id": "matty-pc-inventory",
                            "path": str(inv),
                            "label": "matty-pc",
                            "source_class": "inventory",
                            "discovery": "children",
                        },
                    ]
                },
            )
            compat_path = root / "projects.json"
            base_entries = [
                {
                    "project_key": "github.com/garrytan/gstack",
                    "source_label": "central-active",
                    "path": "/central/repos/active/gstack-main",
                    "name": "gstack",
                    "remote_url": "https://github.com/garrytan/gstack.git",
                    "git": True,
                    "head_branch": "main",
                    "head_commit": "aaa111",
                    "behind_count": 0,
                    "ahead_count": 0,
                    "upstream_ref": "origin/main",
                    "description": "<p align=\"center\">gstack</p>",
                },
                {
                    "project_key": "google-drive:gstack-main",
                    "source_label": "matty-pc",
                    "path": "matty-pc:gstack-main",
                    "name": "gstack",
                    "remote_url": "",
                },
                {
                    "project_key": "hermes",
                    "source_label": "central-active",
                    "path": "/central/repos/active/hermes",
                    "name": "Hermes",
                    "description": "<p align=\"center\">Shared banner</p>",
                },
                {
                    "project_key": "paperclip",
                    "source_label": "central-active",
                    "path": "/central/repos/active/paperclip",
                    "name": "Paperclip",
                    "description": "<p align=\"center\">Shared banner</p>",
                },
                {
                    "project_key": "the-garden",
                    "source_label": "central-active",
                    "path": "/central/repos/active/the_garden",
                    "name": "The Garden",
                },
                {
                    "project_key": "the-garden",
                    "source_label": "matty-pc",
                    "path": "matty-pc:the_garden",
                    "name": "The Garden",
                },
                {
                    "project_key": "dup-ambig",
                    "source_label": "central-active",
                    "path": "/central/repos/active/dup-1",
                    "name": "Dup 1",
                    "remote_url": "https://github.com/example/dup-1.git",
                },
                {
                    "project_key": "dup-ambig",
                    "source_label": "central-active",
                    "path": "/central/repos/active/dup-2",
                    "name": "Dup 2",
                    "remote_url": "https://github.com/example/dup-2.git",
                },
            ]
            _write_json(
                compat_path,
                {"generated_at": "2026-10-30T01:00:00+00:00", "entries": base_entries},
            )
            decisions_path = root / "identity-decisions.json"
            _write_json(decisions_path, {"schema_version": "1.0.0", "decisions": []})
            state_dir = root / "state"

            m1 = compile_state(
                config_path=config_path,
                compat_output_path=compat_path,
                state_dir=state_dir,
                identity_decisions_path=decisions_path,
                generated_at="2026-10-30T01:05:00Z",
            )
            run1_id = m1["run_id"]

            # Simulate pre-Wave-4 state backfill by removing state/runs and state/changes.json.
            shutil.rmtree(state_dir / "runs")
            (state_dir / "changes.json").unlink()

            obs_doc = json.loads((state_dir / "observations.json").read_text(encoding="utf-8"))
            obs_by_loc = {o["location"]: o["observation_id"] for o in obs_doc["observations"]}
            can_r1 = json.loads((state_dir / "canonical-projects.json").read_text(encoding="utf-8"))
            inv_gstack_pid = next(
                p["canonical_project_id"]
                for p in can_r1["projects"]
                if p["project_key"] == "google-drive:gstack-main"
            )

            # Run 2: apply merge + canonical_key on gstack, reject_match on hermes/paperclip,
            # merge on the-garden (clearing its PROJECT_KEY_AMBIGUOUS review), and introduce git drift.
            run2_entries = json.loads(json.dumps(base_entries))
            run2_entries[0]["head_commit"] = "bbb222"
            run2_entries[0]["behind_count"] = 5
            run2_entries[0]["next_step"] = "Pull origin/main before spawning worker."
            _write_json(
                compat_path,
                {"generated_at": "2026-10-30T02:00:00+00:00", "entries": run2_entries},
            )
            _write_json(
                decisions_path,
                {
                    "schema_version": "1.0.0",
                    "decisions": [
                        {
                            "decision_id": "dec-gstack-merge",
                            "type": "merge",
                            "observation_ids": [
                                obs_by_loc["/central/repos/active/gstack-main"],
                                obs_by_loc["matty-pc:gstack-main"],
                            ],
                            "authority": "reviewed_agent",
                        },
                        {
                            "decision_id": "dec-gstack-key",
                            "type": "canonical_key",
                            "observation_id": obs_by_loc["/central/repos/active/gstack-main"],
                            "key": "gstack",
                            "authority": "reviewed_agent",
                        },
                        {
                            "decision_id": "dec-hermes-paperclip-reject",
                            "type": "reject_match",
                            "left_observation_id": obs_by_loc["/central/repos/active/hermes"],
                            "right_observation_id": obs_by_loc["/central/repos/active/paperclip"],
                            "authority": "reviewed_agent",
                        },
                        {
                            "decision_id": "dec-garden-merge",
                            "type": "merge",
                            "observation_ids": [
                                obs_by_loc["/central/repos/active/the_garden"],
                                obs_by_loc["matty-pc:the_garden"],
                            ],
                            "authority": "operator",
                        },
                    ],
                },
            )

            m2 = compile_state(
                config_path=config_path,
                compat_output_path=compat_path,
                state_dir=state_dir,
                identity_decisions_path=decisions_path,
                generated_at="2026-10-30T02:05:00Z",
            )
            run2_id = m2["run_id"]
            c2 = load_changes(state_dir)
            self.assertEqual(c2["previous_run_id"], run1_id)
            c2_types = {ev["change_type"] for ev in c2["changes"]}
            self.assertIn("PROJECT_MERGED", c2_types)
            self.assertIn("PROJECT_KEY_CHANGED", c2_types)
            self.assertIn("PROJECT_GIT_STATE_CHANGED", c2_types)
            self.assertIn("PROJECT_GIT_DRIFT_CHANGED", c2_types)
            self.assertIn("PROJECT_CONTINUITY_CHANGED", c2_types)
            self.assertIn("REVIEW_RESOLVED", c2_types)
            self.assertIn("NEGATIVE_DECISION_APPLIED", c2_types)
            self.assertEqual(c2["summary"]["count_deltas"]["canonical_projects"]["delta"], -2)
            self.assertEqual(c2["summary"]["count_deltas"]["review_items"]["delta"], -1)

            # Query historical merged project ID (`inv_gstack_pid`) via --project!
            hist_filter = changes_payload(state_dir, project_referent=inv_gstack_pid)
            self.assertEqual(hist_filter["status"], "ok")
            self.assertEqual(hist_filter["change_count"], 1)
            self.assertEqual(hist_filter["changes"][0]["change_type"], "PROJECT_MERGED")

            # Explain a change_id via `ledger explain <chg_...>`.
            sample_chg_id = c2["changes"][0]["change_id"]
            explained = explain_payload(state_dir, sample_chg_id, decisions_path=decisions_path)
            self.assertEqual(explained["status"], "resolved")
            self.assertEqual(explained["subject_kind"], "change_event")
            self.assertEqual(explained["change"]["change_id"], sample_chg_id)

            # Run 3: supersede dec-garden-merge -> PROJECT_SPLIT + REVIEW_OPENED, and clear git drift.
            run3_entries = json.loads(json.dumps(run2_entries))
            run3_entries[0]["behind_count"] = 0
            _write_json(
                compat_path,
                {"generated_at": "2026-10-30T03:00:00+00:00", "entries": run3_entries},
            )
            r3_decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
            r3_decisions["decisions"].append(
                {
                    "decision_id": "dec-supersede-garden",
                    "type": "supersede",
                    "supersedes_decision_id": "dec-garden-merge",
                    "authority": "operator",
                }
            )
            _write_json(decisions_path, r3_decisions)

            m3 = compile_state(
                config_path=config_path,
                compat_output_path=compat_path,
                state_dir=state_dir,
                identity_decisions_path=decisions_path,
                generated_at="2026-10-30T03:05:00Z",
            )
            run3_id = m3["run_id"]
            c3 = load_changes(state_dir)
            self.assertEqual(c3["previous_run_id"], run2_id)
            c3_types = {ev["change_type"] for ev in c3["changes"]}
            self.assertIn("PROJECT_SPLIT", c3_types)
            self.assertIn("REVIEW_OPENED", c3_types)
            self.assertIn("PROJECT_GIT_DRIFT_CHANGED", c3_types)

            # Multi-hop --since run1_id computes net delta run1 -> run3!
            since_r1 = changes_payload(state_dir, since_run_id=run1_id)
            self.assertEqual(since_r1["status"], "ok")
            self.assertEqual(since_r1["previous_run_id"], run1_id)
            self.assertEqual(since_r1["run_id"], run3_id)
            # Net between run1 and run3: the-garden was unmerged in both run1 and run3, while gstack stayed merged!
            since_r1_merges = [
                ev for ev in since_r1["changes"] if ev["change_type"] == "PROJECT_MERGED"
            ]
            self.assertEqual(len(since_r1_merges), 1)
            self.assertEqual(since_r1_merges[0]["project_key"], "gstack")

            # --since current run_id returns same_run with 0 changes.
            same_run = changes_payload(state_dir, since_run_id=run3_id)
            self.assertEqual(same_run["status"], "ok")
            self.assertEqual(same_run["baseline_state"], "same_run")
            self.assertEqual(same_run["change_count"], 0)

            # --since unknown run_id fails explicitly with SINCE_RUN_NOT_FOUND.
            missing_since = changes_payload(state_dir, since_run_id="run_unknown_baseline")
            self.assertEqual(missing_since["status"], "unresolved_baseline")
            self.assertEqual(missing_since["reason_code"], "SINCE_RUN_NOT_FOUND")
            self.assertEqual(missing_since["available_run_ids"], [run1_id, run2_id, run3_id])

            # orient_payload exposes changes summary.
            orient = orient_payload(load_manifest(state_dir))
            self.assertEqual(orient["changes"]["previous_run_id"], run2_id)
            self.assertEqual(orient["changes"]["change_count"], c3["change_count"])

            # CLI exit code coverage: 0 ok, 3 ambiguous project, 4 unresolved baseline/project, 2 missing state.
            out_buf = StringIO()
            with redirect_stdout(out_buf):
                self.assertEqual(cli.main(["changes", "--state-dir", str(state_dir)]), 0)
                self.assertEqual(cli.main(["changes", "--state-dir", str(state_dir), "--json"]), 0)
                self.assertEqual(
                    cli.main(["changes", "--state-dir", str(state_dir), "--since", run1_id]),
                    0,
                )
                self.assertEqual(
                    cli.main(
                        [
                            "changes",
                            "--state-dir",
                            str(state_dir),
                            "--project",
                            "gstack",
                            "--entity-kind",
                            "canonical_project",
                            "--change-type",
                            "PROJECT_GIT_DRIFT_CHANGED",
                        ]
                    ),
                    0,
                )
                self.assertEqual(
                    cli.main(["changes", "--state-dir", str(state_dir), "--project", "dup-ambig"]),
                    3,
                )
                self.assertEqual(
                    cli.main(["changes", "--state-dir", str(state_dir), "--project", "no-such-proj"]),
                    4,
                )
                self.assertEqual(
                    cli.main(["changes", "--state-dir", str(state_dir), "--since", "run_missing"]),
                    4,
                )
                self.assertEqual(cli.main(["orient", "--state-dir", str(state_dir)]), 0)

            err_buf = StringIO()
            with redirect_stderr(err_buf):
                self.assertEqual(
                    cli.main(["changes", "--state-dir", str(root / "missing-state")]),
                    2,
                )

    def test_run_history_prunes_beyond_max_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            prev = None
            for idx in range(5):
                snap = {
                    "schema_version": CHANGE_FEED_SCHEMA_VERSION,
                    "compiler_version": "0.2.0",
                    "run_id": f"run_seq_{idx}",
                    "compiled_at": f"2026-10-30T0{idx}:00:00Z",
                    "input_observed_at": f"2026-10-30T0{idx}:00:00Z",
                    "health_state": "ok",
                    "counts": {"sources": 0, "observations": 0, "canonical_projects": 0, "review_items": 0},
                    "sources": {},
                    "observations": {},
                    "projects": {},
                    "reviews": {},
                    "negative_decisions": {},
                }
                payload = materialize_changes(
                    previous_snapshot=prev,
                    current_snapshot=snap,
                    state_dir=state_dir,
                    max_runs=3,
                )
                prev = snap
            self.assertEqual(payload["available_run_ids"], ["run_seq_2", "run_seq_3", "run_seq_4"])
            self.assertEqual(payload["review_count"], 0)
            self.assertEqual(payload["untriaged_weak_overlap_count"], 0)
            retained_files = sorted(p.name for p in (state_dir / "runs").glob("*.json"))
            self.assertEqual(
                retained_files,
                ["run_seq_2.json", "run_seq_3.json", "run_seq_4.json"],
            )


if __name__ == "__main__":
    unittest.main()
