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
_DECISION_TYPES = {"merge", "split", "reject_match", "alias"}


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


def _observation_facts(observation: dict) -> dict:
    compat = observation.get("compat_entry") if isinstance(observation.get("compat_entry"), dict) else {}
    remotes = {
        value
        for raw in (compat.get("remote_url"), compat.get("canonical_url"), observation.get("project_key"))
        if (value := normalize_remote_identity(str(raw or "")))
    }
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
    return {
        "observation_id": observation["observation_id"],
        "remotes": remotes,
        "project_key": project_key,
        "names": names,
        "paths": paths,
        "raw_urls": raw_urls,
    }


def _pair(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((left, right)))


def _review(code: str, observation_ids: list[str], detail: str, *, evidence: list[dict] | None = None) -> dict:
    ids = sorted(set(observation_ids))
    return {
        "review_id": stable_id("rev", code, ids),
        "code": code,
        "state": "open",
        "affected_observation_ids": ids,
        "detail": detail,
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
    raise ValueError(f"Unsupported identity decision type: {decision_type}")


def compile_identity(observations: list[dict], decisions_payload: dict) -> tuple[dict, dict]:
    by_id = {str(item.get("observation_id", "")): item for item in observations}
    if "" in by_id or len(by_id) != len(observations):
        raise ValueError("Typed observations require unique non-empty observation_id values.")

    facts = {obs_id: _observation_facts(observation) for obs_id, observation in by_id.items()}
    uf = _UnionFind(sorted(by_id))
    review_by_id: dict[str, dict] = {}
    negative_pairs: set[tuple[str, str]] = set()
    applicable: list[dict] = []
    aliases_by_observation: dict[str, list[dict]] = {}
    applied_merge_decisions: list[dict] = []

    def add_review(item: dict) -> None:
        review_by_id[item["review_id"]] = item

    # Validate decision references first. Missing observations are a bounded review,
    # not a global compile failure, because a source may simply be unavailable today.
    for decision in decisions_payload.get("decisions", []):
        refs = _decision_observation_ids(decision)
        missing = sorted(set(refs) - set(by_id))
        if missing:
            add_review(
                _review(
                    "DECISION_REFERENCE_UNAVAILABLE",
                    refs,
                    f"Decision {decision['decision_id']} references observations not present in this compile: {missing}",
                    evidence=[{"kind": "decision", "decision_id": decision["decision_id"], "type": decision["type"]}],
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

    projects: list[dict] = []
    obs_to_project: dict[str, str] = {}

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

        if len(remotes) == 1:
            anchor_kind = "normalized_remote"
            anchor_value = remotes[0]
        elif merge_decision_ids:
            anchor_kind = "merge_decision"
            anchor_value = "|".join(merge_decision_ids)
        else:
            anchor_kind = "observation"
            anchor_value = member_ids[0]

        canonical_project_id = stable_id("prj", anchor_kind, anchor_value)
        project_key = key_hints[0] if len(key_hints) == 1 else (remotes[0] if len(remotes) == 1 else canonical_project_id)
        display_name = names[0] if names else project_key

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

        evidence: list[dict] = []
        for obs_id in member_ids:
            for remote in sorted(facts[obs_id]["remotes"]):
                evidence.append(
                    {
                        "kind": "observed_remote",
                        "authority": "observed",
                        "reason_code": "EXACT_NORMALIZED_REMOTE",
                        "observation_id": obs_id,
                        "value": remote,
                    }
                )
        for decision_id in merge_decision_ids:
            evidence.append(
                {
                    "kind": "identity_decision",
                    "authority": "decision",
                    "reason_code": "EXPLICIT_MERGE_DECISION",
                    "decision_id": decision_id,
                }
            )

        project = {
            "schema_version": CANONICAL_PROJECT_SCHEMA_VERSION,
            "canonical_project_id": canonical_project_id,
            "project_key": project_key,
            "display_name": display_name,
            "observation_ids": member_ids,
            "identity_anchor": {"kind": anchor_kind, "value": anchor_value},
            "normalized_remotes": remotes,
            "project_key_hints": key_hints,
            "referents": unique_refs,
            "identity_evidence": evidence,
            "merge_decision_ids": merge_decision_ids,
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
    decisions = load_identity_decisions(decisions_path)
    canonical, reviews = compile_identity(observations, decisions)
    canonical.update({"run_id": run_id, "compiled_at": compiled_at})
    reviews.update({"run_id": run_id, "compiled_at": compiled_at})

    canonical_path = state_dir / "canonical-projects.json"
    review_path = state_dir / "review-queue.json"
    canonical_path.write_text(json.dumps(canonical, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    review_path.write_text(json.dumps(reviews, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
