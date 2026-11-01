from __future__ import annotations

import contextlib
import copy
import inspect
import io
import json
import os
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from ledger import cli, jev
from ledger.capsules import ingest_project_ctx_capsule
from ledger.structural_ctx import extract_structural_ctx
from tests.test_state_schemas import validate_json_schema


def _init_git_repo(repo_dir: Path, remote_url: str) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(repo_dir)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo_dir), "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "remote", "add", "origin", remote_url],
        check=True,
        capture_output=True,
    )


class EnrichedCtxAndJevTests(unittest.TestCase):
    def test_enriched_ctx_operational_surfaces_dependency_cycles_and_capsule_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "svc-repo"
            _init_git_repo(repo, "https://github.com/example/svc-repo.git")
            (repo / "AGENTS.md").write_text("# AGENTS\n\nGovernance contract.\n", encoding="utf-8")
            (repo / "pyproject.toml").write_text(
                '[project]\nname = "svc-repo"\nversion = "0.1.0"\n'
                '[project.scripts]\nsvc = "svc.cli:main"\n',
                encoding="utf-8",
            )
            pkg = repo / "svc"
            pkg.mkdir()
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            # Mutual cycle between a.py and b.py + CLI, HTTP, table-driven MCP tool/resource, and env var
            (pkg / "a.py").write_text(
                "import os\nimport svc.b\n"
                "def get_mode() -> str:\n"
                '    return os.getenv("SVC_RUNTIME_MODE", "prod")\n',
                encoding="utf-8",
            )
            (pkg / "b.py").write_text(
                "import svc.a\n"
                "def helper() -> str:\n"
                "    return svc.a.get_mode()\n",
                encoding="utf-8",
            )
            (pkg / "cli.py").write_text(
                "import argparse\nimport svc.a\n"
                "def main() -> None:\n"
                "    p = argparse.ArgumentParser(prog='svc')\n"
                "    sub = p.add_subparsers(dest='cmd')\n"
                "    sub.add_parser('deploy')\n"
                "    sub.add_parser('status')\n",
                encoding="utf-8",
            )
            (pkg / "api_server.py").write_text(
                "class App:\n"
                "    def get(self, p): return lambda f: f\n"
                "app = App()\n"
                '@app.get("/v1/health")\n'
                "def health(): return {'ok': True}\n"
                '_TOOLS = [{"name": "svc_inspect", "description": "Inspect"}]\n'
                '_STATIC_RESOURCES = [{"uri": "svc://manifest", "mimeType": "application/json"}]\n',
                encoding="utf-8",
            )

            extracted = extract_structural_ctx(repo)
            self.assertIsNotNone(extracted)
            assert extracted is not None
            kinds = {s["kind"] for s in extracted["operational_surfaces"]}
            self.assertTrue({"cli_command", "http_route", "mcp_tool", "mcp_resource", "env_var"} <= kinds)
            self.assertGreaterEqual(extracted["operational_surface_count"], 5)
            # Single subsystem 'svc' has internal self-import edges but 0 cross-subsystem cycles;
            # verify dependency_summary schema fields are populated
            self.assertIn("internal_edge_count", extracted["dependency_summary"])
            self.assertIn("cycle_count", extracted["dependency_summary"])
            self.assertTrue(all("arch_role" in sub for sub in extracted["subsystems"]))

            # Also verify 5-column Tier 2 `.ctx/CONTEXT-0.md` ingestion with `| Subsystem | Role | ... |`
            ctx_dir = repo / ".ctx"
            ctx_dir.mkdir()
            (ctx_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "mode": "tier2",
                        "structural_hash": "sha256:1111111111111111",
                        "boundary_doc_hash": "sha256:2222222222222222",
                    }
                ),
                encoding="utf-8",
            )
            (ctx_dir / "CONTEXT-0.md").write_text(
                "---\ngenerated: '2026-11-01T00:00:00Z'\nfiles: 6\n---\n"
                "# svc-repo — Repository Architecture & Invariants\n\n"
                "## Authority & Architecture Boundaries\n"
                "### Governance & Architecture Docs\n"
                "- **`AGENTS.md`** — AGENTS\n\n"
                "### Entrypoints & Operational Surfaces\n"
                "- `pyproject.toml [project.scripts]: svc = svc.cli:main`\n"
                "- **CLI:** `svc deploy` (`svc/cli.py`), `svc status` (`svc/cli.py`)\n"
                "- **HTTP:** `GET /v1/health` (`svc/api_server.py`)\n"
                "- **MCP Tools:** `svc_inspect` (`svc/api_server.py`)\n"
                "- **Env Config:** `SVC_RUNTIME_MODE`\n\n"
                "## Subsystem Map\n"
                "| Subsystem | Role | Files | Languages | Key Exports |\n"
                "|-----------|------|------:|-----------|-------------|\n"
                "| `svc` | `entrypoint_interface` | 5 | Python | `cli.py:fn main` |\n",
                encoding="utf-8",
            )
            ingested = ingest_project_ctx_capsule(
                [
                    {
                        "observation_id": "obs_1",
                        "source_class": "live",
                        "source_status": "available",
                        "location": str(repo),
                        "score_components": {"accessible_worktree": True},
                    }
                ],
                observations_by_id={},
            )
            self.assertEqual(ingested["source_format"], "tier2_ctx_dir")
            self.assertEqual(ingested["subsystems"][0]["arch_role"], "entrypoint_interface")
            self.assertGreaterEqual(ingested["operational_surface_count"], 4)

            # Remove `.ctx` so real `ledger refresh` exercises compiled_structural_scan + full capsule schema validation
            for child in list(ctx_dir.iterdir()):
                child.unlink()
            ctx_dir.rmdir()
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-m", "svc"], check=True, capture_output=True)

            repos_parent = Path(tmp) / "repos_root"
            repos_parent.mkdir(parents=True, exist_ok=True)
            repo_moved = repos_parent / "svc-repo"
            repo.rename(repo_moved)

            ws = Path(tmp) / "ws_schema"
            cfg_path = ws / "ledger_config.json"
            ws.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(
                json.dumps(
                    {
                        "roots": [
                            {
                                "source_id": "local-svc",
                                "label": "local-svc",
                                "path": str(repos_parent),
                                "discovery": "children",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            state_dir = ws / "state"
            out_dir = ws / "output"
            dec_path = ws / "registry" / "identity-decisions.json"
            dec_path.parent.mkdir(parents=True, exist_ok=True)
            dec_path.write_text('{"schema_version": "1.0.0", "decisions": []}\n', encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()):
                rc = cli.main(
                    [
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
                    ]
                )
            self.assertEqual(rc, 0)
            schema_path = Path(__file__).resolve().parents[1] / "schemas" / "project-capsule.schema.json"
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            capsules = list((state_dir / "projects").glob("*.json"))
            self.assertEqual(len(capsules), 1)
            raw_bytes = capsules[0].read_bytes()
            self.assertLessEqual(len(raw_bytes), 16384)
            capsule_doc = json.loads(raw_bytes.decode("utf-8"))
            self.assertEqual(validate_json_schema(capsule_doc, schema), [])
            self.assertGreaterEqual(capsule_doc["ctx"]["operational_surface_count"], 5)
            self.assertIn("dependency_summary", capsule_doc["ctx"])

    def test_jev_governance_gold_eval_wire_hygiene_and_cli_triage(self) -> None:
        # Public package hygiene: zero hardcoded private 1Password `op://` refs in ledger/jev.py
        self.assertNotIn("op://", inspect.getsource(jev))

        # Offline-first default when LEDGER_JEV is unset
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LEDGER_JEV", None)
            self.assertEqual(jev.effective_jev_mode(), "off")

        # Validate all 3 heads (both disk JSON files and BUILTIN_HEADS)
        self.assertEqual(
            set(jev.list_head_ids()),
            {"estate_attention_urgency", "project_lifecycle_inference", "weak_overlap_relation"},
        )
        for h in jev.list_heads():
            self.assertEqual(jev.validate_head(h, expected_id=h["id"]), [])

        # Permanent ADVISORY ceiling enforced on authority heads
        bad_auth = copy.deepcopy(jev.BUILTIN_HEADS["weak_overlap_relation"])
        bad_auth["ceiling"] = "ROUTING"
        errs = jev.validate_head(bad_auth)
        self.assertTrue(any("permanent ceiling ADVISORY" in e for e in errs))

        # 15-case gold corpus passes 100% on deterministic `code` fallback
        self.assertEqual(len(jev.GOLD_EVAL_CASES), 15)
        eval_report = jev.evaluate_heads(provider="code")
        self.assertEqual(eval_report["total"], 15)
        self.assertEqual(eval_report["accuracy"], 1.0)
        self.assertEqual(eval_report["abstained"], 0)
        self.assertEqual(eval_report["live_model_calls"], 0)

        # Mocked `/v1/systemone` batching + HTTP 500 error body hygiene
        captured: list[tuple[str, dict[str, str], dict[str, object]]] = []

        class _FakeResp:
            def __init__(self, doc: dict[str, object]) -> None:
                self._raw = json.dumps(doc).encode("utf-8")

            def read(self) -> bytes:
                return self._raw

            def __enter__(self) -> "_FakeResp":
                return self

            def __exit__(self, *args: object) -> bool:
                return False

        def _ok_urlopen(req, timeout=15.0):
            headers = {k.lower(): v for k, v in req.header_items()}
            body = json.loads(req.data.decode("utf-8"))
            captured.append((req.full_url, headers, body))
            return _FakeResp(
                {
                    "model": "jev-1.13.0",
                    "answers": {
                        "project_lifecycle_inference": {
                            "choice": "maintained_stable",
                            "probabilities": {
                                "active_development": 0.05,
                                "maintained_stable": 0.85,
                                "dormant_archive": 0.08,
                                "ephemeral_experiment": 0.02,
                            },
                            "confidence": 0.85,
                        },
                        "estate_attention_urgency": {
                            "score": 1.05,
                            "probabilities": {"0": 0.10, "1": 0.78, "2": 0.10, "3": 0.02},
                        },
                    },
                    "usage": {"input_tokens": 140},
                }
            )

        sample_state = {
            "source_classes": ["live"],
            "days_since_last_commit": 45.0,
            "dirty_worktree": False,
            "ahead_count": 0,
            "has_machina_handoff": False,
            "total_files": 40,
            "subsystem_count": 4,
            "operational_surface_count": 6,
            "has_governance_docs": True,
            "open_review_count": 1,
            "git_drift_codes": [],
            "dirty_checkout_count": 0,
            "ahead_total": 0,
            "behind_total": 0,
            "diverged": False,
            "high_severity_review_count": 0,
            "source_health_state": "healthy",
        }

        with (
            patch.dict(os.environ, {"LEDGER_JEV": "auto", "TYPESAFE_API_KEY": "ts-unit-key"}),
            patch("urllib.request.urlopen", side_effect=_ok_urlopen),
        ):
            dec = jev.decide(
                ["project_lifecycle_inference", "estate_attention_urgency"],
                sample_state,
                provider="jev",
                skip_pre_rule=True,
                record_trace=False,
            )
        self.assertEqual(len(captured), 1)
        self.assertEqual(
            set(captured[0][2]["questions"].keys()),
            {"project_lifecycle_inference", "estate_attention_urgency"},
        )
        self.assertIsInstance(captured[0][2]["state"], dict)
        self.assertIn("criteria", captured[0][2]["questions"]["project_lifecycle_inference"])
        self.assertNotIn("options", captured[0][2]["questions"]["project_lifecycle_inference"])
        by_h = {d["head_id"]: d for d in dec["decisions"]}
        self.assertEqual(by_h["project_lifecycle_inference"]["effective_output"], "maintained_stable")
        self.assertEqual(by_h["estate_attention_urgency"]["effective_output"], 1)

        def _err_urlopen(req, timeout=15.0):
            raise urllib.error.HTTPError(
                req.full_url,
                502,
                "Bad Gateway",
                hdrs=None,
                fp=io.BytesIO(b"UPSTREAM_SECRET_TRACE_DO_NOT_LEAK"),
            )

        with (
            patch.dict(os.environ, {"LEDGER_JEV": "auto", "TYPESAFE_API_KEY": "ts-unit-key"}),
            patch("urllib.request.urlopen", side_effect=_err_urlopen),
        ):
            dec_err = jev.decide(
                ["project_lifecycle_inference"],
                sample_state,
                provider="jev",
                skip_pre_rule=True,
                record_trace=False,
            )
        self.assertEqual(dec_err["decisions"][0]["abstain_reason"], "JEV_UNAVAILABLE: HTTP 502")
        self.assertNotIn("UPSTREAM_SECRET_TRACE_DO_NOT_LEAK", json.dumps(dec_err))
        self.assertEqual(dec_err["decisions"][0]["effective_output"], "maintained_stable")
        self.assertTrue(dec_err["decisions"][0]["fallback_used"])

        # CLI `ledger jev` + `ledger review --jev` end-to-end without mutating registry
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            src_root = ws / "sources"
            r1 = src_root / "ledger-core"
            r2 = src_root / "ledger-companion"
            _init_git_repo(r1, "https://github.com/example/ledger-core.git")
            _init_git_repo(r2, "https://github.com/example/ledger-companion.git")
            (r1 / "README.md").write_text("# ledger-core\nCore reality compiler substrate.\n", encoding="utf-8")
            (r2 / "README.md").write_text("# ledger-companion\nCompanion UI for ledger-core.\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(r1), "add", "."], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(r1), "commit", "-m", "init"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(r2), "add", "."], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(r2), "commit", "-m", "init"], check=True, capture_output=True)

            cfg_path = ws / "ledger_config.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "roots": [
                            {
                                "source_id": "local-live",
                                "label": "local-live",
                                "path": str(src_root),
                                "discovery": "children",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            state_dir = ws / "state"
            out_dir = ws / "output"
            decisions_path = ws / "registry" / "identity-decisions.json"
            decisions_path.parent.mkdir(parents=True, exist_ok=True)
            decisions_path.write_text('{"schema_version": "1.0.0", "decisions": []}\n', encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()):
                rc_refresh = cli.main(
                    [
                        "refresh",
                        "--no-markdown-mirror",
                        "--config",
                        str(cfg_path),
                        "--output-dir",
                        str(out_dir),
                        "--state-dir",
                        str(state_dir),
                        "--identity-decisions",
                        str(decisions_path),
                        "--json",
                    ]
                )
            self.assertEqual(rc_refresh, 0)

            before_decisions_bytes = decisions_path.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()):
                rc_rev = cli.main(
                    [
                        "review",
                        "--state-dir",
                        str(state_dir),
                        "--identity-decisions",
                        str(decisions_path),
                        "--jev",
                        "--json",
                    ]
                )
            self.assertEqual(rc_rev, 0)
            self.assertEqual(decisions_path.read_bytes(), before_decisions_bytes)

            with contextlib.redirect_stdout(io.StringIO()):
                rc_eval = cli.main(["jev", "eval", "--provider", "code", "--json"])
                rc_proj = cli.main(
                    ["jev", "project", "github.com/example/ledger-core", "--state-dir", str(state_dir), "--json"]
                )
            self.assertEqual(rc_eval, 0)
            self.assertEqual(rc_proj, 0)

    def test_head_id_path_traversal_and_deep_tarjan_scc(self) -> None:
        from ledger import structural_ctx

        for bad_id in ("../../../outside", "../weak_overlap_relation", "sub/head", "head.json", ""):
            with self.assertRaises(ValueError):
                jev.load_head(bad_id)

        deep_subsystems = [
            {
                "path": f"sub_{i:04d}",
                "internal_deps": [f"sub_{(i + 1) % 1600:04d}"],
            }
            for i in range(1600)
        ]
        summary = structural_ctx.compute_subsystem_dependency_summary(deep_subsystems)
        self.assertEqual(summary, {"internal_edge_count": 1600, "cycle_count": 1})

        # Symlink confinement + NaN/out-of-range confidence abstention
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            outside = base / "outside"
            outside.mkdir()
            sym_heads = base / "sym_heads"
            sym_heads.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                jev.load_head("project_lifecycle_inference", heads_dir=sym_heads)
            sym_trace = base / "sym_trace"
            sym_trace.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                jev.append_decision_traces(sym_trace, [{"head_id": "test"}])

        sample_state = {
            "source_classes": ["live"],
            "days_since_last_commit": 45.0,
            "dirty_worktree": False,
            "ahead_count": 0,
            "has_machina_handoff": False,
            "total_files": 40,
            "subsystem_count": 4,
            "operational_surface_count": 6,
            "has_governance_docs": True,
            "open_review_count": 1,
        }
        for bad_conf in (float("nan"), float("inf"), 2.0, -0.2):
            raw = json.dumps(
                {
                    "model": "jev-1.13.0",
                    "answers": {
                        "project_lifecycle_inference": {
                            "choice": "maintained_stable",
                            "confidence": bad_conf,
                        }
                    },
                    "usage": {"input_tokens": 10},
                },
                allow_nan=True,
            ).encode("utf-8")

            class _BadResp:
                def __init__(self, b: bytes) -> None:
                    self._b = b

                def read(self) -> bytes:
                    return self._b

                def __enter__(self) -> "_BadResp":
                    return self

                def __exit__(self, *args: object) -> bool:
                    return False

            with (
                patch.dict(os.environ, {"LEDGER_JEV": "auto", "TYPESAFE_API_KEY": "ts-unit-key"}),
                patch("urllib.request.urlopen", side_effect=lambda req, timeout=15.0, b=raw: _BadResp(b)),
            ):
                bad_dec = jev.decide(
                    ["project_lifecycle_inference"],
                    sample_state,
                    provider="jev",
                    skip_pre_rule=True,
                    record_trace=False,
                )["decisions"][0]
            self.assertTrue(bad_dec["abstained"])
            self.assertTrue(bad_dec["fallback_used"])

    def test_qa_hardening_mcp_malformed_state_redirect_refusal_authority_and_upstream_fuzz(self) -> None:
        import http.server
        import threading
        from ledger.mcp_server import LedgerMcpServer

        # 1. Permanent authority head (`weak_overlap_relation`) cannot disable `authority: true`
        spoofed_auth = copy.deepcopy(jev.BUILTIN_HEADS["weak_overlap_relation"])
        spoofed_auth["authority"] = False
        spoof_errs = jev.validate_head(spoofed_auth, expected_id="weak_overlap_relation")
        self.assertTrue(any("Permanent authority head" in e for e in spoof_errs))

        # 2. Malformed state types via MCP `ledger_jev decide` and CLI `ledger jev decide`
        #    MUST return structured error (`isError=True` / exit 2) and never crash `serve_stdio`.
        with tempfile.TemporaryDirectory() as tmp:
            base_tmp = Path(tmp)
            srv = LedgerMcpServer(
                state_dir=base_tmp / "state",
                identity_decisions_path=base_tmp / "identity-decisions.json",
                project_lineage_path=base_tmp / "project-lineage.json",
                candidates_path=base_tmp / "candidates.json",
            )
            bad_states = [
                {"total_files": [1]},  # missing keys + wrong type
                {
                    "source_classes": ["live"],
                    "days_since_last_commit": 5.0,
                    "dirty_worktree": False,
                    "ahead_count": 0,
                    "has_machina_handoff": False,
                    "total_files": [1],
                    "subsystem_count": 1,
                    "operational_surface_count": 0,
                    "has_governance_docs": False,
                    "open_review_count": 0,
                },
                {
                    "git_drift_codes": 5,
                    "dirty_checkout_count": 0,
                    "ahead_total": 0,
                    "behind_total": 0,
                    "diverged": False,
                    "open_review_count": 0,
                    "high_severity_review_count": 0,
                    "source_health_state": "healthy",
                },
            ]
            heads_for_bad = [
                "project_lifecycle_inference",
                "project_lifecycle_inference",
                "estate_attention_urgency",
            ]
            for hid, st in zip(heads_for_bad, bad_states):
                frame = srv.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 99,
                        "method": "tools/call",
                        "params": {
                            "name": "ledger_jev",
                            "arguments": {
                                "action": "decide",
                                "head_ids": [hid],
                                "state": st,
                                "provider": "code",
                            },
                        },
                    }
                )
                self.assertIsNotNone(frame)
                assert frame is not None
                self.assertTrue(frame["result"]["isError"])

                err_buf = io.StringIO()
                with contextlib.redirect_stderr(err_buf):
                    rc_bad = cli.main(
                        ["jev", "decide", "--head", hid, "--state", json.dumps(st), "--provider", "code"]
                    )
                self.assertEqual(rc_bad, 2)
                self.assertIn("LEDGER_ERROR:", err_buf.getvalue())

        # 3. Upstream non-object JSON `[]`, bad `usage.input_tokens`, and out-of-range `score: 9`
        #    MUST abstain cleanly into deterministic `code` fallback.
        urg_state = {
            "git_drift_codes": ["GIT_DIRTY_WORKTREE"],
            "dirty_checkout_count": 0,
            "ahead_total": 0,
            "behind_total": 0,
            "diverged": False,
            "open_review_count": 0,
            "high_severity_review_count": 0,
            "source_health_state": "healthy",
        }
        # Also verifies real Project Ledger drift code `GIT_DIRTY_WORKTREE` scores urgency 2 even with dirty_checkout_count=0
        self.assertEqual(
            jev.decide(["estate_attention_urgency"], urg_state, provider="code")["decisions"][0]["effective_output"],
            2,
        )

        class _RawResp:
            def __init__(self, raw: bytes) -> None:
                self._raw = raw

            def read(self) -> bytes:
                return self._raw

            def __enter__(self) -> "_RawResp":
                return self

            def __exit__(self, *args: object) -> bool:
                return False

        for bad_wire in (
            b"[]",
            json.dumps(
                {
                    "answers": {"estate_attention_urgency": {"score": 9, "confidence": 0.9}},
                    "usage": {"input_tokens": "bad-token-count"},
                }
            ).encode("utf-8"),
            json.dumps(
                {
                    "answers": {
                        "estate_attention_urgency": {
                            "probabilities": {"0": 0.05, "9": 0.95},
                        }
                    },
                    "usage": {"input_tokens": [123]},
                }
            ).encode("utf-8"),
        ):
            with (
                patch.dict(os.environ, {"LEDGER_JEV": "auto", "TYPESAFE_API_KEY": "ts-unit-key"}),
                patch("urllib.request.urlopen", side_effect=lambda req, timeout=15.0, b=bad_wire: _RawResp(b)),
            ):
                res_bad = jev.decide(
                    ["estate_attention_urgency"],
                    urg_state,
                    provider="jev",
                    skip_pre_rule=True,
                    record_trace=False,
                )["decisions"][0]
            self.assertTrue(res_bad["abstained"])
            self.assertTrue(res_bad["fallback_used"])
            self.assertEqual(res_bad["effective_output"], 2)

        # 4. `ledger jev eval --provider jev` without `--live` under `LEDGER_JEV=off` stays strictly offline
        with patch.dict(os.environ, {"LEDGER_JEV": "off", "TYPESAFE_API_KEY": "ts-should-not-call"}):
            out_buf = io.StringIO()
            with (
                patch("urllib.request.urlopen", side_effect=AssertionError("Network called while LEDGER_JEV=off!")),
                contextlib.redirect_stdout(out_buf),
            ):
                rc_offline_eval = cli.main(["jev", "eval", "--provider", "jev", "--skip-pre-rule", "--json"])
            self.assertEqual(rc_offline_eval, 2)
            eval_doc = json.loads(out_buf.getvalue())
            self.assertEqual(eval_doc["live_model_calls"], 0)
            self.assertEqual(eval_doc["abstained"], 15)

        # 5. HTTP 302 redirect MUST refuse to forward `Authorization: Bearer` to redirect target
        leaked_headers: list[str] = []

        class _TargetHandler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                auth = self.headers.get("Authorization", "")
                if auth:
                    leaked_headers.append(auth)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def do_GET(self) -> None:  # noqa: N802
                self.do_POST()

            def log_message(self, *args: object) -> None:
                pass

        target_srv = http.server.HTTPServer(("127.0.0.1", 0), _TargetHandler)
        target_port = target_srv.server_address[1]

        class _RedirectHandler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{target_port}/steal")
                self.end_headers()

            def log_message(self, *args: object) -> None:
                pass

        redir_srv = http.server.HTTPServer(("127.0.0.1", 0), _RedirectHandler)
        redir_port = redir_srv.server_address[1]

        t1 = threading.Thread(target=target_srv.serve_forever, daemon=True)
        t2 = threading.Thread(target=redir_srv.serve_forever, daemon=True)
        t1.start()
        t2.start()
        try:
            with patch.dict(
                os.environ,
                {
                    "LEDGER_JEV": "auto",
                    "TYPESAFE_API_KEY": "ts-super-secret-bearer",
                    "LEDGER_JEV_ENDPOINT": f"http://127.0.0.1:{redir_port}/v1/systemone",
                },
            ):
                redir_dec = jev.decide(
                    ["estate_attention_urgency"],
                    urg_state,
                    provider="jev",
                    skip_pre_rule=True,
                    record_trace=False,
                )["decisions"][0]
            self.assertTrue(redir_dec["abstained"])
            self.assertEqual(redir_dec["abstain_reason"], "JEV_UNAVAILABLE: HTTP 302")
            self.assertEqual(leaked_headers, [])
        finally:
            redir_srv.shutdown()
            target_srv.shutdown()
            redir_srv.server_close()
            target_srv.server_close()

    def test_round3_qa_oversize_int_overflow_serve_stdio_triage_source_class_and_law5_unknown_files(self) -> None:
        from ledger.mcp_server import LedgerMcpServer
        from ledger.triage import review_payload

        huge_int = 10**400

        # 1. MCP `serve_stdio`: 400-digit integer in `ledger_jev decide` state must return
        #    a structured error (`isError=True`), never raise `OverflowError`, and continue
        #    serving subsequent JSON-RPC frames on the same stdio session.
        with tempfile.TemporaryDirectory() as tmp:
            base_tmp = Path(tmp)
            srv = LedgerMcpServer(
                state_dir=base_tmp / "state",
                identity_decisions_path=base_tmp / "identity-decisions.json",
                project_lineage_path=base_tmp / "project-lineage.json",
                candidates_path=base_tmp / "candidates.json",
            )
            req_overflow = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "ledger_jev",
                    "arguments": {
                        "action": "decide",
                        "head_ids": ["estate_attention_urgency"],
                        "state": {
                            "git_drift_codes": [],
                            "dirty_checkout_count": huge_int,
                            "ahead_total": 0,
                            "behind_total": 0,
                            "diverged": False,
                            "open_review_count": 0,
                            "high_severity_review_count": 0,
                            "source_health_state": "healthy",
                        },
                    },
                },
            }
            req_list = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
            in_buf = io.BytesIO(
                (json.dumps(req_overflow) + "\n" + json.dumps(req_list) + "\n").encode("utf-8")
            )
            out_buf = io.BytesIO()
            rc = srv.serve_stdio(in_stream=in_buf, out_stream=out_buf)
            self.assertEqual(rc, 0)
            lines = [ln for ln in out_buf.getvalue().decode("utf-8").splitlines() if ln.strip()]
            self.assertEqual(len(lines), 2)
            resp1 = json.loads(lines[0])
            resp2 = json.loads(lines[1])
            self.assertEqual(resp1["id"], 1)
            self.assertTrue(resp1["result"]["isError"])
            self.assertEqual(resp2["id"], 2)
            self.assertGreater(len(resp2["result"]["tools"]), 0)

        # 2. CLI `ledger jev decide` with 400-digit integer returns exit code 2 without crashing,
        #    and upstream Jev wire response with 400-digit score/confidence/input_tokens abstains cleanly.
        cli_state_json = json.dumps(
            {
                "git_drift_codes": [],
                "dirty_checkout_count": huge_int,
                "ahead_total": 0,
                "behind_total": 0,
                "diverged": False,
                "open_review_count": 0,
                "high_severity_review_count": 0,
                "source_health_state": "healthy",
            }
        )
        err_capture = io.StringIO()
        with contextlib.redirect_stderr(err_capture):
            cli_rc = cli.main(
                ["jev", "decide", "--head", "estate_attention_urgency", "--state", cli_state_json]
            )
        self.assertEqual(cli_rc, 2)

        valid_urg_state = {
            "git_drift_codes": [],
            "dirty_checkout_count": 0,
            "ahead_total": 0,
            "behind_total": 0,
            "diverged": False,
            "open_review_count": 0,
            "high_severity_review_count": 0,
            "source_health_state": "healthy",
        }
        huge_wire = json.dumps(
            {
                "model": "jev-1.13.0",
                "answers": {
                    "estate_attention_urgency": {
                        "score": huge_int,
                        "confidence": huge_int,
                    }
                },
                "usage": {"input_tokens": huge_int},
            }
        ).encode("utf-8")

        class _HugeWireResp:
            def read(self) -> bytes:
                return huge_wire

            def __enter__(self) -> "_HugeWireResp":
                return self

            def __exit__(self, *args: object) -> bool:
                return False

        with (
            patch.dict(os.environ, {"LEDGER_JEV": "auto", "TYPESAFE_API_KEY": "ts-unit-key"}),
            patch("urllib.request.urlopen", side_effect=lambda req, timeout=15.0: _HugeWireResp()),
        ):
            huge_res = jev.decide(
                ["estate_attention_urgency"],
                valid_urg_state,
                provider="jev",
                skip_pre_rule=True,
                record_trace=False,
            )
        self.assertTrue(huge_res["decisions"][0]["abstained"])
        self.assertTrue(huge_res["decisions"][0]["fallback_used"])
        self.assertEqual(huge_res["decisions"][0]["input_tokens"], 0)

        # 3. `review_payload(..., include_jev=True)` resolves `source_class` from `system-manifest.json`
        #    `sources[]` when `observations.json` entries carry only `source_id`.
        with tempfile.TemporaryDirectory() as tmp:
            st_dir = Path(tmp) / "state"
            st_dir.mkdir(parents=True, exist_ok=True)
            (st_dir / "review-queue.json").write_text(
                json.dumps({"run_id": "r1", "compiled_at": "2026-11-01T00:00:00Z", "review_count": 0, "items": []}),
                encoding="utf-8",
            )
            (st_dir / "system-manifest.json").write_text(
                json.dumps(
                    {
                        "sources": [
                            {"source_id": "central-active", "source_class": "live"},
                            {"source_id": "macbook-air-2-homelab", "source_class": "mirror"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (st_dir / "observations.json").write_text(
                json.dumps(
                    {
                        "observations": [
                            {
                                "observation_id": "obs-live",
                                "source_id": "central-active",
                                "location": "/central/repos/active/foo",
                                "project_key": "proj-a",
                                "compat_entry": {"name": "foo", "description": "Homelab foo service"},
                            },
                            {
                                "observation_id": "obs-mirror",
                                "source_id": "macbook-air-2-homelab",
                                "location": "/Users/mschwar/repos/foo",
                                "project_key": "proj-b",
                                "compat_entry": {"name": "foo", "description": "Homelab foo service"},
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (st_dir / "identity-evidence.json").write_text(
                json.dumps(
                    {
                        "cross_project_weak_overlaps": [
                            {
                                "left_observation_id": "obs-live",
                                "right_observation_id": "obs-mirror",
                                "verdict": "weak_overlap",
                                "shared_weak": ["name:foo"],
                                "evidence": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            rev = review_payload(
                st_dir,
                candidates_path=Path(tmp) / "candidates.json",
                decisions_path=Path(tmp) / "decisions.json",
                include_jev=True,
                jev_provider="code",
            )
            ov = rev["untriaged_weak_overlaps"][0]
            self.assertEqual(ov["left"]["source_class"], "live")
            self.assertEqual(ov["right"]["source_class"], "mirror")
            self.assertEqual(ov["jev_advisory"]["effective_output"], "redundant_checkout_or_mirror")

        # 4. Law 5 explicit uncertainty: inventory-only capsule with `ctx.state != "available"`
        #    and `ctx.total_files = None` keeps `total_files = None` and classifies as `dormant_archive`
        #    rather than `ephemeral_experiment`.
        import datetime as dt

        inv_capsule = {
            "observations": [{"source_class": "inventory"}],
            "freshness": {"content_as_of": "2025-01-01T00:00:00Z"},
            "git_facts": {"dirty": False, "ahead_count": 0},
            "current_state": {"machina_continuity": {"state": "absent"}},
            "ctx": {
                "state": "absent",
                "total_files": None,
                "subsystem_count": 0,
                "operational_surface_count": 0,
                "authority_boundaries": [],
            },
            "review": {"open_review_count": 0},
        }
        lc_state = jev.capsule_to_lifecycle_state(
            inv_capsule,
            reference_now=dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc),
        )
        self.assertIsNone(lc_state["total_files"])
        lc_dec = jev.decide(["project_lifecycle_inference"], lc_state, provider="code", record_trace=False)[
            "decisions"
        ][0]
        self.assertEqual(lc_dec["effective_output"], "dormant_archive")


if __name__ == "__main__":
    unittest.main()
