from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: object, length: int = 24) -> str:
    material = canonical_json(list(parts))
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}_{digest}"


def normalize_locator(raw: str) -> str:
    text = os.path.expandvars(os.path.expanduser(str(raw).strip())).replace("\\", "/")
    while "//" in text and not text.startswith("//"):
        text = text.replace("//", "/")
    if len(text) > 1:
        text = text.rstrip("/")
    if len(text) >= 2 and text[1] == ":":
        text = text[0].lower() + text[1:]
    return text


def source_id_for(root_cfg: dict, *, index: int = 0) -> str:
    del index  # retained in the call signature for compatibility; root order is not identity.
    explicit = str(root_cfg.get("source_id", "")).strip()
    if explicit:
        return explicit
    payload = {
        "label": str(root_cfg.get("label", "")).strip(),
        "machine_name": str(root_cfg.get("machine_name", "")).strip(),
        "discovery": str(root_cfg.get("discovery", "children")).strip(),
        "remote_name": str(root_cfg.get("remote_name", "")).strip(),
        "path": normalize_locator(str(root_cfg.get("path", ""))),
    }
    return stable_id("src", payload)


def observation_id_for(entry: dict, source_id: str) -> str:
    location_key = (
        str(entry.get("path_from_root", "")).strip()
        or str(entry.get("path", "")).strip()
        or str(entry.get("canonical_url", "")).strip()
        or str(entry.get("remote_url", "")).strip()
        or str(entry.get("project_key", "")).strip()
        or str(entry.get("name", "")).strip()
    )
    return stable_id("obs", source_id, normalize_locator(location_key))


def path_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
