from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .capsules import locate_payload, show_payload
from .changes import SUPPORTED_CHANGE_ENTITY_KINDS, changes_payload
from .checkpoints import CHECKPOINT_FILENAME
from .compiler import compile_state, load_manifest, orient_payload
from .compat_contract import LedgerCompatError
from .config import (
    LedgerConfigError,
    default_workspace_path,
    describe_sources,
    read_config,
    validate_config,
)
from .explain import explain_payload
from .gbrain_pages import sync_gbrain_pages
from .github_sync import DEFAULT_CLONE_ROOT, sync_github_estate
from .identity import resolve_payload
from .lineage import lineage_status_payload
from .mcp_server import add_mcp_args, run_mcp_from_args
from .portfolio import portfolio_payload
from .triage import approve_candidate_decisions, review_payload


def _print_json(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _add_compile_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=default_workspace_path("ledger_config.json"))
    parser.add_argument("--state-dir", default=default_workspace_path("state"))
    parser.add_argument(
        "--identity-decisions",
        default=default_workspace_path("registry/identity-decisions.json"),
    )
    parser.add_argument(
        "--project-lineage",
        default=None,
        help="Optional durable project lineage registry (defaults to sibling project-lineage.json next to --identity-decisions when present).",
    )
    parser.add_argument(
        "--check-remote",
        action="store_true",
        help="Query origin via bounded `git ls-remote` to detect unfetched upstream commits (GIT_REMOTE_AHEAD).",
    )
    parser.add_argument(
        "--remote-timeout",
        type=float,
        default=3.0,
        help="Per-repo timeout in seconds for `--check-remote` `git ls-remote` probes (default: 3.0).",
    )


