from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path
from urllib.parse import urlparse

from . import (
    CANONICAL_PROJECT_SCHEMA_VERSION,
    COMPILER_VERSION,
    IDENTITY_DECISION_SCHEMA_VERSION,
    REVIEW_QUEUE_SCHEMA_VERSION,
)
from .ids import normalize_locator, stable_id


_ALLOWED_REMOTE_SCHEMES = {"http", "https", "ssh", "git"}
_KNOWN_REPOSITORY_HOSTS = {"github.com", "gitlab.com", "bitbucket.org", "codeberg.org"}
_DECISION_TYPES = {"merge", "split", "reject_match", "alias", "canonical_key", "supersede"}
_DECISION_AUTHORITIES = {"operator", "trusted_automation", "reviewed_agent"}


def normalize_remote_identity(raw: str) -> str | None:
    """Return a conservative repository identity or None.

    This deliberately recognizes repository-like network locators only. Cloud/file
    locators such as gdrive:// are useful referents but are not automatic identity
    authority.
    """
    text = str(raw or "").strip()
    if not text:
        return None

    if text.startswith("git@") and ":" in text:
        host_part, repo_part = text.split(":", 1)
        host = host_part.split("@", 1)[1].strip().lower()
        repo = repo_part.strip().strip("/")
        if host and repo:
            return f"{host}/{repo}".removesuffix(".git").casefold()
        return None

    if "://" in text:
        parsed = urlparse(text)
        if parsed.scheme.lower() not in _ALLOWED_REMOTE_SCHEMES:
            return None
        host = (parsed.hostname or "").strip().lower()
        repo = parsed.path.strip("/")
        if not host or not repo:
            return None
        return f"{host}/{repo}".removesuffix(".git").casefold()

    # Compatibility output already uses normalized host/path strings for project_key.
    # Accept only strings that actually look host-qualified, not ordinary slugs/names.
    first, sep, rest = text.partition("/")
    if sep and "." in first and rest and " " not in first:
        return f"{first.lower()}/{rest.strip('/')}".removesuffix(".git").casefold()
    return None


