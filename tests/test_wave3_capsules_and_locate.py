"""Tests for Wave 3 read plane: P3.2 project capsules, P3.4 ledger show, P3.5 ledger locate."""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from ledger import PROJECT_CAPSULE_SCHEMA_VERSION, cli
from ledger.capsules import (
    CAPSULE_MAX_BYTES,
    CAPSULE_MAX_OBSERVATION_SUMMARIES,
    build_project_capsule,
    locate_payload,
    rank_project_observations,
    show_payload,
)
from ledger.compiler import compile_state
from ledger.identity import compile_identity, materialize_identity


def _obs(
    observation_id: str,
    *,
    source_id: str = "central-active",
    source_label: str = "central-active",
    source_resolution: str = "resolved",
    project_key: str = "homelab",
    name: str = "Homelab Agent Platform",
    path: str = "/central/repos/active/homelab",
    remote_url: str = "https://github.com/mschwar/homelab.git",
    canonical_url: str = "",
    git: bool = True,
    head_branch: str = "main",
    head_commit: str = "abc1234",
    head_commit_at: str = "2026-10-20T00:00:00+00:00",
    last_remote_ref_at: str = "2026-10-20T00:00:00+00:00",
    last_touch_at: str = "2026-10-20T00:00:00+00:00",
    last_session_at: str = "",
    last_session_summary: str = "",
    next_step: str = "",
    description: str = "Homelab control plane.",
    status: str = "active",
    tags: list[str] | None = None,
) -> dict:
    return {
        "schema_version": "1.0.0",
        "observation_id": observation_id,
        "source_id": source_id,
        "snapshot_id": f"snap_{source_id}",
        "source_resolution": source_resolution,
        "observed_at": "2026-10-29T00:00:00Z",
        "project_key": project_key,
        "location": path or canonical_url or None,
        "compat_entry": {
            "project_key": project_key,
            "name": name,
            "repo_name": Path(path).name if path.startswith("/") else project_key,
            "source_label": source_label,
            "machine_name": "ai-server",
            "path": path,
            "remote_url": remote_url,
            "canonical_url": canonical_url or remote_url,
            "git": git,
            "head_branch": head_branch,
            "head_commit": head_commit,
            "head_commit_at": head_commit_at,
            "last_remote_ref_at": last_remote_ref_at,
            "last_touch_at": last_touch_at,
            "last_session_at": last_session_at,
            "last_session_summary": last_session_summary,
            "next_step": next_step,
            "description": description,
            "status": status,
            "tags": tags or ["git"],
            "project_type": "git" if git else "code",
        },
    }


