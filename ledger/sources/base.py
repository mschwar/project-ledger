"""Source-adapter abstraction and orchestration.

A ``SourceAdapter`` knows how to discover project candidates for one discovery mode
and how to build a compatibility observation from a candidate. ``collect_entries``
orchestrates every configured root: it resolves the root path and inventory artifacts,
dispatches to the matching adapter, deduplicates, and sorts the resulting observations.
The behavior here mirror-pins the original ``build_ledger.collect_entries`` contract so
the compatibility scanner keeps producing identical output.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from .common import merge_exclude_names, resolve_path, safe_path_exists
from .filesystem import build_entry, discover_children, discover_git_repos
from .inventory_policy import (
    build_inventory_entry,
    discover_inventory_policy_candidates,
)


class SourceAdapter(ABC):
    """Sense one class of source for a single discovery mode."""

    discovery: str

    @abstractmethod
    def discover_candidates(
        self, root_cfg: dict, defaults: dict, exclude_names: set[str]
    ) -> list[Path | dict]:
        """Return Path candidates (filesystem modes) or dict candidates (inventory)."""

    @abstractmethod
    def build(
        self,
        candidate: Path | dict,
        root_cfg: dict,
        defaults: dict,
        output_dir: Path,
    ) -> dict | None:
        """Build a compatibility observation for one candidate, or None if excluded."""


class FilesystemAdapter(SourceAdapter):
    """Covers children / git_repos / self discovery over the local filesystem."""

    def __init__(self, discovery: str) -> None:
        self.discovery = discovery

    def discover_candidates(
        self, root_cfg: dict, defaults: dict, exclude_names: set[str]
    ) -> list[Path]:
        root_path = root_cfg["_resolved_path"]
        if self.discovery == "children":
            return discover_children(root_path, exclude_names)
        if self.discovery == "git_repos":
            max_depth = int(root_cfg.get("max_depth", defaults.get("git_repo_max_depth", 6)))
            return discover_git_repos(root_path, exclude_names, max_depth=max_depth)
        return [root_path]  # discovery == "self"

    def build(
        self,
        candidate: Path | dict,
        root_cfg: dict,
        defaults: dict,
        output_dir: Path,
    ) -> dict | None:
        return build_entry(candidate, root_cfg, defaults, output_dir)


class InventoryPolicyAdapter(SourceAdapter):
    """Covers inventory_policy discovery over an rclone-style inventory + policy file."""

    discovery = "inventory_policy"

    def discover_candidates(
        self, root_cfg: dict, defaults: dict, exclude_names: set[str]
    ) -> list[dict]:
        return discover_inventory_policy_candidates(root_cfg, exclude_names)

    def build(
        self,
        candidate: Path | dict,
        root_cfg: dict,
        defaults: dict,
        output_dir: Path,
    ) -> dict | None:
        return build_inventory_entry(candidate, root_cfg, defaults, output_dir)


ADAPTERS: dict[str, SourceAdapter] = {
    "children": FilesystemAdapter("children"),
    "git_repos": FilesystemAdapter("git_repos"),
    "self": FilesystemAdapter("self"),
    "inventory_policy": InventoryPolicyAdapter(),
}


def get_adapter(discovery: str) -> SourceAdapter:
    return ADAPTERS[discovery]


def collect_entries(config: dict, config_dir: Path, output_dir: Path) -> list[dict]:
    defaults = config.get("defaults", {})
    roots = config.get("roots", [])
    if not roots:
        raise ValueError("Config must contain at least one root in roots[].")

    entries: list[dict] = []
    seen_paths: set[str] = set()

    for root_index, root_cfg_raw in enumerate(roots):
        if not isinstance(root_cfg_raw, dict):
            raise ValueError(f"roots[{root_index}] must be a JSON object.")

        root_cfg = dict(root_cfg_raw)
        raw_root_path = str(root_cfg.get("path", "")).strip()
        if not raw_root_path:
            raise ValueError(f"roots[{root_index}].path is required.")

        discovery = str(root_cfg.get("discovery", "children")).strip() or "children"
        if discovery not in ADAPTERS:
            raise ValueError(f"Unsupported discovery mode in roots[{root_index}]: {discovery}")

        root_path = resolve_path(raw_root_path, config_dir)
        root_cfg["_resolved_path"] = root_path

        if discovery == "inventory_policy":
            inventory_jsonl_raw = str(root_cfg.get("inventory_jsonl", "")).strip()
            policy_path_raw = str(root_cfg.get("policy_path", "")).strip()
            missing_fields = [
                field_name
                for field_name, field_value in (
                    ("inventory_jsonl", inventory_jsonl_raw),
                    ("policy_path", policy_path_raw),
                )
                if not field_value
            ]
            if missing_fields:
                joined = ", ".join(missing_fields)
                raise ValueError(
                    f"roots[{root_index}] with discovery=inventory_policy requires: {joined}."
                )

            inventory_jsonl_path = resolve_path(inventory_jsonl_raw, config_dir)
            policy_path = resolve_path(policy_path_raw, config_dir)
            if not safe_path_exists(inventory_jsonl_path):
                raise ValueError(
                    f"roots[{root_index}].inventory_jsonl does not exist: {inventory_jsonl_path}"
                )
            if not safe_path_exists(policy_path):
                raise ValueError(
                    f"roots[{root_index}].policy_path does not exist: {policy_path}"
                )
            root_cfg["_inventory_jsonl_path"] = inventory_jsonl_path
            root_cfg["_policy_path"] = policy_path
        else:
            if "inventory_jsonl" in root_cfg:
                root_cfg["_inventory_jsonl_path"] = resolve_path(root_cfg["inventory_jsonl"], config_dir)
            if "policy_path" in root_cfg:
                root_cfg["_policy_path"] = resolve_path(root_cfg["policy_path"], config_dir)

        exclude_names = merge_exclude_names(root_cfg, defaults)
        adapter = get_adapter(discovery)
        candidates = adapter.discover_candidates(root_cfg, defaults, exclude_names)

        for candidate in candidates:
            if isinstance(candidate, dict):
                resolved = str(candidate.get("path_key", candidate.get("inventory_path", ""))).lower()
                if resolved in seen_paths:
                    continue
                seen_paths.add(resolved)
                entry = adapter.build(candidate, root_cfg, defaults, output_dir)
            else:
                resolved = str(candidate.resolve()).lower()
                if resolved in seen_paths:
                    continue
                seen_paths.add(resolved)
                entry = adapter.build(candidate, root_cfg, defaults, output_dir)
            if entry:
                entries.append(entry)

    entries.sort(key=lambda item: (item["last_touch_at"], item["name"].lower()), reverse=True)
    return entries