def _parser() -> argparse.ArgumentParser:
    default_state = default_workspace_path("state")
    default_decisions = default_workspace_path("registry/identity-decisions.json")
    default_candidates = default_workspace_path("registry/weak-overlap-triage-candidates.json")
    default_lineage = default_workspace_path("registry/project-lineage.json")

    parser = argparse.ArgumentParser(prog="ledger", description="Project Ledger agent-facing substrate CLI.")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="Validate config and required source artifacts.")
    validate.add_argument("--config", default=default_workspace_path("ledger_config.json"))
    validate.add_argument("--json", action="store_true")

    refresh = sub.add_parser("refresh", help="Run the compatibility scanner, then compile agent-facing state.")
    _add_compile_args(refresh)
    refresh.add_argument("--output-dir", default=default_workspace_path("output"))
    refresh.add_argument(
        "--no-markdown-mirror",
        action="store_true",
        help="Do not update docs/ledgers/projects-ledger.md; intended for external provider automation.",
    )
    refresh.add_argument(
        "--full-scan",
        action="store_true",
        help="Bypass source checkpoint cache and force full extraction across all sources.",
    )
    refresh.add_argument("--json", action="store_true")

    compile_cmd = sub.add_parser("compile", help="Compile typed observations and the system manifest.")
    _add_compile_args(compile_cmd)
    compile_cmd.add_argument("--input-json", default=default_workspace_path("output/projects.json"))
    compile_cmd.add_argument("--json", action="store_true")

    orient = sub.add_parser("orient", help="Read the compact system orientation surface.")
    orient.add_argument("--state-dir", default=default_state)
    orient.add_argument("--json", action="store_true")

    sources = sub.add_parser("sources", help="Show source health from the latest manifest.")
    sources.add_argument("--state-dir", default=default_state)
    sources.add_argument("--json", action="store_true")

    portfolio = sub.add_parser(
        "portfolio",
        help="Query estate-wide portfolio triage, cross-repo blast radius, and fleet occupancy.",
    )
    portfolio.add_argument("--state-dir", default=default_state)
    portfolio.add_argument(
        "--urgent",
        action="store_true",
        help="Show only projects with attention_urgency >= 1.",
    )
    portfolio.add_argument(
        "--dirty",
        action="store_true",
        help="Show only projects with uncommitted worktree changes.",
    )
    portfolio.add_argument(
        "--behind",
        action="store_true",
        help="Show only projects behind their tracked upstream branch.",
    )
    portfolio.add_argument(
        "--remote-ahead",
        action="store_true",
        help="Show only projects with unfetched upstream commits (GIT_REMOTE_AHEAD).",
    )
    portfolio.add_argument(
        "--cycles",
        action="store_true",
        help="Show only projects with internal subsystem dependency cycles.",
    )
    portfolio.add_argument(
        "--lifecycle",
        default=None,
        help="Filter by lifecycle posture (e.g. active_development, maintained_stable, dormant_archive, ephemeral_experiment).",
    )
    portfolio.add_argument(
        "--depends-on",
        default=None,
        help="Return reverse blast-radius consumers that depend on the given project referent.",
    )
    portfolio.add_argument(
        "--depended-on-by",
        default=None,
        help="Return upstream projects depended on by the given project referent.",
    )
    portfolio.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of matched projects to return.",
    )
    portfolio.add_argument("--json", action="store_true")

    resolve = sub.add_parser("resolve", help="Resolve an exact project referent to canonical identity.")
    resolve.add_argument("referent")
    resolve.add_argument("--state-dir", default=default_state)
    resolve.add_argument("--json", action="store_true")

    show = sub.add_parser("show", help="Load the bounded project capsule for a project referent.")
    show.add_argument("referent")
    show.add_argument("--state-dir", default=default_state)
    show.add_argument("--json", action="store_true")

    locate = sub.add_parser(
        "locate",
        help="Resolve the preferred working location and ranked observations for a project.",
    )
    locate.add_argument("referent")
    locate.add_argument("--state-dir", default=default_state)
    locate.add_argument("--json", action="store_true")

    explain = sub.add_parser(
        "explain",
        help="Explain canonical project identity, resolved fields, preferred location, reviews, or decisions.",
    )
    explain.add_argument("referent")
    explain.add_argument("--state-dir", default=default_state)
    explain.add_argument("--identity-decisions", default=default_decisions)
    explain.add_argument("--json", action="store_true")

    review = sub.add_parser(
        "review",
        help="Inspect open identity review items and frontier zero-shot weak-overlap triage candidates.",
    )
    review.add_argument("--state-dir", default=default_state)
    review.add_argument("--candidates", default=default_candidates)
    review.add_argument("--identity-decisions", default=default_decisions)
    review.add_argument(
        "--approve-candidates",
        action="store_true",
        help="Approve and merge candidate decisions from --candidates into --identity-decisions.",
    )
    review.add_argument(
        "--decision-id",
        action="append",
        dest="decision_ids",
        default=None,
        help="Optional candidate decision_id(s) to approve selectively with --approve-candidates.",
    )
    review.add_argument("--json", action="store_true")

    changes = sub.add_parser(
        "changes",
        help="Inspect semantic estate changes since the previous or a specified compiled run.",
    )
    changes.add_argument("--state-dir", default=default_state)
    changes.add_argument(
        "--since",
        default=None,
        dest="since_run_id",
        help="Optional historical run_id baseline to diff against current state.",
    )
    changes.add_argument(
        "--project",
        default=None,
        dest="project_referent",
        help="Optional canonical project referent (or historical project ID/key) to filter changes.",
    )
    changes.add_argument(
        "--entity-kind",
        default=None,
        choices=sorted(SUPPORTED_CHANGE_ENTITY_KINDS),
        help="Optional entity_kind filter.",
    )
    changes.add_argument(
        "--change-type",
        default=None,
        help="Optional change_type code filter (e.g. PROJECT_MERGED, PROJECT_GIT_DRIFT_CHANGED).",
    )
    changes.add_argument("--json", action="store_true")

    sync_gbrain = sub.add_parser(
        "sync-gbrain",
        help="Project canonical project Reality Provider blocks/stubs into a GBrain markdown repository.",
    )
    sync_gbrain.add_argument("--state-dir", default=default_state)
    sync_gbrain.add_argument(
        "--brain-dir",
        default="/home/prime/brain",
        help="Target GBrain markdown repository root (default: /home/prime/brain).",
    )
    sync_gbrain.add_argument(
        "--project",
        action="append",
        dest="project_referents",
        default=None,
        help="Optional canonical project referent(s) to sync selectively (repeatable).",
    )
    sync_gbrain.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute created/updated/unchanged counts without writing files or running git/gbrain.",
    )
    sync_gbrain.add_argument(
        "--ingest",
        action="store_true",
        help="Commit touched projects/<slug>.md files in --brain-dir and run gbrain sync --src-subpath projects --no-embed --no-extract.",
    )
    sync_gbrain.add_argument(
        "--push",
        action="store_true",
        help="Best-effort rebase-pull and push committed project reality changes in --brain-dir to origin (offline-safe).",
    )
    sync_gbrain.add_argument(
        "--prune-shadow-source",
        action="append",
        dest="prune_shadow_sources",
        default=None,
        help="Optional non-default GBrain source ID(s) (e.g. active-repos) from which to soft-delete stale Project Ledger capsule shadow pages.",
    )
    sync_gbrain.add_argument(
        "--gbrain-bin",
        default="gbrain",
        help="gbrain CLI executable name or path (default: gbrain).",
    )
    sync_gbrain.add_argument("--json", action="store_true")

    lineage = sub.add_parser(
        "lineage",
        help="Inspect, check, or sync the durable canonical_project_id lineage registry (registry/project-lineage.json).",
    )
    lineage.add_argument("--state-dir", default=default_state)
    lineage.add_argument(
        "--project-lineage",
        default=default_lineage,
        help="Path to durable project lineage registry (default: registry/project-lineage.json).",
    )
    lineage.add_argument(
        "--merge-from",
        default=None,
        dest="merge_from",
        help="Optional secondary/runtime lineage file to merge into --project-lineage before syncing.",
    )
    lineage_mode = lineage.add_mutually_exclusive_group()
    lineage_mode.add_argument(
        "--sync",
        action="store_true",
        help="Ratchet and persist registry/project-lineage.json from compiled state/canonical-projects.json.",
    )
    lineage_mode.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero (code 5) if registry/project-lineage.json is out of sync with compiled state.",
    )
    lineage.add_argument("--json", action="store_true")

    sync_gh = sub.add_parser(
        "sync-github",
        help="Discover GitHub remotes, clone missing active repos over SSH, fast-forward clean default branches, and audit 1:1 git parity.",
    )
    sync_gh.add_argument("--config", default=default_workspace_path("ledger_config.json"))
    sync_gh.add_argument("--state-dir", default=default_state)
    sync_gh.add_argument(
        "--clone-root",
        default=DEFAULT_CLONE_ROOT,
        help=f"Target local directory for auto-cloning missing active GitHub repos (default: {DEFAULT_CLONE_ROOT}).",
    )
    sync_gh_mode = sync_gh.add_mutually_exclusive_group()
    sync_gh_mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute sync/parity actions without cloning, fetching, or modifying checkouts.",
    )
    sync_gh_mode.add_argument(
        "--check",
        action="store_true",
        help="Read-only 1:1 parity check; exits code 6 when parity drift is present.",
    )
    sync_gh.add_argument(
        "--no-clone",
        action="store_true",
        help="Report missing remote repos without cloning them.",
    )
    sync_gh.add_argument(
        "--no-fetch",
        action="store_true",
        help="Skip fetching existing local checkouts.",
    )
    sync_gh.add_argument(
        "--no-ff",
        action="store_true",
        help="Fetch remote refs without fast-forwarding clean default-branch checkouts.",
    )
    sync_gh.add_argument(
        "--no-ssh-rewrite",
        action="store_true",
        help="Do not rewrite https://github.com/... remotes to git@github.com:...",
    )
    sync_gh.add_argument(
        "--force-fetch",
        action="store_true",
        help="Bypass pushed_at cache and fetch all matched live checkouts.",
    )
    sync_gh.add_argument(
        "--inventory-file",
        default=None,
        help="Optional local JSON file containing GitHub repository inventory records (offline/testing).",
    )
    sync_gh.add_argument(
        "--ssh-fallback-host",
        default=None,
        help="Optional SSH target for `gh api user/repos` fallback (guarded by INV-FLEET-GAME-01).",
    )
    sync_gh.add_argument(
        "--remote-timeout",
        type=float,
        default=45.0,
        help="Per-repo timeout in seconds for git clone/fetch operations (default: 45.0).",
    )
    sync_gh.add_argument("--json", action="store_true")

    mcp = sub.add_parser(
        "mcp",
        help="Run the read-only Model Context Protocol (MCP) JSON-RPC 2.0 server over stdio.",
    )
    add_mcp_args(mcp)
    return parser


