"""Deterministic project lifecycle and attention-urgency classification rules.

Replaces the retired Jev HTTP wrapper with pure-Python scalar threshold rules
over observed capsule state while honoring Architectural Law 3 (inferred posture
never overwrites declared sidecar ``lifecycle_state``) and Architectural Law 5
(missing ``total_files`` stays ``None`` rather than ``0``).
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

_MAX_SCALAR_DIGITS = 32
_MAX_SAFE_INT = 10**12


def _safe_bool(value: Any, *, field: str) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    raise ValueError(f"Field {field!r} must be a boolean (got {type(value).__name__}).")


def _safe_int(value: Any, *, field: str) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Field {field!r} must be an integer (got {type(value).__name__}).")
    if value < -_MAX_SAFE_INT or value > _MAX_SAFE_INT:
        raise ValueError(f"Field {field!r} exceeds bounded integer range.")
    return value


def _safe_opt_int(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    return _safe_int(value, field=field)


def _safe_float_or_none(value: Any, *, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Field {field!r} must be numeric or null (got {type(value).__name__}).")
    if isinstance(value, int) and (value < -_MAX_SAFE_INT or value > _MAX_SAFE_INT):
        raise ValueError(f"Field {field!r} exceeds bounded numeric range.")
    fval = float(value)
    if not math.isfinite(fval):
        raise ValueError(f"Field {field!r} must be finite.")
    return fval


def _safe_opt_str(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Field {field!r} must be a string or null (got {type(value).__name__}).")
    stripped = value.strip()
    return stripped or None


def _safe_str_list(value: Any, *, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"Field {field!r} must be a list of strings.")
    return [item.strip() for item in value if item.strip()]


def classify_project_lifecycle(state: dict[str, Any]) -> tuple[str, float]:
    """Classify advisory lifecycle posture from extracted capsule scalars."""
    classes = set(_safe_str_list(state.get("source_classes"), field="source_classes"))
    days_num = _safe_float_or_none(state.get("days_since_last_commit"), field="days_since_last_commit")
    dirty = _safe_bool(state.get("dirty_worktree"), field="dirty_worktree")
    ahead = _safe_int(state.get("ahead_count"), field="ahead_count")
    handoff = _safe_bool(state.get("has_machina_handoff"), field="has_machina_handoff")
    total_files = _safe_opt_int(state.get("total_files"), field="total_files")
    _safe_int(state.get("subsystem_count"), field="subsystem_count")
    surfaces = _safe_int(state.get("operational_surface_count"), field="operational_surface_count")
    has_gov = _safe_bool(state.get("has_governance_docs"), field="has_governance_docs")
    _safe_int(state.get("open_review_count"), field="open_review_count")

    if total_files is not None and 0 < total_files <= 4 and surfaces == 0 and not has_gov and not handoff:
        return "ephemeral_experiment", 0.96
    if dirty and handoff:
        return "active_development", 0.97
    if dirty or ahead > 0 or handoff or (days_num is not None and days_num <= 14):
        return "active_development", 0.89
    if total_files is not None and total_files <= 5 and surfaces == 0 and not has_gov:
        return "ephemeral_experiment", 0.87
    if (classes and classes <= {"backup", "inventory", "mirror"}) or (
        days_num is not None and days_num >= 120
    ):
        return "dormant_archive", 0.90
    if has_gov or surfaces > 0 or (total_files is not None and total_files >= 8):
        return "maintained_stable", 0.88
    return "dormant_archive", 0.78


def classify_attention_urgency(state: dict[str, Any]) -> tuple[int, float]:
    """Classify attention urgency (`0..3`) from git drift, reviews, and source health."""
    drift_list = _safe_str_list(state.get("git_drift_codes"), field="git_drift_codes")
    drift = set(drift_list)
    dirty_cnt = _safe_int(state.get("dirty_checkout_count"), field="dirty_checkout_count")
    ahead = _safe_int(state.get("ahead_total"), field="ahead_total")
    behind = _safe_int(state.get("behind_total"), field="behind_total")
    diverged = _safe_bool(state.get("diverged"), field="diverged")
    open_rev = _safe_int(state.get("open_review_count"), field="open_review_count")
    high_rev = _safe_int(state.get("high_severity_review_count"), field="high_severity_review_count")
    health = _safe_opt_str(state.get("source_health_state"), field="source_health_state") or "healthy"

    if (
        not drift
        and dirty_cnt == 0
        and ahead == 0
        and behind == 0
        and not diverged
        and open_rev == 0
        and health == "healthy"
    ):
        return 0, 0.99
    if diverged or dirty_cnt >= 2 or high_rev >= 1:
        return 3, 0.97
    if (
        dirty_cnt >= 1
        or ahead >= 1
        or bool(
            drift
            & {
                "GIT_DIRTY_WORKTREE",
                "GIT_AHEAD_OF_UPSTREAM",
            }
        )
    ):
        return 2, 0.88
    if (
        behind >= 1
        or open_rev >= 1
        or bool(
            drift
            & {
                "GIT_BEHIND_UPSTREAM",
                "GIT_MAIN_BEHIND_ORIGIN",
                "GIT_REMOTE_AHEAD",
                "GIT_DETACHED_HEAD",
            }
        )
        or health != "healthy"
    ):
        return 1, 0.85
    return 0, 0.85


def _parse_iso_days_ago(iso_str: str | None, reference_now: dt.datetime) -> float | None:
    raw = str(iso_str or "").strip()
    if not raw or len(raw) > _MAX_SCALAR_DIGITS * 2:
        return None
    try:
        normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        parsed = dt.datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        delta_days = (reference_now - parsed.astimezone(dt.timezone.utc)).total_seconds() / 86400.0
        return round(max(0.0, delta_days), 2)
    except ValueError:
        return None


def capsule_to_lifecycle_state(
    capsule: dict[str, Any],
    *,
    reference_now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Extract deterministic lifecycle-rule inputs from a compiled project capsule."""
    now_dt = reference_now or dt.datetime.now(dt.timezone.utc)
    obs_list = capsule.get("observations") if isinstance(capsule.get("observations"), list) else []
    source_classes = sorted(
        {str(o.get("source_class")) for o in obs_list if isinstance(o, dict) and o.get("source_class")}
    )

    git_facts = capsule.get("git_facts") if isinstance(capsule.get("git_facts"), dict) else {}
    last_commit_at = (
        git_facts.get("head_commit_at")
        or git_facts.get("last_remote_ref_at")
        or (capsule.get("freshness") or {}).get("content_as_of")
    )
    days_since = _parse_iso_days_ago(last_commit_at, now_dt)

    git_warn_codes = {str(c) for c in (git_facts.get("warning_codes") or [])}
    dirty = (
        bool(git_facts.get("dirty"))
        or ("GIT_DIRTY_WORKTREE" in git_warn_codes)
        or any(
            isinstance(o, dict) and (o.get("git_drift") or {}).get("dirty") is True
            for o in obs_list
        )
    )
    ahead = max(
        [int(git_facts.get("ahead_count") or 0)]
        + [
            int((o.get("git_drift") or {}).get("ahead_count") or 0)
            for o in obs_list
            if isinstance(o, dict)
        ]
    )

    cur_state = capsule.get("current_state") if isinstance(capsule.get("current_state"), dict) else {}
    mc = (
        cur_state.get("machina_continuity")
        if isinstance(cur_state.get("machina_continuity"), dict)
        else {}
    )
    has_handoff = bool(
        mc.get("state") == "available"
        and (
            mc.get("active_unit_id")
            or mc.get("has_checkpoint")
            or mc.get("has_frontier")
            or cur_state.get("next_step")
        )
    )

    ctx_blk = capsule.get("ctx") if isinstance(capsule.get("ctx"), dict) else {}
    raw_tf = ctx_blk.get("total_files")
    total_files: int | None = (
        int(raw_tf)
        if (
            ctx_blk.get("state") == "available"
            and isinstance(raw_tf, int)
            and not isinstance(raw_tf, bool)
        )
        else None
    )
    subsystems = ctx_blk.get("subsystems") if isinstance(ctx_blk.get("subsystems"), list) else []
    sub_count = int(ctx_blk.get("subsystem_count") or len(subsystems))
    op_count = int(
        ctx_blk.get("operational_surface_count") or len(ctx_blk.get("operational_surfaces") or [])
    )
    auth_bounds = (
        ctx_blk.get("authority_boundaries")
        if isinstance(ctx_blk.get("authority_boundaries"), list)
        else []
    )
    has_gov = any(
        (isinstance(b, dict) and b.get("kind") in {"authority", "architecture"})
        or ":authority:" in str(b)
        or ":architecture:" in str(b)
        or any(
            tok in str(b).lower()
            for tok in ("agents.md", "architecture.md", "system.md", "claude.md")
        )
        for b in auth_bounds
    )
    open_rev = int((capsule.get("review") or {}).get("open_review_count") or 0)

    return {
        "source_classes": source_classes,
        "days_since_last_commit": days_since,
        "dirty_worktree": dirty,
        "ahead_count": ahead,
        "has_machina_handoff": has_handoff,
        "total_files": total_files,
        "subsystem_count": sub_count,
        "operational_surface_count": op_count,
        "has_governance_docs": has_gov,
        "open_review_count": open_rev,
    }


