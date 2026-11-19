"""Durable repository profile catalog and deterministic profile fallback (`ledger/catalog.py`).

Loads ``registry/project-catalog.json`` (`schemas/project-catalog.schema.json`) as a
read-only compiler input so every canonical repository carries curated ``display_name``,
``description``, ``goal`` (why it exists), ``what_it_does``, and ``primary_language``
without mutating target repositories (Architectural Law 10).

When a repository is absent from both ``.project-ledger.json`` and
``registry/project-catalog.json`` (for example, a newly discovered GitHub repository
or a hermetic unit-test fixture), ``extract_repo_doc_profile`` derives conservative,
provenance-tagged fallbacks from package manifests, structured README sections, and
``ctx`` structural skeletons.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

from . import PROJECT_CATALOG_SCHEMA_VERSION
from .sources.common import README_CANDIDATES, find_first_existing, safe_path_is_file

DEFAULT_CATALOG_FILENAME = "project-catalog.json"
_PRJ_ID_RE = re.compile(r"^prj_[0-9a-f]{24}$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufeff]")
_HTML_TAG_RE = re.compile(r"</?[A-Za-z!][^>]*>")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")

DESCRIPTION_MAX_CHARS = 240
GOAL_MAX_CHARS = 280
WHAT_IT_DOES_MAX_CHARS = 320
DISPLAY_NAME_MAX_CHARS = 120

_DATA_OR_DOC_LANGUAGES = frozenset(
    {
        "JSON",
        "YAML",
        "TOML",
        "Markdown",
        "CSV",
        "TSV",
        "Text",
        "XML",
        "INI",
    }
)

_NOISY_SUMMARY_PREFIXES = (
    "live:",
    "english ·",
    "docs &middot;",
    "docs ·",
    "last reviewed:",
    "canonical home:",
    "built with gemforge",
    "remember this.",
    "this contains everything you need to run your app locally",
    "visit the website",
)

_GOAL_HEADING_KEYWORDS = (
    "goal",
    "why",
    "mission",
    "purpose",
    "motivation",
    "problem",
    "thesis",
    "objective",
)

_WHAT_HEADING_KEYWORDS = (
    "what it does",
    "tl;dr",
    "tldr",
    "overview",
    "how it works",
    "architecture",
    "features",
    "capabilities",
    "summary",
)


def _clean_single_line(raw: Any, *, max_chars: int | None = None) -> str | None:
    if not isinstance(raw, str):
        return None
    cleaned = _CONTROL_RE.sub("", raw).replace("\r", " ").replace("\n", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None
    if max_chars is not None and len(cleaned) > max_chars:
        return cleaned[:max_chars].rstrip()
    return cleaned


def is_noisy_summary_text(text: str | None) -> bool:
    """Return True when extracted README text is a badge/navigation/stub fragment."""
    if not text:
        return True
    cleaned = text.strip().lstrip(">").strip()
    if len(cleaned) < 8:
        return True
    lowered = cleaned.casefold()
    if any(lowered.startswith(prefix) for prefix in _NOISY_SUMMARY_PREFIXES):
        return True
    if lowered in {"live", "docs", "readme", "todo", "wip", "tbd"}:
        return True
    return False


def empty_project_catalog() -> dict[str, Any]:
    return {
        "schema_version": PROJECT_CATALOG_SCHEMA_VERSION,
        "projects": [],
    }


def resolve_catalog_path(
    *,
    decisions_path: Path | None = None,
    catalog_path: Path | None = None,
) -> tuple[Path | None, bool]:
    """Resolve ``registry/project-catalog.json`` sibling to ``decisions_path`` when omitted."""
    if catalog_path is not None:
        return catalog_path.resolve(), True
    if decisions_path is not None:
        candidate = decisions_path.resolve().parent / DEFAULT_CATALOG_FILENAME
        if candidate.is_file():
            return candidate, False
    return None, False


def load_project_catalog(path: Path | None, *, required: bool = False) -> dict[str, Any]:
    """Load and validate ``registry/project-catalog.json``."""
    if path is None:
        return empty_project_catalog()
    resolved = path.resolve()
    if not resolved.is_file():
        if required:
            raise ValueError(f"Project catalog does not exist: {resolved}")
        return empty_project_catalog()
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Project catalog is invalid JSON: {resolved}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("Project catalog must be a JSON object.")
    if payload.get("schema_version") != PROJECT_CATALOG_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported project catalog schema_version {payload.get('schema_version')!r}; "
            f"expected {PROJECT_CATALOG_SCHEMA_VERSION!r}."
        )
    raw_projects = payload.get("projects")
    if not isinstance(raw_projects, list):
        raise ValueError("Project catalog must contain a projects array.")

    seen_ids: set[str] = set()
    normalized_records: list[dict[str, Any]] = []
    for index, rec in enumerate(raw_projects):
        where = f"projects[{index}]"
        if not isinstance(rec, dict):
            raise ValueError(f"{where} must be an object.")
        cid = str(rec.get("canonical_project_id") or "").strip()
        if not _PRJ_ID_RE.match(cid):
            raise ValueError(f"{where}.canonical_project_id={cid!r} must match ^prj_[0-9a-f]{{24}}$.")
        if cid in seen_ids:
            raise ValueError(f"Duplicate canonical_project_id in project catalog: {cid}")
        seen_ids.add(cid)

        entry: dict[str, Any] = {"canonical_project_id": cid}
        for field_name, max_len in (
            ("project_key_hint", 200),
            ("display_name", DISPLAY_NAME_MAX_CHARS),
            ("description", DESCRIPTION_MAX_CHARS),
            ("goal", GOAL_MAX_CHARS),
            ("what_it_does", WHAT_IT_DOES_MAX_CHARS),
            ("primary_language", 60),
        ):
            val = _clean_single_line(rec.get(field_name), max_chars=max_len)
            if not val:
                raise ValueError(f"{where}.{field_name} must be a non-empty string.")
            entry[field_name] = val

        if "tags" in rec and rec["tags"] is not None:
            raw_tags = rec["tags"]
            if not isinstance(raw_tags, list) or any(not isinstance(t, str) or not t.strip() for t in raw_tags):
                raise ValueError(f"{where}.tags must be an array of non-empty strings.")
            entry["tags"] = sorted({t.strip() for t in raw_tags if t.strip()})
        normalized_records.append(entry)

    normalized_records.sort(key=lambda item: item["canonical_project_id"])
    return {
        "schema_version": PROJECT_CATALOG_SCHEMA_VERSION,
        "projects": normalized_records,
    }


def index_project_catalog(catalog_payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(catalog_payload, dict):
        return {}
    records = catalog_payload.get("projects")
    if not isinstance(records, list):
        return {}
    return {
        str(rec["canonical_project_id"]): rec
        for rec in records
        if isinstance(rec, dict) and rec.get("canonical_project_id")
    }


def lookup_catalog_entry(
    catalog_by_id: dict[str, dict[str, Any]],
    project: dict[str, Any],
) -> dict[str, Any] | None:
    """Lookup a project's curated catalog record by canonical ID or historical ID referent."""
    if not catalog_by_id:
        return None
    cid = str(project.get("canonical_project_id") or "").strip()
    if cid and cid in catalog_by_id:
        return catalog_by_id[cid]
    for ref in project.get("referents", []) if isinstance(project.get("referents"), list) else []:
        if isinstance(ref, dict) and ref.get("kind") == "historical_canonical_id":
            hid = str(ref.get("value") or "").strip()
            if hid and hid in catalog_by_id:
                return catalog_by_id[hid]
    return None


