"""Identity evidence model (programme P2.3).

Records and scores the identity evidence an observation or a canonical project
carries, so agents can see *why* observations unified (or did not) without re-running
archaeology.

Scope and authority (deliberately conservative):

- This module classifies and records evidence; it does **not** change the identity
  compiler's merge authority. Exactly one automatic merge rule remains: an exact
  normalized repository remote. Explicit merge/split/reject decisions remain durable
  compiler inputs.
- Weak name / semantic / locator similarity is recorded and *clearly marked weak*; it
  is never treated as identity authority and never auto-merges.
- Negative / conflict evidence (split, reject_match, conflicting decisions) is recorded
  as a first-class evidence kind with strength ``negative``.

Determinism: every evidence record carries a stable ``identity_evidence_id`` derived
from its kind, member observations, and value, so scoring is reproducible across
compiles (SCHEMA.md §2.3).
"""

from __future__ import annotations

from itertools import combinations

from . import COMPILER_VERSION, IDENTITY_EVIDENCE_SCHEMA_VERSION
from .ids import stable_id
from .identity import observation_facts

# Strength is the scoring axis of the model: strong (authoritative identity signal),
# weak (referential/indicative only, never auto-merging), negative (explicitly keeps
# observations apart).
STRENGTH_STRONG = "strong"
STRENGTH_WEAK = "weak"
STRENGTH_NEGATIVE = "negative"
EVIDENCE_STRENGTHS = frozenset({STRENGTH_STRONG, STRENGTH_WEAK, STRENGTH_NEGATIVE})

# kind -> default strength classification. Weak name/semantic kinds are deliberately
# plain to see here; they can never authorize a merge on their own.
KIND_STRENGTH = {
    # Strong identity signals.
    "normalized_remote": STRENGTH_STRONG,  # exact normalized repository remote (auto-merge authority)
    "source_native_identity": STRENGTH_STRONG,  # manifestation anchored to source+snapshot
    "explicit_merge_decision": STRENGTH_STRONG,  # operator-approved union
    # Weak referential/declaration evidence (referents, not identity authority).
    "explicit_project_key": STRENGTH_WEAK,  # compatibility project_key declaration/hint
    "raw_url": STRENGTH_WEAK,  # non-normalizable network locator
    "path_locator": STRENGTH_WEAK,  # filesystem path
    "path_migration": STRENGTH_WEAK,  # renamed/moved path alias
    "display_name": STRENGTH_WEAK,  # name may collide across unrelated projects
    "readme_compound": STRENGTH_WEAK,  # readme/description/repo-nature presence
    "name_similarity": STRENGTH_WEAK,  # same-name/locator overlap, not identity
    "semantic_similarity": STRENGTH_WEAK,  # fuzzy/description overlap, not identity
    "cross_project_weak_overlap": STRENGTH_WEAK,  # weak hit between distinct projects
    # Negative / conflict evidence.
    "negative_decision": STRENGTH_NEGATIVE,  # explicit split / reject_match
    "conflicting_decision": STRENGTH_NEGATIVE,  # positive decision blocked by a negative one
}

# Evidence that is only ever a referent (never a merge authority).
_REFERENT_ONLY_KINDS = frozenset(
    {
        "explicit_project_key",
        "raw_url",
        "path_locator",
        "path_migration",
        "display_name",
        "readme_compound",
        "name_similarity",
        "semantic_similarity",
        "cross_project_weak_overlap",
    }
)

# Reason codes explaining why each evidence kind is present.
REASON_BY_KIND = {
    "normalized_remote": "EXACT_NORMALIZED_REMOTE",
    "source_native_identity": "SOURCE_NATIVE_IDENTITY",
    "explicit_merge_decision": "EXPLICIT_MERGE_DECISION",
    "explicit_project_key": "PROJECT_KEY_DECLARATION",
    "raw_url": "RAW_URL_REFERENT",
    "path_locator": "PATH_LOCATOR",
    "path_migration": "PATH_MIGRATION",
    "display_name": "DISPLAY_NAME",
    "readme_compound": "README_OR_REPO_COMPOUND",
    "name_similarity": "WEAK_NAME_SEMANTIC_OVERLAP",
    "semantic_similarity": "WEAK_NAME_SEMANTIC_OVERLAP",
    "cross_project_weak_overlap": "WEAK_NAME_SEMANTIC_OVERLAP",
    "negative_decision": "EXPLICIT_NEGATIVE_DECISION",
    "conflicting_decision": "DECISION_CONFLICT",
}

