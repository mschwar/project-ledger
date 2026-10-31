"""Versioned sidecar declaration validation and normalization (programme P1.7).

Project-local sidecars (``.project-ledger.json`` / ``project-ledger.json``) are
human-editable *declaration* sources attached to an observation — never canonical
identity authority (Architectural Laws 1, 3, 4, 8).

Contract:

- Supported ``schema_version``: ``"1"``, ``"1.0"``, ``"1.0.0"`` (or ``1`` / any ``1.x`` / ``1.x.y``),
  normalized to ``"1.0.0"``. Legacy unversioned sidecars are accepted with
  ``status = "legacy_unversioned"`` so existing checkouts do not churn.
- Unsupported major/schema versions or malformed/non-object JSON fail locally on
  that sidecar (``status = "invalid"``, ``declarations = {}``) without poisoning
  other observations or healthy estate compilation (Architectural Law 11).
- ``canonical_project_id`` is explicitly forbidden in sidecars until a registry-backed
  assignment/validation contract exists (BACKLOG.md P1.7).
- Validated sidecar fields are exposed explicitly as ``observation["sidecar"]["declarations"]``
  and any validation issue surfaces a bounded ``SIDECAR_DECLARATION_INVALID`` review
  item in ``state/review-queue.json``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SIDECAR_NORMALIZED_SCHEMA_VERSION = "1.0.0"
_V1_VERSION_RE = re.compile(r"^1(?:\.\d+){0,2}$")

FORBIDDEN_SIDECAR_FIELDS: frozenset[str] = frozenset(
    {
        "canonical_project_id",
    }
)

STRING_SIDECAR_FIELDS: frozenset[str] = frozenset(
    {
        "canonical_url",
        "description",
        "display_name",
        "gbrain_source",
        "gbrain_source_slug",
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

KNOWN_SIDECAR_FIELDS: frozenset[str] = (
    frozenset({"schema_version"})
    | STRING_SIDECAR_FIELDS
    | BOOL_SIDECAR_FIELDS
    | STRING_LIST_SIDECAR_FIELDS
    | OBJECT_SIDECAR_FIELDS
)


def absent_sidecar_envelope() -> dict[str, Any]:
    return {
        "present": False,
        "path": None,
        "status": "absent",
        "declared_schema_version": None,
        "normalized_schema_version": None,
        "declarations": {},
        "issues": [],
    }


def _validate_schema_version(raw_version: Any) -> tuple[bool, str | None, str | None, dict[str, Any] | None]:
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


def validate_sidecar_mapping(
    raw: Any,
    *,
    sidecar_path: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate a parsed JSON value from a sidecar.

    Returns ``(envelope, accepted_fields)`` where:
    - ``envelope`` is the durable ``observation["sidecar"]`` metadata record;
    - ``accepted_fields`` contains only schema-valid fields safe for scanner/compiler
      declaration consumption.
    """
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
                "declarations": {},
                "issues": [issue],
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
                "declarations": {},
                "issues": [ver_issue],
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

        if key in FORBIDDEN_SIDECAR_FIELDS:
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

        if key not in KNOWN_SIDECAR_FIELDS:
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
        "declarations": declarations,
        "issues": issues,
    }
    return (envelope, accepted)


def validate_sidecar_file(sidecar_path: Path | None) -> tuple[dict[str, Any], dict[str, Any]]:
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
                "declarations": {},
                "issues": [issue],
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
                "declarations": {},
                "issues": [issue],
            },
            {},
        )

    return validate_sidecar_mapping(parsed, sidecar_path=resolved_str)


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
            "declarations": {},
            "issues": [
                {
                    "code": "SIDECAR_MALFORMED_JSON",
                    "field": None,
                    "detail": f"Compatibility entry recorded sidecar-error for {raw_path}.",
                }
            ],
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

    return {
        "present": True,
        "path": raw_path,
        "status": "unmounted_compat",
        "declared_schema_version": None,
        "normalized_schema_version": SIDECAR_NORMALIZED_SCHEMA_VERSION,
        "declarations": declarations,
        "issues": [],
    }