def infer_primary_language(ctx_block: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """Infer dominant language from ``ctx.languages``, preferring code over config/docs."""
    if not isinstance(ctx_block, dict):
        return None, None
    langs = ctx_block.get("languages")
    if not isinstance(langs, dict) or not langs:
        return None, None
    valid_pairs = [
        (str(lang), int(count))
        for lang, count in langs.items()
        if str(lang).strip() and isinstance(count, int) and not isinstance(count, bool) and count > 0
    ]
    if not valid_pairs:
        return None, None
    code_pairs = [(lang, cnt) for lang, cnt in valid_pairs if lang not in _DATA_OR_DOC_LANGUAGES]
    if code_pairs:
        code_pairs.sort(key=lambda item: (-item[1], item[0].casefold()))
        return code_pairs[0][0], "CTX_PRIMARY_CODE_LANGUAGE"
    valid_pairs.sort(key=lambda item: (-item[1], item[0].casefold()))
    return valid_pairs[0][0], "CTX_PRIMARY_CONTENT_LANGUAGE"


def _extract_manifest_description(repo_dir: Path) -> str | None:
    pyproject = repo_dir / "pyproject.toml"
    if safe_path_is_file(pyproject):
        try:
            doc = tomllib.loads(pyproject.read_text(encoding="utf-8", errors="replace"))
            proj = doc.get("project") if isinstance(doc, dict) else None
            if isinstance(proj, dict):
                desc = _clean_single_line(proj.get("description"), max_chars=DESCRIPTION_MAX_CHARS)
                if desc and not is_noisy_summary_text(desc):
                    return desc
        except (OSError, tomllib.TOMLDecodeError):
            pass

    pkg_json = repo_dir / "package.json"
    if safe_path_is_file(pkg_json):
        try:
            doc = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
            if isinstance(doc, dict):
                desc = _clean_single_line(doc.get("description"), max_chars=DESCRIPTION_MAX_CHARS)
                if desc and not is_noisy_summary_text(desc):
                    return desc
        except (OSError, json.JSONDecodeError):
            pass

    cargo_toml = repo_dir / "Cargo.toml"
    if safe_path_is_file(cargo_toml):
        try:
            doc = tomllib.loads(cargo_toml.read_text(encoding="utf-8", errors="replace"))
            pkg = doc.get("package") if isinstance(doc, dict) else None
            if isinstance(pkg, dict):
                desc = _clean_single_line(pkg.get("description"), max_chars=DESCRIPTION_MAX_CHARS)
                if desc and not is_noisy_summary_text(desc):
                    return desc
        except (OSError, tomllib.TOMLDecodeError):
            pass

    return None


def _clean_md_prose_line(raw: str) -> str:
    text = raw.strip()
    if text.startswith(">"):
        text = text.lstrip(">").strip()
    if text.startswith(("- ", "* ")):
        text = text[2:].strip()
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _HTML_TAG_RE.sub("", text)
    text = re.sub(r"[*_`]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_markdown_sections(md_path: Path) -> dict[str, str]:
    """Return mapping of lowercased heading -> first clean prose paragraph in that section."""
    try:
        raw_bytes = md_path.read_bytes()
        text = (
            raw_bytes.decode("utf-16", errors="replace")
            if raw_bytes.startswith((b"\xff\xfe", b"\xfe\xff"))
            else raw_bytes.decode("utf-8-sig", errors="replace")
        ).replace("\x00", "")
    except OSError:
        return {}

    sections: dict[str, list[str]] = {"__lead__": []}
    current_heading = "__lead__"
    in_fence = False
    in_comment = False
    in_frontmatter = False

    for idx, raw_line in enumerate(text.splitlines()[:260]):
        stripped = raw_line.strip()
        if idx == 0 and stripped == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if stripped == "---":
                in_frontmatter = False
            continue
        if in_comment:
            if "-->" in stripped:
                in_comment = False
            continue
        if stripped.startswith("<!--"):
            if "-->" not in stripped:
                in_comment = True
            continue
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence or not stripped or stripped.startswith(("|", "---", "![", "[![")):
            continue
        if stripped.startswith("#"):
            heading_txt = _HTML_TAG_RE.sub("", stripped.lstrip("#")).strip().casefold()
            if heading_txt:
                current_heading = heading_txt
                sections.setdefault(current_heading, [])
            continue
        if stripped.lstrip(">").strip().startswith("#"):
            continue
        cleaned = _clean_md_prose_line(stripped)
        if not cleaned or is_noisy_summary_text(cleaned):
            continue
        sections.setdefault(current_heading, []).append(cleaned)

    out: dict[str, str] = {}
    for heading, lines in sections.items():
        if lines:
            out[heading] = lines[0]
    return out


def _synthesize_ctx_what_it_does(ctx_block: dict[str, Any] | None) -> str | None:
    if not isinstance(ctx_block, dict) or ctx_block.get("state") != "available":
        return None
    parts: list[str] = []
    langs = ctx_block.get("languages") if isinstance(ctx_block.get("languages"), dict) else {}
    if langs:
        top_langs = sorted(langs.items(), key=lambda kv: (-int(kv[1]), str(kv[0])))[:3]
        lang_str = ", ".join(f"{k} ({v})" for k, v in top_langs if int(v) > 0)
        if lang_str:
            parts.append(f"Repository surfaces {lang_str} files")
    entrypoints = [str(e) for e in (ctx_block.get("entrypoints") or [])[:3] if str(e).strip()]
    if entrypoints:
        parts.append("entrypoints " + ", ".join(entrypoints))
    surfaces = [
        f"{s.get('kind')}:{s.get('name')}"
        for s in (ctx_block.get("operational_surfaces") or [])[:4]
        if isinstance(s, dict) and s.get("name")
    ]
    if surfaces:
        parts.append("operational surfaces " + ", ".join(surfaces))
    if not parts:
        return None
    joined = "; ".join(parts) + "."
    return _clean_single_line(joined, max_chars=WHAT_IT_DOES_MAX_CHARS)


def extract_repo_doc_profile(
    repo_dir: Path | None,
    ctx_block: dict[str, Any] | None,
) -> dict[str, tuple[str | None, str | None]]:
    """Extract deterministic fallback ``(value, reason_code)`` tuples for uncurated repos."""
    desc_val: str | None = None
    desc_reason: str | None = None
    goal_val: str | None = None
    goal_reason: str | None = None
    what_val: str | None = None
    what_reason: str | None = None

    if repo_dir is not None and repo_dir.is_dir():
        manifest_desc = _extract_manifest_description(repo_dir)
        if manifest_desc:
            desc_val = manifest_desc
            desc_reason = "PACKAGE_MANIFEST_DESCRIPTION"

        readme_path = find_first_existing(repo_dir, README_CANDIDATES)
        sections = _parse_markdown_sections(readme_path) if readme_path else {}
        if not sections and safe_path_is_file(repo_dir / "AGENTS.md"):
            sections = _parse_markdown_sections(repo_dir / "AGENTS.md")

        for heading, para in sections.items():
            if heading == "__lead__":
                continue
            if goal_val is None and any(k in heading for k in _GOAL_HEADING_KEYWORDS):
                goal_val = _clean_single_line(para, max_chars=GOAL_MAX_CHARS)
                goal_reason = "README_GOAL_SECTION"
            if what_val is None and any(k in heading for k in _WHAT_HEADING_KEYWORDS):
                what_val = _clean_single_line(para, max_chars=WHAT_IT_DOES_MAX_CHARS)
                what_reason = "README_OVERVIEW_SECTION"

        lead_para = sections.get("__lead__")
        if desc_val is None and lead_para:
            desc_val = _clean_single_line(lead_para, max_chars=DESCRIPTION_MAX_CHARS)
            desc_reason = "README_LEAD_SUMMARY"
        if desc_val is None and what_val:
            desc_val = _clean_single_line(what_val, max_chars=DESCRIPTION_MAX_CHARS)
            desc_reason = what_reason or "README_OVERVIEW_SECTION"
        if desc_val is None and goal_val:
            desc_val = _clean_single_line(goal_val, max_chars=DESCRIPTION_MAX_CHARS)
            desc_reason = goal_reason or "README_GOAL_SECTION"

    if what_val is None:
        ctx_what = _synthesize_ctx_what_it_does(ctx_block)
        if ctx_what:
            what_val = ctx_what
            what_reason = "COMPILED_STRUCTURAL_CTX"

    if goal_val is None and desc_val:
        goal_val = _clean_single_line(desc_val, max_chars=GOAL_MAX_CHARS)
        goal_reason = desc_reason or "DERIVED_FROM_DESCRIPTION"

    if what_val is None and desc_val:
        what_val = _clean_single_line(desc_val, max_chars=WHAT_IT_DOES_MAX_CHARS)
        what_reason = desc_reason or "DERIVED_FROM_DESCRIPTION"

    lang_val, lang_reason = infer_primary_language(ctx_block)
    return {
        "description": (desc_val, desc_reason),
        "goal": (goal_val, goal_reason),
        "what_it_does": (what_val, what_reason),
        "primary_language": (lang_val, lang_reason),
    }


__all__ = [
    "DEFAULT_CATALOG_FILENAME",
    "DESCRIPTION_MAX_CHARS",
    "DISPLAY_NAME_MAX_CHARS",
    "GOAL_MAX_CHARS",
    "WHAT_IT_DOES_MAX_CHARS",
    "empty_project_catalog",
    "extract_repo_doc_profile",
    "index_project_catalog",
    "infer_primary_language",
    "is_noisy_summary_text",
    "load_project_catalog",
    "lookup_catalog_entry",
    "resolve_catalog_path",
]
