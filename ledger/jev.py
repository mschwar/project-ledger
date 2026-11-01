"""Governed Type 1 Jev (`jev-1.13.0`) System-One advisory decision layer for Project Ledger.

Implements a zero-dependency stdlib client and governance wrapper over TypeSafe's
`POST https://api.typesafe.ai/v1/systemone` endpoint (`model: "jev-1.13.0"`), strictly
obeying Project Ledger's Architectural Laws:

- **Offline-first by default (`LEDGER_JEV=off`):** No network call is ever performed
  unless `LEDGER_JEV` is explicitly set to `auto|shadow|advisory|routing|actuating` or
  the operator passes `--live` (`force_live=True`).
- **Laws 1, 2, & 4 (Permanent `ADVISORY` Authority Ceiling):** Identity-authority heads
  (`authority: true`, e.g. `weak_overlap_relation`) have a permanent `ADVISORY` ceiling
  enforced by `validate_head()` and `decide()`. Jev outputs never mutate
  `registry/identity-decisions.json`, `registry/project-lineage.json`, or any target repo.
- **Law 3 (Fact vs Inference Separation):** Advisory inferences (`project_lifecycle_inference`,
  `estate_attention_urgency`, `weak_overlap_relation`) are returned in an explicit
  `jev_advisory` envelope and never overwrite observed capsule facts.
- **Law 10 (Strict Read-Only MCP):** When `record_trace=False` (always enforced in
  `ledger_mcp`), zero bytes are written anywhere on disk.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .capsules import show_payload

DEFAULT_JEV_MODEL = "jev-1.13.0"
DEFAULT_JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
JEV_INPUT_USD_PER_MTOK = 0.042

PROMOTION_STATES = ("SHADOW", "ADVISORY", "ROUTING", "ACTUATING")
_PROMOTION_RANK = {state: idx for idx, state in enumerate(PROMOTION_STATES)}
HEAD_TYPES = ("noul", "choice", "score")
TIERS = ("code", "judge", "deliberate")
VALID_JEV_MODES = ("off", "auto", "shadow", "advisory", "routing", "actuating")

BUILTIN_HEADS: dict[str, dict[str, Any]] = {
    "weak_overlap_relation": {
        "id": "weak_overlap_relation",
        "version": "1.0.0",
        "type": "choice",
        "authority": True,
        "tier": "judge",
        "promotion_state": "ADVISORY",
        "ceiling": "ADVISORY",
        "instructions": (
            "Classify the identity relationship between two Project Ledger repository observations "
            "that share a weak signal (such as name token or partial slug overlap) without a "
            "deterministic remote-URL or git-root match. Output is strictly advisory for human triage."
        ),
        "options": {
            "distinct_projects": (
                "Separate, unrelated codebases that merely share a generic name token, org prefix, "
                "or boilerplate template word."
            ),
            "related_distinct_siblings": (
                "Separate sibling repositories in the same ecosystem or workflow (e.g. a companion "
                "CLI, SDK, or infra repo) that must remain distinct canonical projects."
            ),
            "redundant_checkout_or_mirror": (
                "Secondary mirror, backup snapshot, worktree, or stale copy of the exact same "
                "repository on another path or host."
            ),
            "same_project_candidate": (
                "High-likelihood manifestations of the exact same canonical project across paths or "
                "hosts warranting human merge review."
            ),
        },
        "state_keys": [
            "shared_weak",
            "left_name",
            "right_name",
            "left_source_class",
            "right_source_class",
            "left_remote_url",
            "right_remote_url",
            "left_readme_excerpt",
            "right_readme_excerpt",
            "same_normalized_remote",
            "exact_name_match",
        ],
        "thresholds": {"act_min_confidence": 0.55},
        "provider": {"name": "jev", "model": DEFAULT_JEV_MODEL},
        "fallback": "code",
    },
    "project_lifecycle_inference": {
        "id": "project_lifecycle_inference",
        "version": "1.0.0",
        "type": "choice",
        "authority": False,
        "tier": "judge",
        "promotion_state": "ADVISORY",
        "ceiling": "ADVISORY",
        "instructions": (
            "Infer the advisory operational lifecycle posture of a canonical project from observed "
            "git freshness, worktree activity, Machina handoff presence, structural ctx surface count, "
            "and source classes. Keep strictly advisory and separate from observed facts."
        ),
        "options": {
            "active_development": (
                "Actively worked project with recent commits (<= 14 days), dirty worktree, unpushed "
                "commits ahead of upstream, or an active Machina session handoff."
            ),
            "maintained_stable": (
                "Structured, clean codebase with governance docs, multiple subsystems, or operational "
                "surfaces and no recent churn or drift."
            ),
            "dormant_archive": (
                "Long-inactive (>= 120 days since commit) or backup/archive-only project with clean "
                "or absent live worktrees and no active handoff."
            ),
            "ephemeral_experiment": (
                "Tiny scratch spike or prototype (<= 5 files, zero operational surfaces, no governance "
                "docs, and no active continuity handoff)."
            ),
        },
        "state_keys": [
            "source_classes",
            "days_since_last_commit",
            "dirty_worktree",
            "ahead_count",
            "has_machina_handoff",
            "total_files",
            "subsystem_count",
            "operational_surface_count",
            "has_governance_docs",
            "open_review_count",
        ],
        "thresholds": {"act_min_confidence": 0.50},
        "provider": {"name": "jev", "model": DEFAULT_JEV_MODEL},
        "fallback": "code",
    },
    "estate_attention_urgency": {
        "id": "estate_attention_urgency",
        "version": "1.0.0",
        "type": "score",
        "authority": False,
        "tier": "judge",
        "promotion_state": "ADVISORY",
        "ceiling": "ADVISORY",
        "instructions": (
            "Score how urgently a canonical project warrants operator attention from 0 to 3 based on "
            "git drift across checkouts, diverged branches, dirty worktrees, and open identity review items."
        ),
        "criteria": [
            "0: Healthy, clean project with zero git drift, zero open identity reviews, and available sources",
            "1: Low-priority hygiene note (backup/inventory-only manifestation or minor non-blocking review without git drift)",
            "2: Moderate attention needed (single dirty live worktree or unpushed commits ahead of upstream without history divergence)",
            "3: High urgency (diverged git history, multiple dirty checkouts across paths/hosts, or high-severity open identity review)",
        ],
        "state_keys": [
            "git_drift_codes",
            "dirty_checkout_count",
            "ahead_total",
            "behind_total",
            "diverged",
            "open_review_count",
            "high_severity_review_count",
            "source_health_state",
        ],
        "thresholds": {"act_min_confidence": 0.45},
        "provider": {"name": "jev", "model": DEFAULT_JEV_MODEL},
        "fallback": "code",
    },
}


def _repo_heads_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "heads"


def validate_head(head: dict[str, Any], *, expected_id: str | None = None) -> list[str]:
    """Validate a Jev head contract against governance invariants."""
    errors: list[str] = []
    if not isinstance(head, dict):
        return ["Head definition must be a JSON object."]

    required = (
        "id",
        "version",
        "type",
        "authority",
        "tier",
        "promotion_state",
        "ceiling",
        "instructions",
        "state_keys",
        "thresholds",
        "provider",
        "fallback",
    )
    for key in required:
        if key not in head:
            errors.append(f"Missing required field '{key}'.")
    if errors:
        return errors

    hid = head["id"]
    if not isinstance(hid, str) or not hid.strip():
        errors.append("'id' must be a non-empty string.")
    elif expected_id is not None and hid != expected_id:
        errors.append(f"Head id '{hid}' does not match expected '{expected_id}'.")

    htype = head["type"]
    if htype not in HEAD_TYPES:
        errors.append(f"Unsupported head type '{htype}'; expected one of {HEAD_TYPES}.")

    if head["tier"] not in TIERS:
        errors.append(f"Unsupported tier '{head['tier']}'; expected one of {TIERS}.")

    promo = head["promotion_state"]
    ceiling = head["ceiling"]
    if promo not in _PROMOTION_RANK:
        errors.append(f"Invalid promotion_state '{promo}'.")
    if ceiling not in _PROMOTION_RANK:
        errors.append(f"Invalid ceiling '{ceiling}'.")
    if promo in _PROMOTION_RANK and ceiling in _PROMOTION_RANK:
        if _PROMOTION_RANK[promo] > _PROMOTION_RANK[ceiling]:
            errors.append(f"promotion_state '{promo}' exceeds ceiling '{ceiling}'.")

    if bool(head["authority"]):
        if ceiling != "ADVISORY":
            errors.append("Authority heads ('authority': true) have a permanent ceiling ADVISORY.")
        if promo in _PROMOTION_RANK and _PROMOTION_RANK[promo] > _PROMOTION_RANK["ADVISORY"]:
            errors.append("Authority heads cannot be promoted above ADVISORY.")

    provider = head["provider"]
    if not isinstance(provider, dict):
        errors.append("'provider' must be an object.")
    else:
        model = str(provider.get("model", ""))
        if promo != "SHADOW" and ("latest" in model or not re.search(r"\d+\.\d+", model)):
            errors.append(
                f"Unpinned model '{model}': model aliases are only permitted in SHADOW promotion_state."
            )

    if htype == "choice":
        opts = head.get("criteria") if isinstance(head.get("criteria"), dict) else head.get("options")
        if not isinstance(opts, dict) or len(opts) < 2:
            errors.append("choice head requires 'options' (or 'criteria') object with >= 2 choices.")
    elif htype == "score":
        crit = head.get("criteria")
        if not isinstance(crit, list) or len(crit) < 2:
            errors.append("score head requires 'criteria' array with >= 2 levels.")
    elif htype == "noul":
        thresholds = head.get("thresholds")
        if not isinstance(thresholds, dict) or "true_min_prob" not in thresholds:
            errors.append("noul head requires 'thresholds.true_min_prob'.")

    if not isinstance(head.get("state_keys"), list) or not head["state_keys"]:
        errors.append("'state_keys' must be a non-empty list.")

    return errors


_HEAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _is_valid_prob(value: Any) -> bool:
    """Return True iff `value` is a finite non-boolean float/int in [0.0, 1.0]."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and 0.0 <= float(value) <= 1.0
    )


