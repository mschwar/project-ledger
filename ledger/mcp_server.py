"""Stdlib read-only Model Context Protocol (MCP) server over stdio (`U5`).

Exposes Project Ledger's compiled Reality Provider read plane (`orient`, `sources`,
`resolve`, `show`, `locate`, `explain`, `changes`, `review`, `lineage`) and
materialized `ledger://` resources/templates over JSON-RPC 2.0 stdio with zero
third-party dependencies.

Transport compatibility:
  - Auto-detects both newline-delimited JSON-RPC 2.0 (MCP `2024-11-05` /
    `2025-03-26` / `2025-06-18` stdio spec) and `Content-Length:` header framing,
    replying in the exact framing sent by the client.
  - Strictly read-only: never invokes `refresh`, `compile`, `--approve-candidates`,
    `--sync`, or `sync-gbrain --ingest`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import unquote

from . import COMPILER_VERSION
from .capsules import locate_payload, show_payload
from .changes import SUPPORTED_CHANGE_ENTITY_KINDS, changes_payload
from .compiler import load_manifest, orient_payload
from .config import LedgerConfigError, default_workspace_path
from .explain import explain_payload
from .identity import resolve_payload
from .jev import (
    decide as jev_decide,
    evaluate_heads as jev_evaluate_heads,
    list_heads as jev_list_heads,
    project_advisory_for_referent,
)
from .lineage import lineage_status_payload
from .triage import review_payload

SUPPORTED_MCP_PROTOCOL_VERSIONS = (
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)
DEFAULT_MCP_PROTOCOL_VERSION = "2024-11-05"

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603

_READ_ONLY_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "ledger_orient",
        "description": (
            "Read Project Ledger's compact system orientation surface: compiled run_id, "
            "freshness timestamps, source/observation/canonical-project counts, estate health, "
            "checkpoint summary, change summary, and active capabilities."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
        "annotations": _READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "ledger_sources",
        "description": (
            "List registered Project Ledger sources with health status, source class "
            "(live/mirror/backup/inventory), path availability, and incremental checkpoint status."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
        "annotations": _READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "ledger_resolve",
        "description": (
            "Resolve any project referent (canonical_project_id, historical_canonical_id, "
            "project_key, alias, remote URL, or filesystem path) to canonical identity, "
            "preferred working checkout path, GBrain cross-link, and git drift warnings."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["referent"],
            "properties": {
                "referent": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Project ID, key, alias, normalized/raw git URL, or checkout path.",
                }
            },
        },
        "annotations": _READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "ledger_show",
        "description": (
            "Load the bounded (<= 16 KiB) canonical project capsule for a project referent, "
            "including structural ctx architecture/entrypoints/subsystems, git_facts, "
            "Machina continuity, preferred_location, GBrain page key, warnings, and ranked observations."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["referent"],
            "properties": {
                "referent": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Project ID, key, alias, git remote URL, or checkout path.",
                }
            },
        },
        "annotations": _READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "ledger_locate",
        "description": (
            "Resolve the preferred working checkout and full ranked manifestation list "
            "(live > mirror > backup > inventory) with reason codes, confidence, and git drift warnings."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["referent"],
            "properties": {
                "referent": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Project ID, key, alias, git remote URL, or checkout path.",
                }
            },
        },
        "annotations": _READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "ledger_explain",
        "description": (
            "Explain provenance for a canonical project, observation (obs_*), review item (rev_*), "
            "identity decision (dec_*), or semantic change event (chg_*) from compiled evidence."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["referent"],
            "properties": {
                "referent": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Canonical project referent, obs_*, rev_*, dec_*, or chg_* ID.",
                }
            },
        },
        "annotations": _READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "ledger_changes",
        "description": (
            "Inspect zero-churn semantic estate changes since the previous compiled run or a "
            "historical run_id baseline, optionally filtered by project, entity_kind, or change_type."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "since_run_id": {
                    "type": "string",
                    "description": "Optional retained historical run_id baseline to diff against.",
                },
                "project_referent": {
                    "type": "string",
                    "description": "Optional canonical or historical project referent filter.",
                },
                "entity_kind": {
                    "type": "string",
                    "enum": sorted(SUPPORTED_CHANGE_ENTITY_KINDS),
                    "description": "Optional entity_kind filter.",
                },
                "change_type": {
                    "type": "string",
                    "description": "Optional change_type code filter (e.g. PROJECT_GIT_DRIFT_CHANGED).",
                },
            },
        },
        "annotations": _READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "ledger_review",
        "description": (
            "Inspect open identity review items, untriaged cross-project weak overlaps, "
            "and pending/approved candidate triage decisions (strictly read-only)."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
        "annotations": _READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "ledger_lineage",
        "description": (
            "Inspect and verify the durable immutable canonical_project_id lineage registry "
            "(in_sync status, pinned count, and any unregistered or absorbed project IDs)."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
        "annotations": _READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "ledger_jev",
        "description": (
            "Read-only governed Type 1 Jev (`jev-1.13.0`) System-One advisory decision surface: "
            "list validated head contracts (`heads`), infer advisory lifecycle posture & attention "
            "urgency for a canonical project (`project`), classify untriaged weak overlaps (`triage`), "
            "execute governed head(s) on explicit state (`decide`), or run the 15-case gold eval (`eval`)."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["action"],
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["heads", "project", "triage", "decide", "eval"],
                    "description": "Read-only Jev operation to execute.",
                },
                "referent": {
                    "type": "string",
                    "description": "Project referent when action == 'project'.",
                },
                "head_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Head ID(s) for action == 'decide' (or single head filter for 'eval').",
                },
                "state": {
                    "type": "object",
                    "description": "State JSON object when action == 'decide'.",
                },
                "provider": {
                    "type": "string",
                    "enum": ["code", "jev"],
                    "description": "Optional provider override ('code' offline deterministic or 'jev').",
                },
                "skip_pre_rule": {
                    "type": "boolean",
                    "description": "Bypass deterministic pre-rules during action == 'eval'.",
                },
            },
        },
        "annotations": _READ_ONLY_ANNOTATIONS,
    },
)

_STATIC_RESOURCES: tuple[dict[str, str], ...] = (
    {
        "uri": "ledger://manifest",
        "name": "Project Ledger System Manifest",
        "description": "Compiled estate orientation manifest (state/system-manifest.json).",
        "mimeType": "application/json",
    },
    {
        "uri": "ledger://canonical-projects",
        "name": "Canonical Projects Registry Projection",
        "description": "Compiled canonical projects collection (state/canonical-projects.json).",
        "mimeType": "application/json",
    },
    {
        "uri": "ledger://canonical-projects-markdown",
        "name": "Human Canonical Projects View",
        "description": "Human-auditable canonical ledger markdown view (state/canonical-projects.md).",
        "mimeType": "text/markdown",
    },
    {
        "uri": "ledger://changes",
        "name": "Semantic Estate Change Feed",
        "description": "Zero-churn semantic change feed across runs (state/changes.json).",
        "mimeType": "application/json",
    },
    {
        "uri": "ledger://review-queue",
        "name": "Identity Review & Triage Queue",
        "description": "Open identity review items and weak-overlap triage status.",
        "mimeType": "application/json",
    },
    {
        "uri": "ledger://lineage",
        "name": "Immutable Canonical Project ID Lineage Status",
        "description": "Read-only sync status of registry/project-lineage.json against compiled state.",
        "mimeType": "application/json",
    },
)

_RESOURCE_TEMPLATES: tuple[dict[str, str], ...] = (
    {
        "uriTemplate": "ledger://projects/{referent}",
        "name": "Canonical Project Capsule",
        "description": (
            "Bounded <= 16 KiB project capsule resolved by canonical_project_id, project_key, "
            "alias, or URL-encoded referent."
        ),
        "mimeType": "application/json",
    },
)


class _JsonRpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _jsonrpc_result(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(req_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def _tool_result(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, indent=2, sort_keys=True),
            }
        ],
        "structuredContent": payload,
        "isError": is_error,
    }


def _require_nonempty_str(arguments: dict[str, Any], key: str) -> str:
    val = arguments.get(key)
    if not isinstance(val, str) or not val.strip():
        raise _JsonRpcError(
            JSONRPC_INVALID_PARAMS,
            f"Argument {key!r} must be a non-empty string.",
        )
    return val.strip()


def _optional_str(arguments: dict[str, Any], key: str) -> str | None:
    val = arguments.get(key)
    if val is None:
        return None
    if not isinstance(val, str):
        raise _JsonRpcError(
            JSONRPC_INVALID_PARAMS,
            f"Argument {key!r} must be a string when provided.",
        )
    stripped = val.strip()
    return stripped or None


class LedgerMcpServer:
    """Read-only MCP JSON-RPC 2.0 server bound to compiled Project Ledger artifacts."""

    def __init__(
        self,
        *,
        state_dir: Path,
        identity_decisions_path: Path,
        project_lineage_path: Path,
        candidates_path: Path,
    ) -> None:
        self.state_dir = state_dir.resolve()
        self.identity_decisions_path = identity_decisions_path.resolve()
        self.project_lineage_path = project_lineage_path.resolve()
        self.candidates_path = candidates_path.resolve()

    def handle_message(self, message: Any) -> dict[str, Any] | list[dict[str, Any]] | None:
        if isinstance(message, list):
            if not message:
                return _jsonrpc_error(None, JSONRPC_INVALID_REQUEST, "Empty JSON-RPC batch.")
            responses: list[dict[str, Any]] = []
            for item in message:
                resp = self._handle_single(item)
                if resp is not None:
                    responses.append(resp)
            return responses or None
        return self._handle_single(message)

    def _handle_single(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict):
            return _jsonrpc_error(None, JSONRPC_INVALID_REQUEST, "JSON-RPC message must be an object.")
        if message.get("jsonrpc") != "2.0":
            return _jsonrpc_error(
                message.get("id"),
                JSONRPC_INVALID_REQUEST,
                "jsonrpc version must be '2.0'.",
            )
        method = message.get("method")
        has_id = "id" in message
        req_id = message.get("id")
        if not isinstance(method, str) or not method:
            return _jsonrpc_error(
                req_id if has_id else None,
                JSONRPC_INVALID_REQUEST,
                "JSON-RPC method must be a non-empty string.",
            )
        raw_params = message.get("params")
        if raw_params is None:
            params: dict[str, Any] = {}
        elif isinstance(raw_params, dict):
            params = raw_params
        else:
            if not has_id:
                return None
            return _jsonrpc_error(req_id, JSONRPC_INVALID_PARAMS, "params must be an object.")

        # Notifications (no `id` member) never emit a response frame.
        if not has_id:
            return None

        try:
            result = self._dispatch(method, params)
            return _jsonrpc_result(req_id, result)
        except _JsonRpcError as exc:
            return _jsonrpc_error(req_id, exc.code, exc.message, exc.data)
        except (LedgerConfigError, OSError, ValueError) as exc:
            return _jsonrpc_error(req_id, JSONRPC_INTERNAL_ERROR, str(exc))

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            requested = params.get("protocolVersion")
            negotiated = (
                requested
                if isinstance(requested, str) and requested in SUPPORTED_MCP_PROTOCOL_VERSIONS
                else DEFAULT_MCP_PROTOCOL_VERSION
            )
            return {
                "protocolVersion": negotiated,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                },
                "serverInfo": {
                    "name": "project-ledger",
                    "version": COMPILER_VERSION,
                },
                "instructions": (
                    "Read-only Reality Provider MCP surface for Project Ledger. "
                    "Use ledger_orient for estate health/counts, ledger_resolve or ledger_locate "
                    "for authoritative checkout paths and git drift warnings, ledger_show for "
                    "bounded <= 16 KiB project capsules (including structural ctx and Machina "
                    "continuity), ledger_explain for provenance, and ledger_changes for semantic deltas."
                ),
            }

        if method == "ping":
            return {}

        if method == "tools/list":
            return {"tools": list(_TOOLS)}

        if method == "tools/call":
            return self._call_tool(params)

        if method == "resources/list":
            return {"resources": list(_STATIC_RESOURCES)}

        if method == "resources/templates/list":
            return {"resourceTemplates": list(_RESOURCE_TEMPLATES)}

        if method == "resources/read":
            return self._read_resource(params)

        raise _JsonRpcError(JSONRPC_METHOD_NOT_FOUND, f"Method not found: {method}")

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise _JsonRpcError(JSONRPC_INVALID_PARAMS, "tools/call requires a non-empty tool 'name'.")
        raw_args = params.get("arguments")
        if raw_args is None:
            arguments: dict[str, Any] = {}
        elif isinstance(raw_args, dict):
            arguments = raw_args
        else:
            raise _JsonRpcError(JSONRPC_INVALID_PARAMS, "tools/call 'arguments' must be an object.")

        allowed_by_tool: dict[str, set[str]] = {
            t["name"]: set(t["inputSchema"].get("properties", {}).keys()) for t in _TOOLS
        }
        if name not in allowed_by_tool:
            raise _JsonRpcError(JSONRPC_INVALID_PARAMS, f"Unknown tool: {name!r}")
        unknown_keys = sorted(set(arguments.keys()) - allowed_by_tool[name])
        if unknown_keys:
            raise _JsonRpcError(
                JSONRPC_INVALID_PARAMS,
                f"Unexpected argument(s) for {name}: {', '.join(unknown_keys)}",
            )

        try:
            if name == "ledger_orient":
                manifest = load_manifest(self.state_dir)
                return _tool_result(orient_payload(manifest))

            if name == "ledger_sources":
                manifest = load_manifest(self.state_dir)
                sources = manifest.get("sources", [])
                return _tool_result({"source_count": len(sources), "sources": sources})

            if name == "ledger_resolve":
                referent = _require_nonempty_str(arguments, "referent")
                payload = resolve_payload(self.state_dir, referent)
                return _tool_result(payload, is_error=(payload.get("status") != "resolved"))

            if name == "ledger_show":
                referent = _require_nonempty_str(arguments, "referent")
                payload = show_payload(self.state_dir, referent)
                return _tool_result(payload, is_error=(payload.get("status") != "resolved"))

            if name == "ledger_locate":
                referent = _require_nonempty_str(arguments, "referent")
                payload = locate_payload(self.state_dir, referent)
                return _tool_result(payload, is_error=(payload.get("status") != "resolved"))

            if name == "ledger_explain":
                referent = _require_nonempty_str(arguments, "referent")
                payload = explain_payload(
                    self.state_dir,
                    referent,
                    decisions_path=self.identity_decisions_path,
                )
                return _tool_result(payload, is_error=(payload.get("status") != "resolved"))

            if name == "ledger_changes":
                since_run_id = _optional_str(arguments, "since_run_id")
                project_referent = _optional_str(arguments, "project_referent")
                entity_kind = _optional_str(arguments, "entity_kind")
                if entity_kind is not None and entity_kind not in SUPPORTED_CHANGE_ENTITY_KINDS:
                    raise _JsonRpcError(
                        JSONRPC_INVALID_PARAMS,
                        f"Unsupported entity_kind {entity_kind!r}; expected one of "
                        f"{sorted(SUPPORTED_CHANGE_ENTITY_KINDS)}.",
                    )
                change_type = _optional_str(arguments, "change_type")
                payload = changes_payload(
                    self.state_dir,
                    since_run_id=since_run_id,
                    project_referent=project_referent,
                    entity_kind=entity_kind,
                    change_type=change_type,
                )
                status = payload.get("status", "ok")
                return _tool_result(payload, is_error=(status != "ok"))

            if name == "ledger_review":
                payload = review_payload(
                    self.state_dir,
                    candidates_path=self.candidates_path,
                    decisions_path=self.identity_decisions_path,
                )
                return _tool_result(payload)

            if name == "ledger_lineage":
                payload = lineage_status_payload(
                    state_dir=self.state_dir,
                    lineage_path=self.project_lineage_path,
                    sync=False,
                )
                return _tool_result(payload)

            if name == "ledger_jev":
                action = _require_nonempty_str(arguments, "action")
                if action not in {"heads", "project", "triage", "decide", "eval"}:
                    raise _JsonRpcError(
                        JSONRPC_INVALID_PARAMS,
                        f"Unsupported ledger_jev action {action!r}.",
                    )
                prov = _optional_str(arguments, "provider")
                if prov is not None and prov not in {"code", "jev"}:
                    raise _JsonRpcError(
                        JSONRPC_INVALID_PARAMS,
                        f"Unsupported provider {prov!r}; expected 'code' or 'jev'.",
                    )
                if action == "heads":
                    heads = jev_list_heads()
                    return _tool_result({"count": len(heads), "heads": heads})
                if action == "project":
                    referent = _require_nonempty_str(arguments, "referent")
                    payload = project_advisory_for_referent(
                        self.state_dir,
                        referent,
                        provider=prov,
                        force_live=False,
                        allow_vault=False,
                        record_trace=False,
                    )
                    return _tool_result(payload, is_error=(payload.get("status") != "resolved"))
                if action == "triage":
                    payload = review_payload(
                        self.state_dir,
                        candidates_path=self.candidates_path,
                        decisions_path=self.identity_decisions_path,
                        include_jev=True,
                        jev_provider=prov,
                        jev_force_live=False,
                        jev_allow_vault=False,
                    )
                    return _tool_result(payload)
                if action == "decide":
                    raw_hids = arguments.get("head_ids")
                    if not isinstance(raw_hids, list) or not raw_hids or not all(
                        isinstance(h, str) and h.strip() for h in raw_hids
                    ):
                        raise _JsonRpcError(
                            JSONRPC_INVALID_PARAMS,
                            "ledger_jev action 'decide' requires non-empty string array 'head_ids'.",
                        )
                    raw_state = arguments.get("state")
                    if not isinstance(raw_state, dict):
                        raise _JsonRpcError(
                            JSONRPC_INVALID_PARAMS,
                            "ledger_jev action 'decide' requires object 'state'.",
                        )
                    payload = jev_decide(
                        [h.strip() for h in raw_hids],
                        raw_state,
                        provider=prov,
                        allow_vault=False,
                        force_live=False,
                        record_trace=False,
                    )
                    return _tool_result(payload)
                raw_hids = arguments.get("head_ids")
                h_filter = (
                    raw_hids[0].strip()
                    if isinstance(raw_hids, list) and len(raw_hids) == 1 and isinstance(raw_hids[0], str)
                    else None
                )
                skip_pre = bool(arguments.get("skip_pre_rule", False))
                payload = jev_evaluate_heads(
                    provider=prov or "code",
                    head_filter=h_filter,
                    allow_vault=False,
                    force_live=False,
                    skip_pre_rule=skip_pre,
                )
                return _tool_result(payload)
        except (LedgerConfigError, OSError, ValueError) as exc:
            err_payload = {
                "status": "error",
                "code": getattr(exc, "code", "LEDGER_STATE_UNAVAILABLE"),
                "detail": str(exc),
                "state_dir": str(self.state_dir),
            }
            return _tool_result(err_payload, is_error=True)

        raise _JsonRpcError(JSONRPC_INVALID_PARAMS, f"Unknown tool: {name!r}")

    def _read_resource(self, params: dict[str, Any]) -> dict[str, Any]:
        uri = params.get("uri")
        if not isinstance(uri, str) or not uri.strip():
            raise _JsonRpcError(JSONRPC_INVALID_PARAMS, "resources/read requires a non-empty 'uri'.")
        uri = uri.strip()

        if uri == "ledger://manifest":
            manifest = load_manifest(self.state_dir)
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(manifest, indent=2, sort_keys=True),
                    }
                ]
            }

        if uri == "ledger://canonical-projects":
            cp_path = self.state_dir / "canonical-projects.json"
            if not cp_path.is_file():
                raise _JsonRpcError(JSONRPC_INVALID_PARAMS, f"Compiled resource unavailable: {cp_path}")
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": cp_path.read_text(encoding="utf-8"),
                    }
                ]
            }

        if uri == "ledger://canonical-projects-markdown":
            md_path = self.state_dir / "canonical-projects.md"
            if not md_path.is_file():
                raise _JsonRpcError(JSONRPC_INVALID_PARAMS, f"Compiled resource unavailable: {md_path}")
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "text/markdown",
                        "text": md_path.read_text(encoding="utf-8"),
                    }
                ]
            }

        if uri == "ledger://changes":
            payload = changes_payload(self.state_dir)
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(payload, indent=2, sort_keys=True),
                    }
                ]
            }

        if uri == "ledger://review-queue":
            payload = review_payload(
                self.state_dir,
                candidates_path=self.candidates_path,
                decisions_path=self.identity_decisions_path,
            )
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(payload, indent=2, sort_keys=True),
                    }
                ]
            }

        if uri == "ledger://lineage":
            payload = lineage_status_payload(
                state_dir=self.state_dir,
                lineage_path=self.project_lineage_path,
                sync=False,
            )
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(payload, indent=2, sort_keys=True),
                    }
                ]
            }

        prefix = "ledger://projects/"
        if uri.startswith(prefix):
            raw_ref = unquote(uri[len(prefix) :]).strip()
            if not raw_ref:
                raise _JsonRpcError(JSONRPC_INVALID_PARAMS, f"Missing project referent in URI: {uri!r}")
            payload = show_payload(self.state_dir, raw_ref)
            if payload.get("status") != "resolved":
                raise _JsonRpcError(
                    JSONRPC_INVALID_PARAMS,
                    f"Project resource {uri!r} could not be uniquely resolved ({payload.get('status')}).",
                    data=payload,
                )
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(payload, indent=2, sort_keys=True),
                    }
                ]
            }

        raise _JsonRpcError(JSONRPC_INVALID_PARAMS, f"Unknown resource URI: {uri!r}")

    def serve_stdio(
        self,
        in_stream: BinaryIO | None = None,
        out_stream: BinaryIO | None = None,
    ) -> int:
        reader = in_stream if in_stream is not None else sys.stdin.buffer
        writer = out_stream if out_stream is not None else sys.stdout.buffer

        while True:
            frame = _read_stdio_frame(reader)
            if frame is None:
                return 0
            framing, raw_bytes, parse_err = frame
            if parse_err is not None:
                err_resp = _jsonrpc_error(None, JSONRPC_PARSE_ERROR, parse_err)
                _write_stdio_frame(writer, err_resp, framing=framing)
                continue
            try:
                decoded = json.loads(raw_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                err_resp = _jsonrpc_error(None, JSONRPC_PARSE_ERROR, f"Parse error: {exc}")
                _write_stdio_frame(writer, err_resp, framing=framing)
                continue

            response = self.handle_message(decoded)
            if response is not None:
                _write_stdio_frame(writer, response, framing=framing)


def _read_stdio_frame(in_stream: BinaryIO) -> tuple[str, bytes, str | None] | None:
    """Read one JSON-RPC message frame from ``in_stream``.

    Returns ``(framing, payload_bytes, parse_error_message)`` where ``framing`` is
    ``"newline"`` or ``"content_length"``, or ``None`` on clean EOF.
    """
    while True:
        line = in_stream.readline()
        if line == b"":
            return None
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.lower().startswith(b"content-length:"):
            parts = stripped.split(b":", 1)
            try:
                content_length = int(parts[1].strip())
                if content_length < 0:
                    raise ValueError("negative Content-Length")
            except ValueError as exc:
                return ("content_length", b"", f"Invalid Content-Length header: {exc}")

            while True:
                hdr_line = in_stream.readline()
                if hdr_line == b"":
                    return ("content_length", b"", "Unexpected EOF in message headers.")
                if hdr_line in (b"\r\n", b"\n"):
                    break

            chunks: list[bytes] = []
            remaining = content_length
            while remaining > 0:
                chunk = in_stream.read(remaining)
                if not chunk:
                    return ("content_length", b"", "Unexpected EOF while reading Content-Length body.")
                chunks.append(chunk)
                remaining -= len(chunk)
            return ("content_length", b"".join(chunks), None)

        return ("newline", stripped, None)


def _write_stdio_frame(
    out_stream: BinaryIO,
    payload: dict[str, Any] | list[dict[str, Any]],
    *,
    framing: str,
) -> None:
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if framing == "content_length":
        header = f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii")
        out_stream.write(header + encoded)
    else:
        out_stream.write(encoded + b"\n")
    out_stream.flush()


def add_mcp_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--state-dir",
        default=default_workspace_path("state"),
        help="Path to compiled Project Ledger state/ directory.",
    )
    parser.add_argument(
        "--identity-decisions",
        default=default_workspace_path("registry/identity-decisions.json"),
        help="Path to durable identity decisions registry.",
    )
    parser.add_argument(
        "--project-lineage",
        default=default_workspace_path("registry/project-lineage.json"),
        help="Path to durable canonical_project_id lineage registry.",
    )
    parser.add_argument(
        "--candidates",
        default=default_workspace_path("registry/weak-overlap-triage-candidates.json"),
        help="Path to frontier weak-overlap triage candidates registry.",
    )


def run_mcp_from_args(args: argparse.Namespace) -> int:
    server = LedgerMcpServer(
        state_dir=Path(args.state_dir),
        identity_decisions_path=Path(args.identity_decisions),
        project_lineage_path=Path(args.project_lineage),
        candidates_path=Path(args.candidates),
    )
    return server.serve_stdio()


def _mcp_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ledger-mcp",
        description="Read-only Model Context Protocol (MCP) stdio server for Project Ledger.",
    )
    add_mcp_args(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _mcp_parser().parse_args(argv)
    return run_mcp_from_args(args)


if __name__ == "__main__":
    raise SystemExit(main())