def capsule_to_urgency_state(capsule: dict[str, Any]) -> dict[str, Any]:
    """Extract deterministic urgency-rule inputs from a compiled project capsule."""
    obs_list = capsule.get("observations") if isinstance(capsule.get("observations"), list) else []
    git_facts = capsule.get("git_facts") if isinstance(capsule.get("git_facts"), dict) else {}
    code_set: set[str] = {str(c) for c in (git_facts.get("warning_codes") or []) if str(c).strip()}
    for o in obs_list:
        if isinstance(o, dict):
            for c in o.get("warning_codes") or []:
                if str(c).strip():
                    code_set.add(str(c).strip())
    drift_codes = sorted(code_set)

    obs_dirty_cnt = sum(
        1
        for o in obs_list
        if isinstance(o, dict) and (o.get("git_drift") or {}).get("dirty") is True
    )
    dirty_cnt = max(obs_dirty_cnt, 1 if git_facts.get("dirty") is True else 0)
    ahead_total = max(
        [int(git_facts.get("ahead_count") or 0)]
        + [
            int((o.get("git_drift") or {}).get("ahead_count") or 0)
            for o in obs_list
            if isinstance(o, dict)
        ]
    )
    behind_total = max(
        [int(git_facts.get("behind_count") or 0), int(git_facts.get("main_behind_count") or 0)]
        + [
            int((o.get("git_drift") or {}).get("behind_count") or 0)
            for o in obs_list
            if isinstance(o, dict)
        ]
    )
    diverged = (
        str(git_facts.get("drift_state") or "") == "diverged"
        or any("DIVERGED" in c for c in drift_codes)
        or (ahead_total > 0 and behind_total > 0)
        or (ahead_total > 0 and "GIT_REMOTE_AHEAD" in code_set)
    )

    review_blk = capsule.get("review") if isinstance(capsule.get("review"), dict) else {}
    open_rev = int(review_blk.get("open_review_count") or 0)
    rev_items = review_blk.get("items") if isinstance(review_blk.get("items"), list) else []
    high_rev = sum(
        1
        for item in rev_items
        if isinstance(item, dict) and item.get("severity") in {"high", "error"}
    )

    pref = (
        capsule.get("preferred_location")
        if isinstance(capsule.get("preferred_location"), dict)
        else {}
    )
    health = "healthy" if pref.get("state") in {"resolved", None} else "degraded"

    return {
        "git_drift_codes": drift_codes,
        "dirty_checkout_count": dirty_cnt,
        "ahead_total": ahead_total,
        "behind_total": behind_total,
        "diverged": diverged,
        "open_review_count": open_rev,
        "high_severity_review_count": high_rev,
        "source_health_state": health,
    }


