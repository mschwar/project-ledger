"""GBrain source and project-page cross-linking for canonical projects.

Maps each canonical project in ``state/canonical-projects.json`` and
``state/projects/<canonical_project_id>.json`` to its registered GBrain source
slug on ``ai-server`` plus its normalized ``projects/<project_slug>`` page key,
so an agent resolving a project receives both deterministic git/filesystem truth
(from Project Ledger) and semantic history/decisions (from GBrain).
"""

from __future__ import annotations

import re

GBRAIN_SLUG_MAX_LEN = 32
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")

# Registered GBrain corpus paths on ai-server (`gbrain sources list --json`),
# ordered longest-prefix first for deterministic prefix matching.
REGISTERED_GBRAIN_PATH_SOURCES: tuple[tuple[str, str], ...] = (
    ("/central/data/messages/gbrain-export", "messages"),
    ("/central/imports/google-keep-md", "google-keep"),
    ("/central/imports/xport-bookmarks", "xport-bookmarks"),
    ("/home/prime/.hermes/memories", "hermes-memory"),
    ("/central/projects/notes", "central-notes"),
    ("/central/git/the_garden", "the-garden"),
    ("/central/repos/active", "active-repos"),
    ("/central/the_garden", "the-garden"),
    ("/central/knowledge", "central-knowledge"),
    ("/central/seed-bank", "central-catalog"),
    ("/central/registry", "atlas-registry"),
)

REGISTERED_GBRAIN_SOURCE_IDS: dict[str, str] = {
    "central-active": "active-repos",
    "git-the-garden": "the-garden",
    "the-garden": "the-garden",
}


def normalize_gbrain_slug(raw: str | None) -> str:
    """Normalize a project key, URL tail, or name into a GBrain slug (`[a-z0-9-]{1,32}`)."""
    text = str(raw or "").strip()
    if not text:
        return "project"
    if text.lower().startswith("google-drive:"):
        text = text.split(":", 1)[1].strip()
    if "/" in text:
        parts = [part.strip() for part in text.split("/") if part.strip()]
        if parts:
            text = parts[-1]
    if text.lower().endswith(".git"):
        text = text[:-4]
    slug = _NON_SLUG_RE.sub("-", text.lower()).strip("-")
    if len(slug) > GBRAIN_SLUG_MAX_LEN:
        slug = slug[:GBRAIN_SLUG_MAX_LEN].rstrip("-")
    return slug or "project"


def _match_observation_gbrain_source(observation: dict) -> tuple[str | None, str | None]:
    """Return ``(source_slug, match_basis)`` if ``observation`` maps to a GBrain corpus."""
    compat = observation.get("compat_entry") if isinstance(observation.get("compat_entry"), dict) else {}
    for field in ("gbrain_source_slug", "gbrain_source"):
        declared = str(compat.get(field) or observation.get(field) or "").strip()
        if declared:
            return normalize_gbrain_slug(declared), f"declared_sidecar:{field}"

    raw_loc = str(observation.get("location") or compat.get("path") or "").strip().rstrip("/")
    if raw_loc.startswith("/"):
        # `/central/registry/mirrors/` is excluded from `atlas-registry` per gbrain-ops policy.
        for prefix, slug in REGISTERED_GBRAIN_PATH_SOURCES:
            if prefix == "/central/registry" and (
                raw_loc == "/central/registry/mirrors"
                or raw_loc.startswith("/central/registry/mirrors/")
            ):
                continue
            if raw_loc == prefix or raw_loc.startswith(prefix + "/"):
                return slug, f"source_path:{prefix}"

    source_id = str(observation.get("source_id") or "").strip()
    if source_id in REGISTERED_GBRAIN_SOURCE_IDS:
        return REGISTERED_GBRAIN_SOURCE_IDS[source_id], f"source_id:{source_id}"

    return None, None


def resolve_gbrain_link(project: dict, observations_by_id: dict[str, dict]) -> dict:
    """Resolve the GBrain source slug and project page key for ``project``."""
    member_ids = sorted(set(project.get("observation_ids", [])))
    project_slug = normalize_gbrain_slug(
        str(project.get("project_key") or "")
        or str(project.get("display_name") or "")
        or str(project.get("canonical_project_id") or "")
    )

    # Prefer non-mirror local paths first when multiple observations exist.
    def _obs_priority(obs_id: str) -> tuple[int, str]:
        obs = observations_by_id.get(obs_id) or {}
        loc = str(obs.get("location") or "").strip()
        is_mirror = "/mirrors/" in loc
        is_local = loc.startswith("/")
        return (0 if (is_local and not is_mirror) else (1 if is_local else 2), obs_id)

    for obs_id in sorted(member_ids, key=_obs_priority):
        obs = observations_by_id.get(obs_id)
        if obs is None:
            continue
        slug, basis = _match_observation_gbrain_source(obs)
        if slug and basis:
            return {
                "state": "mapped",
                "source_slug": slug,
                "fallback_source_slug": "default",
                "project_slug": project_slug,
                "page_key": f"projects/{project_slug}",
                "match_basis": basis,
                "matched_observation_id": obs_id,
            }

    return {
        "state": "mapped",
        "source_slug": "default",
        "fallback_source_slug": "default",
        "project_slug": project_slug,
        "page_key": f"projects/{project_slug}",
        "match_basis": "default_brain_source",
        "matched_observation_id": None,
    }