class PreferredLocationResolverTests(unittest.TestCase):
    def test_ranks_live_over_mirror_over_backup_over_inventory(self) -> None:
        live = _obs(
            "obs-live",
            source_id="central-active",
            source_label="central-active",
            path="/central/repos/active/homelab",
            last_remote_ref_at="2026-10-25T00:00:00Z",
        )
        mirror = _obs(
            "obs-mirror",
            source_id="mac-mirror",
            source_label="mac-mirror",
            path="/central/registry/mirrors/mac/homelab",
            last_remote_ref_at="2026-10-28T00:00:00Z",
        )
        backup = _obs(
            "obs-backup",
            source_id="archive-backup",
            source_label="archive-backup",
            path="/central/archive/root-backups/homelab",
            last_remote_ref_at="2026-10-29T00:00:00Z",
        )
        inventory = _obs(
            "obs-inventory",
            source_id="matty-pc-inventory",
            source_label="matty-pc",
            path="matty-pc:Documents/homelab",
            git=False,
            head_branch="",
            head_commit="",
        )
        observations = [backup, inventory, mirror, live]
        canonical, _ = compile_identity(observations, {"schema_version": "1.0.0", "decisions": []})
        self.assertEqual(canonical["canonical_project_count"], 1)
        project = canonical["projects"][0]

        sources_by_id = {
            "central-active": {"source_id": "central-active", "source_class": "live", "status": "available"},
            "mac-mirror": {"source_id": "mac-mirror", "source_class": "mirror", "status": "available"},
            "archive-backup": {"source_id": "archive-backup", "source_class": "backup", "status": "available"},
            "matty-pc-inventory": {
                "source_id": "matty-pc-inventory",
                "source_class": "inventory",
                "status": "available",
            },
        }
        ranked, preferred = rank_project_observations(
            project,
            {o["observation_id"]: o for o in observations},
            sources_by_id=sources_by_id,
        )

        self.assertEqual([item["observation_id"] for item in ranked], ["obs-live", "obs-mirror", "obs-backup", "obs-inventory"])
        self.assertEqual([item["rank"] for item in ranked], [1, 2, 3, 4])
        self.assertEqual(preferred["state"], "resolved")
        self.assertEqual(preferred["observation_id"], "obs-live")
        self.assertEqual(preferred["source_class"], "live")
        self.assertEqual(preferred["confidence"], "high")
        self.assertIn("ACCESSIBLE_SOURCE", preferred["reason_codes"])
        self.assertIn("SOURCE_CLASS_LIVE", preferred["reason_codes"])
        self.assertIn("CANONICAL_REMOTE_MATCH", preferred["reason_codes"])

    def test_unavailable_live_source_falls_back_to_available_mirror(self) -> None:
        live = _obs("obs-live", source_id="live-down", path="/central/repos/active/homelab")
        mirror = _obs("obs-mirror", source_id="mac-mirror", path="/central/registry/mirrors/mac/homelab")
        observations = [live, mirror]
        canonical, _ = compile_identity(observations, {"schema_version": "1.0.0", "decisions": []})
        project = canonical["projects"][0]
        sources_by_id = {
            "live-down": {"source_id": "live-down", "source_class": "live", "status": "unavailable"},
            "mac-mirror": {"source_id": "mac-mirror", "source_class": "mirror", "status": "available"},
        }
        ranked, preferred = rank_project_observations(
            project,
            {o["observation_id"]: o for o in observations},
            sources_by_id=sources_by_id,
        )
        self.assertEqual(preferred["state"], "resolved")
        self.assertEqual(preferred["observation_id"], "obs-mirror")
        self.assertEqual(preferred["confidence"], "medium")
        self.assertIn("SOURCE_CLASS_MIRROR", preferred["reason_codes"])
        self.assertIn("SOURCE_UNAVAILABLE", ranked[1]["reason_codes"])

    def test_session_evidence_and_git_freshness_break_ties_between_live_roots(self) -> None:
        older_live = _obs(
            "obs-a",
            source_id="central-projects",
            path="/central/projects/app",
            last_remote_ref_at="2026-09-01T00:00:00Z",
        )
        session_live = _obs(
            "obs-b",
            source_id="central-projects",
            path="/central/projects/app-work",
            last_remote_ref_at="2026-09-01T00:00:00Z",
            last_session_at="2026-10-28T12:00:00Z",
            last_session_summary="Completed migration.",
            next_step="Run verification.",
        )
        observations = [older_live, session_live]
        canonical, _ = compile_identity(observations, {"schema_version": "1.0.0", "decisions": []})
        project = canonical["projects"][0]
        _, preferred = rank_project_observations(
            project,
            {o["observation_id"]: o for o in observations},
            sources_by_id={"central-projects": {"source_id": "central-projects", "source_class": "live", "status": "available"}},
        )
        self.assertEqual(preferred["observation_id"], "obs-b")
        self.assertIn("RECENT_SESSION_EVIDENCE", preferred["reason_codes"])

    def test_inventory_only_project_reports_inventory_only_state(self) -> None:
        inv = _obs(
            "obs-inv",
            source_id="matty-pc-inventory",
            source_label="matty-pc",
            path="matty-pc:Documents/solo-project",
            remote_url="",
            git=False,
            head_branch="",
            head_commit="",
        )
        canonical, _ = compile_identity([inv], {"schema_version": "1.0.0", "decisions": []})
        project = canonical["projects"][0]
        _, preferred = rank_project_observations(
            project,
            {"obs-inv": inv},
            sources_by_id={"matty-pc-inventory": {"source_id": "matty-pc-inventory", "source_class": "inventory", "status": "available"}},
        )
        self.assertEqual(preferred["state"], "inventory_only")
        self.assertEqual(preferred["confidence"], "low")
        self.assertIn("INVENTORY_ONLY_LOCATOR", preferred["reason_codes"])

    def test_primary_active_root_on_worker_branch_outranks_idle_secondary_primary_branch(self) -> None:
        frozen_secondary = _obs(
            "obs-secondary-apps",
            source_id="central-apps",
            source_label="central-apps",
            path="/central/repos/apps/steam-db-mattified",
            remote_url="https://github.com/mschwar/gamma-steamdb.git",
            head_branch="master",
            head_commit="f3eca62",
            head_commit_at="2026-08-23T13:52:11-06:00",
            last_remote_ref_at="2026-08-23T13:52:11-06:00",
            last_touch_at="2026-08-23T13:52:11-06:00",
        )
        active_worker = _obs(
            "obs-active-gamma",
            source_id="central-active",
            source_label="central-active",
            path="/central/repos/active/gamma-steamdb",
            remote_url="https://github.com/mschwar/gamma-steamdb.git",
            head_branch="c007/C007-W001-U01-machina-thin-waist",
            head_commit="3006a90",
            head_commit_at="2026-10-30T15:50:48+00:00",
            last_remote_ref_at="2026-10-30T15:50:48+00:00",
            last_touch_at="2026-10-30T15:50:48+00:00",
        )
        observations = [frozen_secondary, active_worker]
        canonical, _ = compile_identity(observations, {"schema_version": "1.0.0", "decisions": []})
        project = canonical["projects"][0]
        sources_by_id = {
            "central-apps": {"source_id": "central-apps", "source_class": "live", "status": "available"},
            "central-active": {"source_id": "central-active", "source_class": "live", "status": "available"},
        }
        ranked, preferred = rank_project_observations(
            project,
            {o["observation_id"]: o for o in observations},
            sources_by_id=sources_by_id,
        )
        self.assertEqual([item["observation_id"] for item in ranked], ["obs-active-gamma", "obs-secondary-apps"])
        self.assertEqual(preferred["observation_id"], "obs-active-gamma")
        self.assertIn("PRIMARY_ACTIVE_ROOT", preferred["reason_codes"])
        self.assertNotIn("PRIMARY_BRANCH", preferred["reason_codes"])

    def test_recent_content_as_of_outranks_stale_primary_branch_between_secondary_roots(self) -> None:
        stale_main = _obs(
            "obs-stale-main",
            source_id="central-projects",
            source_label="central-projects",
            path="/central/projects/service",
            head_branch="main",
            last_remote_ref_at="2026-05-01T00:00:00Z",
        )
        fresh_branch = _obs(
            "obs-fresh-branch",
            source_id="central-apps",
            source_label="central-apps",
            path="/central/repos/apps/service",
            head_branch="feat/worker-slice",
            last_remote_ref_at="2026-10-30T00:00:00Z",
        )
        observations = [stale_main, fresh_branch]
        canonical, _ = compile_identity(observations, {"schema_version": "1.0.0", "decisions": []})
        project = canonical["projects"][0]
        sources_by_id = {
            "central-projects": {"source_id": "central-projects", "source_class": "live", "status": "available"},
            "central-apps": {"source_id": "central-apps", "source_class": "live", "status": "available"},
        }
        _, preferred = rank_project_observations(
            project,
            {o["observation_id"]: o for o in observations},
            sources_by_id=sources_by_id,
        )
        self.assertEqual(preferred["observation_id"], "obs-fresh-branch")