def infer_capsule_posture(
    capsule: dict[str, Any],
    *,
    reference_now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Compute deterministic advisory lifecycle posture and attention urgency for a capsule."""
    lc_state = capsule_to_lifecycle_state(capsule, reference_now=reference_now)
    urg_state = capsule_to_urgency_state(capsule)
    lifecycle, lc_conf = classify_project_lifecycle(lc_state)
    urgency, urg_conf = classify_attention_urgency(urg_state)
    return {
        "lifecycle_posture": lifecycle,
        "lifecycle_confidence": lc_conf,
        "attention_urgency": urgency,
        "urgency_confidence": urg_conf,
    }


def infer_capsule_posture_reasons(
    capsule: dict[str, Any],
    *,
    reference_now: dt.datetime | None = None,
) -> dict[str, list[str]]:
    """Return deterministic reason codes explaining lifecycle posture and urgency."""
    lc_state = capsule_to_lifecycle_state(capsule, reference_now=reference_now)
    urg_state = capsule_to_urgency_state(capsule)

    lc_reasons: list[str] = []
    total_files = lc_state.get("total_files")
    days_num = lc_state.get("days_since_last_commit")
    classes = set(lc_state.get("source_classes") or [])
    if lc_state.get("dirty_worktree"):
        lc_reasons.append("DIRTY_WORKTREE")
    if int(lc_state.get("ahead_count") or 0) > 0:
        lc_reasons.append("AHEAD_OF_UPSTREAM")
    if lc_state.get("has_machina_handoff"):
        lc_reasons.append("ACTIVE_MACHINA_CONTINUITY")
    if days_num is not None and days_num <= 14:
        lc_reasons.append("RECENT_COMMIT_ACTIVITY")
    elif days_num is not None and days_num >= 120:
        lc_reasons.append("STALE_COMMIT_AGE")
    if classes and classes <= {"backup", "inventory", "mirror"}:
        lc_reasons.append("NON_LIVE_MANIFESTATIONS_ONLY")
    if lc_state.get("has_governance_docs"):
        lc_reasons.append("GOVERNANCE_DOCS_PRESENT")
    if int(lc_state.get("operational_surface_count") or 0) > 0:
        lc_reasons.append("OPERATIONAL_SURFACES_PRESENT")
    if total_files is not None and total_files <= 5:
        lc_reasons.append("SMALL_FILE_FOOTPRINT")
    elif total_files is not None and total_files >= 8:
        lc_reasons.append("ESTABLISHED_CODEBASE")
    if not lc_reasons:
        lc_reasons.append("DEFAULT_DORMANT_FALLBACK")

    urg_reasons: list[str] = []
    if urg_state.get("diverged"):
        urg_reasons.append("DIVERGED_CHECKOUT")
    dirty_cnt = int(urg_state.get("dirty_checkout_count") or 0)
    if dirty_cnt >= 2:
        urg_reasons.append("MULTIPLE_DIRTY_CHECKOUTS")
    elif dirty_cnt == 1:
        urg_reasons.append("DIRTY_WORKTREE")
    if int(urg_state.get("high_severity_review_count") or 0) >= 1:
        urg_reasons.append("HIGH_SEVERITY_REVIEW")
    elif int(urg_state.get("open_review_count") or 0) >= 1:
        urg_reasons.append("OPEN_IDENTITY_REVIEW")
    if int(urg_state.get("ahead_total") or 0) >= 1:
        urg_reasons.append("UNPUSHED_COMMITS")
    if int(urg_state.get("behind_total") or 0) >= 1:
        urg_reasons.append("BEHIND_UPSTREAM")
    for code in urg_state.get("git_drift_codes") or []:
        if code in {"GIT_REMOTE_AHEAD", "GIT_MAIN_BEHIND_ORIGIN", "GIT_DETACHED_HEAD"}:
            urg_reasons.append(code)
    if str(urg_state.get("source_health_state") or "healthy") != "healthy":
        urg_reasons.append("PREFERRED_SOURCE_DEGRADED")
    if not urg_reasons:
        urg_reasons.append("CLEAN_HEALTHY_STATE")

    return {
        "lifecycle_reasons": lc_reasons,
        "urgency_reasons": urg_reasons,
    }
