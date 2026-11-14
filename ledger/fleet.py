"""Passive multi-host fleet occupancy overlay (`INV-FLEET-GAME-01`-compliant).

Reads the local ``tmpfs`` fleet pulse scoreboard (``/run/user/1000/homelab/fleet-pulse.json``
when present) without opening network sockets or probing gaming/occupied hosts.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

DEFAULT_FLEET_PULSE_PATH = Path("/run/user/1000/homelab/fleet-pulse.json")
_STALE_PULSE_SECONDS = 600.0


def _parse_iso_utc(raw: str | None) -> dt.datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        parsed = dt.datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except ValueError:
        return None


def read_fleet_pulse_overlay(
    *,
    pulse_path: Path | None = None,
    reference_iso: str | None = None,
) -> dict[str, Any]:
    """Read local homelab fleet occupancy scoreboard without touching remote nodes."""
    env_override = os.environ.get("PROJECT_LEDGER_FLEET_PULSE", "").strip()
    target = (
        pulse_path
        if pulse_path is not None
        else (Path(env_override) if env_override else DEFAULT_FLEET_PULSE_PATH)
    )
    resolved_str = str(target)

    if not target.is_file():
        return {
            "state": "unavailable",
            "reason_code": "PULSE_FILE_ABSENT",
            "pulse_path": resolved_str,
            "generated_at": None,
            "node_count": 0,
            "dispatchable_nodes": [],
            "gaming_nodes": [],
            "nodes": [],
        }

    try:
        doc = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "state": "unavailable",
            "reason_code": "PULSE_FILE_INVALID",
            "pulse_path": resolved_str,
            "generated_at": None,
            "node_count": 0,
            "dispatchable_nodes": [],
            "gaming_nodes": [],
            "nodes": [],
        }

    if not isinstance(doc, dict) or not isinstance(doc.get("nodes"), dict):
        return {
            "state": "unavailable",
            "reason_code": "PULSE_SCHEMA_UNEXPECTED",
            "pulse_path": resolved_str,
            "generated_at": None,
            "node_count": 0,
            "dispatchable_nodes": [],
            "gaming_nodes": [],
            "nodes": [],
        }

    generated_at = str(doc.get("generated_at_iso") or "").strip() or None
    state = "available"
    reason_code = "PULSE_FRESH"
    gen_dt = _parse_iso_utc(generated_at)
    ref_dt = _parse_iso_utc(reference_iso)
    if gen_dt is not None and ref_dt is not None:
        age = (ref_dt - gen_dt).total_seconds()
        if age > _STALE_PULSE_SECONDS:
            state = "stale"
            reason_code = "PULSE_STALE"

    nodes_list: list[dict[str, Any]] = []
    raw_nodes = doc["nodes"]
    for node_name in sorted(raw_nodes):
        info = raw_nodes[node_name]
        if not isinstance(info, dict):
            continue
        occ = str(info.get("occupancy_state") or "UNKNOWN")
        gaming_active = bool(info.get("gaming_active")) or occ == "GAMING"
        dispatchable = bool(info.get("dispatchable")) and not gaming_active and occ != "HUMAN_HEAVY"
        nodes_list.append(
            {
                "node": str(node_name),
                "os_family": str(info.get("os_family") or "unknown"),
                "online": bool(info.get("online")),
                "dispatchable": dispatchable,
                "occupancy_state": occ,
                "gaming_active": gaming_active,
                "active_leases": int(info.get("active_leases") or 0),
                "gpu_inference_online": bool(info.get("gpu_inference_online")),
                "free_ram_gib": round(float(info.get("free_ram_gib") or 0.0), 2),
                "free_disk_gib": round(float(info.get("free_disk_gib") or 0.0), 2),
            }
        )

    dispatchable_nodes = sorted(n["node"] for n in nodes_list if n["dispatchable"])
    gaming_nodes = sorted(n["node"] for n in nodes_list if n["gaming_active"])

    return {
        "state": state,
        "reason_code": reason_code,
        "pulse_path": resolved_str,
        "generated_at": generated_at,
        "node_count": len(nodes_list),
        "dispatchable_nodes": dispatchable_nodes,
        "gaming_nodes": gaming_nodes,
        "nodes": nodes_list,
    }