AUTHORITY_OBSERVED = "observed"
AUTHORITY_DECLARED = "declared"
AUTHORITY_DECISION = "decision"


def _fold(value: str | None) -> str:
    return (str(value or "").strip()).casefold()


def evidence_kinds() -> dict[str, dict]:
    """The identity evidence taxonomy: kind -> {strength, reason_code, referent_only}."""
    return {
        kind: {
            "strength": KIND_STRENGTH[kind],
            "reason_code": REASON_BY_KIND[kind],
            "referent_only": kind in _REFERENT_ONLY_KINDS,
        }
        for kind in sorted(KIND_STRENGTH)
    }


def evidence_strength(kind: str) -> str:
    """Score a single evidence kind as strong / weak / negative."""
    try:
        return KIND_STRENGTH[kind]
    except (
        KeyError
    ) as exc:  # pragma: no cover - defensive guard against typos/regression.
        raise ValueError(f"Unknown identity evidence kind: {kind}") from exc


def evidence_record(
    kind: str,
    observation_ids: list[str],
    *,
    strength: str | None = None,
    authority: str = AUTHORITY_OBSERVED,
    reason_code: str | None = None,
    value: str | None = None,
    detail: str | None = None,
) -> dict:
    """Build one deterministic identity evidence record (SCHEMA.md §2.3)."""
    member_ids = sorted(set(observation_ids))
    resolved_strength = strength or KIND_STRENGTH[kind]
    if resolved_strength not in EVIDENCE_STRENGTHS:  # pragma: no cover - defensive.
        raise ValueError(f"Unsupported evidence strength: {resolved_strength}")
    record: dict[str, object] = {
        "identity_evidence_id": stable_id(
            "ev", kind, member_ids, value or reason_code or ""
        ),
        "kind": kind,
        "strength": resolved_strength,
        "authority": authority,
        "reason_code": reason_code or REASON_BY_KIND[kind],
        "observation_ids": member_ids,
    }
    if value is not None:
        record["value"] = value
    if detail is not None:
        record["detail"] = detail
    return record


def _pair(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((left, right)))


def observation_evidence(observation: dict) -> list[dict]:
    """Record all identity evidence a single observation carries (observation-scoped).

    Covers: normalized remote, source-native identity, explicit project key/declaration,
    path locator (+ migration alias), display name, raw URL, and README/repo compound
    evidence. Evidence here is per-observation; it does not, by itself, relate two
    observations.
    """
    facts = observation_facts(observation)
    obs_id = facts["observation_id"]
    records: list[dict] = []

    for remote in sorted(facts["remotes"]):
        records.append(
            evidence_record(
                "normalized_remote",
                [obs_id],
                value=remote,
            )
        )
    if facts["source_id"]:
        detail = f"manifestation anchored to source {facts['source_id']}"
        if facts["snapshot_id"]:
            detail += f" / snapshot {facts['snapshot_id']}"
        records.append(
            evidence_record(
                "source_native_identity",
                [obs_id],
                detail=detail,
            )
        )
    if facts["project_key"]:
        records.append(
            evidence_record(
                "explicit_project_key",
                [obs_id],
                authority=AUTHORITY_DECLARED,
                value=facts["project_key"],
            )
        )
    for path in sorted(facts["paths"]):
        records.append(
            evidence_record(
                "path_locator",
                [obs_id],
                value=path,
            )
        )
    for raw in sorted(facts["raw_urls"]):
        records.append(
            evidence_record(
                "raw_url",
                [obs_id],
                value=raw,
            )
        )
    for name in sorted(facts["names"], key=str.casefold):
        records.append(
            evidence_record(
                "display_name",
                [obs_id],
                authority=AUTHORITY_OBSERVED,
                value=name,
            )
        )
    readme = facts["readme"]
    if (
        readme["sha256"]
        or readme["path"]
        or readme["description"]
        or readme["repo_name"]
        or readme["git"]
    ):
        parts = []
        if readme["sha256"]:
            parts.append("readme content hash present")
        if readme["path"]:
            parts.append(f"readme at {readme['path']}")
        if readme["description"]:
            parts.append("description present")
        if readme["repo_name"]:
            parts.append(f"repo-named {readme['repo_name']}")
        if readme["git"]:
            parts.append("git repository")
        records.append(
            evidence_record(
                "readme_compound",
                [obs_id],
                value=readme["sha256"] or readme["repo_name"] or None,
                detail="; ".join(parts) or "readme/repo evidence present",
            )
        )
    return records


