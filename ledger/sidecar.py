"""Versioned sidecar declaration validation and HEAD-drift sensing (programme P1.7).

Project-local sidecars (``.project-ledger.json`` / ``project-ledger.json``) are
human-editable *declaration* sources attached to an observation — never canonical
identity authority (Architectural Laws 1, 3, 4, 8).

Contract:

- Formal JSON Schema lives at ``schemas/project-ledger-sidecar.schema.json``.
- Supported ``schema_version``: ``"1"``, ``"1.0"``, ``"1.0.0"`` (or ``1`` / any ``1.x`` / ``1.x.y``),
  normalized to ``"1.0.0"``. Legacy unversioned sidecars are accepted with
  ``status = "legacy_unversioned"`` so existing checkouts do not churn.
- Unsupported major/schema versions or malformed/non-object JSON fail locally on
  that sidecar (``status = "invalid"``, ``declarations = {}``) without poisoning
  other observations or healthy estate compilation (Architectural Law 11).
- ``canonical_project_id`` is explicitly forbidden in sidecars until a registry-backed
  assignment/validation contract exists (BACKLOG.md P1.7).
- Validated sidecar fields are exposed explicitly as ``observation["sidecar"]["declarations"]``
  and any schema validation issue surfaces a bounded ``SIDECAR_DECLARATION_INVALID`` review
  item in ``state/review-queue.json``.
- Whenever declared commit/branch/timestamp metadata in a valid sidecar lags live
  ``git rev-parse HEAD`` in the checkout, ``validate_sidecar_file`` / ``load_sidecar``
  emits a structured ``stale_sidecar`` warning (``observation["sidecar"]["stale"] = True``,
  ``observation["sidecar"]["warnings"]``) without falsely invalidating schema-valid fields.
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path
from typing import Any

from .models import parse_iso_datetime
from .sources.git import git_output

SIDECAR_NORMALIZED_SCHEMA_VERSION = "1.0.0"
SIDECAR_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "schemas" / "project-ledger-sidecar.schema.json"
)
_V1_VERSION_RE = re.compile(r"^1(?:\.\d+){0,2}$")

FORBIDDEN_SIDECAR_FIELDS: frozenset[str] = frozenset(
    {
        "canonical_project_id",
    }
)

STRING_SIDECAR_FIELDS: frozenset[str] = frozenset(
    {
        "canonical_url",
        "commit",
        "description",
        "display_name",
        "gbrain_source",
        "gbrain_source_slug",
        "head_commit",
        "kind",
        "last_push_at",
        "last_session_at",
        "last_session_summary",
        "lifecycle_state",
        "machine_name",
        "name",
        "next_step",
        "notes",
        "owner",
        "path",
        "primary_language",
        "project_key",
        "project_type",
        "purpose",
        "repo_name",
        "status",
        "storage_scope",
        "updated_at",
    }
)

BOOL_SIDECAR_FIELDS: frozenset[str] = frozenset(
    {
        "shared",
    }
)

STRING_LIST_SIDECAR_FIELDS: frozenset[str] = frozenset(
    {
        "aliases",
        "tags",
    }
)

OBJECT_SIDECAR_FIELDS: frozenset[str] = frozenset(
    {
        "git",
    }
)

GIT_OBJECT_STRING_FIELDS: frozenset[str] = frozenset(
    {
        "branch",
        "commit",
        "last_commit",
        "remote",
    }
)

GIT_OBJECT_BOOL_FIELDS: frozenset[str] = frozenset(
    {
        "dirty",
        "is_git",
    }
)

GIT_OBJECT_INT_FIELDS: frozenset[str] = frozenset(
    {
        "dirty_count",
    }
)

KNOWN_GIT_OBJECT_FIELDS: frozenset[str] = (
    GIT_OBJECT_STRING_FIELDS | GIT_OBJECT_BOOL_FIELDS | GIT_OBJECT_INT_FIELDS
)

KNOWN_SIDECAR_FIELDS: frozenset[str] = (
    frozenset({"schema_version"})
    | STRING_SIDECAR_FIELDS
    | BOOL_SIDECAR_FIELDS
    | STRING_LIST_SIDECAR_FIELDS
    | OBJECT_SIDECAR_FIELDS
)


@functools.lru_cache(maxsize=1)
def load_sidecar_schema() -> dict[str, Any]:
    """Load and cache ``schemas/project-ledger-sidecar.schema.json``."""
    return json.loads(SIDECAR_SCHEMA_PATH.read_text(encoding="utf-8"))


def absent_sidecar_envelope() -> dict[str, Any]:
    return {
        "present": False,
        "path": None,
        "status": "absent",
        "declared_schema_version": None,
        "normalized_schema_version": None,
        "stale": False,
        "declarations": {},
        "issues": [],
        "warnings": [],
    }


def _validate_schema_version(
    raw_version: Any,
) -> tuple[bool, str | None, str | None, dict[str, Any] | None]:
    """Return ``(ok, declared_version, normalized_version, error_issue)``."""
    if raw_version is None or (isinstance(raw_version, str) and not raw_version.strip()):
        return (True, None, SIDECAR_NORMALIZED_SCHEMA_VERSION, None)

    if isinstance(raw_version, bool):
        return (
            False,
            str(raw_version),
            None,
            {
                "code": "SIDECAR_UNSUPPORTED_VERSION",
                "field": "schema_version",
                "detail": f"Unsupported sidecar schema_version {raw_version!r}; expected '1.0' or '1.0.0'.",
            },
        )

    if isinstance(raw_version, int):
        version_text = str(raw_version)
    elif isinstance(raw_version, str):
        version_text = raw_version.strip()
    else:
        return (
            False,
            repr(raw_version),
            None,
            {
                "code": "SIDECAR_UNSUPPORTED_VERSION",
                "field": "schema_version",
                "detail": f"Sidecar schema_version must be a string (got {type(raw_version).__name__}).",
            },
        )

    if _V1_VERSION_RE.match(version_text):
        return (True, version_text, SIDECAR_NORMALIZED_SCHEMA_VERSION, None)

    return (
        False,
        version_text,
        None,
        {
            "code": "SIDECAR_UNSUPPORTED_VERSION",
            "field": "schema_version",
            "detail": (
                f"Unsupported sidecar schema_version {version_text!r}; "
                "only major version 1 ('1', '1.0', '1.0.0') is supported."
            ),
        },
    )


def _validate_git_subobject(raw_git: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate nested ``git`` metadata object against ``project-ledger-sidecar.schema.json``."""
    cleaned: dict[str, Any] = {}
    issues: list[dict[str, Any]] = []
    for subkey in sorted(raw_git.keys()):
        subval = raw_git[subkey]
        field_ref = f"git.{subkey}"
        if subkey not in KNOWN_GIT_OBJECT_FIELDS:
            issues.append(
                {
                    "code": "SIDECAR_UNKNOWN_FIELD",
                    "field": field_ref,
                    "detail": f"Unrecognized sidecar field {field_ref!r} in schema v1.",
                }
            )
            continue
        if subval is None:
            continue
        if subkey in GIT_OBJECT_STRING_FIELDS:
            if not isinstance(subval, str):
                issues.append(
                    {
                        "code": "SIDECAR_INVALID_FIELD_TYPE",
                        "field": field_ref,
                        "detail": f"Sidecar field {field_ref!r} must be a string (got {type(subval).__name__}).",
                    }
                )
                continue
            stripped = subval.strip()
            if stripped:
                cleaned[subkey] = stripped
        elif subkey in GIT_OBJECT_BOOL_FIELDS:
            if not isinstance(subval, bool):
                issues.append(
                    {
                        "code": "SIDECAR_INVALID_FIELD_TYPE",
                        "field": field_ref,
                        "detail": f"Sidecar field {field_ref!r} must be a boolean (got {type(subval).__name__}).",
                    }
                )
                continue
            cleaned[subkey] = subval
        elif subkey in GIT_OBJECT_INT_FIELDS:
            if isinstance(subval, bool) or not isinstance(subval, int) or subval < 0:
                issues.append(
                    {
                        "code": "SIDECAR_INVALID_FIELD_TYPE",
                        "field": field_ref,
                        "detail": f"Sidecar field {field_ref!r} must be a non-negative integer.",
                    }
                )
                continue
            cleaned[subkey] = subval
    return cleaned, issues