class CapsuleBudgetAndMaterializationTests(unittest.TestCase):
    def test_capsule_enforces_size_and_observation_summary_budget(self) -> None:
        observations = [
            _obs(
                f"obs-{idx:03d}",
                path=f"/central/repos/active/homelab-copy-{idx:03d}",
                description="A" * 500,
            )
            for idx in range(40)
        ]
        canonical, _ = compile_identity(observations, {"schema_version": "1.0.0", "decisions": []})
        project = canonical["projects"][0]
        with tempfile.TemporaryDirectory() as tmp:
            capsule = build_project_capsule(
                project=project,
                observations_by_id={o["observation_id"]: o for o in observations},
                state_dir=Path(tmp),
                run_id="run_budget",
                compiled_at="2026-10-29T00:00:00Z",
                input_observed_at="2026-10-29T00:00:00Z",
            )
            encoded = (json.dumps(capsule, indent=2, sort_keys=True) + "\n").encode("utf-8")
            self.assertLessEqual(len(encoded), CAPSULE_MAX_BYTES)
            self.assertEqual(capsule["observation_count"], 40)
            self.assertLessEqual(len(capsule["observations"]), CAPSULE_MAX_OBSERVATION_SUMMARIES)
            self.assertTrue(capsule["budget"]["observations_truncated"])
            self.assertTrue(capsule["budget"]["within_budget"])
            self.assertEqual(len(capsule["pointers"]["observation_ids"]), 40)
            self.assertLessEqual(len(capsule["purpose"] or ""), 240)

    def test_compile_state_materializes_capsules_and_enables_show_and_locate_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live_dir = root / "live"
            mirror_dir = root / "mirror"
            live_dir.mkdir()
            mirror_dir.mkdir()

            config_path = root / "ledger_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "roots": [
                            {
                                "source_id": "central-active",
                                "path": str(live_dir),
                                "label": "central-active",
                                "discovery": "children",
                            },
                            {
                                "source_id": "macbook-mirror",
                                "path": str(mirror_dir),
                                "label": "macbook-mirror",
                                "discovery": "children",
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
                        "generated_at": "2026-10-29T02:00:00+00:00",
                        "entries": [
                            {
                                "project_key": "homelab",
                                "source_label": "central-active",
                                "path": str(live_dir / "homelab"),
                                "name": "Homelab Agent Platform",
                                "remote_url": "git@github.com:mschwar/homelab.git",
                                "canonical_url": "git@github.com:mschwar/homelab.git",
                                "git": True,
                                "head_branch": "main",
                                "head_commit": "1111111",
                                "head_commit_at": "2026-10-29T01:00:00+00:00",
                                "last_remote_ref_at": "2026-10-29T01:00:00+00:00",
                                "description": "Canonical homelab repo.",
                                "status": "active",
                            },
                            {
                                "project_key": "homelab",
                                "source_label": "macbook-mirror",
                                "path": str(mirror_dir / "homelab"),
                                "name": "Homelab Agent Platform",
                                "remote_url": "https://github.com/mschwar/homelab.git",
                                "canonical_url": "https://github.com/mschwar/homelab.git",
                                "git": True,
                                "head_branch": "main",
                                "head_commit": "0000000",
                                "head_commit_at": "2026-04-20T00:00:00+00:00",
                                "last_remote_ref_at": "2026-04-20T00:00:00+00:00",
                                "description": "Mirror copy.",
                            },
                            {
                                "project_key": "ambig-key",
                                "source_label": "central-active",
                                "path": str(live_dir / "ambig-a"),
                                "name": "Ambiguous A",
                                "remote_url": "https://github.com/example/a.git",
                            },
                            {
                                "project_key": "ambig-key",
                                "source_label": "central-active",
                                "path": str(live_dir / "ambig-b"),
                                "name": "Ambiguous B",
                                "remote_url": "https://github.com/example/b.git",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            state_dir = root / "state"
            # Create a stale capsule file to prove compile cleans up obsolete capsules.
            stale_dir = state_dir / "projects"
            stale_dir.mkdir(parents=True, exist_ok=True)
            stale_file = stale_dir / "prj_stale_obsolete.json"
            stale_file.write_text("{}", encoding="utf-8")

            manifest = compile_state(
                config_path=config_path,
                compat_output_path=compat_path,
                state_dir=state_dir,
                generated_at="2026-10-29T02:10:00Z",
            )

            self.assertFalse(stale_file.exists())
            self.assertEqual(manifest["capabilities"]["project_capsules"]["state"], "available")
            self.assertEqual(manifest["counts"]["project_capsules"], 3)
            self.assertEqual(manifest["artifacts"]["project_capsules"], "state/projects")

            shown = show_payload(state_dir, "homelab")
            self.assertEqual(shown["status"], "resolved")
            self.assertEqual(shown["schema_version"], PROJECT_CAPSULE_SCHEMA_VERSION)
            self.assertEqual(shown["as_of"], "2026-10-29T02:00:00+00:00")
            self.assertEqual(shown["project_key"], "homelab")
            self.assertEqual(shown["observation_count"], 2)
            self.assertEqual(shown["preferred_location"]["location"], str(live_dir / "homelab"))
            self.assertEqual(shown["preferred_location"]["source_class"], "live")
            self.assertEqual(shown["git_facts"]["head_commit"], "1111111")

            located = locate_payload(state_dir, "https://github.com/mschwar/homelab.git")
            self.assertEqual(located["status"], "resolved")
            self.assertEqual(located["candidate_count"], 2)
            self.assertEqual(located["preferred_location"]["location"], str(live_dir / "homelab"))
            self.assertEqual([loc["source_class"] for loc in located["locations"]], ["live", "mirror"])

            # CLI exit codes for show and locate: 0 resolved, 3 ambiguous, 4 unresolved, 2 missing state.
            out_buf = StringIO()
            with redirect_stdout(out_buf):
                self.assertEqual(cli.main(["show", "homelab", "--state-dir", str(state_dir), "--json"]), 0)
                self.assertEqual(cli.main(["show", "homelab", "--state-dir", str(state_dir)]), 0)
                self.assertEqual(cli.main(["locate", "homelab", "--state-dir", str(state_dir), "--json"]), 0)
                self.assertEqual(cli.main(["locate", "homelab", "--state-dir", str(state_dir)]), 0)
                self.assertEqual(cli.main(["show", "ambig-key", "--state-dir", str(state_dir), "--json"]), 3)
                self.assertEqual(cli.main(["locate", "ambig-key", "--state-dir", str(state_dir), "--json"]), 3)
                self.assertEqual(cli.main(["show", "no-such-project", "--state-dir", str(state_dir), "--json"]), 4)
                self.assertEqual(cli.main(["locate", "no-such-project", "--state-dir", str(state_dir), "--json"]), 4)

            err_buf = StringIO()
            with redirect_stderr(err_buf):
                self.assertEqual(cli.main(["show", "homelab", "--state-dir", str(root / "missing-state")]), 2)
                self.assertEqual(cli.main(["locate", "homelab", "--state-dir", str(root / "missing-state")]), 2)

    def test_materialize_identity_writes_deterministic_capsules(self) -> None:
        obs = [_obs("obs-1")]
        with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
            can_a, _ = materialize_identity(
                observations=obs,
                decisions_path=None,
                state_dir=Path(tmp_a),
                run_id="run-fixed",
                compiled_at="2026-10-29T00:00:00Z",
            )
            can_b, _ = materialize_identity(
                observations=obs,
                decisions_path=None,
                state_dir=Path(tmp_b),
                run_id="run-fixed",
                compiled_at="2026-10-29T00:00:00Z",
            )
            pid = can_a["projects"][0]["canonical_project_id"]
            self.assertEqual(pid, can_b["projects"][0]["canonical_project_id"])
            cap_a = json.loads((Path(tmp_a) / "projects" / f"{pid}.json").read_text(encoding="utf-8"))
            cap_b = json.loads((Path(tmp_b) / "projects" / f"{pid}.json").read_text(encoding="utf-8"))
            # Exclude state_dir-dependent pointer paths when comparing across temp dirs.
            cap_a.pop("pointers")
            cap_b.pop("pointers")
            self.assertEqual(cap_a, cap_b)


if __name__ == "__main__":
    unittest.main()
