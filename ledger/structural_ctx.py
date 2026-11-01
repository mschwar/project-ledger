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


def _parse_code_file(
    path: Path,
    language: str,
    rel_posix: str,
) -> tuple[list[str], list[tuple[str, bool]]]:
    """Return ``(symbols, [(import_target, is_relative), ...])``."""
    try:
        raw = path.read_bytes()
    except OSError:
        return [], []
    if len(raw) > STRUCTURAL_CTX_MAX_PARSE_BYTES:
        return [], []
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return [], []

    symbols: list[str] = []
    imports: list[tuple[str, bool]] = []

    if language == "Python":
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(content)
        except SyntaxError:
            return [], []
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

    imports.sort(key=lambda item: item[0])
    return symbols[:16], imports


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

    sub_file_counts: Counter[str] = Counter()
    sub_code_counts: Counter[str] = Counter()
    sub_lang_counts: dict[str, Counter[str]] = defaultdict(Counter)
    sub_symbols: dict[str, list[str]] = defaultdict(list)
    sub_raw_imports: dict[str, list[tuple[str, bool, str]]] = defaultdict(list)

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

        fname_lower = fpath.name.lower()
        if depth <= 2 and fname_lower in _ENTRYPOINT_FILENAMES:
            file_entrypoints.append(f"file:{rel_posix}")

        sub_name = _subsystem_for_rel_path(rel_posix)
        sub_file_counts[sub_name] += 1
        if lang:
            sub_lang_counts[sub_name][lang] += 1
            if lang in _CODE_LANGUAGES:
                sub_code_counts[sub_name] += 1

        symbols: list[str] = []
        imports: list[tuple[str, bool]] = []
        if lang in _PARSEABLE_LANGUAGES and parsed_code_files < max_parse_files:
            parsed_code_files += 1
            symbols, imports = _parse_code_file(fpath, lang, rel_posix)
            for sym in symbols:
                if sym not in sub_symbols[sub_name] and len(sub_symbols[sub_name]) < 12:
                    sub_symbols[sub_name].append(sym)
            for imp_target, is_rel in imports:
                sub_raw_imports[sub_name].append((imp_target, is_rel, lang))

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
        subsystems.append(
            {
                "path": sub_name,
                "file_count": sub_file_counts[sub_name],
                "languages": sub_langs,
                "key_symbols": sub_symbols.get(sub_name, [])[:6],
                "internal_deps": sorted(internal_deps)[:8],
                "external_deps": sorted(external_deps)[:8],
            }
        )

    languages_sorted = {
        lname: count
        for lname, count in sorted(lang_counts.items(), key=lambda pair: (-pair[1], pair[0]))
    }

    return {
        "structural_hash": structural_hash,
        "boundary_doc_hash": boundary_doc_hash,
        "total_files": len(discovered_files),
        "languages": languages_sorted,
        "entrypoints": entrypoints,
        "boundary_docs": boundary_docs,
        "subsystems": subsystems,
        "subsystem_count": len(subsystems),
    }