def score_pair(left: dict, right: dict) -> dict:
    """Score whether two *observations* are related, and on what basis.

    Returns a verdict: ``strong_match`` (exact normalized remote shared), ``weak``
    (only referent overlap, clearly not identity authority), ``conflict``, or ``none``.
    The verdict is descriptive evidence, not a merge decision.
    """
    left_id = str(left.get("observation_id", ""))
    right_id = str(right.get("observation_id", ""))
    ids = sorted({left_id, right_id})
    left_facts = observation_facts(left)
    right_facts = observation_facts(right)

    verdict: str = "none"
    records: list[dict] = []
    shared_strong: list[str] = []
    shared_weak: list[str] = []

    shared_remotes = left_facts["remotes"] & right_facts["remotes"]
    if shared_remotes:
        verdict = "strong_match"
        for remote in sorted(shared_remotes):
            records.append(
                evidence_record(
                    "normalized_remote",
                    ids,
                    value=remote,
                )
            )
        shared_strong = ["normalized_remote"]

    # Weak referent overlaps are recorded but never elevate the verdict past weak.
    weak_hits: list[tuple[str, str, str]] = []  # (kind, reason, value-cased)
    name_overlap = {_fold(v) for v in left_facts["names"]} & {
        _fold(v) for v in right_facts["names"]
    }
    for hit in sorted(name_overlap):
        weak_hits.append(("name_similarity", hit, hit))
    key_overlap = _fold(left_facts["project_key"]) == _fold(
        right_facts["project_key"]
    ) and bool(left_facts["project_key"] and right_facts["project_key"])
    if key_overlap:
        weak_hits.append(
            (
                "semantic_similarity",
                _fold(left_facts["project_key"]),
                _fold(left_facts["project_key"]),
            )
        )
    path_overlap = left_facts["paths"] & right_facts["paths"]
    for hit in sorted(path_overlap):
        weak_hits.append(("path_migration", hit, hit))

    for kind, value, _cased in sorted(weak_hits):
        records.append(
            evidence_record(
                kind,
                ids,
                authority=AUTHORITY_OBSERVED,
                value=value,
            )
        )
        shared_weak.append(kind)
        if verdict == "none":
            verdict = "weak"

    return {
        "left_observation_id": left_id,
        "right_observation_id": right_id,
        "verdict": verdict,
        "shared_strong": shared_strong,
        "shared_weak": sorted(set(shared_weak)),
        "evidence": sorted(records, key=lambda item: item["identity_evidence_id"]),
    }


