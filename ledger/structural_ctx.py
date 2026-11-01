"""Deterministic read-only structural skeleton extractor for Project Ledger capsules (U2).

Synthesizes a ``RepoSkeleton``-compatible ``capsule["ctx"]`` payload
(``source_format = "compiled_structural_scan"``) directly from any accessible
local repository worktree using only the Python standard library and zero LLM
calls, honoring Architectural Law 10 (strictly read-only sensing: zero bytes
written into target repositories).

When an explicit on-disk ``.ctx/skeleton.json``, ``.ctx/fleet-capsule.json``,
``.ctx/capsule.json``, Tier-2 ``.ctx/CONTEXT-0.md``, or ``ctx``-generated
``CONTEXT.md`` exists, ``ledger/capsules.py`` prefers the explicit artifact;
otherwise it falls back to ``extract_structural_ctx`` (cached across warm
refreshes in ``state/source-checkpoints.json``).
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sys
import tomllib
import warnings
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .sources.common import safe_path_is_dir, safe_path_is_file

STRUCTURAL_CTX_MAX_FILES = 800
STRUCTURAL_CTX_MAX_DEPTH = 6
STRUCTURAL_CTX_MAX_PARSE_FILES = 200
STRUCTURAL_CTX_MAX_PARSE_BYTES = 131072

_ALWAYS_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".ctx",
        ".ctx-cache",
        ".machina",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".tmp",
        ".cache",
        ".next",
        ".nuxt",
        ".turbo",
        ".svelte-kit",
        ".parcel-cache",
        ".idea",
        ".vscode",
        "dist",
        "build",
        "target",
        "coverage",
        "htmlcov",
        "vendor",
    }
)

_SKIP_FILE_SUFFIXES = frozenset(
    {
        ".pyc",
        ".pyo",
        ".pyd",
        ".so",
        ".dylib",
        ".dll",
        ".exe",
        ".o",
        ".a",
        ".class",
        ".jar",
        ".war",
        ".whl",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".zst",
        ".7z",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".bmp",
        ".pdf",
        ".mp3",
        ".mp4",
        ".wav",
        ".ogg",
        ".webm",
        ".mov",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".eot",
        ".sqlite",
        ".sqlite3",
        ".db",
        ".parquet",
        ".feather",
        ".onnx",
        ".pt",
        ".pth",
        ".safetensors",
        ".gguf",
        ".bin",
    }
)

_EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "Python",
    ".pyi": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".mts": "TypeScript",
    ".cts": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".cs": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".c": "C",
    ".h": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".cxx": "C++",
    ".hpp": "C++",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".ps1": "PowerShell",
    ".sql": "SQL",
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".astro": "Astro",
    ".lua": "Lua",
    ".zig": "Zig",
    ".nim": "Nim",
    ".r": "R",
    ".jl": "Julia",
    ".dart": "Dart",
    ".scala": "Scala",
    ".clj": "Clojure",
    ".md": "Markdown",
    ".mdx": "Markdown",
    ".rst": "reStructuredText",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
    ".proto": "Protobuf",
    ".graphql": "GraphQL",
    ".gql": "GraphQL",
    ".tf": "Terraform",
}

_FILENAME_TO_LANGUAGE: dict[str, str] = {
    "dockerfile": "Dockerfile",
    "makefile": "Makefile",
    "justfile": "Justfile",
    "cmakelists.txt": "CMake",
}

_CODE_LANGUAGES = frozenset(
    {
        "Python",
        "TypeScript",
        "JavaScript",
        "Rust",
        "Go",
        "Java",
        "Kotlin",
        "C#",
        "Ruby",
        "PHP",
        "Swift",
        "Elixir",
        "C",
        "C++",
        "Shell",
        "PowerShell",
        "SQL",
        "Vue",
        "Svelte",
        "Astro",
        "Lua",
        "Zig",
        "Nim",
        "R",
        "Julia",
        "Dart",
        "Scala",
        "Clojure",
        "Terraform",
    }
)

_PARSEABLE_LANGUAGES = frozenset({"Python", "TypeScript", "JavaScript", "Rust", "Go"})

_BOUNDARY_DOC_KINDS: dict[str, str] = {
    "agents.md": "authority",
    "system.md": "authority",
    "claude.md": "authority",
    "rules.md": "authority",
    "agent_protocol.md": "authority",
    "gate_closeout.md": "authority",
    "architecture.md": "architecture",
    "schema.md": "architecture",
    "current.md": "architecture",
    "prd.md": "architecture",
    "runbook.md": "architecture",
    "roadmap.md": "architecture",
    "state.md": "architecture",
    "todos.md": "architecture",
    "readme.md": "overview",
    "readme.rst": "overview",
    "readme.txt": "overview",
}

_BOUNDARY_KIND_ORDER = {"authority": 0, "architecture": 1, "overview": 2}

_ENTRYPOINT_FILENAMES = frozenset(
    {
        "__main__.py",
        "cli.py",
        "main.py",
        "app.py",
        "server.py",
        "mcp_server.py",
        "api.py",
        "wsgi.py",
        "asgi.py",
        "manage.py",
        "build_ledger.py",
        "main.rs",
        "lib.rs",
        "main.go",
        "index.ts",
        "index.tsx",
        "index.js",
        "server.ts",
        "server.js",
        "app.ts",
        "app.js",
    }
)

_MULTI_PACKAGE_ROOTS = frozenset(
    {"src", "packages", "apps", "services", "crates", "cmd", "libs", "modules"}
)

_PY_STDLIB_MODULES: frozenset[str] = frozenset(getattr(sys, "stdlib_module_names", ())) | frozenset(
    {"__future__", "typing_extensions"}
)

_JS_SYMBOL_RE = re.compile(
    r"^\s*export\s+(?:default\s+)?(?:async\s+)?(class|function|interface|type|enum|const)\s+([A-Za-z_$][A-Za-z0-9_$]*)",
    re.MULTILINE,
)
_JS_IMPORT_RE = re.compile(
    r"""(?:import\s+(?:[^'"]+?\s+from\s+)?['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\))""",
    re.MULTILINE,
)
_RUST_SYMBOL_RE = re.compile(
    r"^\s*pub(?:\([^)]*\))?\s+(?:async\s+)?(struct|enum|trait|fn|mod|type)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE,
)
_RUST_USE_RE = re.compile(r"^\s*(?:pub\s+)?use\s+([a-zA-Z0-9_:]+)", re.MULTILINE)
_GO_SYMBOL_RE = re.compile(
    r"^\s*(func|type)\s+(?:\([^)]*\)\s*)?([A-Z][A-Za-z0-9_]*)",
    re.MULTILINE,
)
_GO_IMPORT_SINGLE_RE = re.compile(r'^\s*import\s+"([^"]+)"', re.MULTILINE)
_GO_IMPORT_BLOCK_RE = re.compile(r"^\s*import\s*\(([^)]*)\)", re.MULTILINE)
_GO_QUOTED_PKG_RE = re.compile(r'"([^"]+)"')

_ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_IGNORED_GENERIC_ENV_VARS = frozenset(
    {
        "CI",
        "COLUMNS",
        "COMPUTERNAME",
        "EDITOR",
        "HOME",
        "HOSTNAME",
        "LANG",
        "LC_ALL",
        "LINES",
        "LOGNAME",
        "NODE_ENV",
        "OLDPWD",
        "PAGER",
        "PATH",
        "PWD",
        "PYTHONPATH",
        "RUST_BACKTRACE",
        "RUST_LOG",
        "SHELL",
        "SHLVL",
        "SYSTEMROOT",
        "TEMP",
        "TERM",
        "TMP",
        "TMPDIR",
        "TZ",
        "USER",
        "USERPROFILE",
        "VIRTUAL_ENV",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_RUNTIME_DIR",
        "XDG_STATE_HOME",
    }
)
_HTTP_METHODS = frozenset({"get", "post", "put", "delete", "patch", "head", "options"})
_SURFACE_KIND_ORDER = {
    "cli_command": 0,
    "http_route": 1,
    "mcp_tool": 2,
    "mcp_resource": 3,
    "env_var": 4,
}
_JS_HTTP_ROUTE_RE = re.compile(
    r"""\b(?:app|router|server|fastify|hono)\.(get|post|put|delete|patch)\(\s*['"`]([^'"`\s]+)['"`]""",
    re.MULTILINE,
)
_NEXT_APP_ROUTE_RE = re.compile(
    r"^\s*export\s+(?:async\s+)?function\s+(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\b",
    re.MULTILINE,
)
_JS_ENV_RE = re.compile(r"\bprocess\.env\.([A-Z][A-Z0-9_]{2,63})\b")
_GO_HTTP_ROUTE_RE = re.compile(
    r"""\b(?:http|mux|r|router|api|v1|group)\.(?:HandleFunc|Handle|Get|Post|Put|Delete|Patch|GET|POST|PUT|DELETE|PATCH)\(\s*"(?:(GET|POST|PUT|DELETE|PATCH)\s+)?(/[^"]*)" """,
    re.VERBOSE,
)
_GO_HTTP_ROUTE_SIMPLE_RE = re.compile(
    r"""\b(?:http|mux|r|router|api|v1|group)\.(HandleFunc|Handle|Get|Post|Put|Delete|Patch|GET|POST|PUT|DELETE|PATCH)\(\s*"(?:(GET|POST|PUT|DELETE|PATCH)\s+)?(/[^"]*)" """.strip()
)
_GO_ENV_RE = re.compile(r'\bos\.(?:Getenv|LookupEnv)\(\s*"([A-Z][A-Z0-9_]{2,63})"\s*\)')
_RUST_ATTR_ROUTE_RE = re.compile(
    r'#\[(get|post|put|delete|patch)\(\s*"([^"]+)"',
    re.MULTILINE,
)
_RUST_AXUM_ROUTE_RE = re.compile(r'\.route\(\s*"(/[^"]*)"')
_RUST_ENV_RE = re.compile(r'\benv::var(?:_os)?\(\s*"([A-Z][A-Z0-9_]{2,63})"\s*\)')
_NODE_BUILTINS = frozenset(
    {
        "assert",
        "buffer",
        "child_process",
        "cluster",
        "crypto",
        "dgram",
        "dns",
        "events",
        "fs",
        "http",
        "http2",
        "https",
        "module",
        "net",
        "os",
        "path",
        "perf_hooks",
        "process",
        "querystring",
        "readline",
        "stream",
        "string_decoder",
        "timers",
        "tls",
        "tty",
        "url",
        "util",
        "v8",
        "vm",
        "worker_threads",
        "zlib",
    }
)


def _load_simple_gitignore_dirs(repo_dir: Path) -> tuple[set[str], set[str]]:
    """Return ``(any_depth_dir_names, root_only_dir_names)`` from root ``.gitignore``."""
    gitignore_path = repo_dir / ".gitignore"
    any_depth: set[str] = set()
    root_only: set[str] = set()
    if not safe_path_is_file(gitignore_path):
        return any_depth, root_only
    try:
        lines = gitignore_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return any_depth, root_only
    for raw in lines[:200]:
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        if any(ch in line for ch in ("*", "?", "[")):
            continue
        anchored = line.startswith("/")
        cleaned = line.strip("/")
        if not cleaned or "/" in cleaned:
            continue
        # Only treat bare names as directory exclusions when trailing `/` is explicit
        # or there is no file extension dot (except leading dotdirs like `.cache`).
        is_dir_rule = line.endswith("/") or "." not in cleaned.lstrip(".")
        if not is_dir_rule:
            continue
        if anchored:
            root_only.add(cleaned)
        else:
            any_depth.add(cleaned)
    return any_depth, root_only


def _detect_language(name: str) -> str | None:
    lower = name.lower()
    if lower in _FILENAME_TO_LANGUAGE:
        return _FILENAME_TO_LANGUAGE[lower]
    dot = lower.rfind(".")
    if dot == -1:
        return None
    ext = lower[dot:]
    return _EXTENSION_TO_LANGUAGE.get(ext)


def _classify_boundary_doc(rel_posix: str, depth: int) -> str | None:
    lower = rel_posix.lower()
    base = lower.rsplit("/", 1)[-1]
    if depth <= 1 and base in _BOUNDARY_DOC_KINDS:
        return _BOUNDARY_DOC_KINDS[base]
    if depth <= 3 and ("/adr/" in lower or lower.startswith("docs/adr/")) and lower.endswith(".md"):
        return "architecture"
    return None


def _extract_boundary_doc_meta(path: Path) -> tuple[list[str], str, str]:
    try:
        raw = path.read_bytes()
    except OSError:
        return [], "", ""
    content_hash = f"sha256:{hashlib.sha256(raw).hexdigest()}"
    text = raw[:65536].decode("utf-8", errors="replace")
    headings: list[str] = []
    summary_line = ""
    in_frontmatter = False
    in_code_block = False

    for idx, raw_line in enumerate(text.splitlines()):
        stripped = raw_line.strip()
        if idx == 0 and stripped == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if stripped == "---":
                in_frontmatter = False
            continue
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block or not stripped:
            continue
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            title = stripped[level:].strip()
            if title and level <= 2 and len(headings) < 8:
                headings.append(title[:80])
            continue
        if not summary_line and not stripped.startswith(("<!--", "|", "-", "*", ">", "`", "[!")):
            summary_line = stripped[:160]
    return headings, summary_line, content_hash


def _subsystem_for_rel_path(rel_posix: str) -> str:
    parts = rel_posix.split("/")
    if len(parts) == 1:
        return "."
    top = parts[0]
    if top in _MULTI_PACKAGE_ROOTS and len(parts) >= 3:
        return f"{top}/{parts[1]}"
    return top


def _ast_const_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.strip()
    return None


def _dict_literal_strings(node: ast.Dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for k_node, v_node in zip(node.keys, node.values):
        key = _ast_const_str(k_node)
        val = _ast_const_str(v_node)
        if key and val:
            out[key] = val
    return out


def _dict_literal_keys(node: ast.Dict) -> set[str]:
    out: set[str] = set()
    for k_node in node.keys:
        key = _ast_const_str(k_node)
        if key:
            out.add(key)
    return out


def _extract_python_surfaces(tree: ast.Module, rel_posix: str) -> list[dict[str, str]]:
    surfaces: list[dict[str, str]] = []

    def _add(kind: str, name: str, detail: str = "") -> None:
        clean_name = name.strip()[:120]
        if not clean_name:
            return
        rec: dict[str, str] = {"kind": kind, "name": clean_name, "file": rel_posix}
        if detail.strip():
            rec["detail"] = detail.strip()[:120]
        surfaces.append(rec)

    for node in ast.walk(tree):
        # 1. Decorated functions: Click/Typer CLI, FastAPI/Flask/Starlette HTTP, MCP @mcp.tool / @mcp.resource
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                dec_call = dec if isinstance(dec, ast.Call) else None
                dec_target = dec_call.func if dec_call is not None else dec
                if not isinstance(dec_target, ast.Attribute):
                    continue
                owner_name = (
                    dec_target.value.id.lower()
                    if isinstance(dec_target.value, ast.Name)
                    else ""
                )
                attr = dec_target.attr
                attr_lower = attr.lower()
                first_arg = _ast_const_str(dec_call.args[0]) if (dec_call and dec_call.args) else None
                kw_map: dict[str, ast.AST] = (
                    {kw.arg: kw.value for kw in dec_call.keywords if kw.arg}
                    if dec_call is not None
                    else {}
                )
                kw_name = _ast_const_str(kw_map.get("name"))

                if attr_lower in {"command", "group"} and owner_name not in {"mcp", "fastmcp"}:
                    cmd_name = first_arg or kw_name or node.name.replace("_", "-")
                    _add("cli_command", cmd_name)
                elif attr_lower in _HTTP_METHODS and first_arg and first_arg.startswith("/"):
                    _add("http_route", f"{attr.upper()} {first_arg}")
                elif attr_lower in {"route", "api_route"} and first_arg and first_arg.startswith("/"):
                    methods_node = kw_map.get("methods")
                    verbs: list[str] = []
                    if isinstance(methods_node, (ast.List, ast.Tuple, ast.Set)):
                        for elt in methods_node.elts:
                            v = _ast_const_str(elt)
                            if v:
                                verbs.append(v.upper())
                    for verb in (verbs or ["ANY"]):
                        _add("http_route", f"{verb} {first_arg}")
                elif attr_lower == "tool" and (
                    "mcp" in owner_name or "server" in owner_name or owner_name in {"app", "srv"}
                ):
                    tool_name = first_arg or kw_name or node.name
                    _add("mcp_tool", tool_name)
                elif attr_lower == "resource" and (
                    "mcp" in owner_name or "server" in owner_name
                ):
                    uri = first_arg or kw_name or node.name
                    _add("mcp_resource", uri)

        # 2. Call sites: argparse add_parser(...) and os.getenv / os.environ.get(...)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr == "add_parser" and node.args:
                cmd_name = _ast_const_str(node.args[0])
                if cmd_name:
                    help_str = ""
                    for kw in node.keywords:
                        if kw.arg == "help":
                            help_str = _ast_const_str(kw.value) or ""
                    _add("cli_command", cmd_name, help_str)
            elif attr == "getenv" and isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                key = _ast_const_str(node.args[0]) if node.args else None
                if key and _ENV_KEY_RE.match(key) and key not in _IGNORED_GENERIC_ENV_VARS:
                    _add("env_var", key)
            elif (
                attr == "get"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "environ"
            ):
                key = _ast_const_str(node.args[0]) if node.args else None
                if key and _ENV_KEY_RE.match(key) and key not in _IGNORED_GENERIC_ENV_VARS:
                    _add("env_var", key)

        # 3. Subscript os.environ["KEY"]
        elif isinstance(node, ast.Subscript):
            if (
                isinstance(node.value, ast.Attribute)
                and node.value.attr == "environ"
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "os"
            ):
                key = _ast_const_str(node.slice)
                if key and _ENV_KEY_RE.match(key) and key not in _IGNORED_GENERIC_ENV_VARS:
                    _add("env_var", key)

        # 4. Table-driven MCP tool/resource catalogs (e.g. `_TOOLS = (...)`, `_STATIC_RESOURCES = (...)`)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            var_names: list[str] = []
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        var_names.append(tgt.id.upper())
                val_node = node.value
            else:
                if isinstance(node.target, ast.Name):
                    var_names.append(node.target.id.upper())
                val_node = node.value
            if not var_names or not isinstance(val_node, (ast.List, ast.Tuple)):
                continue
            is_tool_var = any("TOOL" in vn for vn in var_names)
            is_res_var = any(("RESOURCE" in vn or "TEMPLATE" in vn) for vn in var_names)
            if not (is_tool_var or is_res_var):
                continue
            for elt in val_node.elts:
                if not isinstance(elt, ast.Dict):
                    continue
                str_map = _dict_literal_strings(elt)
                all_keys = _dict_literal_keys(elt)
                if is_tool_var and "name" in str_map and (
                    "inputSchema" in all_keys or "description" in all_keys
                ):
                    _add("mcp_tool", str_map["name"], str_map.get("description", ""))
                elif is_res_var and ("uri" in str_map or "uriTemplate" in str_map):
                    uri_val = str_map.get("uri") or str_map.get("uriTemplate") or ""
                    _add("mcp_resource", uri_val, str_map.get("name", ""))

    return surfaces


def _extract_text_surfaces(content: str, language: str, rel_posix: str) -> list[dict[str, str]]:
    surfaces: list[dict[str, str]] = []

    def _add(kind: str, name: str) -> None:
        clean_name = name.strip()[:120]
        if clean_name:
            surfaces.append({"kind": kind, "name": clean_name, "file": rel_posix})

    if language in {"TypeScript", "JavaScript"}:
        for match in _JS_HTTP_ROUTE_RE.finditer(content):
            route = match.group(2).strip()
            if route.startswith("/"):
                _add("http_route", f"{match.group(1).upper()} {route}")
        lower_rel = rel_posix.lower()
        if lower_rel.endswith(("/route.ts", "/route.js", "/route.tsx", "/route.jsx")):
            # Next.js App Router route handler
            parts = rel_posix.split("/")
            app_idx = next((i for i, seg in enumerate(parts) if seg == "app"), -1)
            route_segs = parts[app_idx + 1 : -1] if app_idx != -1 else parts[:-1]
            route_path = "/" + "/".join(s for s in route_segs if not (s.startswith("(") and s.endswith(")")))
            for match in _NEXT_APP_ROUTE_RE.finditer(content):
                _add("http_route", f"{match.group(1).upper()} {route_path}")
        for match in _JS_ENV_RE.finditer(content):
            key = match.group(1)
            if key not in _IGNORED_GENERIC_ENV_VARS:
                _add("env_var", key)
    elif language == "Go":
        for match in _GO_HTTP_ROUTE_SIMPLE_RE.finditer(content):
            call_name = match.group(1)
            explicit_verb = match.group(2)
            route = match.group(3)
            verb = (
                explicit_verb.upper()
                if explicit_verb
                else (call_name.upper() if call_name.lower() in _HTTP_METHODS else "ANY")
            )
            _add("http_route", f"{verb} {route}")
        for match in _GO_ENV_RE.finditer(content):
            key = match.group(1)
            if key not in _IGNORED_GENERIC_ENV_VARS:
                _add("env_var", key)
    elif language == "Rust":
        for match in _RUST_ATTR_ROUTE_RE.finditer(content):
            _add("http_route", f"{match.group(1).upper()} {match.group(2)}")
        for match in _RUST_AXUM_ROUTE_RE.finditer(content):
            _add("http_route", f"ANY {match.group(1)}")
        for match in _RUST_ENV_RE.finditer(content):
            key = match.group(1)
            if key not in _IGNORED_GENERIC_ENV_VARS:
                _add("env_var", key)

    return surfaces


def compute_subsystem_dependency_summary(subsystems: list[dict[str, Any]]) -> dict[str, int]:
    """Compute ``internal_edge_count`` and Tarjan SCC ``cycle_count`` over subsystem deps."""
    known = {str(s.get("path") or "").strip() for s in subsystems if isinstance(s, dict)}
    known.discard("")
    adj: dict[str, list[str]] = {name: [] for name in sorted(known)}
    edge_count = 0
    for sub in subsystems:
        if not isinstance(sub, dict):
            continue
        src = str(sub.get("path") or "").strip()
        if src not in adj:
            continue
        raw_deps = sub.get("internal_deps")
        if not isinstance(raw_deps, list):
            continue
        seen_targets: set[str] = set()
        for dep in raw_deps:
            dst = str(dep or "").strip()
            if dst and dst != src and dst in adj and dst not in seen_targets:
                seen_targets.add(dst)
                adj[src].append(dst)
                edge_count += 1

    index = 0
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    scc_stack: list[str] = []
    on_stack: set[str] = set()
    cycle_count = 0

    for root_node in sorted(adj):
        if root_node in indices:
            continue
        indices[root_node] = index
        lowlink[root_node] = index
        index += 1
        scc_stack.append(root_node)
        on_stack.add(root_node)
        call_stack: list[tuple[str, list[str], int]] = [
            (root_node, list(adj.get(root_node, ())), 0)
        ]

        while call_stack:
            node, succs, idx = call_stack[-1]
            if idx < len(succs):
                nxt = succs[idx]
                call_stack[-1] = (node, succs, idx + 1)
                if nxt not in indices:
                    indices[nxt] = index
                    lowlink[nxt] = index
                    index += 1
                    scc_stack.append(nxt)
                    on_stack.add(nxt)
                    call_stack.append((nxt, list(adj.get(nxt, ())), 0))
                elif nxt in on_stack:
                    if indices[nxt] < lowlink[node]:
                        lowlink[node] = indices[nxt]
            else:
                call_stack.pop()
                if call_stack:
                    parent = call_stack[-1][0]
                    if lowlink[node] < lowlink[parent]:
                        lowlink[parent] = lowlink[node]
                if lowlink[node] == indices[node]:
                    scc: list[str] = []
                    while True:
                        w = scc_stack.pop()
                        on_stack.discard(w)
                        scc.append(w)
                        if w == node:
                            break
                    if len(scc) > 1:
                        cycle_count += 1

    return {
        "internal_edge_count": edge_count,
        "cycle_count": cycle_count,
    }


def infer_subsystem_arch_role(
    sub_path: str,
    languages: list[str],
    *,
    has_entrypoint: bool = False,
    has_interface_surface: bool = False,
) -> str:
    """Classify a subsystem into one of 6 deterministic architectural roles using whole-token segments."""
    lower = sub_path.lower().strip("/")
    tokens = {tok for tok in re.split(r"[/_\-.]+", lower) if tok}
    leaf = lower.rsplit("/", 1)[-1] if lower else "."
    if tokens & {"tests", "test", "spec", "specs", "e2e", "benchmarks", "benchmark", "bench", "fixtures"}:
        return "test_or_verification"
    if tokens & {"docs", "doc", "schemas", "schema", "adr", "adrs", "design", "rfcs", "rfc"} or (
        languages
        and all(lang in {"Markdown", "reStructuredText", "JSON", "YAML"} for lang in languages)
        and leaf not in {".", "registry", "heads"}
    ):
        return "docs_or_specs"
    if tokens & {"scripts", "script", "ops", "infra", "deploy", "ci", "github", "tools", "tooling", "bin", "docker"}:
        return "tooling_or_ops"
    if tokens & {"sources", "adapters", "adapter", "clients", "client", "providers", "provider", "drivers", "connectors", "db", "storage"}:
        return "adapter_or_io"
    if has_entrypoint or has_interface_surface or (
        tokens & {"cli", "api", "web", "ui", "frontend", "server", "cmd", "mcp"}
    ):
        return "entrypoint_or_interface"
    return "core_domain"


def _parse_code_file(
    path: Path,
    language: str,
    rel_posix: str,
) -> tuple[list[str], list[tuple[str, bool]], list[dict[str, str]]]:
    """Return ``(symbols, [(import_target, is_relative), ...], operational_surfaces)``."""
    try:
        raw = path.read_bytes()
    except OSError:
        return [], [], []
    if len(raw) > STRUCTURAL_CTX_MAX_PARSE_BYTES:
        return [], [], []
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return [], [], []

    symbols: list[str] = []
    imports: list[tuple[str, bool]] = []
    surfaces: list[dict[str, str]] = []

    if language == "Python":
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(content)
        except SyntaxError:
            return [], [], []
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                if not node.name.startswith("_"):
                    symbols.append(f"class {node.name}")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_") or (
                    node.name.startswith("__") and node.name.endswith("__")
                ):
                    symbols.append(f"fn {node.name}")
        seen_imps: set[tuple[str, bool]] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name:
                        item = (alias.name, False)
                        if item not in seen_imps:
                            seen_imps.add(item)
                            imports.append(item)
            elif isinstance(node, ast.ImportFrom):
                is_rel = (node.level or 0) > 0
                mod = node.module or ""
                if is_rel:
                    parent_parts = rel_posix.split("/")[:-1]
                    up = max(0, (node.level or 1) - 1)
                    base_parts = (
                        parent_parts[: max(0, len(parent_parts) - up)]
                        if up <= len(parent_parts)
                        else []
                    )
                    resolved = ".".join([*base_parts, mod]) if mod else ".".join(base_parts)
                    if resolved:
                        item = (resolved, True)
                        if item not in seen_imps:
                            seen_imps.add(item)
                            imports.append(item)
                elif mod:
                    item = (mod, False)
                    if item not in seen_imps:
                        seen_imps.add(item)
                        imports.append(item)
        surfaces = _extract_python_surfaces(tree, rel_posix)
    elif language in {"TypeScript", "JavaScript"}:
        kind_map = {
            "class": "class",
            "function": "fn",
            "interface": "interface",
            "type": "type",
            "enum": "enum",
            "const": "const",
        }
        for match in _JS_SYMBOL_RE.finditer(content):
            prefix = kind_map.get(match.group(1), match.group(1))
            symbols.append(f"{prefix} {match.group(2)}")
        seen_js: set[str] = set()
        for match in _JS_IMPORT_RE.finditer(content):
            target = (match.group(1) or match.group(2) or "").strip()
            if target and target not in seen_js:
                seen_js.add(target)
                imports.append((target, target.startswith(".")))
        surfaces = _extract_text_surfaces(content, language, rel_posix)
    elif language == "Rust":
        kind_map = {
            "struct": "struct",
            "enum": "enum",
            "trait": "trait",
            "fn": "fn",
            "mod": "mod",
            "type": "type",
        }
        for match in _RUST_SYMBOL_RE.finditer(content):
            prefix = kind_map.get(match.group(1), match.group(1))
            symbols.append(f"{prefix} {match.group(2)}")
        seen_rs: set[str] = set()
        for match in _RUST_USE_RE.finditer(content):
            target = match.group(1).strip()
            if target and target not in seen_rs:
                seen_rs.add(target)
                imports.append((target, target.startswith(("crate::", "self::", "super::"))))
        surfaces = _extract_text_surfaces(content, language, rel_posix)
    elif language == "Go":
        for match in _GO_SYMBOL_RE.finditer(content):
            prefix = "fn" if match.group(1) == "func" else "type"
            symbols.append(f"{prefix} {match.group(2)}")
        seen_go: set[str] = set()
        for match in _GO_IMPORT_SINGLE_RE.finditer(content):
            pkg = match.group(1).strip()
            if pkg and pkg not in seen_go:
                seen_go.add(pkg)
                imports.append((pkg, False))
        for block in _GO_IMPORT_BLOCK_RE.findall(content):
            for pkg in _GO_QUOTED_PKG_RE.findall(block):
                pkg_clean = pkg.strip()
                if pkg_clean and pkg_clean not in seen_go:
                    seen_go.add(pkg_clean)
                    imports.append((pkg_clean, False))
        surfaces = _extract_text_surfaces(content, language, rel_posix)

    imports.sort(key=lambda item: item[0])
    return symbols[:16], imports, surfaces


def _extract_manifest_entrypoints(repo_dir: Path) -> list[str]:
    entrypoints: list[str] = []

    pyproject_path = repo_dir / "pyproject.toml"
    if safe_path_is_file(pyproject_path):
        try:
            py_data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
            scripts = (py_data.get("project") or {}).get("scripts")
            if isinstance(scripts, dict):
                for script_name in sorted(scripts):
                    target = str(scripts[script_name] or "").strip()
                    if script_name and target:
                        entrypoints.append(f"cli:{script_name} -> {target}")
        except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
            pass

    package_json_path = repo_dir / "package.json"
    if safe_path_is_file(package_json_path):
        try:
            pkg_data = json.loads(package_json_path.read_text(encoding="utf-8"))
            if isinstance(pkg_data, dict):
                main_val = str(pkg_data.get("main") or "").strip()
                if main_val:
                    entrypoints.append(f"npm:main -> {main_val}")
                bin_val = pkg_data.get("bin")
                if isinstance(bin_val, str) and bin_val.strip():
                    pkg_name = str(pkg_data.get("name") or "bin").strip()
                    entrypoints.append(f"npm:bin:{pkg_name} -> {bin_val.strip()}")
                elif isinstance(bin_val, dict):
                    for bin_name in sorted(bin_val):
                        target = str(bin_val[bin_name] or "").strip()
                        if bin_name and target:
                            entrypoints.append(f"npm:bin:{bin_name} -> {target}")
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            pass

    cargo_toml_path = repo_dir / "Cargo.toml"
    if safe_path_is_file(cargo_toml_path):
        try:
            cargo_data = tomllib.loads(cargo_toml_path.read_text(encoding="utf-8"))
            bins = cargo_data.get("bin")
            if isinstance(bins, list):
                for item in bins:
                    if isinstance(item, dict):
                        bname = str(item.get("name") or "").strip()
                        bpath = str(item.get("path") or "src/main.rs").strip()
                        if bname:
                            entrypoints.append(f"cargo:bin:{bname} -> {bpath}")
        except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
            pass

    return entrypoints


def extract_structural_ctx(
    repo_dir: Path,
    *,
    max_files: int = STRUCTURAL_CTX_MAX_FILES,
    max_depth: int = STRUCTURAL_CTX_MAX_DEPTH,
    max_parse_files: int = STRUCTURAL_CTX_MAX_PARSE_FILES,
) -> dict[str, Any] | None:
    """Scan ``repo_dir`` read-only and synthesize a ``RepoSkeleton``-compatible dict."""
    if not safe_path_is_dir(repo_dir):
        return None

    ignore_any_depth, ignore_root_only = _load_simple_gitignore_dirs(repo_dir)
    skip_any = _ALWAYS_SKIP_DIRS | ignore_any_depth

    base_str = str(repo_dir)
    base_prefix_len = len(base_str) + (0 if base_str.endswith(os.sep) else 1)

    discovered_files: list[tuple[str, Path, int, str | None]] = []
    stack: list[tuple[str, int]] = [(base_str, 0)]

    while stack and len(discovered_files) < max_files:
        curr_dir, depth = stack.pop()
        try:
            with os.scandir(curr_dir) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            continue

        sub_dirs: list[tuple[str, int]] = []
        for entry in entries:
            name = entry.name
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if name in skip_any or (depth == 0 and name in ignore_root_only):
                        continue
                    if name.startswith(".") and name not in {".github"}:
                        continue
                    if depth + 1 <= max_depth:
                        sub_dirs.append((entry.path, depth + 1))
                elif entry.is_file(follow_symlinks=False):
                    lower_name = name.lower()
                    dot = lower_name.rfind(".")
                    if dot != -1 and lower_name[dot:] in _SKIP_FILE_SUFFIXES:
                        continue
                    rel_posix = entry.path[base_prefix_len:].replace(os.sep, "/")
                    lang = _detect_language(name)
                    discovered_files.append((rel_posix, Path(entry.path), depth, lang))
                    if len(discovered_files) >= max_files:
                        break
            except OSError:
                continue

        for sd in reversed(sub_dirs):
            stack.append(sd)

    lang_counts: Counter[str] = Counter()
    boundary_candidates: list[tuple[tuple[int, int, str], dict[str, Any]]] = []
    file_entrypoints: list[str] = []
    raw_surfaces: list[dict[str, str]] = []

    sub_file_counts: Counter[str] = Counter()
    sub_code_counts: Counter[str] = Counter()
    sub_lang_counts: dict[str, Counter[str]] = defaultdict(Counter)
    sub_symbols: dict[str, list[str]] = defaultdict(list)
    sub_raw_imports: dict[str, list[tuple[str, bool, str]]] = defaultdict(list)
    sub_has_entrypoint: set[str] = set()
    sub_has_interface_surface: set[str] = set()

    known_subsystems: set[str] = set()
    known_top_modules: dict[str, str] = {}
    for rel_posix, _, _, _ in discovered_files:
        sub_name = _subsystem_for_rel_path(rel_posix)
        known_subsystems.add(sub_name)
        if sub_name != ".":
            mod_leaf = sub_name.split("/")[-1]
            known_top_modules.setdefault(mod_leaf, sub_name)
            known_top_modules.setdefault(sub_name.replace("/", "."), sub_name)

    parsed_code_files = 0
    struct_hasher = hashlib.sha256()

    for rel_posix, fpath, depth, lang in discovered_files:
        if lang:
            lang_counts[lang] += 1

        bkind = _classify_boundary_doc(rel_posix, depth)
        if bkind is not None:
            headings, summary, chash = _extract_boundary_doc_meta(fpath)
            if chash:
                boundary_candidates.append(
                    (
                        (_BOUNDARY_KIND_ORDER.get(bkind, 9), depth, rel_posix.lower()),
                        {
                            "path": rel_posix,
                            "kind": bkind,
                            "headings": headings,
                            "summary": summary,
                            "content_hash": chash,
                        },
                    )
                )

        sub_name = _subsystem_for_rel_path(rel_posix)
        fname_lower = fpath.name.lower()
        if depth <= 2 and fname_lower in _ENTRYPOINT_FILENAMES:
            file_entrypoints.append(f"file:{rel_posix}")
            sub_has_entrypoint.add(sub_name)

        sub_file_counts[sub_name] += 1
        if lang:
            sub_lang_counts[sub_name][lang] += 1
            if lang in _CODE_LANGUAGES:
                sub_code_counts[sub_name] += 1

        symbols: list[str] = []
        imports: list[tuple[str, bool]] = []
        file_surfaces: list[dict[str, str]] = []
        if lang in _PARSEABLE_LANGUAGES and parsed_code_files < max_parse_files:
            parsed_code_files += 1
            symbols, imports, file_surfaces = _parse_code_file(fpath, lang, rel_posix)
            for sym in symbols:
                if sym not in sub_symbols[sub_name] and len(sub_symbols[sub_name]) < 12:
                    sub_symbols[sub_name].append(sym)
            for imp_target, is_rel in imports:
                sub_raw_imports[sub_name].append((imp_target, is_rel, lang))
            for surf in file_surfaces:
                raw_surfaces.append(surf)
                if surf.get("kind") in {"cli_command", "http_route", "mcp_tool", "mcp_resource"}:
                    sub_has_interface_surface.add(sub_name)

        struct_hasher.update(rel_posix.encode("utf-8"))
        struct_hasher.update(b"\x00")
        struct_hasher.update((lang or "").encode("utf-8"))
        struct_hasher.update(b"\x00")
        if symbols:
            struct_hasher.update("\x1f".join(symbols).encode("utf-8"))
        struct_hasher.update(b"\x00")
        if imports:
            struct_hasher.update("\x1f".join(t for t, _ in imports).encode("utf-8"))
        struct_hasher.update(b"\x1e")

    manifest_entrypoints = _extract_manifest_entrypoints(repo_dir)
    seen_eps: set[str] = set()
    entrypoints: list[str] = []
    for ep in [*manifest_entrypoints, *file_entrypoints]:
        if ep not in seen_eps:
            seen_eps.add(ep)
            entrypoints.append(ep)

    for ep in entrypoints:
        struct_hasher.update(b"ep:")
        struct_hasher.update(ep.encode("utf-8"))
        struct_hasher.update(b"\x1e")

    deduped_surfaces_map: dict[tuple[str, str], dict[str, str]] = {}
    for surf in raw_surfaces:
        skind = str(surf.get("kind") or "").strip()
        sname = str(surf.get("name") or "").strip()
        sfile = str(surf.get("file") or "").strip()
        if not skind or not sname:
            continue
        dedup_key = (skind, sname)
        if dedup_key not in deduped_surfaces_map:
            entry_surf: dict[str, str] = {"kind": skind, "name": sname, "file": sfile}
            sdetail = str(surf.get("detail") or "").strip()
            if sdetail:
                entry_surf["detail"] = sdetail
            deduped_surfaces_map[dedup_key] = entry_surf

    ordered_surfaces = sorted(
        deduped_surfaces_map.values(),
        key=lambda item: (
            _SURFACE_KIND_ORDER.get(item["kind"], 99),
            item["kind"],
            item["name"],
            item["file"],
        ),
    )
    for surf in ordered_surfaces:
        struct_hasher.update(b"surf:")
        struct_hasher.update(f"{surf['kind']}:{surf['name']}@{surf['file']}".encode("utf-8"))
        struct_hasher.update(b"\x1e")
    structural_hash = f"sha256:{struct_hasher.hexdigest()}"

    boundary_candidates.sort(key=lambda pair: pair[0])
    boundary_docs = [item for _, item in boundary_candidates]
    bdoc_hasher = hashlib.sha256()
    for doc in boundary_docs:
        bdoc_hasher.update(doc["path"].encode("utf-8"))
        bdoc_hasher.update(b"\x00")
        bdoc_hasher.update(doc["kind"].encode("utf-8"))
        bdoc_hasher.update(b"\x00")
        bdoc_hasher.update(str(doc.get("content_hash") or "").encode("utf-8"))
        bdoc_hasher.update(b"\x1e")
    boundary_doc_hash = f"sha256:{bdoc_hasher.hexdigest()}"

    subsystems: list[dict[str, Any]] = []
    full_dep_subsystems: list[dict[str, Any]] = []
    ordered_sub_names = sorted(
        sub_file_counts,
        key=lambda s: (
            0 if (sub_code_counts[s] > 0 and s != ".") else (1 if sub_code_counts[s] > 0 else 2),
            -sub_code_counts[s],
            -sub_file_counts[s],
            s,
        ),
    )

    for sub_name in ordered_sub_names:
        internal_deps: set[str] = set()
        external_deps: set[str] = set()
        for imp_target, is_rel, imp_lang in sub_raw_imports.get(sub_name, []):
            if imp_lang == "Python":
                top_pkg = imp_target.split(".")[0]
                dotted_two = ".".join(imp_target.split(".")[:2])
                matched_sub = known_top_modules.get(dotted_two) or known_top_modules.get(top_pkg)
                if matched_sub:
                    if matched_sub != sub_name:
                        internal_deps.add(matched_sub)
                elif not is_rel and top_pkg and top_pkg not in _PY_STDLIB_MODULES:
                    external_deps.add(top_pkg)
            elif imp_lang in {"TypeScript", "JavaScript"}:
                if is_rel or imp_target.startswith(("@/", "~/")):
                    cleaned = imp_target.lstrip("./@~").split("/")[0]
                    matched_sub = known_top_modules.get(cleaned)
                    if matched_sub and matched_sub != sub_name:
                        internal_deps.add(matched_sub)
                else:
                    clean_js = imp_target[5:] if imp_target.startswith("node:") else imp_target
                    pkg_name = (
                        "/".join(clean_js.split("/")[:2])
                        if clean_js.startswith("@")
                        else clean_js.split("/")[0]
                    )
                    if pkg_name and pkg_name not in _NODE_BUILTINS and not imp_target.startswith("node:"):
                        external_deps.add(pkg_name)
            elif imp_lang == "Rust":
                parts = imp_target.split("::")
                root_seg = parts[0]
                if root_seg == "crate" and len(parts) >= 2:
                    matched_sub = known_top_modules.get(parts[1])
                    if matched_sub and matched_sub != sub_name:
                        internal_deps.add(matched_sub)
                elif root_seg not in {"std", "core", "alloc", "self", "super", "crate"} and root_seg:
                    external_deps.add(root_seg)
            elif imp_lang == "Go":
                if "/" in imp_target:
                    external_deps.add("/".join(imp_target.split("/")[:3]))

        sub_langs = [
            lname
            for lname, _ in sorted(
                sub_lang_counts[sub_name].items(), key=lambda pair: (-pair[1], pair[0])
            )[:3]
        ]
        sorted_int_deps = sorted(internal_deps)
        full_dep_subsystems.append({"path": sub_name, "internal_deps": sorted_int_deps})
        arch_role = infer_subsystem_arch_role(
            sub_name,
            sub_langs,
            has_entrypoint=sub_name in sub_has_entrypoint,
            has_interface_surface=sub_name in sub_has_interface_surface,
        )
        subsystems.append(
            {
                "path": sub_name,
                "arch_role": arch_role,
                "file_count": sub_file_counts[sub_name],
                "languages": sub_langs,
                "key_symbols": sub_symbols.get(sub_name, [])[:6],
                "internal_deps": sorted_int_deps[:8],
                "external_deps": sorted(external_deps)[:8],
            }
        )

    languages_sorted = {
        lname: count
        for lname, count in sorted(lang_counts.items(), key=lambda pair: (-pair[1], pair[0]))
    }
    dependency_summary = compute_subsystem_dependency_summary(full_dep_subsystems)

    return {
        "structural_hash": structural_hash,
        "boundary_doc_hash": boundary_doc_hash,
        "total_files": len(discovered_files),
        "languages": languages_sorted,
        "entrypoints": entrypoints,
        "operational_surfaces": ordered_surfaces[:24],
        "operational_surface_count": len(ordered_surfaces),
        "dependency_summary": dependency_summary,
        "boundary_docs": boundary_docs,
        "subsystems": subsystems,
        "subsystem_count": len(subsystems),
    }
