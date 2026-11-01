"""Tests for Strategic Unlock U5: stdlib read-only MCP stdio server and global CLI path fallback."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ledger import cli
from ledger.config import PACKAGE_REPO_ROOT, default_workspace_root
from ledger.mcp_server import (
    JSONRPC_INVALID_PARAMS,
    JSONRPC_INVALID_REQUEST,
    JSONRPC_METHOD_NOT_FOUND,
    JSONRPC_PARSE_ERROR,
    LedgerMcpServer,
)


def _init_git_repo(repo_dir: Path, remote_url: str) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(repo_dir), "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "remote", "add", "origin", remote_url], check=True)
    (repo_dir / "README.md").write_text("# Alpha\n\nMCP test service.\n", encoding="utf-8")
    (repo_dir / "app.py").write_text("def serve() -> int:\n    return 0\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_dir), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "commit", "-q", "-m", "init"], check=True)


def _dir_digest(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


class StdlibMcpServerAndGlobalCliTests(unittest.TestCase):
    def _build_workspace(self, base: Path) -> dict[str, Path]:
        ws = base / "ledger-ws"
        repos = base / "repos"
        alpha = repos / "alpha"
        _init_git_repo(alpha, "https://github.com/example/alpha.git")

        (ws / "registry").mkdir(parents=True)
        config_path = ws / "ledger_config.json"
        config_path.write_text(
            json.dumps(
                {
                    "roots": [
                        {
                            "source_id": "central-active",
                            "label": "central-active",
                            "path": str(repos),
                            "discovery": "children",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        decisions_path = ws / "registry" / "identity-decisions.json"
        decisions_path.write_text(
            json.dumps({"schema_version": "1.0.0", "decisions": []}),
            encoding="utf-8",
        )
        candidates_path = ws / "registry" / "weak-overlap-triage-candidates.json"
        candidates_path.write_text(
            json.dumps({"schema_version": "1.0.0", "candidates": []}),
            encoding="utf-8",
        )
        lineage_path = ws / "registry" / "project-lineage.json"
        state_dir = ws / "state"
        output_dir = ws / "output"

        with contextlib.redirect_stdout(io.StringIO()):
            rc = cli.main(
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
            )
            self.assertEqual(rc, 0)
            rc_sync = cli.main(
                [
                    "lineage",
                    "--state-dir",
                    str(state_dir),
                    "--project-lineage",
                    str(lineage_path),
                    "--sync",
                ]
            )
            self.assertEqual(rc_sync, 0)

        return {
            "ws": ws,
            "alpha": alpha,
            "state_dir": state_dir,
            "decisions_path": decisions_path,
            "candidates_path": candidates_path,
            "lineage_path": lineage_path,
        }

    def test_mcp_tools_resources_errors_and_strict_read_only_invariance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._build_workspace(Path(tmp))
            server = LedgerMcpServer(
                state_dir=paths["state_dir"],
                identity_decisions_path=paths["decisions_path"],
                project_lineage_path=paths["lineage_path"],
                candidates_path=paths["candidates_path"],
            )

            before_ws = _dir_digest(paths["ws"])
            before_alpha = _dir_digest(paths["alpha"])

            # 1. initialize negotiation & notification silence
            init_resp = server.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test"}},
                }
            )
            assert isinstance(init_resp, dict)
            self.assertEqual(init_resp["result"]["protocolVersion"], "2025-03-26")
            self.assertEqual(init_resp["result"]["serverInfo"]["name"], "project-ledger")

            # Fallback protocol version when unknown future version is requested
            init_fallback = server.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "initialize",
                    "params": {"protocolVersion": "1999-01-01"},
                }
            )
            assert isinstance(init_fallback, dict)
            self.assertEqual(init_fallback["result"]["protocolVersion"], "2024-11-05")

            # Notification emits None
            self.assertIsNone(
                server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"})
            )

            # ping
            ping_resp = server.handle_message({"jsonrpc": "2.0", "id": 3, "method": "ping"})
            assert isinstance(ping_resp, dict)
            self.assertEqual(ping_resp["result"], {})

            # 2. tools/list: all 9 read-only tools present, zero mutating tools
            tools_resp = server.handle_message({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
            assert isinstance(tools_resp, dict)
            tools = tools_resp["result"]["tools"]
            tool_names = [t["name"] for t in tools]
            self.assertEqual(
                tool_names,
                [
                    "ledger_orient",
                    "ledger_sources",
                    "ledger_resolve",
                    "ledger_show",
                    "ledger_locate",
                    "ledger_explain",
                    "ledger_changes",
                    "ledger_review",
                    "ledger_lineage",
                    "ledger_jev",
                ],
            )
            for t in tools:
                self.assertTrue(t["annotations"]["readOnlyHint"])
                self.assertFalse(t["annotations"]["destructiveHint"])

            # 3. Call all 9 read-only tools
            def _call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
                resp = server.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": f"call-{name}",
                        "method": "tools/call",
                        "params": {"name": name, "arguments": arguments or {}},
                    }
                )
                assert isinstance(resp, dict)
                self.assertIn("result", resp, f"Expected result for {name}, got {resp}")
                return resp["result"]

            orient_res = _call_tool("ledger_orient")
            self.assertFalse(orient_res["isError"])
            self.assertEqual(orient_res["structuredContent"]["counts"]["canonical_projects"], 1)

            sources_res = _call_tool("ledger_sources")
            self.assertFalse(sources_res["isError"])
            self.assertEqual(sources_res["structuredContent"]["source_count"], 1)

            resolve_res = _call_tool("ledger_resolve", {"referent": "github.com/example/alpha"})
            self.assertFalse(resolve_res["isError"])
            pid = resolve_res["structuredContent"]["canonical_project_id"]
            self.assertTrue(pid.startswith("prj_"))

            show_res = _call_tool("ledger_show", {"referent": pid})
            self.assertFalse(show_res["isError"])
            self.assertEqual(show_res["structuredContent"]["ctx"]["state"], "available")
            self.assertEqual(show_res["structuredContent"]["ctx"]["source_format"], "compiled_structural_scan")

            locate_res = _call_tool("ledger_locate", {"referent": "alpha"})
            self.assertFalse(locate_res["isError"])
            self.assertEqual(
                locate_res["structuredContent"]["preferred_location"]["location"],
                str(paths["alpha"].resolve()),
            )

            explain_res = _call_tool("ledger_explain", {"referent": pid})
            self.assertFalse(explain_res["isError"])
            self.assertEqual(explain_res["structuredContent"]["subject_kind"], "canonical_project")

            changes_res = _call_tool("ledger_changes", {"entity_kind": "canonical_project"})
            self.assertFalse(changes_res["isError"])
            self.assertEqual(changes_res["structuredContent"]["status"], "ok")

            review_res = _call_tool("ledger_review")
            self.assertFalse(review_res["isError"])
            self.assertEqual(review_res["structuredContent"]["review_count"], 0)

            lineage_res = _call_tool("ledger_lineage")
            self.assertFalse(lineage_res["isError"])
            self.assertTrue(lineage_res["structuredContent"]["in_sync"])

            jev_heads_res = _call_tool("ledger_jev", {"action": "heads"})
            self.assertFalse(jev_heads_res["isError"])
            self.assertEqual(jev_heads_res["structuredContent"]["count"], 3)

            jev_proj_res = _call_tool("ledger_jev", {"action": "project", "referent": pid})
            self.assertFalse(jev_proj_res["isError"])
            self.assertEqual(
                jev_proj_res["structuredContent"]["jev_advisory"]["inference_layer"],
                "jev_system_one_advisory",
            )

            jev_eval_res = _call_tool("ledger_jev", {"action": "eval", "provider": "code"})
            self.assertFalse(jev_eval_res["isError"])
            self.assertEqual(jev_eval_res["structuredContent"]["accuracy"], 1.0)

            # Unresolved referent returns isError=True with structured status payload
            missing_res = _call_tool("ledger_resolve", {"referent": "nonexistent-project-xyz"})
            self.assertTrue(missing_res["isError"])
            self.assertEqual(missing_res["structuredContent"]["status"], "unresolved")

            # Mutating flag injection (e.g. approve_candidates or sync) is rejected with -32602
            mut_attempt = server.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 99,
                    "method": "tools/call",
                    "params": {"name": "ledger_lineage", "arguments": {"sync": True}},
                }
            )
            assert isinstance(mut_attempt, dict)
            self.assertEqual(mut_attempt["error"]["code"], JSONRPC_INVALID_PARAMS)

            # 4. resources/list, resources/templates/list, and resources/read
            res_list = server.handle_message({"jsonrpc": "2.0", "id": 10, "method": "resources/list"})
            assert isinstance(res_list, dict)
            uris = [r["uri"] for r in res_list["result"]["resources"]]
            self.assertIn("ledger://manifest", uris)
            self.assertIn("ledger://lineage", uris)

            tmpl_list = server.handle_message(
                {"jsonrpc": "2.0", "id": 11, "method": "resources/templates/list"}
            )
            assert isinstance(tmpl_list, dict)
            self.assertEqual(
                tmpl_list["result"]["resourceTemplates"][0]["uriTemplate"],
                "ledger://projects/{referent}",
            )

            for uri in uris:
                read_resp = server.handle_message(
                    {"jsonrpc": "2.0", "id": f"r-{uri}", "method": "resources/read", "params": {"uri": uri}}
                )
                assert isinstance(read_resp, dict)
                self.assertTrue(read_resp["result"]["contents"][0]["text"])

            encoded_key = quote("github.com/example/alpha", safe="")
            proj_res = server.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 12,
                    "method": "resources/read",
                    "params": {"uri": f"ledger://projects/{encoded_key}"},
                }
            )
            assert isinstance(proj_res, dict)
            proj_doc = json.loads(proj_res["result"]["contents"][0]["text"])
            self.assertEqual(proj_doc["canonical_project_id"], pid)

            # 5. JSON-RPC error codes
            err_method = server.handle_message({"jsonrpc": "2.0", "id": 20, "method": "unknown/method"})
            assert isinstance(err_method, dict)
            self.assertEqual(err_method["error"]["code"], JSONRPC_METHOD_NOT_FOUND)

            err_req = server.handle_message({"jsonrpc": "1.0", "id": 21, "method": "ping"})
            assert isinstance(err_req, dict)
            self.assertEqual(err_req["error"]["code"], JSONRPC_INVALID_REQUEST)

            err_uri = server.handle_message(
                {"jsonrpc": "2.0", "id": 22, "method": "resources/read", "params": {"uri": "ledger://nope"}}
            )
            assert isinstance(err_uri, dict)
            self.assertEqual(err_uri["error"]["code"], JSONRPC_INVALID_PARAMS)

            # 6. Strict read-only proof: zero byte changes in workspace or target repo
            self.assertEqual(_dir_digest(paths["ws"]), before_ws)
            self.assertEqual(_dir_digest(paths["alpha"]), before_alpha)

    def test_stdio_framing_supports_newline_delimited_and_content_length(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._build_workspace(Path(tmp))
            server = LedgerMcpServer(
                state_dir=paths["state_dir"],
                identity_decisions_path=paths["decisions_path"],
                project_lineage_path=paths["lineage_path"],
                candidates_path=paths["candidates_path"],
            )

            # Newline-delimited stream (including invalid JSON line -> -32700 + valid ping)
            nl_in = io.BytesIO(
                b"{broken json\n"
                + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode("utf-8")
                + b"\n"
                + json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}).encode("utf-8")
                + b"\n"
            )
            nl_out = io.BytesIO()
            self.assertEqual(server.serve_stdio(nl_in, nl_out), 0)
            lines = [ln for ln in nl_out.getvalue().splitlines() if ln.strip()]
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])["error"]["code"], JSONRPC_PARSE_ERROR)
            self.assertEqual(json.loads(lines[1])["id"], 7)

            # Content-Length framed stream
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 42,
                    "method": "tools/call",
                    "params": {"name": "ledger_orient", "arguments": {}},
                }
            ).encode("utf-8")
            cl_in = io.BytesIO(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
            cl_out = io.BytesIO()
            self.assertEqual(server.serve_stdio(cl_in, cl_out), 0)
            raw_reply = cl_out.getvalue()
            self.assertTrue(raw_reply.startswith(b"Content-Length: "))
            hdr, payload = raw_reply.split(b"\r\n\r\n", 1)
            declared_len = int(hdr.split(b":", 1)[1].strip())
            self.assertEqual(declared_len, len(payload))
            parsed_reply = json.loads(payload.decode("utf-8"))
            self.assertEqual(parsed_reply["id"], 42)
            self.assertEqual(
                parsed_reply["result"]["structuredContent"]["counts"]["canonical_projects"],
                1,
            )

    def test_external_cwd_workspace_fallback_and_subprocess_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            paths = self._build_workspace(base)

            # An unrelated external repo that even has its own unrelated `state/app.db` directory!
            unrelated_cwd = base / "some-other-app"
            (unrelated_cwd / "state").mkdir(parents=True)
            (unrelated_cwd / "state" / "app.db").write_text("unrelated", encoding="utf-8")

            # Without PROJECT_LEDGER_HOME, default_workspace_root falls back to PACKAGE_REPO_ROOT, not unrelated_cwd!
            old_env = os.environ.pop("PROJECT_LEDGER_HOME", None)
            try:
                self.assertEqual(default_workspace_root(unrelated_cwd), PACKAGE_REPO_ROOT)
                self.assertEqual(default_workspace_root(paths["ws"]), paths["ws"].resolve())
            finally:
                if old_env is not None:
                    os.environ["PROJECT_LEDGER_HOME"] = old_env

            # With PROJECT_LEDGER_HOME pointing at paths["ws"], running `python -m ledger show alpha --json`
            # and `python -m ledger mcp` from `unrelated_cwd` resolves seamlessly!
            env = dict(os.environ)
            env["PROJECT_LEDGER_HOME"] = str(paths["ws"])
            env["PYTHONPATH"] = str(PACKAGE_REPO_ROOT)

            cli_proc = subprocess.run(
                [sys.executable, "-m", "ledger", "show", "alpha", "--json"],
                cwd=unrelated_cwd,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            cli_payload = json.loads(cli_proc.stdout)
            self.assertEqual(cli_payload["status"], "resolved")
            self.assertEqual(cli_payload["project_key"], "github.com/example/alpha")

            mcp_input = (
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05"},
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": "ledger_resolve", "arguments": {"referent": "alpha"}},
                    }
                )
                + "\n"
            )
            mcp_proc = subprocess.run(
                [sys.executable, "-m", "ledger", "mcp"],
                cwd=unrelated_cwd,
                env=env,
                input=mcp_input,
                check=True,
                capture_output=True,
                text=True,
            )
            out_lines = [json.loads(line) for line in mcp_proc.stdout.splitlines() if line.strip()]
            self.assertEqual(len(out_lines), 2)
            self.assertEqual(out_lines[0]["result"]["serverInfo"]["name"], "project-ledger")
            self.assertEqual(
                out_lines[1]["result"]["structuredContent"]["project_key"],
                "github.com/example/alpha",
            )


if __name__ == "__main__":
    unittest.main()