def list_head_ids(heads_dir: Path | None = None) -> list[str]:
    ids = set(BUILTIN_HEADS.keys())
    eff_dir = heads_dir or _repo_heads_dir()
    if eff_dir.is_symlink():
        return sorted(ids)
    resolved_dir = eff_dir.resolve()
    if resolved_dir.is_dir():
        for p in resolved_dir.glob("*.json"):
            if p.is_symlink() or not p.resolve().is_relative_to(resolved_dir):
                continue
            if _HEAD_ID_RE.fullmatch(p.stem):
                ids.add(p.stem)
    return sorted(ids)


def load_head(head_id: str, heads_dir: Path | None = None) -> dict[str, Any]:
    if not isinstance(head_id, str) or not _HEAD_ID_RE.fullmatch(head_id):
        raise ValueError(
            f"Invalid Jev head ID {head_id!r}: must match ^[A-Za-z0-9_-]+$"
        )
    raw_dir = heads_dir or _repo_heads_dir()
    if raw_dir.is_symlink():
        raise ValueError(f"Refusing to load Jev head from symlinked heads_dir: {raw_dir}")
    eff_dir = raw_dir.resolve()
    raw_candidate = eff_dir / f"{head_id}.json"
    if raw_candidate.is_symlink():
        raise ValueError(f"Refusing to load symlinked Jev head file: {raw_candidate}")
    candidate = raw_candidate.resolve()
    if candidate.parent == eff_dir and candidate.is_file():
        try:
            raw = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Malformed Jev head '{head_id}': {exc}") from exc
    elif head_id in BUILTIN_HEADS:
        raw = copy.deepcopy(BUILTIN_HEADS[head_id])
    else:
        raise ValueError(f"Unknown Jev head '{head_id}'. Available: {', '.join(list_head_ids(eff_dir))}")

    errs = validate_head(raw, expected_id=head_id)
    if errs:
        raise ValueError(f"Invalid Jev head '{head_id}': {'; '.join(errs)}")
    return raw


def list_heads(heads_dir: Path | None = None) -> list[dict[str, Any]]:
    heads: list[dict[str, Any]] = []
    for hid in list_head_ids(heads_dir=heads_dir):
        try:
            heads.append(load_head(hid, heads_dir=heads_dir))
        except ValueError:
            if hid in BUILTIN_HEADS:
                heads.append(copy.deepcopy(BUILTIN_HEADS[hid]))
    return heads


def effective_jev_mode() -> str:
    """Resolve active Jev mode from `LEDGER_JEV` (defaults to `off` for strict offline safety)."""
    raw = os.environ.get("LEDGER_JEV", "off").strip().lower()
    return raw if raw in VALID_JEV_MODES else "off"