def project_identity_evidence(
    project: dict,
    observations_by_id: dict[str, dict],
) -> tuple[list[dict], dict]:
    """Records + scoring for ONE canonical project (its members' evidence, unifying
    evidence, weak overlaps, and an evidence summary). Called by the compiler so the
    materialized canonical project exposes why it is unified. Purely descriptive;
    does not change which observations the project contains.
    """
    member_ids = sorted(project["observation_ids"])
    members = [
        observations_by_id[oid] for oid in member_ids if oid in observations_by_id
    ]
    evidence: list[dict] = []
    for observation in members:
        private = observation_evidence(observation)
        # Per-member records stay scoped to their own observation; unifying evidence
        # below is attributed to the whole project.
        evidence.extend(private)

    anchor = project.get("identity_anchor", {})
    anchor_kind = anchor.get("kind", "")
    if anchor_kind == "normalized_remote" and len(member_ids) > 1:
        # Exact normalized remote is the unifying (strong) authority.
        for remote in sorted(project.get("normalized_remotes", [])):
            evidence.append(
                evidence_record(
                    "normalized_remote",
                    member_ids,
                    value=remote,
                )
            )
        unifying_authority = "exact_normalized_remote"
        strong_reason_code = "EXACT_NORMALIZED_REMOTE"
    elif anchor_kind == "merge_decision" and len(member_ids) > 1:
        for decision_id in sorted(project.get("merge_decision_ids", [])):
            evidence.append(
                evidence_record(
                    "explicit_merge_decision",
                    member_ids,
                    authority=AUTHORITY_DECISION,
                    value=decision_id,
                )
            )
        unifying_authority = "explicit_decision"
        strong_reason_code = "EXPLICIT_MERGE_DECISION"
    else:
        unifying_authority = "singleton" if len(member_ids) == 1 else "referential"
        strong_reason_code = "NONE"

    # Intra-project weak overlaps: record that members share only weak referents too,
    # but never let that be mistaken for identity authority.
    weak_overlap_pair_count = 0
    for left_id, right_id in combinations(member_ids, 2):
        if left_id not in observations_by_id or right_id not in observations_by_id:
            continue
        pair = score_pair(observations_by_id[left_id], observations_by_id[right_id])
        if pair["verdict"] == "weak":
            weak_overlap_pair_count += 1

    summary = {
        "canonical_project_id": project["canonical_project_id"],
        "member_count": len(member_ids),
        "unifying_authority": unifying_authority,
        "strong_reason_code": strong_reason_code,
        "automatic": anchor_kind == "normalized_remote" and len(member_ids) > 1,
        "weak_overlap_pair_count": weak_overlap_pair_count,
        "evidence_count": len(evidence),
    }
    return sorted(evidence, key=lambda item: item["identity_evidence_id"]), summary


def negative_pairs_from_decisions(decisions_payload: dict) -> set[tuple[str, str]]:
    """Pairs of observations an explicit split/reject decision keeps apart."""
    pairs: set[tuple[str, str]] = set()
    for decision in decisions_payload.get("decisions", []):
        decision_type = str(decision.get("type", "")).strip()
        if decision_type == "split":
            refs = [
                str(v).strip()
                for v in decision.get("observation_ids", [])
                if str(v).strip()
            ]
            for left, right in combinations(refs, 2):
                pairs.add(_pair(left, right))
        elif decision_type == "reject_match":
            left = str(decision.get("left_observation_id", "")).strip()
            right = str(decision.get("right_observation_id", "")).strip()
            if left and right and left != right:
                pairs.add(_pair(left, right))
    return pairs


def negative_decision_ids_by_pair(
    decisions_payload: dict,
) -> dict[tuple[str, str], set[str]]:
    """Map a negative observation pair to the decision IDs that keep it apart."""
    by_pair: dict[tuple[str, str], set[str]] = {}
    for decision in decisions_payload.get("decisions", []):
        decision_type = str(decision.get("type", "")).strip()
        decision_id = str(decision.get("decision_id", "")).strip()
        if not decision_id:
            continue
        affected: set[tuple[str, str]] = set()
        if decision_type == "split":
            refs = [
                str(v).strip()
                for v in decision.get("observation_ids", [])
                if str(v).strip()
            ]
            affected = {_pair(a, b) for a, b in combinations(refs, 2)}
        elif decision_type == "reject_match":
            left = str(decision.get("left_observation_id", "")).strip()
            right = str(decision.get("right_observation_id", "")).strip()
            if left and right and left != right:
                affected = {_pair(left, right)}
        for pair_key in affected:
            by_pair.setdefault(pair_key, set()).add(decision_id)
    return by_pair


