"""Tests for Waves 7 & 8: Estate portfolio triage index, cross-repo ecosystem blast
radius, passive INV-FLEET-GAME-01 fleet overlay, and stdlib Streamable HTTP MCP server.
"""

from __future__ import annotations

import contextlib
import http.client
import io
import json
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

from ledger import cli
from ledger.fleet import read_fleet_pulse_overlay
from ledger.mcp_server import (
    JSONRPC_INVALID_PARAMS,
    JSONRPC_INVALID_REQUEST,
    JSONRPC_PARSE_ERROR,
    MAX_HTTP_BODY_BYTES,
    LedgerMcpServer,
)
from tests.test_state_schemas import SCHEMAS_DIR, validate_json_schema


def _init_git_repo(repo_dir: Path, remote_url: str) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(repo_dir), "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "remote", "add", "origin", remote_url], check=True)


class Wave7And8PortfolioEcosystemHttpMcpTests(unittest.TestCase):
    def _build_multi_repo_workspace(self, base: Path) -> dict[str, Any]:
        ws = base / "ledger-ws"
        repos = base / "central" / "repos" / "active"

        # 1. core-substrate: has internal subsystem import cycle + distinctive package & CLI script
        core = repos / "core-substrate"
        _init_git_repo(core, "git@github.com:mschwar/core-substrate.git")
        (core / "README.md").write_text("# Core Substrate\n\nShared compiler kernel.\n", encoding="utf-8")
        (core / "AGENTS.md").write_text("# AGENTS\n\nCore governance.\n", encoding="utf-8")
        (core / "pyproject.toml").write_text(
            '[project]\nname = "core-substrate"\nversion = "0.1.0"\n'
            '[project.scripts]\ncore-substrate-cli = "alpha_mod.mod:run"\n',
            encoding="utf-8",
        )
        (core / "alpha_mod").mkdir()
        (core / "beta_mod").mkdir()
        (core / "alpha_mod" / "__init__.py").write_text("import beta_mod\n", encoding="utf-8")
        (core / "alpha_mod" / "mod.py").write_text("import beta_mod\ndef run():\n    return 0\n", encoding="utf-8")
        (core / "beta_mod" / "__init__.py").write_text("import alpha_mod\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(core), "add", "."], check=True)
        subprocess.run(["git", "-C", str(core), "commit", "-q", "-m", "init core"], check=True)

        # 2. generic-ledger: declares ambiguous single-word package name "ledger" (must NOT false-merge)
        generic = repos / "generic-ledger"
        _init_git_repo(generic, "git@github.com:mschwar/generic-ledger.git")
        (generic / "README.md").write_text("# Generic Ledger\n", encoding="utf-8")
        (generic / "pyproject.toml").write_text(
            '[project]\nname = "ledger"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )
        subprocess.run(["git", "-C", str(generic), "add", "."], check=True)
        subprocess.run(["git", "-C", str(generic), "commit", "-q", "-m", "init generic"], check=True)

        # 3. orchestrator-hub: depends on core-substrate via pyproject, gitmodules, github ref, and CLI binding;
        #    also lists bare "ledger" in dependencies (must NOT create a false edge to generic-ledger!).
        hub = repos / "orchestrator-hub"
        _init_git_repo(hub, "git@github.com:mschwar/orchestrator-hub.git")
        (hub / "README.md").write_text("# Orchestrator Hub\n\nControl plane.\n", encoding="utf-8")
        (hub / "pyproject.toml").write_text(
            '[project]\nname = "orchestrator-hub"\nversion = "0.1.0"\n'
            'dependencies = ["core-substrate>=0.1.0", "ledger>=0.1.0"]\n',
            encoding="utf-8",
        )
        (hub / ".gitmodules").write_text(
            '[submodule "vendor/core"]\n\tpath = vendor/core\n\turl = git@github.com:mschwar/core-substrate.git\n',
            encoding="utf-8",
        )
        (hub / "ops").mkdir()
        (hub / "ops" / "watchdog.sh").write_text(
            "#!/usr/bin/env bash\n"
            "# Sync https://github.com/mschwar/core-substrate\n"
            "core-substrate-cli --check\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "-C", str(hub), "add", "."], check=True)
        subprocess.run(["git", "-C", str(hub), "commit", "-q", "-m", "init hub"], check=True)
        # Leave dirty uncommitted edit in hub so attention_urgency >= 2
        (hub / "README.md").write_text("# Orchestrator Hub (dirty WIP)\n", encoding="utf-8")

        (ws / "registry").mkdir(parents=True)
        config_path = ws / "ledger_config.json"
        config_path.write_text(
            json.dumps(
                {
                    "defaults": {"require_git": True},
                    "roots": [
                        {
                            "source_id": "central-active",
                            "label": "central-active",
                            "path": str(repos),
                            "discovery": "children",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        decisions_path = ws / "registry" / "identity-decisions.json"
        decisions_path.write_text(json.dumps({"schema_version": "1.0.0", "decisions": []}), encoding="utf-8")
        candidates_path = ws / "registry" / "weak-overlap-triage-candidates.json"
        candidates_path.write_text(json.dumps({"schema_version": "1.0.0", "candidates": []}), encoding="utf-8")
        lineage_path = ws / "registry" / "project-lineage.json"
        state_dir = ws / "state"
        output_dir = ws / "output"

        pulse_path = base / "fleet-pulse.json"
        pulse_path.write_text(
            json.dumps(
                {
                    "generated_at_iso": "2026-11-14T10:00:00Z",
                    "nodes": {
                        "iz": {
                            "os_family": "windows",
                            "online": True,
                            "dispatchable": True,
                            "occupancy_state": "IDLE_FREE",
                            "gaming_active": False,
                            "active_leases": 0,
                            "gpu_inference_online": True,
                            "free_ram_gib": 25.4,
                            "free_disk_gib": 1412.0,
                        },
                        "matty-pc": {
                            "os_family": "windows",
                            "online": True,
                            "dispatchable": True,
                            "occupancy_state": "GAMING",
                            "gaming_active": True,
                            "active_leases": 0,
                            "gpu_inference_online": False,
                            "free_ram_gib": 14.0,
                            "free_disk_gib": 500.0,
                        },
                    },
                }
            ),
            encoding="utf-8",
        )

        old_pulse = os.environ.get("PROJECT_LEDGER_FLEET_PULSE")
        os.environ["PROJECT_LEDGER_FLEET_PULSE"] = str(pulse_path)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    cli.main(
                        [
                            "refresh",
                            "--config",
                            str(config_path),
                            "--output-dir",
                            str(output_dir),
                            "--state-dir",
                            str(state_dir),
                            "--identity-decisions",
                            str(decisions_path),
                            "--no-markdown-mirror",
                        ]
                    ),
                    0,
                )
                self.assertEqual(
                    cli.main(
                        [
                            "lineage",
                            "--state-dir",
                            str(state_dir),
                            "--project-lineage",
                            str(lineage_path),
                            "--sync",
                        ]
                    ),
                    0,
                )
        finally:
            if old_pulse is None:
                os.environ.pop("PROJECT_LEDGER_FLEET_PULSE", None)
            else:
                os.environ["PROJECT_LEDGER_FLEET_PULSE"] = old_pulse

        return {
            "ws": ws,
            "state_dir": state_dir,
            "decisions_path": decisions_path,
            "candidates_path": candidates_path,
            "lineage_path": lineage_path,
            "pulse_path": pulse_path,
        }

    def test_ecosystem_graph_portfolio_filters_and_ambiguous_word_guardrail(self) -> None:
        portfolio_schema = json.loads((SCHEMAS_DIR / "portfolio-index.schema.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._build_multi_repo_workspace(Path(tmp))
            state_dir = paths["state_dir"]

            portfolio_doc = json.loads((state_dir / "portfolio-index.json").read_text(encoding="utf-8"))
            self.assertEqual(validate_json_schema(portfolio_doc, portfolio_schema), [])
            self.assertEqual(portfolio_doc["summary"]["project_count"], 3)
            self.assertEqual(portfolio_doc["summary"]["dirty_count"], 1)
            self.assertEqual(portfolio_doc["summary"]["cycle_project_count"], 1)
            self.assertGreaterEqual(portfolio_doc["summary"]["ecosystem_edge_count"], 4)

            # Verify exact edge kinds from orchestrator-hub -> core-substrate AND zero false edges to generic-ledger
            edges = portfolio_doc["ecosystem_edges"]
            rel_kinds = {
                e["relation_kind"]
                for e in edges
                if e["source_project_key"] == "github.com/mschwar/orchestrator-hub"
                and e["target_project_key"] == "github.com/mschwar/core-substrate"
            }
            self.assertEqual(
                rel_kinds,
                {"git_submodule", "package_dependency", "github_repo_reference", "cli_or_mcp_binding"},
            )
            false_edges = [
                e for e in edges if e["target_project_key"] == "github.com/mschwar/generic-ledger"
            ]
            self.assertEqual(false_edges, [])

            # CLI filters: --depends-on core-substrate (reverse blast radius -> orchestrator-hub)
            old_pulse = os.environ.get("PROJECT_LEDGER_FLEET_PULSE")
            os.environ["PROJECT_LEDGER_FLEET_PULSE"] = str(paths["pulse_path"])
            try:
                buf_rdeps = io.StringIO()
                with contextlib.redirect_stdout(buf_rdeps):
                    rc = cli.main(
                        [
                            "portfolio",
                            "--state-dir",
                            str(state_dir),
                            "--depends-on",
                            "mschwar/core-substrate",
                            "--json",
                        ]
                    )
                self.assertEqual(rc, 0)
                rdeps_payload = json.loads(buf_rdeps.getvalue())
                self.assertEqual(rdeps_payload["matched_project_count"], 1)
                self.assertEqual(
                    rdeps_payload["projects"][0]["project_key"],
                    "github.com/mschwar/orchestrator-hub",
                )
                # Gaming guard enforced in fleet overlay even when raw JSON had dispatchable=True
                self.assertEqual(rdeps_payload["fleet"]["dispatchable_nodes"], ["iz"])
                self.assertEqual(rdeps_payload["fleet"]["gaming_nodes"], ["matty-pc"])

                # CLI filter: --depended-on-by orchestrator-hub (-> core-substrate)
                buf_deps = io.StringIO()
                with contextlib.redirect_stdout(buf_deps):
                    rc_deps = cli.main(
                        [
                            "portfolio",
                            "--state-dir",
                            str(state_dir),
                            "--depended-on-by",
                            "orchestrator-hub",
                            "--json",
                        ]
                    )
                self.assertEqual(rc_deps, 0)
                deps_payload = json.loads(buf_deps.getvalue())
                self.assertEqual(deps_payload["matched_project_count"], 1)
                self.assertEqual(
                    deps_payload["projects"][0]["project_key"],
                    "github.com/mschwar/core-substrate",
                )

                # CLI filter: --cycles (-> core-substrate) and --dirty (-> orchestrator-hub)
                buf_cyc = io.StringIO()
                with contextlib.redirect_stdout(buf_cyc):
                    self.assertEqual(
                        cli.main(["portfolio", "--state-dir", str(state_dir), "--cycles", "--json"]),
                        0,
                    )
                self.assertEqual(
                    [p["project_key"] for p in json.loads(buf_cyc.getvalue())["projects"]],
                    ["github.com/mschwar/core-substrate"],
                )

                # show & explain enrichments include ecosystem slice without inflating capsule bytes
                buf_show = io.StringIO()
                with contextlib.redirect_stdout(buf_show):
                    self.assertEqual(
                        cli.main(["show", "core-substrate", "--state-dir", str(state_dir), "--json"]),
                        0,
                    )
                show_doc = json.loads(buf_show.getvalue())
                self.assertEqual(show_doc["ecosystem"]["depended_on_by_count"], 1)
                self.assertEqual(
                    show_doc["ecosystem"]["depended_on_by"][0]["project_key"],
                    "github.com/mschwar/orchestrator-hub",
                )

                buf_exp = io.StringIO()
                with contextlib.redirect_stdout(buf_exp):
                    self.assertEqual(
                        cli.main(
                            [
                                "explain",
                                "core-substrate",
                                "--state-dir",
                                str(state_dir),
                                "--identity-decisions",
                                str(paths["decisions_path"]),
                                "--json",
                            ]
                        ),
                        0,
                    )
                exp_doc = json.loads(buf_exp.getvalue())
                self.assertEqual(exp_doc["ecosystem"]["depended_on_by_count"], 1)
                self.assertIn("lifecycle_posture", exp_doc["posture"])
            finally:
                if old_pulse is None:
                    os.environ.pop("PROJECT_LEDGER_FLEET_PULSE", None)
                else:
                    os.environ["PROJECT_LEDGER_FLEET_PULSE"] = old_pulse

    def test_fleet_pulse_overlay_handles_missing_invalid_stale_and_human_heavy_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = read_fleet_pulse_overlay(pulse_path=root / "absent.json")
            self.assertEqual(missing["state"], "unavailable")
            self.assertEqual(missing["reason_code"], "PULSE_FILE_ABSENT")

            bad = root / "bad.json"
            bad.write_text("{not valid json", encoding="utf-8")
            inv = read_fleet_pulse_overlay(pulse_path=bad)
            self.assertEqual(inv["state"], "unavailable")
            self.assertEqual(inv["reason_code"], "PULSE_FILE_INVALID")

            stale = root / "stale.json"
            stale.write_text(
                json.dumps(
                    {
                        "generated_at_iso": "2026-11-14T08:00:00Z",
                        "nodes": {
                            "workstation": {
                                "os_family": "windows",
                                "online": True,
                                "dispatchable": True,
                                "occupancy_state": "HUMAN_HEAVY",
                                "gaming_active": False,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            stale_overlay = read_fleet_pulse_overlay(
                pulse_path=stale,
                reference_iso="2026-11-14T10:00:00Z",
            )
            self.assertEqual(stale_overlay["state"], "stale")
            self.assertEqual(stale_overlay["reason_code"], "PULSE_STALE")
            # HUMAN_HEAVY is strictly non-dispatchable under INV-FLEET-GAME-01
            self.assertEqual(stale_overlay["dispatchable_nodes"], [])

    def test_streamable_http_mcp_server_health_jsonrpc_notifications_batches_and_limits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._build_multi_repo_workspace(Path(tmp))
            old_pulse = os.environ.get("PROJECT_LEDGER_FLEET_PULSE")
            os.environ["PROJECT_LEDGER_FLEET_PULSE"] = str(paths["pulse_path"])
            server = LedgerMcpServer(
                state_dir=paths["state_dir"],
                identity_decisions_path=paths["decisions_path"],
                project_lineage_path=paths["lineage_path"],
                candidates_path=paths["candidates_path"],
            )
            httpd = server.create_http_server(host="127.0.0.1", port=0)
            port = int(httpd.server_address[1])
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                def _req(
                    method: str,
                    path: str,
                    body: bytes | None = None,
                    headers: dict[str, str] | None = None,
                ) -> tuple[int, dict[str, str], bytes]:
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5.0)
                    try:
                        conn.request(method, path, body=body, headers=headers or {})
                        resp = conn.getresponse()
                        raw = resp.read()
                        hdrs = {k.lower(): v for k, v in resp.getheaders()}
                        return resp.status, hdrs, raw
                    finally:
                        conn.close()

                # 1. GET /health -> 200 OK
                st, _, raw_h = _req("GET", "/health")
                self.assertEqual(st, 200)
                h_doc = json.loads(raw_h.decode("utf-8"))
                self.assertTrue(h_doc["ok"])
                self.assertEqual(h_doc["schema_version"], "1.3.0")
                self.assertEqual(h_doc["tool_count"], 10)
                self.assertEqual(h_doc["canonical_projects"], 3)

                # 2. GET /mcp -> 405 Method Not Allowed (stateless Streamable HTTP contract)
                st_get, hdr_get, _ = _req("GET", "/mcp")
                self.assertEqual(st_get, 405)
                self.assertIn("POST", hdr_get.get("allow", ""))

                # 3. DELETE /mcp -> 204 No Content
                st_del, _, raw_del = _req("DELETE", "/mcp")
                self.assertEqual(st_del, 204)
                self.assertEqual(raw_del, b"")

                # 4. POST /mcp notification -> 202 Accepted with empty body
                notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode("utf-8")
                st_not, _, raw_not = _req(
                    "POST",
                    "/mcp",
                    body=notif,
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(st_not, 202)
                self.assertEqual(raw_not, b"")

                # 5. POST /mcp tools/call ledger_portfolio -> 200 OK
                call_body = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": "p-1",
                        "method": "tools/call",
                        "params": {
                            "name": "ledger_portfolio",
                            "arguments": {"depends_on": "core-substrate", "urgent_only": True},
                        },
                    }
                ).encode("utf-8")
                st_call, _, raw_call = _req(
                    "POST",
                    "/mcp",
                    body=call_body,
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(st_call, 200)
                call_doc = json.loads(raw_call.decode("utf-8"))
                sc = call_doc["result"]["structuredContent"]
                self.assertEqual(sc["matched_project_count"], 1)
                self.assertEqual(sc["projects"][0]["project_key"], "github.com/mschwar/orchestrator-hub")
                self.assertEqual(sc["fleet"]["gaming_nodes"], ["matty-pc"])

                # 6. POST /mcp JSON-RPC batch + resources/read ledger://portfolio
                batch_body = json.dumps(
                    [
                        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                        {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "resources/read",
                            "params": {"uri": "ledger://portfolio"},
                        },
                    ]
                ).encode("utf-8")
                st_batch, _, raw_batch = _req(
                    "POST",
                    "/mcp",
                    body=batch_body,
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(st_batch, 200)
                batch_doc = json.loads(raw_batch.decode("utf-8"))
                self.assertEqual(len(batch_doc), 2)
                res_portfolio = json.loads(batch_doc[1]["result"]["contents"][0]["text"])
                self.assertEqual(res_portfolio["summary"]["project_count"], 3)

                # 7. Invalid JSON -> 400 JSONRPC_PARSE_ERROR
                st_bad, _, raw_bad = _req(
                    "POST",
                    "/mcp",
                    body=b"{not-json",
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(st_bad, 400)
                self.assertEqual(json.loads(raw_bad.decode("utf-8"))["error"]["code"], JSONRPC_PARSE_ERROR)

                # 8. Invalid param type -> JSONRPC_INVALID_PARAMS
                bad_param = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 9,
                        "method": "tools/call",
                        "params": {"name": "ledger_portfolio", "arguments": {"limit": 0}},
                    }
                ).encode("utf-8")
                st_bp, _, raw_bp = _req(
                    "POST",
                    "/mcp",
                    body=bad_param,
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(st_bp, 200)
                self.assertEqual(json.loads(raw_bp.decode("utf-8"))["error"]["code"], JSONRPC_INVALID_PARAMS)

                # 9. Oversized Content-Length header -> 413 Payload Too Large without reading >1MiB
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5.0)
                try:
                    conn.putrequest("POST", "/mcp")
                    conn.putheader("Content-Type", "application/json")
                    conn.putheader("Content-Length", str(MAX_HTTP_BODY_BYTES + 64))
                    conn.endheaders()
                    resp_big = conn.getresponse()
                    raw_big = resp_big.read()
                    self.assertEqual(resp_big.status, 413)
                    self.assertEqual(
                        json.loads(raw_big.decode("utf-8"))["error"]["code"],
                        JSONRPC_INVALID_REQUEST,
                    )
                finally:
                    conn.close()
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=2.0)
                if old_pulse is None:
                    os.environ.pop("PROJECT_LEDGER_FLEET_PULSE", None)
                else:
                    os.environ["PROJECT_LEDGER_FLEET_PULSE"] = old_pulse


if __name__ == "__main__":
    unittest.main()
