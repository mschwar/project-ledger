"""Minimal epistemic claim surface (programme P1.4).

A *claim* is a typed statement about an observation: what a source *observed* vs.
what an attached declaration source (sidecar) *declares*. This module deliberately
delivers only what canonical identity needs immediately — a declared-value claim
envelope and divergence detection — not a generalized knowledge framework
(SCHEMA.md §2.2 is the broader direction; only the minimal surface is implemented).

Claim kinds (SCHEMA.md §2.2)::

    declared  — an attached declaration source (sidecar) asserts a value
    observed  — the source observed a native fact
    inferred  — the compiler derived a value (reserved; confidence-first, none
                emitted in this minimal slice)

The immediate consumer is the identity compiler's ``DIVERGENT_SIDECAR_DECLARATIONS``
review: when two observations that merged into one canonical project carry
conflicting sidecar ``project_key`` declarations, the compiler surfaces a bounded
review instead of silently letting remote authority absorb the ambiguity
(SCHEMA.md §6: "emit claims/conflict/review; do not use last-write-wins").

Claims here are descriptive evidence, not merge authority — they never decide which
observations belong to which project.
"""

from __future__ import annotations

from .ids import stable_id
from .identity import observation_facts

CLAIM_KIND_DECLARED = "declared"
CLAIM_KIND_OBSERVED = "observed"
CLAIM_KIND_INFERRED = "inferred"
CLAIM_KINDS = frozenset({CLAIM_KIND_DECLARED, CLAIM_KIND_OBSERVED, CLAIM_KIND_INFERRED})

# The canonical identity fields a sidecar may declare. Today only ``project_key``
# affects identity resolution, so it is the only field divergence detection tracks.
DIVERGENCE_FIELDS = frozenset({"project_key"})


def sidecar_declaration_claim(observation: dict) -> dict | None:
    """Build the typed ``declared`` claim an observation's sidecar makes, or None.

    A sidecar-declared value (e.g. ``project_key``) is a *declaration* attached to a
    single observation. It is weak referential evidence — never identity authority —
    and carries its observation provenance so the compiler can surface conflicts
    instead of choosing a value by last-write-wins.
    """
    facts = observation_facts(observation)
    values: dict[str, str] = {}
    if facts["project_key"]:
        values["project_key"] = facts["project_key"]
    if not values:
        return None
    value = sorted(values.items())[0]  # deterministic: only project_key today
    field, value_text = value
    return {
        "claim_id": stable_id(
            "claim", CLAIM_KIND_DECLARED, facts["observation_id"], field, value_text
        ),
        "claim_kind": CLAIM_KIND_DECLARED,
        "subject_type": "observation",
        "subject_id": facts["observation_id"],
        "field": field,
        "value": value_text,
        "source_id": facts["source_id"],
        "observed_at": str(observation.get("observed_at") or ""),
    }


def divergent_declarations(
    observations: list[dict],
    *,
    fields: set[str] | frozenset[str] = DIVERGENCE_FIELDS,
) -> list[dict]:
    """Return a conflicting-declaration claim set, or [] when not divergent.

    A set of observations is *divergent* when at least two of them declare different
    values for the same identity field (``project_key`` today). The returned claims
    are the typed ``declared`` claims for every observation that declared a value for
    any divergent field — the operator must choose (e.g. via a ``canonical_key``
    decision) rather than the compiler picking by last-write-wins.
    """
    claims_by_field: dict[str, dict[str, str]] = {}
    label_by_field: dict[str, str] = {}
    selected: list[dict] = []
    used_subjects: set[str] = set()
    for observation in observations:
        claim = sidecar_declaration_claim(observation)
        if claim is None:
            continue
        field = str(claim["field"])
        if field not in fields:
            continue
        value = str(claim["value"]).casefold()
        prior_value = claims_by_field.setdefault(field, {}).get(
            str(claim["subject_id"])
        )
        label_by_field.setdefault(field, str(claim["value"]))
        if prior_value is None:
            claims_by_field[field][str(claim["subject_id"])] = value
        subject = str(claim["subject_id"])
        if subject not in used_subjects:
            selected.append(claim)
            used_subjects.add(subject)

    divergent: list[dict] = []
    for field in sorted(fields):
        seen_values = set(claims_by_field.get(field, {}).values())
        if len(seen_values) > 1:
            divergent.extend(claim for claim in selected if claim["field"] == field)
    # Deterministic ordering for stable review evidence.
    divergent.sort(key=lambda claim: (str(claim["field"]), str(claim["subject_id"])))
    return divergent