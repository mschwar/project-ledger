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
            self.assertEqual(cap_a, cap_b)


class CtxTier2CapsuleIngestionTests(unittest.TestCase):
    def test_ingests_ctx_skeleton_json_fleet_capsule_and_tier2_dir_within_budget(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            state_dir = base / "state"

            # 1. Repo with .ctx/skeleton.json (RepoSkeleton schema)
            repo_skel = base / "repo-skel"
            (repo_skel / ".ctx").mkdir(parents=True)
            (repo_skel / ".ctx" / "skeleton.json").write_text(
                json.dumps(
                    {
                        "root": str(repo_skel),
                        "repo_name": "repo-skel",
                        "structural_hash": "sha256:1111",
                        "boundary_doc_hash": "sha256:2222",
                        "total_files": 24,
                        "languages": {"Python": 20, "Markdown": 4},
                        "entrypoints": ["cli:ledger -> ledger.cli:main"],
                        "boundary_docs": [
                            {
                                "relative_path": "AGENTS.md",
                                "kind": "authority",
                                "headings": ["Mission", "Architectural laws"],
                                "summary_line": "Durable project-reality compiler contract.",
                                "content_hash": "sha256:aaaa",
                            }
                        ],
                        "subsystems": [
                            {
                                "path": f"ledger/mod_{idx}",
                                "file_count": 3,
                                "languages": ["Python"],
                                "key_symbols": ["fn compile_state", "class Capsule"],
                                "internal_deps": ["ledger.models"],
                                "external_deps": ["json"],
                            }
                            for idx in range(20)
                        ],
                    }
                ),
                encoding="utf-8",
            )

            # 2. Repo with .ctx/capsule.json (FleetCapsuleResult schema)
            repo_fleet = base / "repo-fleet"
            (repo_fleet / ".ctx").mkdir(parents=True)
            (repo_fleet / ".ctx" / "capsule.json").write_text(
                json.dumps(
                    {
                        "repo_id": "sha256:9999",
                        "repo_name": "repo-fleet",
                        "root": str(repo_fleet),
                        "structural_hash": "sha256:3333",
                        "boundary_doc_hash": "sha256:4444",
                        "total_files": 8,
                        "languages": {"TypeScript": 8},
                        "entrypoints": ["npm:bin:ctx -> dist/cli.js"],
                        "authority_boundaries": [
                            {
                                "path": "README.md",
                                "kind": "overview",
                                "headings": ["Overview"],
                                "summary": "Fleet capsule overview fallback purpose.",
                                "content_hash": "sha256:bbbb",
                            }
                        ],
                        "subsystems": [
                            {
                                "path": "src/core",
                                "file_count": 8,
                                "languages": ["TypeScript"],
                                "key_symbols": ["fn buildFleetCapsule"],
                                "internal_deps": [],
                                "external_deps": [],
                            }
                        ],
                        "capsule_markdown": "# repo-fleet\n",
                    }
                ),
                encoding="utf-8",
            )

            # 3. Repo with Tier-2 .ctx/metadata.json + CONTEXT-0.md
            repo_t2 = base / "repo-t2"
            (repo_t2 / ".ctx").mkdir(parents=True)
            (repo_t2 / ".ctx" / "metadata.json").write_text(
                json.dumps(
                    {
                        "mode": "tier2",
                        "repo_id": "sha256:7777",
                        "structural_hash": "sha256:5555",
                        "boundary_doc_hash": "sha256:6666",
                    }
                ),
                encoding="utf-8",
            )
            (repo_t2 / ".ctx" / "CONTEXT-0.md").write_text(
                "\n".join(
                    [
                        "---",
                        "mode: tier2",
                        "files: 15",
                        "subsystems: 4",
                        'structural_hash: "sha256:5555"',
                        "---",
                        "",
                        "# repo-t2 — Architecture & Authority Map (CONTEXT-0)",
                        "",
                        "**Files:** 15 | **Subsystems:** 4 | **Languages:** python (12), markdown (3)",
                        "",
                        "## Authority Boundaries & Governance",
                        "- **AGENTS.md** [`authority`] (Tier-2 repo protocol) — Sections: Mission, Rules",
                        "",
                        "## Entrypoints & Operational Surfaces",
                        "- `pyproject.toml:[project.scripts] -> t2=t2.cli:main`",
                        "",
                        "## Subsystem Overview",
                        "| Subsystem | Files | Languages | Key Exports |",
                        "|-----------|------:|-----------|-------------|",
                        "| `src/t2` | 12 | python | `cli.py:main` |",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            # 4. Repo with Tier-1 root CONTEXT.md generated by ctx
            repo_t1 = base / "repo-t1"
            repo_t1.mkdir(parents=True)
            (repo_t1 / "CONTEXT.md").write_text(
                "\n".join(
                    [
                        "---",
                        "generated: '2026-10-30T15:05:37Z'",
                        "generator: ctx/1.1.0",
                        "content_hash: sha256:1c0bacbb",
                        "files: 20",
                        "dirs: 8",
                        "---",
                        "# /central/repos/active/repo-t1",
                        "",
                        "Tier-1 ctx manifest overview summary.",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            obs_list = [
                _obs(
                    "obs_skel",
                    source_id="central-active",
                    source_label="central-active",
                    project_key="repo-skel",
                    name="Repo Skel",
                    path=str(repo_skel),
                    remote_url="https://github.com/example/repo-skel.git",
                    description="",
                ),
                _obs(
                    "obs_fleet",
                    source_id="central-active",
                    source_label="central-active",
                    project_key="repo-fleet",
                    name="Repo Fleet",
                    path=str(repo_fleet),
                    remote_url="https://github.com/example/repo-fleet.git",
                    description="",
                ),
                _obs(
                    "obs_t2",
                    source_id="central-active",
                    source_label="central-active",
                    project_key="repo-t2",
                    name="Repo T2",
                    path=str(repo_t2),
                    remote_url="https://github.com/example/repo-t2.git",
                    description="Tier 2 repo.",
                ),
                _obs(
                    "obs_t1",
                    source_id="central-active",
                    source_label="central-active",
                    project_key="repo-t1",
                    name="Repo T1",
                    path=str(repo_t1),
                    remote_url="https://github.com/example/repo-t1.git",
                    description="",
                ),
            ]
            materialize_identity(
                observations=obs_list,
                decisions_path=None,
                state_dir=state_dir,
                run_id="run-ctx",
                compiled_at="2026-10-31T04:30:00Z",
            )

            cap_skel = show_payload(state_dir, "repo-skel")
            self.assertEqual(cap_skel["ctx"]["state"], "available")
            self.assertEqual(cap_skel["ctx"]["source_format"], "skeleton_json")
            self.assertEqual(cap_skel["ctx"]["structural_hash"], "sha256:1111")
            self.assertEqual(cap_skel["ctx"]["subsystem_count"], 20)
            self.assertTrue(cap_skel["ctx"]["subsystems_truncated"])
            self.assertLessEqual(len(cap_skel["ctx"]["subsystems"]), 12)
            self.assertEqual(cap_skel["purpose"], "Durable project-reality compiler contract.")
            self.assertTrue(cap_skel["budget"]["within_budget"])

            cap_fleet = show_payload(state_dir, "repo-fleet")
            self.assertEqual(cap_fleet["ctx"]["state"], "available")
            self.assertEqual(cap_fleet["ctx"]["source_format"], "fleet_capsule_json")
            self.assertEqual(cap_fleet["ctx"]["repo_id"], "sha256:9999")
            self.assertEqual(cap_fleet["purpose"], "Fleet capsule overview fallback purpose.")
            self.assertTrue(cap_fleet["budget"]["within_budget"])

            cap_t2 = show_payload(state_dir, "repo-t2")
            self.assertEqual(cap_t2["ctx"]["state"], "available")
            self.assertEqual(cap_t2["ctx"]["source_format"], "tier2_ctx_dir")
            self.assertEqual(cap_t2["ctx"]["total_files"], 15)
            self.assertEqual(cap_t2["ctx"]["subsystem_count"], 4)
            self.assertEqual(cap_t2["ctx"]["languages"], {"python": 12, "markdown": 3})
            self.assertEqual(cap_t2["ctx"]["entrypoints"], ["pyproject.toml:[project.scripts] -> t2=t2.cli:main"])
            self.assertEqual(cap_t2["ctx"]["subsystems"][0]["path"], "src/t2")

            cap_t1 = show_payload(state_dir, "repo-t1")
            self.assertEqual(cap_t1["ctx"]["state"], "available")
            self.assertEqual(cap_t1["ctx"]["source_format"], "tier1_context_md")
            self.assertEqual(cap_t1["ctx"]["total_files"], 20)
            self.assertEqual(cap_t1["ctx"]["subsystem_count"], 8)
            self.assertEqual(cap_t1["purpose"], "Tier-1 ctx manifest overview summary.")


if __name__ == "__main__":
    unittest.main()