def build_identity_evidence(
    observations: list[dict],
    canonical_payload: dict,
    decisions_payload: dict,
) -> dict:
    """Materialize the full identity evidence store (SCHEMA.md §2.3).

    Per canonical project: member evidence + scoring summary. Plus a bounded set of
    *cross-project* weak overlaps and negative records so evidence that was considered
    but deliberately NOT used is visible and clearly weak.
    """
    by_id = {
        str(o.get("observation_id", "")): o
        for o in observations
        if str(o.get("observation_id", ""))
    }

    # A canonical project is keyed by its (unique) observation membership rather than
    # its canonical_project_id, because two *distinct* projects can briefly share a
    # canonical ID when a split/reject keeps two observations that still share an exact
    # remote (a legacy anchor quirk owned by the canonical-ID compiler, P2.5). Keying by
    # membership keeps the evidence store lossless and deterministic regardless.
    projects_by_members: dict[str, dict] = {}
    project_of_obs: dict[str, str] = {}
    evidence_list: list[dict] = []
    for project in canonical_payload.get("projects", []):
        member_key = "|".join(sorted(project["observation_ids"]))
        project_evidence, summary = project_identity_evidence(project, by_id)
        projects_by_members[member_key] = {
            "canonical_project_id": project["canonical_project_id"],
            "observation_ids": sorted(project["observation_ids"]),
            "summary": summary,
            "evidence": project_evidence,
        }
        evidence_list.extend(project_evidence)
        for obs_id in project["observation_ids"]:
            project_of_obs[obs_id] = member_key
    projects_sorted = sorted(projects_by_members)

    # Cross-project weak overlaps + negatives: pairs that share a referent across
    # DISTINCT projects, or that a negative decision keeps apart. These are recorded
    # to make "weak name/semantic similarity clearly marked as weak" explicit and to
    # keep negative/conflict evidence visible at the store level.
    negative_by_pair = negative_decision_ids_by_pair(decisions_payload)
    cross_project: list[dict] = []
    for left_id, right_id in combinations(sorted(project_of_obs), 2):
        left_members = project_of_obs[left_id]
        right_members = project_of_obs[right_id]
        if left_members == right_members:
            continue
        pair_key = _pair(left_id, right_id)
        scored = score_pair(by_id[left_id], by_id[right_id])
        if scored["verdict"] != "weak" and pair_key not in negative_by_pair:
            continue
        records = scored["evidence"]
        if pair_key in negative_by_pair:
            # A negative decision dominates any weak/strong overlap and stays categorized
            # as negative evidence, so it is never mistaken for identity authority.
            records = [r for r in records if r["kind"] != "negative_decision"]
            records.append(
                evidence_record(
                    "negative_decision",
                    list(pair_key),
                    authority=AUTHORITY_DECISION,
                    value="|".join(sorted(negative_by_pair[pair_key])) or None,
                )
            )
        cross_project.append(
            {
                "left_observation_id": left_id,
                "right_observation_id": right_id,
                "projects": sorted({left_members, right_members}),
                "verdict": scored["verdict"]
                if scored["verdict"] != "none"
                else "negative",
                "shared_weak": scored["shared_weak"],
                "evidence": records,
            }
        )

    cross_project.sort(
        key=lambda item: (
            item["projects"][0],
            item["projects"][1],
            item["left_observation_id"],
        )
    )
    evidence_list.extend(
        record for item in cross_project for record in item["evidence"]
    )

    identity_evidence = {
        "schema_version": IDENTITY_EVIDENCE_SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "identity_evidence_count": len(
            {r["identity_evidence_id"] for r in evidence_list}
        ),
        "by_project": {k: projects_by_members[k] for k in projects_sorted},
        "cross_project_weak_overlaps": cross_project,
    }
    return identity_evidence