def _compile_from_paths(
    args: argparse.Namespace,
    *,
    input_json: Path,
    use_checkpoint_compile_cache: bool = False,
) -> dict:
    return compile_state(
        config_path=Path(args.config),
        compat_output_path=input_json,
        state_dir=Path(args.state_dir),
        identity_decisions_path=Path(args.identity_decisions),
        lineage_path=Path(args.project_lineage) if getattr(args, "project_lineage", None) else None,
        use_checkpoint_compile_cache=use_checkpoint_compile_cache,
        check_remote=bool(getattr(args, "check_remote", False)),
        remote_timeout=float(getattr(args, "remote_timeout", 3.0)),
    )


def _print_compile_result(manifest: dict, state_dir: str, as_json: bool) -> None:
    if as_json:
        _print_json(manifest)
        return
    summary = orient_payload(manifest)
    print(
        f"compiled {summary['counts']['observations']} observations from "
        f"{summary['counts']['sources']} sources; health={summary['health']['state']}"
    )
    print(f"manifest: {Path(state_dir).resolve() / 'system-manifest.json'}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            config_path = Path(args.config).resolve()
            config = read_config(config_path)
            validate_config(config, config_path.parent, check_artifacts=True)
            sources = describe_sources(config, config_path.parent)
            payload = {"ok": True, "source_count": len(sources), "sources": sources}
            if args.json:
                _print_json(payload)
            else:
                print(f"config valid: {config_path} ({len(sources)} sources)")
            return 0

        if args.command == "refresh":
            repo_root = Path(__file__).resolve().parents[1]
            config_path = Path(args.config).resolve()
            output_dir = Path(args.output_dir).resolve()
            checkpoint_path = Path(args.state_dir).resolve() / CHECKPOINT_FILENAME
            if args.no_markdown_mirror:
                from .compat import scan_without_markdown_mirror

                scan_without_markdown_mirror(
                    config_path=config_path,
                    output_dir=output_dir,
                    checkpoint_path=checkpoint_path,
                    full_scan=bool(args.full_scan),
                )
            else:
                from .compat import scan_with_markdown_mirror

                scan_with_markdown_mirror(
                    config_path=config_path,
                    output_dir=output_dir,
                    mirror_path=repo_root / "docs" / "ledgers" / "projects-ledger.md",
                    checkpoint_path=checkpoint_path,
                    full_scan=bool(args.full_scan),
                )
            manifest = _compile_from_paths(
                args,
                input_json=output_dir / "projects.json",
                use_checkpoint_compile_cache=True,
            )
            _print_compile_result(manifest, args.state_dir, args.json)
            return 0

        if args.command == "compile":
            manifest = _compile_from_paths(args, input_json=Path(args.input_json))
            _print_compile_result(manifest, args.state_dir, args.json)
            return 0

        if args.command == "resolve":
            payload = resolve_payload(Path(args.state_dir), args.referent)
            if args.json:
                _print_json(payload)
            elif payload["status"] == "resolved":
                print(
                    f"resolved {payload['referent']!r} -> {payload['canonical_project_id']} "
                    f"({payload['project_key']})"
                )
                if payload.get("gbrain_source_slug"):
                    gbrain = payload.get("gbrain") or {}
                    print(
                        f"gbrain: {payload['gbrain_source_slug']} "
                        f"({gbrain.get('page_key', '')})"
                    )
                pref = payload.get("preferred_location")
                if isinstance(pref, dict) and pref.get("location"):
                    print(
                        f"checkout: {pref['location']} "
                        f"[{pref.get('state')}/{pref.get('confidence')}]"
                    )
                for warning in payload.get("warnings") or []:
                    print(warning)
            elif payload["status"] == "ambiguous":
                print(f"ambiguous {payload['referent']!r}: {payload['candidate_count']} candidates")
                for candidate in payload["candidates"]:
                    print(
                        f"  {candidate['canonical_project_id']}\t{candidate['project_key']}\t"
                        f"{candidate['display_name']}"
                    )
            else:
                print(f"unresolved {payload['referent']!r}")
            return {"resolved": 0, "ambiguous": 3, "unresolved": 4}[payload["status"]]

        if args.command == "show":
            payload = show_payload(Path(args.state_dir), args.referent)
            if args.json:
                _print_json(payload)
            elif payload["status"] == "resolved":
                pref = payload["preferred_location"]
                gbrain = payload.get("gbrain") or {}
                print(f"project: {payload['canonical_project_id']} ({payload['project_key']})")
                print(f"display name: {payload['display_name']}")
                print(f"schema: {payload['schema_version']}")
                print(f"as-of: {payload['as_of']}")
                print(
                    f"gbrain: {payload.get('gbrain_source_slug')} "
                    f"({gbrain.get('page_key')}, basis={gbrain.get('match_basis')})"
                )
                print(
                    f"freshness: {payload['freshness']['state']} "
                    f"(content_as_of={payload['freshness']['content_as_of']})"
                )
                print(
                    f"preferred location: {pref['location']} "
                    f"[{pref['state']}/{pref['confidence']}] "
                    f"({', '.join(pref['reason_codes'])})"
                )
                for warning in payload.get("warnings") or []:
                    print(warning)
                print(f"observations: {payload['observation_count']}")
                print(f"open reviews: {payload['review']['open_review_count']}")
                print(f"capsule: {payload['pointers']['capsule_path']}")
            elif payload["status"] == "ambiguous":
                print(f"ambiguous {payload['referent']!r}: {payload['candidate_count']} candidates")
                for candidate in payload["candidates"]:
                    print(
                        f"  {candidate['canonical_project_id']}\t{candidate['project_key']}\t"
                        f"{candidate['display_name']}"
                    )
            else:
                print(f"unresolved {payload['referent']!r}")
            return {"resolved": 0, "ambiguous": 3, "unresolved": 4}[payload["status"]]

        if args.command == "locate":
            payload = locate_payload(Path(args.state_dir), args.referent)
            if args.json:
                _print_json(payload)
            elif payload["status"] == "resolved":
                pref = payload["preferred_location"]
                gbrain = payload.get("gbrain") or {}
                print(f"project: {payload['canonical_project_id']} ({payload['project_key']})")
                print(
                    f"gbrain: {payload.get('gbrain_source_slug')} "
                    f"({gbrain.get('page_key')})"
                )
                print(
                    f"preferred: {pref['location']} "
                    f"[{pref['state']}/{pref['confidence']}] "
                    f"({', '.join(pref['reason_codes'])})"
                )
                for warning in payload.get("warnings") or []:
                    print(warning)
                for loc in payload["locations"]:
                    print(
                        f"  #{loc['rank']}\t{loc['observation_id']}\t{loc['source_class']}\t"
                        f"{loc['source_status']}\t{loc['location']}\t{','.join(loc['reason_codes'])}"
                    )
            elif payload["status"] == "ambiguous":
                print(f"ambiguous {payload['referent']!r}: {payload['candidate_count']} candidates")
                for candidate in payload["candidates"]:
                    print(
                        f"  {candidate['canonical_project_id']}\t{candidate['project_key']}\t"
                        f"{candidate['display_name']}"
                    )
            else:
                print(f"unresolved {payload['referent']!r}")
            return {"resolved": 0, "ambiguous": 3, "unresolved": 4}[payload["status"]]

        if args.command == "explain":
            payload = explain_payload(
                Path(args.state_dir),
                args.referent,
                decisions_path=Path(args.identity_decisions),
            )
            if args.json:
                _print_json(payload)
            elif payload["status"] == "resolved":
                print(f"subject: {payload['subject_kind']} ({payload['referent']!r})")
                print(payload["explanation"])
                for warning in payload.get("warnings") or []:
                    print(warning)
            elif payload["status"] == "ambiguous":
                print(f"ambiguous {payload['referent']!r}: {payload['candidate_count']} candidates")
                for candidate in payload["candidates"]:
                    print(
                        f"  {candidate['canonical_project_id']}\t{candidate['project_key']}\t"
                        f"{candidate['display_name']}"
                    )
            else:
                print(f"unresolved {payload['referent']!r}")
            return {"resolved": 0, "ambiguous": 3, "unresolved": 4}[payload["status"]]

        if args.command == "review":
            if args.approve_candidates:
                approval = approve_candidate_decisions(
                    candidates_path=Path(args.candidates),
                    decisions_path=Path(args.identity_decisions),
                    decision_ids=args.decision_ids,
                )
                if args.json:
                    _print_json(approval)
                else:
                    print(
                        f"approved {approval['approved_count']} candidate decision(s) -> "
                        f"{approval['decisions_path']} (total={approval['total_decisions']})"
                    )
                return 0

            payload = review_payload(
                Path(args.state_dir),
                candidates_path=Path(args.candidates),
                decisions_path=Path(args.identity_decisions),
            )
            if args.json:
                _print_json(payload)
            else:
                print(f"open identity reviews: {payload['review_count']}")
                for item in payload["items"]:
                    print(f"  {item['review_id']}\t{item['code']}\t{item['severity']}\t{item['detail']}")
                print(
                    f"cross-project weak overlaps: {payload['untriaged_weak_overlap_count']} untriaged "
                    f"(of {payload['weak_overlap_count']} total)"
                )
                print(
                    f"frontier candidate decisions: {payload['pending_candidate_count']} pending approval "
                    f"({payload['approved_candidate_count']} already approved)"
                )
            return 0

        if args.command == "changes":
            payload = changes_payload(
                Path(args.state_dir),
                since_run_id=args.since_run_id,
                project_referent=args.project_referent,
                entity_kind=args.entity_kind,
                change_type=args.change_type,
            )
            status = payload.get("status", "ok")
            if args.json:
                _print_json(payload)
            elif status == "ok":
                print(f"run: {payload['run_id']} (compiled {payload.get('compiled_at')})")
                print(
                    f"baseline: {payload.get('previous_run_id') or 'none'} "
                    f"({payload.get('baseline_state')})"
                )
                print(f"changes: {payload['change_count']}")
                deltas = (payload.get("summary") or {}).get("count_deltas") or {}
                non_zero_deltas = [
                    f"{k} {v['before']} -> {v['after']} ({v['delta']:+d})"
                    for k, v in deltas.items()
                    if isinstance(v.get("delta"), int) and v["delta"] != 0
                ]
                if non_zero_deltas:
                    print("deltas: " + ", ".join(non_zero_deltas))
                for ev in payload.get("changes", []):
                    print(
                        f"  [{ev['severity']}] {ev['change_type']}\t"
                        f"{ev['entity_kind']}:{ev['entity_id']}\t{ev['summary']}"
                    )
            elif status == "unresolved_baseline":
                print(
                    f"unresolved baseline {payload.get('requested_since_run_id')!r} "
                    f"({payload.get('reason_code')}): {payload.get('detail')}"
                )
            elif status == "ambiguous":
                print(f"ambiguous {payload['referent']!r}: {payload['candidate_count']} candidates")
                for candidate in payload["candidates"]:
                    print(
                        f"  {candidate['canonical_project_id']}\t{candidate['project_key']}\t"
                        f"{candidate['display_name']}"
                    )
            else:
                print(f"unresolved {payload.get('referent')!r}")
            return {
                "ok": 0,
                "ambiguous": 3,
                "unresolved": 4,
                "unresolved_baseline": 4,
            }.get(status, 2)

        if args.command == "sync-gbrain":
            payload = sync_gbrain_pages(
                state_dir=Path(args.state_dir),
                brain_dir=Path(args.brain_dir),
                project_referents=args.project_referents,
                dry_run=bool(args.dry_run),
                ingest=bool(args.ingest),
                push=bool(args.push),
                prune_shadow_sources=args.prune_shadow_sources,
                gbrain_bin=str(args.gbrain_bin),
            )
            if args.json:
                _print_json(payload)
            else:
                mode_tag = " (dry-run)" if payload["dry_run"] else ""
                shadow_cnt = (payload.get("shadow_prune") or {}).get("pruned_count", 0)
                shadow_suffix = f" shadow_pruned={shadow_cnt}" if shadow_cnt else ""
                print(
                    f"sync-gbrain{mode_tag}: targets={payload['target_project_count']} "
                    f"created={payload['created_count']} updated={payload['updated_count']} "
                    f"unchanged={payload['unchanged_count']} ingest={payload['ingest']['status']}{shadow_suffix}"
                )
            return 0

        if args.command == "lineage":
            payload = lineage_status_payload(
                state_dir=Path(args.state_dir),
                lineage_path=Path(args.project_lineage),
                sync=bool(args.sync),
                merge_from_path=Path(args.merge_from) if args.merge_from else None,
            )
            if args.json:
                _print_json(payload)
            else:
                print(
                    f"lineage ({payload['mode']}): in_sync={payload['in_sync']} "
                    f"compiled={payload['compiled_project_count']} "
                    f"pinned={payload['lineage_project_count']} "
                    f"added={payload['added_count']} updated={payload['updated_count']} "
                    f"absorbed={payload['absorbed_count']}"
                )
            if args.check and not payload["in_sync"]:
                return 5
            return 0

        if args.command == "sync-github":
            mode = "check" if args.check else ("dry_run" if args.dry_run else "sync")
            payload = sync_github_estate(
                config_path=Path(args.config),
                state_dir=Path(args.state_dir),
                clone_root=Path(args.clone_root),
                mode=mode,
                clone_missing=not bool(args.no_clone),
                fetch_existing=not bool(args.no_fetch),
                ff_clean=not bool(args.no_ff),
                normalize_ssh_remotes=not bool(args.no_ssh_rewrite),
                force_fetch=bool(args.force_fetch),
                inventory_file=Path(args.inventory_file) if args.inventory_file else None,
                ssh_fallback_host=args.ssh_fallback_host,
                remote_timeout=float(args.remote_timeout),
            )
            if args.json:
                _print_json(payload)
            else:
                cnt = payload["counts"]
                print(
                    f"sync-github ({payload['mode']}): status={payload['status']} "
                    f"source={payload['inventory_source']} parity={payload['in_1to1_parity']} "
                    f"remote_active={cnt['remote_active']} matched={cnt['matched_local']} "
                    f"cloned={cnt['cloned']} fetched={cnt['fetched']} "
                    f"ff={cnt['fast_forwarded']} missing={cnt['missing_local']} "
                    f"no_remote={cnt['local_without_remote']} errors={cnt['errors']}"
                )
            if args.check and not payload["in_1to1_parity"]:
                return 6
            return 0

        if args.command == "mcp":
            return run_mcp_from_args(args)

        if args.command == "portfolio":
            payload = portfolio_payload(
                Path(args.state_dir),
                urgent_only=bool(args.urgent),
                dirty_only=bool(args.dirty),
                behind_only=bool(args.behind),
                remote_ahead_only=bool(args.remote_ahead),
                cycles_only=bool(args.cycles),
                lifecycle=args.lifecycle,
                depends_on=args.depends_on,
                depended_on_by=args.depended_on_by,
                limit=args.limit,
            )
            status = payload.get("status", "ok")
            if args.json:
                _print_json(payload)
            elif status == "ok":
                summ = payload["summary"]
                fleet = payload.get("fleet") or {}
                print(
                    f"portfolio: projects={summ['project_count']} matched={payload['matched_project_count']} "
                    f"urgent={summ['urgent_count']} dirty={summ['dirty_count']} "
                    f"behind={summ['behind_count']} cycles={summ['cycle_project_count']} "
                    f"edges={summ['ecosystem_edge_count']}"
                )
                print(
                    f"fleet ({fleet.get('state')}): "
                    f"dispatchable={','.join(fleet.get('dispatchable_nodes') or []) or 'none'} "
                    f"gaming={','.join(fleet.get('gaming_nodes') or []) or 'none'}"
                )
                for item in payload["projects"]:
                    pos = item["posture"]
                    eco = item["ecosystem"]
                    sh = item["structural_health"]
                    print(
                        f"  [u={pos['attention_urgency']}] {item['project_key']}\t"
                        f"{pos['lifecycle_posture']}\t"
                        f"deps={eco['depends_on_count']}\trdeps={eco['depended_on_by_count']}\t"
                        f"cycles={sh['cycle_count']}\t{item['preferred_location']['location'] or 'unavailable'}"
                    )
            elif status == "ambiguous":
                print(f"ambiguous {payload['referent']!r}: {payload['candidate_count']} candidates")
                for candidate in payload["candidates"]:
                    print(
                        f"  {candidate['canonical_project_id']}\t{candidate['project_key']}\t"
                        f"{candidate['display_name']}"
                    )
            else:
                print(f"unresolved {payload.get('referent')!r}")
            return {"ok": 0, "ambiguous": 3, "unresolved": 4}.get(status, 2)

        manifest = load_manifest(Path(args.state_dir))
        if args.command == "orient":
            payload = orient_payload(manifest)
            if args.json:
                _print_json(payload)
            else:
                print(f"run: {payload['run_id']}")
                print(f"compiled: {payload['compiled_at']}")
                print(f"input as-of: {payload['input_observed_at']}")
                print(f"health: {payload['health']['state']}")
                print(f"sources: {payload['counts']['sources']}")
                print(f"observations: {payload['counts']['observations']}")
                if payload["counts"].get("canonical_projects") is not None:
                    print(f"canonical projects: {payload['counts']['canonical_projects']}")
                if payload["counts"].get("project_capsules") is not None:
                    print(f"project capsules: {payload['counts']['project_capsules']}")
                if payload["counts"].get("ecosystem_edges") is not None:
                    print(f"ecosystem edges: {payload['counts']['ecosystem_edges']}")
                if payload["counts"].get("review_items") is not None:
                    print(f"identity review: {payload['counts']['review_items']}")
                if payload["counts"].get("change_events") is not None:
                    chg_meta = payload.get("changes") or {}
                    print(
                        f"change events: {payload['counts']['change_events']} "
                        f"(baseline={chg_meta.get('baseline_state', 'unknown')})"
                    )
                cp_meta = payload.get("checkpoints")
                if isinstance(cp_meta, dict) and cp_meta.get("state") in {"active", "bypassed"}:
                    print(
                        f"checkpoints: {cp_meta['state']} "
                        f"(hits={cp_meta.get('hit_source_count', 0)}/{cp_meta.get('source_count', 0)}, "
                        f"reused={cp_meta.get('reused_candidate_count', 0)}, "
                        f"rebuilt={cp_meta.get('rebuilt_candidate_count', 0)})"
                    )
                print("available: " + ", ".join(payload["available_capabilities"]))
                if payload["unavailable_capabilities"]:
                    print("not yet available: " + ", ".join(payload["unavailable_capabilities"]))
            return 0

        if args.command == "sources":
            sources = manifest.get("sources", [])
            if args.json:
                _print_json(sources)
            else:
                for source in sources:
                    cp_status = source.get("checkpoint_status", "not_applicable")
                    print(
                        f"{source['source_id']}\t{source['status']}\t{source['source_class']}\t"
                        f"{source['label']}\t{source['path']}\tcheckpoint={cp_status}"
                    )
            return 0

    except LedgerConfigError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2
    except LedgerCompatError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, ArithmeticError) as exc:
        print(f"LEDGER_ERROR: {exc}", file=sys.stderr)
        return 2
    return 2