def _resolve_api_key(*, allow_vault: bool = False) -> str | None:
    """Resolve `TYPESAFE_API_KEY` from env, or explicit user-provided `op` ref when opted in."""
    env_key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if env_key:
        return env_key

    use_op = allow_vault or os.environ.get("LEDGER_JEV_USE_OP", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    op_ref = (
        os.environ.get("LEDGER_JEV_OP_REF", "").strip()
        or os.environ.get("TYPESAFE_OP_REF", "").strip()
    )
    if use_op and op_ref:
        try:
            proc = subprocess.run(
                ["op", "read", op_ref],
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def _project_state(state: dict[str, Any], state_keys: list[str]) -> dict[str, Any]:
    return {k: state.get(k) for k in state_keys if k in state}


def state_digest(projected_state: dict[str, Any]) -> str:
    canonical = json.dumps(projected_state, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def noul_decision_from_prob(prob_true: float, true_min_prob: float) -> tuple[bool, float]:
    p = max(0.0, min(1.0, float(prob_true)))
    t = max(1e-6, min(1.0 - 1e-6, float(true_min_prob)))
    if p >= t:
        conf = 0.5 + 0.5 * ((p - t) / (1.0 - t))
        return True, round(min(1.0, max(0.5, conf)), 6)
    conf = 0.5 + 0.5 * ((t - p) / t)
    return False, round(min(1.0, max(0.5, conf)), 6)


def _norm_url(url: str | None) -> str:
    raw = str(url or "").strip().lower()
    if not raw:
        return ""
    raw = re.sub(r"^(?:https?://|ssh://|git@)", "", raw)
    raw = raw.replace(":", "/")
    if raw.endswith(".git"):
        raw = raw[:-4]
    return raw.strip("/")


def _token_set(text: str | None) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", str(text or "").lower()) if len(t) >= 3}


# ── Deterministic `pre_rule` & `code` Fallback Implementations ──────────────


def _pre_rule_weak_overlap_relation(state: dict[str, Any]) -> tuple[str, float] | None:
    same_remote = bool(state.get("same_normalized_remote"))
    l_remote = _norm_url(state.get("left_remote_url"))
    r_remote = _norm_url(state.get("right_remote_url"))
    if (same_remote and l_remote) or (l_remote and r_remote and l_remote == r_remote):
        classes = {str(state.get("left_source_class") or ""), str(state.get("right_source_class") or "")}
        if classes & {"mirror", "backup"}:
            return "redundant_checkout_or_mirror", 0.98
        return "same_project_candidate", 0.96
    if l_remote and r_remote and l_remote != r_remote:
        l_org = l_remote.split("/")[1] if "/" in l_remote else ""
        r_org = r_remote.split("/")[1] if "/" in r_remote else ""
        if l_org and r_org and l_org != r_org and not bool(state.get("exact_name_match")):
            return "distinct_projects", 0.95
    return None


def _rule_weak_overlap_relation(state: dict[str, Any]) -> tuple[str, float]:
    hit = _pre_rule_weak_overlap_relation(state)
    if hit is not None:
        return hit

    l_name = str(state.get("left_name") or "").strip().lower()
    r_name = str(state.get("right_name") or "").strip().lower()
    exact_name = bool(state.get("exact_name_match")) or (bool(l_name) and l_name == r_name)
    classes = {str(state.get("left_source_class") or ""), str(state.get("right_source_class") or "")}
    l_remote = _norm_url(state.get("left_remote_url"))
    r_remote = _norm_url(state.get("right_remote_url"))
    l_tokens = _token_set(state.get("left_readme_excerpt"))
    r_tokens = _token_set(state.get("right_readme_excerpt"))
    overlap_ratio = (
        len(l_tokens & r_tokens) / max(1, min(len(l_tokens), len(r_tokens)))
        if (l_tokens and r_tokens)
        else 0.0
    )

    if l_remote and r_remote and l_remote != r_remote:
        if overlap_ratio >= 0.25 or (
            l_name and r_name and (l_name in r_name or r_name in l_name) and l_name != r_name
        ):
            return "related_distinct_siblings", 0.86
        return "distinct_projects", 0.88

    if exact_name:
        if (classes & {"mirror", "backup"}) and (not l_tokens or not r_tokens or overlap_ratio >= 0.35):
            return "redundant_checkout_or_mirror", 0.90
        if overlap_ratio >= 0.40 or (not l_tokens and not r_tokens):
            return "same_project_candidate", 0.85

    if overlap_ratio >= 0.25 and l_name != r_name:
        return "related_distinct_siblings", 0.82
    return "distinct_projects", 0.84


def _pre_rule_project_lifecycle_inference(state: dict[str, Any]) -> tuple[str, float] | None:
    total_files = int(state.get("total_files") or 0)
    surfaces = int(state.get("operational_surface_count") or 0)
    has_gov = bool(state.get("has_governance_docs"))
    has_handoff = bool(state.get("has_machina_handoff"))
    if 0 < total_files <= 4 and surfaces == 0 and not has_gov and not has_handoff:
        return "ephemeral_experiment", 0.96
    if bool(state.get("dirty_worktree")) and has_handoff:
        return "active_development", 0.97
    return None


def _rule_project_lifecycle_inference(state: dict[str, Any]) -> tuple[str, float]:
    hit = _pre_rule_project_lifecycle_inference(state)
    if hit is not None:
        return hit

    classes = {str(c) for c in (state.get("source_classes") or [])}
    days = state.get("days_since_last_commit")
    days_num = float(days) if isinstance(days, (int, float)) else None
    dirty = bool(state.get("dirty_worktree"))
    ahead = int(state.get("ahead_count") or 0)
    handoff = bool(state.get("has_machina_handoff"))
    total_files = int(state.get("total_files") or 0)
    surfaces = int(state.get("operational_surface_count") or 0)
    has_gov = bool(state.get("has_governance_docs"))

    if dirty or ahead > 0 or handoff or (days_num is not None and days_num <= 14):
        return "active_development", 0.89
    if total_files <= 5 and surfaces == 0 and not has_gov:
        return "ephemeral_experiment", 0.87
    if (classes and classes <= {"backup", "inventory", "mirror"}) or (
        days_num is not None and days_num >= 120
    ):
        return "dormant_archive", 0.90
    if has_gov or surfaces > 0 or total_files >= 8:
        return "maintained_stable", 0.88
    return "dormant_archive", 0.78


def _pre_rule_estate_attention_urgency(state: dict[str, Any]) -> tuple[int, float] | None:
    drift = [str(c) for c in (state.get("git_drift_codes") or [])]
    dirty_cnt = int(state.get("dirty_checkout_count") or 0)
    ahead = int(state.get("ahead_total") or 0)
    behind = int(state.get("behind_total") or 0)
    diverged = bool(state.get("diverged"))
    open_rev = int(state.get("open_review_count") or 0)
    health = str(state.get("source_health_state") or "healthy")
    if not drift and dirty_cnt == 0 and ahead == 0 and behind == 0 and not diverged and open_rev == 0 and health == "healthy":
        return 0, 0.99
    if diverged or dirty_cnt >= 2 or int(state.get("high_severity_review_count") or 0) >= 1:
        return 3, 0.97
    return None


def _rule_estate_attention_urgency(state: dict[str, Any]) -> tuple[int, float]:
    hit = _pre_rule_estate_attention_urgency(state)
    if hit is not None:
        return hit

    dirty_cnt = int(state.get("dirty_checkout_count") or 0)
    ahead = int(state.get("ahead_total") or 0)
    behind = int(state.get("behind_total") or 0)
    open_rev = int(state.get("open_review_count") or 0)
    drift = {str(c) for c in (state.get("git_drift_codes") or [])}

    if dirty_cnt >= 1 or ahead >= 1 or ("GIT_WORKTREE_DIRTY" in drift) or ("GIT_AHEAD_UNPUSHED" in drift):
        return 2, 0.88
    if behind >= 1 or open_rev >= 1 or str(state.get("source_health_state") or "healthy") != "healthy":
        return 1, 0.85
    return 0, 0.85


def apply_pre_rule(head_id: str, state: dict[str, Any]) -> tuple[Any, float] | None:
    if head_id == "weak_overlap_relation":
        return _pre_rule_weak_overlap_relation(state)
    if head_id == "project_lifecycle_inference":
        return _pre_rule_project_lifecycle_inference(state)
    if head_id == "estate_attention_urgency":
        return _pre_rule_estate_attention_urgency(state)
    return None


def run_code_fallback(head: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    hid = head["id"]
    t0 = time.perf_counter()
    if hid == "weak_overlap_relation":
        out, conf = _rule_weak_overlap_relation(state)
    elif hid == "project_lifecycle_inference":
        out, conf = _rule_project_lifecycle_inference(state)
    elif hid == "estate_attention_urgency":
        out, conf = _rule_estate_attention_urgency(state)
    else:
        return {
            "output": None,
            "confidence": 0.0,
            "probabilities": {},
            "provider": "code",
            "model": "deterministic-v1",
            "abstained": True,
            "abstain_reason": f"NO_CODE_FALLBACK_FOR_HEAD:{hid}",
            "latency_ms": round((time.perf_counter() - t0) * 1000.0, 3),
            "input_tokens": 0,
            "est_cost_usd": 0.0,
        }
    return {
        "output": out,
        "confidence": round(conf, 4),
        "probabilities": {str(out): round(conf, 4)},
        "provider": "code",
        "model": "deterministic-v1",
        "abstained": False,
        "abstain_reason": None,
        "latency_ms": round((time.perf_counter() - t0) * 1000.0, 3),
        "input_tokens": 0,
        "est_cost_usd": 0.0,
    }


def _build_wire_question(head: dict[str, Any]) -> dict[str, Any]:
    htype = head["type"]
    q: dict[str, Any] = {"type": htype, "instructions": head["instructions"]}
    if htype == "choice":
        opts = head.get("criteria") if isinstance(head.get("criteria"), dict) else head["options"]
        q["criteria"] = dict(opts)
    elif htype == "score":
        q["criteria"] = list(head["criteria"])
    return q


def _parse_wire_answer(head: dict[str, Any], ans: dict[str, Any]) -> tuple[Any, float, dict[str, float], float | None]:
    htype = head["type"]
    raw_probs_obj = ans.get("probabilities")
    if raw_probs_obj is not None:
        if not isinstance(raw_probs_obj, dict) or any(not _is_valid_prob(v) for v in raw_probs_obj.values()):
            raise ValueError(f"Invalid probabilities for '{head['id']}'")
        probs = {str(k): float(v) for k, v in raw_probs_obj.items()}
    else:
        probs = {}

    raw_conf = ans.get("confidence")
    if raw_conf is not None and not _is_valid_prob(raw_conf):
        raise ValueError(f"Invalid confidence for '{head['id']}'")

    if htype == "noul":
        raw_noul = ans.get("noul")
        if raw_noul is not None:
            if not _is_valid_prob(raw_noul):
                raise ValueError(f"Invalid noul probability for '{head['id']}'")
            p_true = float(raw_noul)
        else:
            p_true = float(probs.get("true", 1.0 if ans.get("answer") is True else 0.0))
        if not probs:
            probs = {"true": round(p_true, 6), "false": round(1.0 - p_true, 6)}
        true_min = float(head.get("thresholds", {}).get("true_min_prob", 0.5))
        decision, conf = noul_decision_from_prob(p_true, true_min)
        return decision, conf, probs, None

    if htype == "choice":
        valid_opts = head.get("criteria") if isinstance(head.get("criteria"), dict) else head.get("options", {})
        choice = ans.get("choice")
        if not isinstance(choice, str) or choice not in valid_opts:
            if probs:
                choice = max(probs.items(), key=lambda kv: kv[1])[0]
            else:
                raise ValueError(f"Malformed choice answer for '{head['id']}'")
        if choice not in valid_opts:
            raise ValueError(f"Invalid choice '{choice}' for '{head['id']}'")
        conf = float(raw_conf) if raw_conf is not None else float(probs.get(choice, 0.0))
        return choice, round(conf, 6), probs, None

    if htype == "score":
        raw_score = ans.get("score")
        if raw_score is not None:
            if (
                not isinstance(raw_score, (int, float))
                or isinstance(raw_score, bool)
                or not math.isfinite(float(raw_score))
            ):
                raise ValueError(f"Invalid score for '{head['id']}'")
            expected: float | None = float(raw_score)
        else:
            expected = None
        if probs:
            top_k, top_p = max(probs.items(), key=lambda kv: kv[1])
            discrete = int(top_k)
            conf = float(raw_conf) if raw_conf is not None else float(top_p)
        elif expected is not None:
            discrete = int(round(expected))
            conf = float(raw_conf) if raw_conf is not None else 0.5
        else:
            raise ValueError(f"Malformed score answer for '{head['id']}'")
        return discrete, round(conf, 6), probs, expected

    raise ValueError(f"Unsupported head type '{htype}'")


def run_jev(
    heads: list[dict[str, Any]],
    state: dict[str, Any],
    *,
    allow_vault: bool = False,
    force_live: bool = False,
    skip_pre_rule: bool = False,
    timeout: float = 15.0,
) -> dict[str, dict[str, Any]]:
    """Execute one or more Jev heads against shared *state* in a single `/v1/systemone` batch."""
    results: dict[str, dict[str, Any]] = {}
    pending_heads: list[dict[str, Any]] = []

    for head in heads:
        hid = head["id"]
        if not skip_pre_rule:
            pre = apply_pre_rule(hid, state)
            if pre is not None:
                val, conf = pre
                results[hid] = {
                    "output": val,
                    "confidence": conf,
                    "probabilities": {str(val): conf},
                    "provider": "pre_rule",
                    "model": "pre_rule-v1",
                    "abstained": False,
                    "abstain_reason": None,
                    "latency_ms": 0.0,
                    "input_tokens": 0,
                    "est_cost_usd": 0.0,
                }
                continue
        pending_heads.append(head)

    if not pending_heads:
        return results

    mode = effective_jev_mode()
    if mode == "off" and not force_live:
        for head in pending_heads:
            results[head["id"]] = {
                "output": None,
                "confidence": 0.0,
                "probabilities": {},
                "provider": "jev",
                "model": head["provider"]["model"],
                "abstained": True,
                "abstain_reason": "LEDGER_JEV=off",
                "latency_ms": 0.0,
                "input_tokens": 0,
                "est_cost_usd": 0.0,
            }
        return results

    api_key = _resolve_api_key(allow_vault=allow_vault)
    if not api_key:
        for head in pending_heads:
            results[head["id"]] = {
                "output": None,
                "confidence": 0.0,
                "probabilities": {},
                "provider": "jev",
                "model": head["provider"]["model"],
                "abstained": True,
                "abstain_reason": "JEV_UNAVAILABLE: TYPESAFE_API_KEY not set",
                "latency_ms": 0.0,
                "input_tokens": 0,
                "est_cost_usd": 0.0,
            }
        return results

    union_keys: list[str] = []
    seen_k: set[str] = set()
    for head in pending_heads:
        for k in head["state_keys"]:
            if k not in seen_k:
                seen_k.add(k)
                union_keys.append(k)

    projected = _project_state(state, union_keys)
    model_name = str(pending_heads[0]["provider"].get("model") or DEFAULT_JEV_MODEL)
    endpoint = os.environ.get("LEDGER_JEV_ENDPOINT", DEFAULT_JEV_ENDPOINT).strip() or DEFAULT_JEV_ENDPOINT

    wire_body = {
        "model": model_name,
        "state": projected,
        "questions": {h["id"]: _build_wire_question(h) for h in pending_heads},
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(wire_body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "project-ledger-jev/0.2",
        },
        method="POST",
    )

    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_bytes = resp.read()
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        payload = json.loads(raw_bytes.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        reason = f"JEV_UNAVAILABLE: HTTP {exc.code}"
        try:
            exc.close()
        except OSError:
            pass
        for head in pending_heads:
            results[head["id"]] = {
                "output": None,
                "confidence": 0.0,
                "probabilities": {},
                "provider": "jev",
                "model": model_name,
                "abstained": True,
                "abstain_reason": reason,
                "latency_ms": latency_ms,
                "input_tokens": 0,
                "est_cost_usd": 0.0,
            }
        return results
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        reason = f"JEV_UNAVAILABLE: {type(exc).__name__}"
        for head in pending_heads:
            results[head["id"]] = {
                "output": None,
                "confidence": 0.0,
                "probabilities": {},
                "provider": "jev",
                "model": model_name,
                "abstained": True,
                "abstain_reason": reason,
                "latency_ms": latency_ms,
                "input_tokens": 0,
                "est_cost_usd": 0.0,
            }
        return results

    answers = payload.get("answers") if isinstance(payload.get("answers"), dict) else {}
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    total_in_tok = int(usage.get("input_tokens") or 0)
    n_heads = max(1, len(pending_heads))
    per_head_tok = total_in_tok // n_heads
    remainder_tok = total_in_tok % n_heads

    for idx, head in enumerate(pending_heads):
        hid = head["id"]
        h_tok = per_head_tok + (1 if idx < remainder_tok else 0)
        h_cost = round((h_tok / 1_000_000.0) * JEV_INPUT_USD_PER_MTOK, 9)
        ans = answers.get(hid)
        if not isinstance(ans, dict):
            results[hid] = {
                "output": None,
                "confidence": 0.0,
                "probabilities": {},
                "provider": "jev",
                "model": str(payload.get("model") or model_name),
                "abstained": True,
                "abstain_reason": "MALFORMED_UPSTREAM_RESPONSE: missing answer",
                "latency_ms": latency_ms,
                "input_tokens": h_tok,
                "est_cost_usd": h_cost,
            }
            continue
        try:
            out, conf, probs, expected_score = _parse_wire_answer(head, ans)
            rec: dict[str, Any] = {
                "output": out,
                "confidence": conf,
                "probabilities": probs,
                "provider": "jev",
                "model": str(payload.get("model") or model_name),
                "abstained": False,
                "abstain_reason": None,
                "latency_ms": latency_ms,
                "input_tokens": h_tok,
                "est_cost_usd": h_cost,
            }
            if expected_score is not None:
                rec["expected_score"] = round(expected_score, 4)
            results[hid] = rec
        except (ValueError, KeyError, TypeError) as exc:
            results[hid] = {
                "output": None,
                "confidence": 0.0,
                "probabilities": {},
                "provider": "jev",
                "model": str(payload.get("model") or model_name),
                "abstained": True,
                "abstain_reason": f"MALFORMED_UPSTREAM_RESPONSE: {type(exc).__name__}",
                "latency_ms": latency_ms,
                "input_tokens": h_tok,
                "est_cost_usd": h_cost,
            }

    return results


def append_decision_traces(
    trace_dir: Path,
    entries: list[dict[str, Any]],
    *,
    max_entries: int = 1000,
) -> Path:
    """Append decision trace JSONL entries under an OS file lock (raises OSError on lock timeout)."""
    import fcntl

    if trace_dir.is_symlink():
        raise ValueError(f"Refusing to write Jev traces through symlinked trace_dir: {trace_dir}")
    trace_dir.mkdir(parents=True, exist_ok=True)
    resolved_dir = trace_dir.resolve()
    trace_path = trace_dir / "jev-decisions.jsonl"
    lock_path = trace_dir / "jev-decisions.jsonl.lock"
    for p in (trace_path, lock_path):
        if p.is_symlink() or not p.resolve().is_relative_to(resolved_dir):
            raise ValueError(f"Refusing to write Jev trace through symlink: {p}")

    serialized = "".join(json.dumps(e, sort_keys=True, default=str) + "\n" for e in entries)
    with open(lock_path, "a+", encoding="utf-8") as lock_fh:
        deadline = time.monotonic() + 2.0
        acquired = False
        while True:
            try:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise OSError(f"Timed out acquiring Jev trace lock: {lock_path}") from None
                time.sleep(0.02)
        try:
            with open(trace_path, "a", encoding="utf-8") as fh:
                fh.write(serialized)
            try:
                fsize = trace_path.stat().st_size
            except OSError:
                fsize = 0
            if fsize > 262_144:
                lines = trace_path.read_text(encoding="utf-8", errors="replace").splitlines()
                if len(lines) > max_entries:
                    kept = lines[-max_entries:]
                    tmp = trace_dir / ".jev-decisions.jsonl.tmp"
                    tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
                    os.replace(tmp, trace_path)
        finally:
            if acquired:
                try:
                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
    return trace_path


def decide(
    head_ids: list[str],
    state: dict[str, Any],
    *,
    heads_dir: Path | None = None,
    trace_dir: Path | None = None,
    provider: str | None = None,
    allow_vault: bool = False,
    force_live: bool = False,
    skip_pre_rule: bool = False,
    record_trace: bool = False,
) -> dict[str, Any]:
    """Governed execution of one or more Jev heads with ceiling enforcement & deterministic fallback."""
    heads = [load_head(hid, heads_dir=heads_dir) for hid in head_ids]
    mode = effective_jev_mode()
    eff_provider = (provider or ("code" if (mode == "off" and not force_live) else "jev")).strip().lower()

    if eff_provider == "code":
        raw_results = {h["id"]: run_code_fallback(h, state) for h in heads}
    else:
        raw_results = run_jev(
            heads,
            state,
            allow_vault=allow_vault,
            force_live=force_live,
            skip_pre_rule=skip_pre_rule,
        )

    decisions: list[dict[str, Any]] = []
    trace_entries: list[dict[str, Any]] = []
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for head in heads:
        hid = head["id"]
        res = raw_results[hid]
        projected = _project_state(state, head["state_keys"])
        digest = state_digest(projected)

        min_conf = float(head.get("thresholds", {}).get("act_min_confidence", 0.5))
        abstained = bool(res.get("abstained"))
        abstain_reason = res.get("abstain_reason")
        raw_res_conf = res.get("confidence")
        if not abstained and (not _is_valid_prob(raw_res_conf) or float(raw_res_conf) < min_conf):
            abstained = True
            abstain_reason = f"LOW_CONFIDENCE:{raw_res_conf}<{min_conf:.4f}"

        promo = head["promotion_state"]
        ceiling = head["ceiling"]
        if mode in {"shadow", "advisory", "routing", "actuating"}:
            requested_promo = mode.upper()
            eff_promo = (
                requested_promo
                if _PROMOTION_RANK[requested_promo] <= _PROMOTION_RANK[ceiling]
                else ceiling
            )
        else:
            eff_promo = promo if _PROMOTION_RANK[promo] <= _PROMOTION_RANK[ceiling] else ceiling
        if bool(head["authority"]):
            eff_promo = "ADVISORY" if _PROMOTION_RANK[eff_promo] > _PROMOTION_RANK["ADVISORY"] else eff_promo

        effective_output = res.get("output")
        effective_tier = head["tier"] if res.get("provider") == "jev" else "code"
        fallback_used = False

        if abstained and head.get("fallback") == "code":
            fb = run_code_fallback(head, state)
            effective_output = fb["output"]
            effective_tier = "code"
            fallback_used = True

        dec_record: dict[str, Any] = {
            "head_id": hid,
            "head_version": head["version"],
            "authority": bool(head["authority"]),
            "promotion_state": eff_promo,
            "ceiling": ceiling,
            "tier": effective_tier,
            "provider": res.get("provider"),
            "model": res.get("model"),
            "jev_output": res.get("output"),
            "effective_output": effective_output,
            "confidence": res.get("confidence", 0.0),
            "probabilities": res.get("probabilities", {}),
            "abstained": abstained,
            "abstain_reason": abstain_reason,
            "fallback": head.get("fallback"),
            "fallback_used": fallback_used,
            "state_digest": digest,
            "latency_ms": res.get("latency_ms", 0.0),
            "input_tokens": res.get("input_tokens", 0),
            "est_cost_usd": res.get("est_cost_usd", 0.0),
            "timestamp": now_iso,
        }
        if "expected_score" in res:
            dec_record["expected_score"] = res["expected_score"]
        decisions.append(dec_record)
        trace_entries.append(dec_record)

    trace_path_str: str | None = None
    if record_trace and trace_dir is not None and trace_entries:
        try:
            tp = append_decision_traces(trace_dir, trace_entries)
            trace_path_str = str(tp)
        except OSError:
            trace_path_str = None

    all_decided = all(d["effective_output"] is not None for d in decisions)
    return {
        "result": "DECIDED" if all_decided else "ABSTAINED",
        "inference_layer": "jev_system_one_advisory",
        "mode": mode,
        "trace_path": trace_path_str,
        "decisions": decisions,
    }


# ── Project Ledger Capsule & Weak-Overlap State Extractors ──────────────────


def overlap_to_relation_state(overlap_item: dict[str, Any]) -> dict[str, Any]:
    left = overlap_item.get("left") if isinstance(overlap_item.get("left"), dict) else {}
    right = overlap_item.get("right") if isinstance(overlap_item.get("right"), dict) else {}
    l_name = str(left.get("name") or left.get("project_key") or "").strip()
    r_name = str(right.get("name") or right.get("project_key") or "").strip()
    l_remote = str(left.get("remote_url") or "").strip() or None
    r_remote = str(right.get("remote_url") or "").strip() or None
    norm_l = _norm_url(l_remote)
    norm_r = _norm_url(r_remote)
    return {
        "shared_weak": list(overlap_item.get("shared_weak") or []),
        "left_name": l_name,
        "right_name": r_name,
        "left_source_class": str(left.get("source_class") or left.get("source_id") or ""),
        "right_source_class": str(right.get("source_class") or right.get("source_id") or ""),
        "left_remote_url": l_remote,
        "right_remote_url": r_remote,
        "left_readme_excerpt": left.get("readme_excerpt") or left.get("description"),
        "right_readme_excerpt": right.get("readme_excerpt") or right.get("description"),
        "same_normalized_remote": bool(norm_l and norm_r and norm_l == norm_r),
        "exact_name_match": bool(l_name and r_name and l_name.lower() == r_name.lower()),
    }


def _parse_iso_days_ago(iso_str: str | None, reference_now: dt.datetime) -> float | None:
    raw = str(iso_str or "").strip()
    if not raw:
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
    now_dt = reference_now or dt.datetime.now(dt.timezone.utc)
    obs_list = capsule.get("observations") if isinstance(capsule.get("observations"), list) else []
    source_classes = sorted({str(o.get("source_class")) for o in obs_list if isinstance(o, dict) and o.get("source_class")})

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
    mc = cur_state.get("machina_continuity") if isinstance(cur_state.get("machina_continuity"), dict) else {}
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
    total_files = int(ctx_blk.get("total_files") or 0)
    subsystems = ctx_blk.get("subsystems") if isinstance(ctx_blk.get("subsystems"), list) else []
    sub_count = int(ctx_blk.get("subsystem_count") or len(subsystems))
    op_count = int(ctx_blk.get("operational_surface_count") or len(ctx_blk.get("operational_surfaces") or []))
    auth_bounds = ctx_blk.get("authority_boundaries") if isinstance(ctx_blk.get("authority_boundaries"), list) else []
    has_gov = any(
        ":authority:" in str(b)
        or ":architecture:" in str(b)
        or any(tok in str(b).lower() for tok in ("agents.md", "architecture.md", "system.md", "claude.md"))
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
    )

    review_blk = capsule.get("review") if isinstance(capsule.get("review"), dict) else {}
    open_rev = int(review_blk.get("open_review_count") or 0)
    rev_items = review_blk.get("items") if isinstance(review_blk.get("items"), list) else []
    high_rev = sum(1 for item in rev_items if isinstance(item, dict) and item.get("severity") in {"high", "error"})

    pref = capsule.get("preferred_location") if isinstance(capsule.get("preferred_location"), dict) else {}
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


def project_advisory_for_referent(
    state_dir: Path,
    referent: str,
    *,
    provider: str | None = None,
    force_live: bool = False,
    allow_vault: bool = False,
    record_trace: bool = False,
) -> dict[str, Any]:
    """Run batched advisory Jev heads (`project_lifecycle_inference`, `estate_attention_urgency`) on a resolved capsule."""
    capsule = show_payload(state_dir, referent)
    if capsule.get("status") != "resolved":
        return capsule

    combined_state = {
        **capsule_to_lifecycle_state(capsule),
        **capsule_to_urgency_state(capsule),
    }
    decision_envelope = decide(
        ["project_lifecycle_inference", "estate_attention_urgency"],
        combined_state,
        trace_dir=state_dir if record_trace else None,
        provider=provider,
        allow_vault=allow_vault,
        force_live=force_live,
        record_trace=record_trace,
    )
    by_head = {d["head_id"]: d for d in decision_envelope["decisions"]}
    return {
        "status": "resolved",
        "referent": referent,
        "canonical_project_id": capsule["canonical_project_id"],
        "project_key": capsule["project_key"],
        "display_name": capsule["display_name"],
        "observed_lifecycle_state": (capsule.get("current_state") or {}).get("lifecycle_state"),
        "jev_advisory": {
            "inference_layer": "jev_system_one_advisory",
            "mode": decision_envelope["mode"],
            "lifecycle_posture": by_head["project_lifecycle_inference"]["effective_output"],
            "attention_urgency": by_head["estate_attention_urgency"]["effective_output"],
            "decisions": decision_envelope["decisions"],
        },
        "extracted_state": combined_state,
    }


# ── Gold Evaluation Corpus (15 Cases Across All 3 Heads) ────────────────────

GOLD_EVAL_CASES: tuple[dict[str, Any], ...] = (
    # 1–5: weak_overlap_relation
    {
        "case_id": "overlap_same_remote_backup_mirror",
        "head_id": "weak_overlap_relation",
        "state": {
            "shared_weak": ["name_token:context-project"],
            "left_name": "context-project",
            "right_name": "context-project-backup",
            "left_source_class": "live",
            "right_source_class": "backup",
            "left_remote_url": "https://github.com/mschwar/context-project.git",
            "right_remote_url": "git@github.com:mschwar/context-project.git",
            "left_readme_excerpt": "Filesystem-native context layer for AI agents generating CONTEXT.md manifests.",
            "right_readme_excerpt": "Filesystem-native context layer for AI agents generating CONTEXT.md manifests.",
            "same_normalized_remote": True,
            "exact_name_match": False,
        },
        "expected": "redundant_checkout_or_mirror",
    },
    {
        "case_id": "overlap_distinct_generic_token",
        "head_id": "weak_overlap_relation",
        "state": {
            "shared_weak": ["name_token:agent"],
            "left_name": "voice-travel-agent",
            "right_name": "k8s-metrics-agent",
            "left_source_class": "live",
            "right_source_class": "live",
            "left_remote_url": None,
            "right_remote_url": None,
            "left_readme_excerpt": "Twilio voice booking assistant for flight and hotel itineraries.",
            "right_readme_excerpt": "Prometheus DaemonSet collector exporting node cgroup metrics in Kubernetes.",
            "same_normalized_remote": False,
            "exact_name_match": False,
        },
        "expected": "distinct_projects",
    },
    {
        "case_id": "overlap_related_ecosystem_siblings",
        "head_id": "weak_overlap_relation",
        "state": {
            "shared_weak": ["name_token:ledger"],
            "left_name": "project-ledger",
            "right_name": "project-ledger-menubar",
            "left_source_class": "live",
            "right_source_class": "live",
            "left_remote_url": "https://github.com/mschwar/project-ledger",
            "right_remote_url": "https://github.com/mschwar/project-ledger-menubar",
            "left_readme_excerpt": "Durable project-reality compiler and read-only MCP substrate for project capsules.",
            "right_readme_excerpt": "Companion macOS SwiftUI menu bar client displaying Project Ledger capsules and drift alerts.",
            "same_normalized_remote": False,
            "exact_name_match": False,
        },
        "expected": "related_distinct_siblings",
    },
    {
        "case_id": "overlap_same_project_across_hosts_no_remote",
        "head_id": "weak_overlap_relation",
        "state": {
            "shared_weak": ["exact_slug:invoice-reconciler"],
            "left_name": "invoice-reconciler",
            "right_name": "invoice-reconciler",
            "left_source_class": "live",
            "right_source_class": "live",
            "left_remote_url": None,
            "right_remote_url": None,
            "left_readme_excerpt": "CLI reconciling Stripe payouts against QuickBooks ledger exports in CSV.",
            "right_readme_excerpt": "CLI reconciling Stripe payouts against QuickBooks ledger exports in CSV.",
            "same_normalized_remote": False,
            "exact_name_match": True,
        },
        "expected": "same_project_candidate",
    },
    {
        "case_id": "overlap_unrelated_orgs_shared_suffix",
        "head_id": "weak_overlap_relation",
        "state": {
            "shared_weak": ["name_token:starter"],
            "left_name": "nextjs-blog-starter",
            "right_name": "rust-axum-starter",
            "left_source_class": "live",
            "right_source_class": "inventory",
            "left_remote_url": "https://github.com/vercel/nextjs-blog-starter",
            "right_remote_url": "https://github.com/tokio-rs/rust-axum-starter",
            "left_readme_excerpt": "Next.js MDX static blog template.",
            "right_readme_excerpt": "Rust Axum REST API template with SQLx.",
            "same_normalized_remote": False,
            "exact_name_match": False,
        },
        "expected": "distinct_projects",
    },
    # 6–10: project_lifecycle_inference
    {
        "case_id": "lifecycle_active_dirty_handoff",
        "head_id": "project_lifecycle_inference",
        "state": {
            "source_classes": ["live"],
            "days_since_last_commit": 0.4,
            "dirty_worktree": True,
            "ahead_count": 2,
            "has_machina_handoff": True,
            "total_files": 94,
            "subsystem_count": 8,
            "operational_surface_count": 19,
            "has_governance_docs": True,
            "open_review_count": 0,
        },
        "expected": "active_development",
    },
    {
        "case_id": "lifecycle_maintained_stable_library",
        "head_id": "project_lifecycle_inference",
        "state": {
            "source_classes": ["live", "mirror"],
            "days_since_last_commit": 48.0,
            "dirty_worktree": False,
            "ahead_count": 0,
            "has_machina_handoff": False,
            "total_files": 62,
            "subsystem_count": 5,
            "operational_surface_count": 11,
            "has_governance_docs": True,
            "open_review_count": 0,
        },
        "expected": "maintained_stable",
    },
    {
        "case_id": "lifecycle_dormant_archive_backup",
        "head_id": "project_lifecycle_inference",
        "state": {
            "source_classes": ["backup"],
            "days_since_last_commit": 410.0,
            "dirty_worktree": False,
            "ahead_count": 0,
            "has_machina_handoff": False,
            "total_files": 38,
            "subsystem_count": 4,
            "operational_surface_count": 2,
            "has_governance_docs": False,
            "open_review_count": 0,
        },
        "expected": "dormant_archive",
    },
    {
        "case_id": "lifecycle_ephemeral_scratch_spike",
        "head_id": "project_lifecycle_inference",
        "state": {
            "source_classes": ["live"],
            "days_since_last_commit": 65.0,
            "dirty_worktree": False,
            "ahead_count": 0,
            "has_machina_handoff": False,
            "total_files": 2,
            "subsystem_count": 1,
            "operational_surface_count": 0,
            "has_governance_docs": False,
            "open_review_count": 0,
        },
        "expected": "ephemeral_experiment",
    },
    {
        "case_id": "lifecycle_active_recent_commits_clean",
        "head_id": "project_lifecycle_inference",
        "state": {
            "source_classes": ["live"],
            "days_since_last_commit": 2.0,
            "dirty_worktree": False,
            "ahead_count": 3,
            "has_machina_handoff": False,
            "total_files": 120,
            "subsystem_count": 9,
            "operational_surface_count": 24,
            "has_governance_docs": True,
            "open_review_count": 0,
        },
        "expected": "active_development",
    },
    # 11–15: estate_attention_urgency
    {
        "case_id": "urgency_0_clean_healthy",
        "head_id": "estate_attention_urgency",
        "state": {
            "git_drift_codes": [],
            "dirty_checkout_count": 0,
            "ahead_total": 0,
            "behind_total": 0,
            "diverged": False,
            "open_review_count": 0,
            "high_severity_review_count": 0,
            "source_health_state": "healthy",
        },
        "expected": 0,
    },
    {
        "case_id": "urgency_1_minor_review_no_drift",
        "head_id": "estate_attention_urgency",
        "state": {
            "git_drift_codes": [],
            "dirty_checkout_count": 0,
            "ahead_total": 0,
            "behind_total": 0,
            "diverged": False,
            "open_review_count": 1,
            "high_severity_review_count": 0,
            "source_health_state": "healthy",
        },
        "expected": 1,
    },
    {
        "case_id": "urgency_2_single_dirty_worktree",
        "head_id": "estate_attention_urgency",
        "state": {
            "git_drift_codes": ["GIT_WORKTREE_DIRTY", "GIT_AHEAD_UNPUSHED"],
            "dirty_checkout_count": 1,
            "ahead_total": 2,
            "behind_total": 0,
            "diverged": False,
            "open_review_count": 0,
            "high_severity_review_count": 0,
            "source_health_state": "healthy",
        },
        "expected": 2,
    },
    {
        "case_id": "urgency_3_diverged_history",
        "head_id": "estate_attention_urgency",
        "state": {
            "git_drift_codes": ["GIT_DIVERGED"],
            "dirty_checkout_count": 1,
            "ahead_total": 4,
            "behind_total": 3,
            "diverged": True,
            "open_review_count": 0,
            "high_severity_review_count": 0,
            "source_health_state": "healthy",
        },
        "expected": 3,
    },
    {
        "case_id": "urgency_3_multi_host_dirty_conflict",
        "head_id": "estate_attention_urgency",
        "state": {
            "git_drift_codes": ["GIT_WORKTREE_DIRTY", "GIT_CROSS_CHECKOUT_HEAD_MISMATCH"],
            "dirty_checkout_count": 2,
            "ahead_total": 1,
            "behind_total": 1,
            "diverged": False,
            "open_review_count": 1,
            "high_severity_review_count": 1,
            "source_health_state": "healthy",
        },
        "expected": 3,
    },
)


def evaluate_heads(
    *,
    heads_dir: Path | None = None,
    provider: str = "code",
    head_filter: str | None = None,
    allow_vault: bool = False,
    force_live: bool = False,
    skip_pre_rule: bool = False,
) -> dict[str, Any]:
    """Evaluate Project Ledger Jev heads against the 15-case gold corpus."""
    selected = [
        c for c in GOLD_EVAL_CASES if (head_filter is None or c["head_id"] == head_filter)
    ]
    if not selected:
        raise ValueError(f"No gold evaluation cases matched head_filter={head_filter!r}.")

    case_results: list[dict[str, Any]] = []
    by_head: dict[str, dict[str, Any]] = {}
    pre_rule_hits = 0
    live_model_calls = 0
    abstained_count = 0
    correct_count = 0
    total_tokens = 0
    total_cost = 0.0

    for case in selected:
        hid = case["head_id"]
        head = load_head(hid, heads_dir=heads_dir)
        if provider == "code":
            res = run_code_fallback(head, case["state"])
        else:
            res = run_jev(
                [head],
                case["state"],
                allow_vault=allow_vault,
                force_live=force_live,
                skip_pre_rule=skip_pre_rule,
            )[hid]

        prov_used = str(res.get("provider") or provider)
        if prov_used == "pre_rule":
            pre_rule_hits += 1
        elif prov_used == "jev" and not res.get("abstained"):
            live_model_calls += 1

        is_abstained = bool(res.get("abstained"))
        if is_abstained:
            abstained_count += 1

        predicted = res.get("output")
        expected = case["expected"]
        if head["type"] == "score" and isinstance(predicted, int) and isinstance(expected, int):
            is_correct = (not is_abstained) and (abs(predicted - expected) == 0)
        else:
            is_correct = (not is_abstained) and (predicted == expected)

        if is_correct:
            correct_count += 1
        total_tokens += int(res.get("input_tokens") or 0)
        total_cost += float(res.get("est_cost_usd") or 0.0)

        h_bucket = by_head.setdefault(hid, {"total": 0, "correct": 0, "abstained": 0})
        h_bucket["total"] += 1
        if is_correct:
            h_bucket["correct"] += 1
        if is_abstained:
            h_bucket["abstained"] += 1

        case_results.append(
            {
                "case_id": case["case_id"],
                "head_id": hid,
                "expected": expected,
                "predicted": predicted,
                "correct": is_correct,
                "confidence": res.get("confidence", 0.0),
                "provider": prov_used,
                "abstained": is_abstained,
                "abstain_reason": res.get("abstain_reason"),
                "input_tokens": res.get("input_tokens", 0),
            }
        )

    for h_bucket in by_head.values():
        denom = max(1, h_bucket["total"] - h_bucket["abstained"])
        h_bucket["accuracy"] = round(h_bucket["correct"] / denom, 4)

    evaluated = len(selected) - abstained_count
    accuracy = round(correct_count / evaluated, 4) if evaluated > 0 else 0.0
    return {
        "provider_requested": provider,
        "skip_pre_rule": skip_pre_rule,
        "total": len(selected),
        "evaluated": evaluated,
        "abstained": abstained_count,
        "correct": correct_count,
        "accuracy": accuracy,
        "pre_rule_hits": pre_rule_hits,
        "live_model_calls": live_model_calls,
        "total_input_tokens": total_tokens,
        "total_cost_usd": round(total_cost, 9),
        "by_head": by_head,
        "cases": case_results,
    }
