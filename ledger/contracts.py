from __future__ import annotations

SUPPORTED_DISCOVERY_MODES = frozenset({"children", "git_repos", "self", "inventory_policy"})
VALUE_STATES = frozenset({"known", "unknown", "unavailable", "stale", "absent", "conflicted", "not_applicable"})
CAPABILITY_STATES = frozenset({"available", "unavailable", "degraded"})
SOURCE_HEALTH_STATES = frozenset({"available", "unavailable", "degraded"})
SOURCE_RESULT_STATES = frozenset({"observed", "observed_empty", "unavailable"})


def capability(state: str, *, reason_code: str | None = None, detail: str | None = None) -> dict:
    if state not in CAPABILITY_STATES:
        raise ValueError(f"Unsupported capability state: {state}")
    payload: dict[str, str] = {"state": state}
    if reason_code:
        payload["reason_code"] = reason_code
    if detail:
        payload["detail"] = detail
    return payload