def _commits_match(declared_commit: str, live_head: str) -> bool:
    dc = declared_commit.strip().lower()
    lh = live_head.strip().lower()
    if not dc or not lh or dc in {"none", "null", "unknown"}:
        return False
    return lh.startswith(dc) or dc.startswith(lh)


def check_sidecar_head_drift(
    sidecar_path: Path,
    declarations: dict[str, Any],
    *,
    repo_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Compare declared sidecar commit/branch/timestamp metadata against live ``git rev-parse HEAD``.

    Emits a ``stale_sidecar`` warning dictionary whenever:
    1. Declared ``git.commit`` / ``head_commit`` / ``commit`` lags or mismatches live ``HEAD``;
    2. Declared ``git.branch`` mismatches live checked-out branch (when not detached);
    3. Declared ``updated_at`` / ``last_session_at`` timestamp precedes live ``HEAD``'s commit
       timestamp AND the sidecar file itself was not modified in ``HEAD``.
    """
    target_dir = repo_dir if repo_dir is not None else sidecar_path.parent
    if not (target_dir / ".git").exists():
        return []

    live_head = git_output(target_dir, "rev-parse", "HEAD")
    if not live_head:
        return []

    live_head_at = git_output(target_dir, "log", "-1", "--format=%cI", "HEAD")
    live_branch = git_output(target_dir, "rev-parse", "--abbrev-ref", "HEAD")

    git_decl = declarations.get("git") if isinstance(declarations.get("git"), dict) else {}
    declared_commit = str(
        git_decl.get("commit")
        or declarations.get("head_commit")
        or declarations.get("commit")
        or ""
    ).strip()
    declared_branch = str(git_decl.get("branch") or "").strip()
    declared_at = str(
        declarations.get("updated_at") or declarations.get("last_session_at") or ""
    ).strip()

    warnings: list[dict[str, Any]] = []

    if declared_commit and not _commits_match(declared_commit, live_head):
        warnings.append(
            {
                "code": "stale_sidecar",
                "reason": "declared_commit_lags_head",
                "declared_commit": declared_commit,
                "head_commit": live_head,
                "declared_at": declared_at or None,
                "head_commit_at": live_head_at or None,
                "detail": (
                    f"stale_sidecar: declared commit {declared_commit!r} in {sidecar_path.name} "
                    f"lags live HEAD {live_head[:12]}"
                ),
            }
        )
        return warnings

    if (
        declared_branch
        and declared_branch.lower() not in {"none", "null", "head"}
        and live_branch
        and live_branch != "HEAD"
        and declared_branch != live_branch
    ):
        warnings.append(
            {
                "code": "stale_sidecar",
                "reason": "declared_branch_lags_head",
                "declared_branch": declared_branch,
                "head_branch": live_branch,
                "declared_commit": declared_commit or None,
                "head_commit": live_head,
                "declared_at": declared_at or None,
                "head_commit_at": live_head_at or None,
                "detail": (
                    f"stale_sidecar: declared branch {declared_branch!r} in {sidecar_path.name} "
                    f"differs from live branch {live_branch!r} at HEAD {live_head[:12]}"
                ),
            }
        )
        return warnings

    if declared_at and live_head_at and not declared_commit:
        declared_epoch = parse_iso_datetime(declared_at)
        head_epoch = parse_iso_datetime(live_head_at)
        if declared_epoch is not None and head_epoch is not None and declared_epoch < head_epoch:
            sidecar_last_commit = git_output(
                target_dir, "log", "-1", "-m", "--format=%H", "--", str(sidecar_path)
            )
            if sidecar_last_commit.strip().lower() != live_head.strip().lower():
                warnings.append(
                    {
                        "code": "stale_sidecar",
                        "reason": "declared_metadata_lags_head",
                        "declared_commit": None,
                        "head_commit": live_head,
                        "declared_at": declared_at,
                        "head_commit_at": live_head_at,
                        "detail": (
                            f"stale_sidecar: declared timestamp {declared_at} in {sidecar_path.name} "
                            f"lags live HEAD {live_head[:12]} ({live_head_at})"
                        ),
                    }
                )

    return warnings


def validate_sidecar_mapping(
    raw: Any,
    *,
    sidecar_path: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate a parsed JSON value from a sidecar against ``project-ledger-sidecar.schema.json``.

    Returns ``(envelope, accepted_fields)`` where:
    - ``envelope`` is the durable ``observation["sidecar"]`` metadata record;
    - ``accepted_fields`` contains only schema-valid fields safe for scanner/compiler
      declaration consumption.
    """
    schema = load_sidecar_schema()
    schema_props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    forbidden_fields = frozenset(schema.get("x-forbidden-fields") or FORBIDDEN_SIDECAR_FIELDS)

    if not isinstance(raw, dict):
        issue = {
            "code": "SIDECAR_MALFORMED_JSON",
            "field": None,
            "detail": f"Sidecar root must be a JSON object (got {type(raw).__name__}).",
        }
        return (
            {
                "present": True,
                "path": sidecar_path,
                "status": "invalid",
                "declared_schema_version": None,
                "normalized_schema_version": None,
                "stale": False,
                "declarations": {},
                "issues": [issue],
                "warnings": [],
            },
            {},
        )

    ver_ok, declared_ver, normalized_ver, ver_issue = _validate_schema_version(
        raw.get("schema_version")
    )
    if not ver_ok:
        assert ver_issue is not None
        return (
            {
                "present": True,
                "path": sidecar_path,
                "status": "invalid",
                "declared_schema_version": declared_ver,
                "normalized_schema_version": None,
                "stale": False,
                "declarations": {},
                "issues": [ver_issue],
                "warnings": [],
            },
            {},
        )

    issues: list[dict[str, Any]] = []
    accepted: dict[str, Any] = {}
    declarations: dict[str, Any] = {}

    for key in sorted(raw.keys()):
        if key == "schema_version":
            continue
        val = raw[key]

        if key in forbidden_fields:
            issues.append(
                {
                    "code": "SIDECAR_FORBIDDEN_FIELD",
                    "field": key,
                    "detail": (
                        f"Sidecar field {key!r} is forbidden; canonical identity is "
                        "assigned by Project Ledger compilation, not project-local sidecars."
                    ),
                }
            )
            continue

        if key not in KNOWN_SIDECAR_FIELDS or (schema_props and key not in schema_props):
            issues.append(
                {
                    "code": "SIDECAR_UNKNOWN_FIELD",
                    "field": key,
                    "detail": f"Unrecognized sidecar field {key!r} in schema v1.",
                }
            )
            continue

        if val is None:
            continue

        if key in STRING_SIDECAR_FIELDS:
            if not isinstance(val, str):
                issues.append(
                    {
                        "code": "SIDECAR_INVALID_FIELD_TYPE",
                        "field": key,
                        "detail": f"Sidecar field {key!r} must be a string (got {type(val).__name__}).",
                    }
                )
                continue
            accepted[key] = val
            stripped = val.strip()
            if stripped:
                declarations[key] = stripped
            continue

        if key in BOOL_SIDECAR_FIELDS:
            if not isinstance(val, bool):
                issues.append(
                    {
                        "code": "SIDECAR_INVALID_FIELD_TYPE",
                        "field": key,
                        "detail": f"Sidecar field {key!r} must be a boolean (got {type(val).__name__}).",
                    }
                )
                continue
            accepted[key] = val
            declarations[key] = val
            continue

        if key in STRING_LIST_SIDECAR_FIELDS:
            if not isinstance(val, list) or any(not isinstance(item, str) for item in val):
                issues.append(
                    {
                        "code": "SIDECAR_INVALID_FIELD_TYPE",
                        "field": key,
                        "detail": f"Sidecar field {key!r} must be an array of strings.",
                    }
                )
                continue
            cleaned = [item.strip() for item in val if item.strip()]
            accepted[key] = cleaned
            if cleaned:
                declarations[key] = cleaned
            continue

        if key in OBJECT_SIDECAR_FIELDS:
            if not isinstance(val, dict):
                issues.append(
                    {
                        "code": "SIDECAR_INVALID_FIELD_TYPE",
                        "field": key,
                        "detail": f"Sidecar field {key!r} must be a JSON object (got {type(val).__name__}).",
                    }
                )
                continue
            if key == "git":
                cleaned_git, git_issues = _validate_git_subobject(val)
                issues.extend(git_issues)
                if cleaned_git:
                    accepted[key] = cleaned_git
                    declarations[key] = cleaned_git
            else:
                accepted[key] = val
                if val:
                    declarations[key] = val
            continue

    if issues:
        status = "invalid"
    elif declared_ver is None:
        status = "legacy_unversioned"
    else:
        status = "valid"

    envelope = {
        "present": True,
        "path": sidecar_path,
        "status": status,
        "declared_schema_version": declared_ver,
        "normalized_schema_version": normalized_ver,
        "stale": False,
        "declarations": declarations,
        "issues": issues,
        "warnings": [],
    }
    return (envelope, accepted)


def validate_sidecar_file(
    sidecar_path: Path | None,
    *,
    repo_dir: Path | None = None,
    check_head_drift: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read and validate a sidecar file on disk, returning ``(envelope, accepted_fields)``."""
    if sidecar_path is None:
        return (absent_sidecar_envelope(), {})

    resolved_str = str(sidecar_path)
    try:
        raw_text = sidecar_path.read_text(encoding="utf-8")
    except OSError as exc:
        issue = {
            "code": "SIDECAR_UNREADABLE",
            "field": None,
            "detail": f"Could not read sidecar {resolved_str}: {exc}",
        }
        return (
            {
                "present": True,
                "path": resolved_str,
                "status": "invalid",
                "declared_schema_version": None,
                "normalized_schema_version": None,
                "stale": False,
                "declarations": {},
                "issues": [issue],
                "warnings": [],
            },
            {},
        )

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        issue = {
            "code": "SIDECAR_MALFORMED_JSON",
            "field": None,
            "detail": f"Invalid JSON in sidecar {resolved_str}: {exc}",
        }
        return (
            {
                "present": True,
                "path": resolved_str,
                "status": "invalid",
                "declared_schema_version": None,
                "normalized_schema_version": None,
                "stale": False,
                "declarations": {},
                "issues": [issue],
                "warnings": [],
            },
            {},
        )

    envelope, accepted = validate_sidecar_mapping(parsed, sidecar_path=resolved_str)
    if check_head_drift and envelope["declarations"]:
        drift_warnings = check_sidecar_head_drift(
            sidecar_path,
            envelope["declarations"],
            repo_dir=repo_dir,
        )
        if drift_warnings:
            envelope["stale"] = True
            envelope["warnings"] = drift_warnings
    return (envelope, accepted)


def inspect_entry_sidecar(compat_entry: dict[str, Any]) -> dict[str, Any]:
    """Derive the ``observation["sidecar"]`` declaration envelope for a compat entry."""
    if "_sidecar_envelope" in compat_entry and isinstance(compat_entry["_sidecar_envelope"], dict):
        return compat_entry["_sidecar_envelope"]

    raw_path = str(compat_entry.get("sidecar_path") or "").strip()
    if not raw_path:
        return absent_sidecar_envelope()

    path = Path(raw_path)
    if path.exists():
        envelope, _ = validate_sidecar_file(path)
        return envelope

    # Historical/off-host fixture where sidecar_path was recorded in compat output
    # without the underlying file mounted locally: synthesize declared values from compat_entry
    # only if include_reason did not record a sidecar-error.
    include_reason = str(compat_entry.get("include_reason") or "")
    if "sidecar-error" in include_reason:
        return {
            "present": True,
            "path": raw_path,
            "status": "invalid",
            "declared_schema_version": None,
            "normalized_schema_version": None,
            "stale": False,
            "declarations": {},
            "issues": [
                {
                    "code": "SIDECAR_MALFORMED_JSON",
                    "field": None,
                    "detail": f"Compatibility entry recorded sidecar-error for {raw_path}.",
                }
            ],
            "warnings": [],
        }

    declarations: dict[str, Any] = {}
    for compat_key, decl_key in (
        ("project_key", "project_key"),
        ("name", "display_name"),
        ("status", "status"),
        ("description", "description"),
        ("next_step", "next_step"),
        ("last_session_at", "last_session_at"),
        ("last_session_summary", "last_session_summary"),
        ("last_push_at", "last_push_at"),
    ):
        val = str(compat_entry.get(compat_key) or "").strip()
        if val:
            declarations[decl_key] = val
    raw_tags = compat_entry.get("tags")
    if isinstance(raw_tags, list) and raw_tags:
        declarations["tags"] = [str(t).strip() for t in raw_tags if str(t).strip()]

    stale_flag = "stale_sidecar" in include_reason
    warnings = (
        [
            {
                "code": "stale_sidecar",
                "reason": "compat_include_reason",
                "detail": f"stale_sidecar: recorded on compatibility entry for {raw_path}",
            }
        ]
        if stale_flag
        else []
    )
    return {
        "present": True,
        "path": raw_path,
        "status": "unmounted_compat",
        "declared_schema_version": None,
        "normalized_schema_version": SIDECAR_NORMALIZED_SCHEMA_VERSION,
        "stale": stale_flag,
        "declarations": declarations,
        "issues": [],
        "warnings": warnings,
    }
