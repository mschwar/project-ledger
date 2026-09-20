"""Frozen compatibility observation contract (P1.1).

Defines the v0 flat compatibility contract emitted by ``build_ledger.py`` and
read at the compile boundary, plus the explicit major/minor version policy.

The contract is frozen so a future or external producer cannot silently change
field meanings and have the compiler "guess through" an unsupported shape. The
policy (see ``docs/COMPATIBILITY.md``):

- the current supported contract is ``COMPAT_FLAT_SCHEMA_VERSION`` (v0);
- a payload that declares a different ``format`` is rejected
  (``COMPAT_FORMAT_UNSUPPORTED``);
- a payload that declares a schema with a different **major** is rejected
  explicitly (``COMPAT_MAJOR_UNSUPPORTED``) rather than guessed through;
- a payload that declares a same-major, newer **minor** is tolerated (minor
  bumps are backward-compatible additive changes by the semver policy);
- a payload with no ``schema_version`` is treated as the supported v0 contract
  (the current ``build_ledger.py`` emits no version marker).
"""

from __future__ import annotations

from . import COMPAT_FLAT_SCHEMA_VERSION

# Stable format identifier for the flat compatibility output.
COMPAT_FORMAT = "project-ledger-compat"

# Envelope keys the v0 contract recognizes on the compatibility output. All are
# optional on v0 input except `entries` (validated by the compile boundary).
# Unknown keys are tolerated on read (additive extensions are forward-safe).
ENVELOPE_KEYS = frozenset(
    {
        "generated_at",
        "config_path",
        "entry_count",
        "format",
        "schema_version",
        "entries",
    }
)


class LedgerCompatError(ValueError):
    """Stable-code error for the compatibility observation contract.

    Extends :class:`ValueError` so it is caught by existing broad error handling,
    and carries a stable machine-readable ``code`` for the CLI surface.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _supported_major() -> int:
    return int(str(COMPAT_FLAT_SCHEMA_VERSION).split(".", 1)[0])


def _parse_major(text: str) -> int | None:
    """Parse the major component of a semantic-version string safely.

    Returns ``None`` when the text is not a valid ``N[.M[.p]]`` semver shape,
    so the caller can reject the version as unsupported rather than guess.
    """
    candidate = str(text).strip().split(".")[0].strip()
    if not candidate or not candidate.isdigit():
        return None
    return int(candidate)


def check_compat_schema(payload: dict) -> None:
    """Validate a compatibility output payload against the frozen v0 contract.

    Raises :class:`LedgerCompatError` with a stable code when the payload
    declares an unsupported format or an unsupported schema major. This is the
    explicit unsupported-major behavior: refuse to compile an observation set
    whose field semantics could differ from this build, instead of guessing.
    Absent version markers are treated as the supported v0 contract.
    """
    declared_format = str(payload.get("format", "")).strip()
    if declared_format and declared_format != COMPAT_FORMAT:
        raise LedgerCompatError(
            "COMPAT_FORMAT_UNSUPPORTED",
            f"Compatibility output declares format={declared_format!r}; "
            f"this build understands {COMPAT_FORMAT!r}.",
        )

    declared = payload.get("schema_version")
    if declared is None:
        return  # No version marker is the current v0 producer's output.
    text = str(declared).strip()
    if not text:
        return
    major = _parse_major(text)
    if major is None or major != _supported_major():
        raise LedgerCompatError(
            "COMPAT_MAJOR_UNSUPPORTED",
            f"Compatibility output declares schema_version={text!r}; "
            f"this build supports {COMPAT_FLAT_SCHEMA_VERSION} (major {_supported_major()}). "
            "Refusing to guess through an unsupported major version.",
        )