def load_identity_decisions(path: Path | None) -> dict:
    if path is None:
        return {"schema_version": IDENTITY_DECISION_SCHEMA_VERSION, "decisions": []}
    path = path.resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Identity decision registry does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Identity decision registry is invalid JSON: {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("Identity decision registry must be a JSON object.")
    if payload.get("schema_version") != IDENTITY_DECISION_SCHEMA_VERSION:
        raise ValueError(
            "Unsupported identity decision registry schema_version "
            f"{payload.get('schema_version')!r}; expected {IDENTITY_DECISION_SCHEMA_VERSION!r}."
        )
    decisions = payload.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("Identity decision registry must contain a decisions array.")

    seen: set[str] = set()
    for index, decision in enumerate(decisions):
        if not isinstance(decision, dict):
            raise ValueError(f"decisions[{index}] must be an object.")
        decision_id = str(decision.get("decision_id", "")).strip()
        decision_type = str(decision.get("type", "")).strip()
        if not decision_id:
            raise ValueError(f"decisions[{index}].decision_id must be non-empty.")
        if decision_id in seen:
            raise ValueError(f"Duplicate identity decision_id: {decision_id}")
        seen.add(decision_id)
        if decision_type not in _DECISION_TYPES:
            raise ValueError(
                f"decisions[{index}].type={decision_type!r} is unsupported; "
                f"expected one of {sorted(_DECISION_TYPES)}."
            )
        authority = str(decision.get("authority", "")).strip()
        if authority and authority not in _DECISION_AUTHORITIES:
            raise ValueError(
                f"decisions[{index}].authority={authority!r} is unsupported; "
                f"expected one of {sorted(_DECISION_AUTHORITIES)}."
            )
        if decision_type == "canonical_key":
            observation_id = str(decision.get("observation_id", "")).strip()
            key = str(decision.get("key", "")).strip()
            if not observation_id:
                raise ValueError(f"decisions[{index}].canonical_key requires observation_id.")
            if not key:
                raise ValueError(f"decisions[{index}].canonical_key requires a non-empty key.")
        elif decision_type == "supersede":
            supersedes = str(decision.get("supersedes_decision_id", "")).strip()
            if not supersedes:
                raise ValueError(
                    f"decisions[{index}].supersede requires supersedes_decision_id."
                )
            if supersedes == decision_id:
                raise ValueError(
                    f"decisions[{index}].supersede cannot supersede itself."
                )
    return payload


class _UnionFind:
    def __init__(self, ids: list[str]) -> None:
        self.parent = {value: value for value in ids}
        self.members = {value: {value} for value in ids}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def component(self, value: str) -> set[str]:
        return set(self.members[self.find(value)])

    def union(self, left: str, right: str) -> str:
        a = self.find(left)
        b = self.find(right)
        if a == b:
            return a
        # Deterministic root selection makes debugging/replays stable.
        if b < a:
            a, b = b, a
        self.parent[b] = a
        self.members[a] = self.members[a] | self.members[b]
        del self.members[b]
        return a

    def components(self) -> list[set[str]]:
        return [set(self.members[root]) for root in sorted(self.members)]


def _canonical_url_repository_identity(raw: str) -> str | None:
    text = str(raw or "").strip()
    normalized = normalize_remote_identity(text)
    if not normalized:
        return None
    if text.startswith("git@") or text.endswith(".git"):
        return normalized
    if "://" in text:
        parsed = urlparse(text)
        if (parsed.hostname or "").lower() in _KNOWN_REPOSITORY_HOSTS:
            return normalized
    return None


def observation_facts(observation: dict) -> dict:
    """Extract the normalized identity-evidence facts an observation carries.

    Public so the identity evidence model (``ledger.evidence``) can reuse the
    exact same normalization instead of drifting a second copy. Facts are the raw
    inputs; strength/scoring is decided by the evidence model, not here.
    """
    compat = observation.get("compat_entry") if isinstance(observation.get("compat_entry"), dict) else {}
    remotes: set[str] = set()
    remote_identity = normalize_remote_identity(str(compat.get("remote_url") or ""))
    if remote_identity:
        remotes.add(remote_identity)
    canonical_remote = _canonical_url_repository_identity(str(compat.get("canonical_url") or ""))
    if canonical_remote:
        remotes.add(canonical_remote)
    project_key = str(observation.get("project_key") or "").strip() or None
    names = {
        str(value).strip()
        for value in (compat.get("name"), compat.get("repo_name"))
        if str(value or "").strip()
    }
    paths = {
        normalize_locator(str(value))
        for value in (observation.get("location"), compat.get("path"))
        if str(value or "").strip()
    }
    raw_urls = {
        str(value).strip()
        for value in (compat.get("remote_url"), compat.get("canonical_url"))
        if str(value or "").strip()
    }
    readme_sha = str(compat.get("readme_sha256") or "").strip() or None
    readme_path = str(compat.get("readme_path") or "").strip() or None
    description = str(compat.get("description") or "").strip() or None
    return {
        "observation_id": observation["observation_id"],
        "source_id": str(observation.get("source_id") or "").strip() or None,
        "snapshot_id": str(observation.get("snapshot_id") or "").strip() or None,
        "remotes": remotes,
        "project_key": project_key,
        "names": names,
        "paths": paths,
        "raw_urls": raw_urls,
        "readme": {
            "sha256": readme_sha,
            "path": readme_path,
            "description": description,
            "repo_name": str(compat.get("repo_name") or "").strip() or None,
            "git": bool(compat.get("git")),
        },
    }


def _pair(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((left, right)))


def _review(
    code: str,
    observation_ids: list[str],
    detail: str,
    *,
    evidence: list[dict] | None = None,
    decision_ids: list[str] | None = None,
    severity: str = "warning",
    reason_automation_stopped: str | None = None,
    resolution_actions: list[str] | None = None,
) -> dict:
    """Build one first-class identity review item (P2.6).

    ``affected_observation_ids`` stays present (possibly empty) for backward
    compatibility. Decision-scoped reviews (e.g. ``DECISION_SUPERSEDE_UNKNOWN``)
    carry a non-empty ``affected_decision_ids`` referent so the review is about the
    decision, not observations. The decision referent is fed into the ``stable_id``
    material so two distinct decision-scoped reviews never collapse to one
    ``review_id`` (a data-loss bug fixed in P2.6).
    """
    ids = sorted(set(observation_ids))
    decision_ids = sorted(set(decision_ids or []))
    # Only include the decision referent in the ID material when present, so
    # observation-scoped review IDs are unchanged (pinned fixtures preserved).
    id_parts: list[object] = [code, ids]
    if decision_ids:
        id_parts.append(decision_ids)
    return {
        "review_id": stable_id("rev", *id_parts),
        "code": code,
        "state": "open",
        "severity": severity,
        "affected_observation_ids": ids,
        "affected_decision_ids": decision_ids,
        "detail": detail,
        "reason_automation_stopped": reason_automation_stopped,
        "resolution_actions": resolution_actions or [],
        "evidence": evidence or [],
    }


def _decision_observation_ids(decision: dict) -> list[str]:
    decision_type = decision["type"]
    if decision_type in {"merge", "split"}:
        values = decision.get("observation_ids")
        if not isinstance(values, list) or len(values) < 2:
            raise ValueError(f"{decision['decision_id']} requires observation_ids with at least two IDs.")
        ids = [str(value).strip() for value in values if str(value).strip()]
        if len(ids) < 2:
            raise ValueError(f"{decision['decision_id']} requires at least two non-empty observation IDs.")
        return ids
    if decision_type == "reject_match":
        left = str(decision.get("left_observation_id", "")).strip()
        right = str(decision.get("right_observation_id", "")).strip()
        if not left or not right or left == right:
            raise ValueError(f"{decision['decision_id']} requires two distinct observation IDs.")
        return [left, right]
    if decision_type == "alias":
        observation_id = str(decision.get("observation_id", "")).strip()
        alias = str(decision.get("alias", "")).strip()
        if not observation_id or not alias:
            raise ValueError(f"{decision['decision_id']} requires observation_id and alias.")
        return [observation_id]
    if decision_type == "canonical_key":
        observation_id = str(decision.get("observation_id", "")).strip()
        if not observation_id:
            raise ValueError(f"{decision['decision_id']} requires observation_id.")
        return [observation_id]
    if decision_type == "supersede":
        # A supersede decision references another decision, not observations. It is
        # applied by the compiler before observation refs are resolved.
        return []
    raise ValueError(f"Unsupported identity decision type: {decision_type}")


def compile_identity(observations: list[dict], decisions_payload: dict) -> tuple[dict, dict]:
    # Imported lazily to avoid a module-load cycle: evidence.py imports identity.py's
    # observation_facts, which must be fully defined before evidence is imported here.
    from .evidence import project_identity_evidence

    by_id = {str(item.get("observation_id", "")): item for item in observations}
    if "" in by_id or len(by_id) != len(observations):
        raise ValueError("Typed observations require unique non-empty observation_id values.")

    facts = {obs_id: observation_facts(observation) for obs_id, observation in by_id.items()}
    uf = _UnionFind(sorted(by_id))
    review_by_id: dict[str, dict] = {}
    negative_pairs: set[tuple[str, str]] = set()
    applicable: list[dict] = []
    aliases_by_observation: dict[str, list[dict]] = {}
    applied_merge_decisions: list[dict] = []

    def add_review(item: dict) -> None:
        review_by_id[item["review_id"]] = item

    # Supersede decisions are applied first: a superseded decision is inactive for
    # this compile (append/supersede oriented, never silently rewritten). A supersede
    # referencing an unknown decision is a bounded review, not a compile failure.
    decision_by_id = {str(d.get("decision_id", "")): d for d in decisions_payload.get("decisions", [])}
    superseded_ids: set[str] = set()
    for decision in decisions_payload.get("decisions", []):
        if decision["type"] != "supersede":
            continue
        target = str(decision.get("supersedes_decision_id", "")).strip()
        if target not in decision_by_id:
            add_review(
                _review(
                    "DECISION_SUPERSEDE_UNKNOWN",
                    [],
                    f"Supersede decision {decision['decision_id']} references unknown decision {target!r}.",
                    evidence=[{"kind": "decision", "decision_id": decision["decision_id"], "type": "supersede"}],
                    decision_ids=[decision["decision_id"]],
                    severity="warning",
                    reason_automation_stopped=(
                        "A supersede decision references a decision id that is not present in the "
                        "decision registry; the supersede cannot be applied."
                    ),
                    resolution_actions=[
                        "Correct the supersedes_decision_id to a decision that exists in the registry.",
                        "Add the referenced decision to the registry if it was omitted.",
                    ],
                )
            )
            continue
        superseded_ids.add(target)

    # Validate decision references first. Missing observations are a bounded review,
    # not a global compile failure, because a source may simply be unavailable today.
    for decision in decisions_payload.get("decisions", []):
        if decision["type"] == "supersede":
            continue
        if decision["decision_id"] in superseded_ids:
            continue
        refs = _decision_observation_ids(decision)
        missing = sorted(set(refs) - set(by_id))
        if missing:
            add_review(
                _review(
                    "DECISION_REFERENCE_UNAVAILABLE",
                    refs,
                    f"Decision {decision['decision_id']} references observations not present in this compile: {missing}",
                    evidence=[{"kind": "decision", "decision_id": decision["decision_id"], "type": decision["type"]}],
                    severity="warning",
                    reason_automation_stopped=(
                        "A decision references observations that are not present in this compile "
                        "(their source may be unavailable); the decision cannot be applied."
                    ),
                    resolution_actions=[
                        "Confirm the referenced source is available and recompile.",
                        "Supersede or correct the decision if the reference is stale.",
                    ],
                )
            )
            continue
        applicable.append(decision)

    # Negative decisions constrain both automatic matches and explicit merges.
    for decision in applicable:
        if decision["type"] == "split":
            for left, right in combinations(_decision_observation_ids(decision), 2):
                negative_pairs.add(_pair(left, right))
        elif decision["type"] == "reject_match":
            left, right = _decision_observation_ids(decision)
            negative_pairs.add(_pair(left, right))

    def components_conflict(left: str, right: str) -> bool:
        left_members = uf.component(left)
        right_members = uf.component(right)
        return any(_pair(a, b) in negative_pairs for a in left_members for b in right_members)

    # Operator merge decisions have authority over automatic evidence, but conflicting
    # negative decisions stop automation rather than letting decision order decide truth.
    for decision in applicable:
        if decision["type"] != "merge":
            continue
        refs = _decision_observation_ids(decision)
        if any(_pair(a, b) in negative_pairs for a, b in combinations(refs, 2)):
            add_review(
                _review(
                    "DECISION_CONFLICT",
                    refs,
                    f"Merge decision {decision['decision_id']} conflicts with a split/reject decision.",
                    evidence=[{"kind": "decision", "decision_id": decision["decision_id"], "type": "merge"}],
                    severity="error",
                    reason_automation_stopped=(
                        "A positive merge decision conflicts with an explicit negative (split/reject) "
                        "decision; automation stopped rather than letting decision order decide truth."
                    ),
                    resolution_actions=[
                        "Resolve the conflict between the positive and negative decisions.",
                        "Supersede the conflicting decision once the intended outcome is clear.",
                    ],
                )
            )
            continue
        first = refs[0]
        blocked = False
        for other in refs[1:]:
            if components_conflict(first, other):
                blocked = True
                break
        if blocked:
            add_review(
                _review(
                    "DECISION_CONFLICT",
                    refs,
                    f"Merge decision {decision['decision_id']} would violate an existing negative decision.",
                    evidence=[{"kind": "decision", "decision_id": decision["decision_id"], "type": "merge"}],
                    severity="error",
                    reason_automation_stopped=(
                        "A positive merge decision would violate an existing negative (split/reject) "
                        "decision; automation stopped rather than letting decision order decide truth."
                    ),
                    resolution_actions=[
                        "Resolve the conflict between the positive and negative decisions.",
                        "Supersede the conflicting decision once the intended outcome is clear.",
                    ],
                )
            )
            continue
        for other in refs[1:]:
            uf.union(first, other)
        applied_merge_decisions.append(decision)

    # Exact normalized remote identity is the only automatic merge rule in the first
    # walking skeleton. Names/project-key hints never merge projects.
    remote_to_ids: dict[str, list[str]] = {}
    for obs_id, item in facts.items():
        for remote in item["remotes"]:
            remote_to_ids.setdefault(remote, []).append(obs_id)

    for remote, ids in sorted(remote_to_ids.items()):
        anchor = sorted(ids)[0]
        for other in sorted(ids)[1:]:
            if uf.find(anchor) == uf.find(other):
                continue
            if components_conflict(anchor, other):
                add_review(
                    _review(
                        "AUTO_MATCH_BLOCKED_BY_DECISION",
                        sorted(uf.component(anchor) | uf.component(other)),
                        f"Exact remote {remote!r} matched observations that an explicit split/reject decision keeps separate.",
                        evidence=[{"kind": "normalized_remote", "value": remote}],
                        severity="warning",
                        reason_automation_stopped=(
                            "An exact normalized remote matched observations that an explicit "
                            "split/reject decision keeps in separate conceptual projects."
                        ),
                        resolution_actions=[
                            "Review the split/reject decision to confirm it is still intended.",
                            "Supersede the negative decision if the observations are the same project after all.",
                        ],
                    )
                )
                continue
            uf.union(anchor, other)

    for decision in applicable:
        if decision["type"] == "alias":
            observation_id = _decision_observation_ids(decision)[0]
            aliases_by_observation.setdefault(observation_id, []).append(
                {
                    "kind": "decision_alias",
                    "value": str(decision["alias"]).strip(),
                    "decision_id": decision["decision_id"],
                }
            )

    # Canonical human key assignment: an operator-approved stable project_key for the
    # project containing an observation. It overrides the auto-derived project_key but
    # never changes which observations are members (identity authority is unchanged).
    canonical_key_by_observation: dict[str, dict] = {}
    for decision in applicable:
        if decision["type"] != "canonical_key":
            continue
        observation_id = _decision_observation_ids(decision)[0]
        canonical_key_by_observation[observation_id] = {
            "key": str(decision["key"]).strip(),
            "decision_id": decision["decision_id"],
        }

    projects: list[dict] = []
    obs_to_project: dict[str, str] = {}
    # A normalized remote is only a valid identity anchor when it is unique to ONE
    # project across the whole compile. When a split/reject keeps two observations
    # that share an exact remote in separate projects, that remote is shared by two
    # distinct projects and can no longer anchor either (P2.5: otherwise both would
    # derive the same canonical_project_id and collide). Each such project falls back
    # to a membership-scoped anchor so IDs stay distinct and stable.
    remote_to_memberships: dict[str, set[str]] = {}
    for members in uf.components():
        member_ids = sorted(members)
        for obs_id in member_ids:
            for remote in facts[obs_id]["remotes"]:
                remote_to_memberships.setdefault(remote, set()).add("|".join(member_ids))

    for members in uf.components():
        member_ids = sorted(members)
        remotes = sorted({remote for obs_id in member_ids for remote in facts[obs_id]["remotes"]})
        key_hints = sorted(
            {facts[obs_id]["project_key"] for obs_id in member_ids if facts[obs_id]["project_key"]}
        )
        names = sorted({name for obs_id in member_ids for name in facts[obs_id]["names"]}, key=str.casefold)
        merge_decision_ids = sorted(
            decision["decision_id"]
            for decision in applied_merge_decisions
            if set(_decision_observation_ids(decision)).issubset(members)
        )

        membership_key = "|".join(member_ids)
        unique_remotes = [
            remote
            for remote in remotes
            if remote_to_memberships.get(remote) == {membership_key}
        ]
        if len(unique_remotes) == 1:
            anchor_kind = "normalized_remote"
            anchor_value = unique_remotes[0]
        elif merge_decision_ids:
            anchor_kind = "merge_decision"
            anchor_value = "|".join(merge_decision_ids)
        else:
            anchor_kind = "observation"
            anchor_value = member_ids[0]

        canonical_project_id = stable_id("prj", anchor_kind, anchor_value)
        # An operator-approved canonical_key overrides the auto-derived project_key.
        canonical_key_decision = next(
            (
                canonical_key_by_observation[obs_id]
                for obs_id in member_ids
                if obs_id in canonical_key_by_observation
            ),
            None,
        )
        if canonical_key_decision is not None:
            project_key = canonical_key_decision["key"]
        else:
            project_key = key_hints[0] if len(key_hints) == 1 else (remotes[0] if len(remotes) == 1 else canonical_project_id)
        display_name = names[0] if names else project_key
        # Resolved-field claim provenance (P2.5): for each resolved canonical field,
        # record which observation/decision/evidence produced the value, so a cold
        # agent can see *why* a canonical value exists without re-running archaeology.
        resolved_fields: dict[str, dict] = {}
        if canonical_key_decision is not None:
            resolved_fields["project_key"] = {
                "value": project_key,
                "provenance": [
                    {
                        "kind": "decision",
                        "decision_id": canonical_key_decision["decision_id"],
                        "reason_code": "CANONICAL_KEY_DECISION",
                    }
                ],
            }
        elif len(key_hints) == 1:
            hint_obs = sorted(
                obs_id
                for obs_id in member_ids
                if facts[obs_id]["project_key"] == key_hints[0]
            )
            resolved_fields["project_key"] = {
                "value": project_key,
                "provenance": [
                    {
                        "kind": "declaration",
                        "observation_id": obs_id,
                        "reason_code": "PROJECT_KEY_DECLARATION",
                    }
                    for obs_id in hint_obs
                ],
            }
        elif len(remotes) == 1:
            resolved_fields["project_key"] = {
                "value": project_key,
                "provenance": [
                    {
                        "kind": "observed",
                        "value": remotes[0],
                        "reason_code": "EXACT_NORMALIZED_REMOTE",
                    }
                ],
            }
        else:
            resolved_fields["project_key"] = {
                "value": project_key,
                "provenance": [
                    {
                        "kind": "derived",
                        "value": canonical_project_id,
                        "reason_code": "CANONICAL_PROJECT_ID",
                    }
                ],
            }
        if names:
            name_obs = sorted(
                obs_id
                for obs_id in member_ids
                if display_name in facts[obs_id]["names"]
            )
            resolved_fields["display_name"] = {
                "value": display_name,
                "provenance": [
                    {
                        "kind": "observed",
                        "observation_id": obs_id,
                        "reason_code": "DISPLAY_NAME",
                    }
                    for obs_id in name_obs
                ],
            }
        else:
            resolved_fields["display_name"] = {
                "value": display_name,
                "provenance": [
                    {
                        "kind": "derived",
                        "value": project_key,
                        "reason_code": "PROJECT_KEY",
                    }
                ],
            }

        referents: list[dict] = [
            {"kind": "canonical_project_id", "value": canonical_project_id},
            {"kind": "project_key", "value": project_key},
        ]
        for remote in remotes:
            referents.append({"kind": "normalized_remote", "value": remote})
        for hint in key_hints:
            referents.append({"kind": "project_key_hint", "value": hint})
        for name in names:
            referents.append({"kind": "display_name", "value": name})
        for obs_id in member_ids:
            for path in sorted(facts[obs_id]["paths"]):
                referents.append({"kind": "path", "value": path, "observation_id": obs_id})
            for url in sorted(facts[obs_id]["raw_urls"]):
                referents.append({"kind": "raw_url", "value": url, "observation_id": obs_id})
            referents.extend(aliases_by_observation.get(obs_id, []))

        # Stable de-duplication without throwing away why a referent exists.
        seen_refs: set[tuple[str, str]] = set()
        unique_refs: list[dict] = []
        for ref in referents:
            key = (ref["kind"], str(ref["value"]).casefold())
            if key in seen_refs:
                continue
            seen_refs.add(key)
            unique_refs.append(ref)

        evidence, evidence_summary = project_identity_evidence(
            {
                "canonical_project_id": canonical_project_id,
                "observation_ids": member_ids,
                "identity_anchor": {"kind": anchor_kind, "value": anchor_value},
                "normalized_remotes": remotes,
                "merge_decision_ids": merge_decision_ids,
            },
            by_id,
        )

        project = {
            "schema_version": CANONICAL_PROJECT_SCHEMA_VERSION,
            "canonical_project_id": canonical_project_id,
            "project_key": project_key,
            "display_name": display_name,
            "resolved_fields": resolved_fields,
            "observation_ids": member_ids,
            "identity_anchor": {"kind": anchor_kind, "value": anchor_value},
            "normalized_remotes": remotes,
            "project_key_hints": key_hints,
            "referents": unique_refs,
            "identity_evidence": evidence,
            "identity_evidence_summary": evidence_summary,
            "merge_decision_ids": merge_decision_ids,
            "canonical_key_decision_id": canonical_key_decision["decision_id"]
            if canonical_key_decision is not None
            else None,
        }
        projects.append(project)
        for obs_id in member_ids:
            obs_to_project[obs_id] = canonical_project_id

    projects.sort(key=lambda item: item["canonical_project_id"])

    # A compatibility project_key is a useful exact referent, but not automatic merge
    # authority. Surface collisions so agents do not mistake a clean-looking key for identity.
    key_to_projects: dict[str, set[str]] = {}
    key_to_observations: dict[str, set[str]] = {}
    for obs_id, item in facts.items():
        key = item["project_key"]
        if not key:
            continue
        folded = key.casefold()
        key_to_projects.setdefault(folded, set()).add(obs_to_project[obs_id])
        key_to_observations.setdefault(folded, set()).add(obs_id)
    for folded, project_ids in sorted(key_to_projects.items()):
        if len(project_ids) > 1:
            obs_ids = sorted(key_to_observations[folded])
            original = next(facts[obs_id]["project_key"] for obs_id in obs_ids if facts[obs_id]["project_key"])
            add_review(
                _review(
                    "PROJECT_KEY_AMBIGUOUS",
                    obs_ids,
                    f"Compatibility project_key {original!r} resolves to {len(project_ids)} canonical projects; no automatic merge was performed.",
                    evidence=[{"kind": "project_key_hint", "value": original}],
                    severity="warning",
                    reason_automation_stopped=(
                        "A compatibility project_key resolves to more than one canonical project; "
                        "names and project keys are referents, not automatic identity authority."
                    ),
                    resolution_actions=[
                        "Add a merge decision if the observations are the same conceptual project.",
                        "Add a canonical_key decision to assign a stable operator-approved key.",
                        "Add a reject_match decision if they are genuinely distinct projects.",
                    ],
                )
            )

    canonical_payload = {
        "schema_version": CANONICAL_PROJECT_SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "canonical_project_count": len(projects),
        "projects": projects,
    }
    review_items = sorted(review_by_id.values(), key=lambda item: item["review_id"])
    review_payload = {
        "schema_version": REVIEW_QUEUE_SCHEMA_VERSION,
        "review_count": len(review_items),
        "items": review_items,
    }
    return canonical_payload, review_payload


def materialize_identity(
    *,
    observations: list[dict],
    decisions_path: Path | None,
    state_dir: Path,
    run_id: str,
    compiled_at: str,
) -> tuple[dict, dict]:
    from .evidence import build_identity_evidence

    state_dir = state_dir.resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    decisions = load_identity_decisions(decisions_path)
    canonical, reviews = compile_identity(observations, decisions)
    canonical.update({"run_id": run_id, "compiled_at": compiled_at})
    reviews.update({"run_id": run_id, "compiled_at": compiled_at})

    canonical_path = state_dir / "canonical-projects.json"
    review_path = state_dir / "review-queue.json"
    canonical_path.write_text(json.dumps(canonical, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    review_path.write_text(json.dumps(reviews, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    identity_evidence = build_identity_evidence(observations, canonical, decisions)
    identity_evidence.update({"run_id": run_id, "compiled_at": compiled_at})
    evidence_path = state_dir / "identity-evidence.json"
    evidence_path.write_text(
        json.dumps(identity_evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return canonical, reviews


def load_canonical_projects(state_dir: Path) -> dict:
    path = state_dir.resolve() / "canonical-projects.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Canonical project state does not exist: {path}; run `python -m ledger compile` first.") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Canonical project state is invalid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("projects"), list):
        raise ValueError(f"Canonical project state must contain a projects array: {path}")
    return payload


_RESOLVE_PRIORITY = {
    "canonical_project_id": 0,
    "normalized_remote": 1,
    "path": 1,
    "decision_alias": 1,
    "raw_url": 2,
    "project_key": 2,
    "project_key_hint": 3,
    "display_name": 4,
}


def _referent_matches(query: str, ref: dict) -> bool:
    value = str(ref.get("value", "")).strip()
    if not value:
        return False
    kind = str(ref.get("kind", ""))
    if kind == "normalized_remote":
        return normalize_remote_identity(query) == value.casefold()
    if kind == "path":
        return normalize_locator(query).casefold() == normalize_locator(value).casefold()
    if kind == "raw_url":
        remote_query = normalize_remote_identity(query)
        remote_value = normalize_remote_identity(value)
        if remote_query and remote_value:
            return remote_query == remote_value
    return query.casefold() == value.casefold()


def resolve_payload(state_dir: Path, referent: str) -> dict:
    query = str(referent).strip()
    if not query:
        raise ValueError("Resolve referent must be non-empty.")

    payload = load_canonical_projects(state_dir)
    matches: list[dict] = []
    for project in payload["projects"]:
        matched = [ref for ref in project.get("referents", []) if _referent_matches(query, ref)]
        if not matched:
            continue
        best = min(_RESOLVE_PRIORITY.get(str(ref.get("kind")), 99) for ref in matched)
        matches.append(
            {
                "project": project,
                "priority": best,
                "matched_by": [
                    ref for ref in matched if _RESOLVE_PRIORITY.get(str(ref.get("kind")), 99) == best
                ],
            }
        )

    if not matches:
        return {"status": "unresolved", "referent": query, "candidate_count": 0, "candidates": []}

    best_priority = min(item["priority"] for item in matches)
    finalists = [item for item in matches if item["priority"] == best_priority]
    if len(finalists) == 1:
        item = finalists[0]
        project = item["project"]
        return {
            "status": "resolved",
            "referent": query,
            "canonical_project_id": project["canonical_project_id"],
            "project_key": project["project_key"],
            "display_name": project["display_name"],
            "matched_by": item["matched_by"],
            "observation_ids": project["observation_ids"],
        }

    candidates = [
        {
            "canonical_project_id": item["project"]["canonical_project_id"],
            "project_key": item["project"]["project_key"],
            "display_name": item["project"]["display_name"],
            "matched_by": item["matched_by"],
        }
        for item in finalists
    ]
    candidates.sort(key=lambda item: item["canonical_project_id"])
    return {
        "status": "ambiguous",
        "referent": query,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }