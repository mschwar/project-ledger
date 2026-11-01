from __future__ import annotations

import argparse
import json
import subprocess
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
from .identity import resolve_payload
from .jev import (
    decide as jev_decide,
    evaluate_heads as jev_evaluate_heads,
    list_heads as jev_list_heads,
    project_advisory_for_referent,
)
from .lineage import lineage_status_payload
from .mcp_server import add_mcp_args, run_mcp_from_args
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
    review.add_argument(
        "--jev",
        action="store_true",
        help="Attach read-only Type 1 Jev System-One advisory relation classifications to untriaged weak overlaps.",
    )
    review.add_argument(
        "--live",
        action="store_true",
        help="Allow live TypeSafe `/v1/systemone` calls when combined with --jev (otherwise obeys LEDGER_JEV).",
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

    jev_cmd = sub.add_parser(
        "jev",
        help="Inspect, execute, or evaluate governed Type 1 Jev (`jev-1.13.0`) System-One advisory heads.",
    )
    jev_sub = jev_cmd.add_subparsers(dest="jev_action", required=True)

    jev_heads = jev_sub.add_parser("heads", help="List validated Project Ledger Jev head contracts.")
    jev_heads.add_argument("--json", action="store_true")

    jev_decide_cmd = jev_sub.add_parser(
        "decide",
        help="Execute one or more governed Jev heads against an explicit state JSON payload or @file.",
    )
    jev_decide_cmd.add_argument(
        "--head",
        action="append",
        dest="head_ids",
        required=True,
        help="Jev head ID(s) to evaluate (repeatable).",
    )
    jev_decide_cmd.add_argument(
        "--state",
        required=True,
        help="Inline JSON object or @path/to/state.json.",
    )
    jev_decide_cmd.add_argument("--state-dir", default=default_state)
    jev_decide_cmd.add_argument("--provider", choices=["code", "jev"], default=None)
    jev_decide_cmd.add_argument(
        "--live",
        action="store_true",
        help="Force live TypeSafe `/v1/systemone` execution even when LEDGER_JEV=off.",
    )
    jev_decide_cmd.add_argument(
        "--trace",
        action="store_true",
        help="Append decision trace record(s) to <state-dir>/jev-decisions.jsonl.",
    )
    jev_decide_cmd.add_argument("--json", action="store_true")

    jev_proj = jev_sub.add_parser(
        "project",
        help="Run batched lifecycle & urgency advisory heads on a resolved canonical project capsule.",
    )
    jev_proj.add_argument("referent")
    jev_proj.add_argument("--state-dir", default=default_state)
    jev_proj.add_argument("--provider", choices=["code", "jev"], default=None)
    jev_proj.add_argument("--live", action="store_true")
    jev_proj.add_argument("--json", action="store_true")

    jev_eval = jev_sub.add_parser(
        "eval",
        help="Evaluate Project Ledger Jev heads against the 15-case gold corpus.",
    )
    jev_eval.add_argument("--provider", choices=["code", "jev"], default="code")
    jev_eval.add_argument("--head", default=None, dest="head_filter")
    jev_eval.add_argument("--live", action="store_true")
    jev_eval.add_argument("--skip-pre-rule", action="store_true")
    jev_eval.add_argument("--json", action="store_true")

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
                build_script = repo_root / "build_ledger.py"
                cmd_prefix = (
                    [sys.executable, str(build_script)]
                    if build_script.is_file()
                    else [sys.executable, "-m", "build_ledger"]
                )
                cmd = [
                    *cmd_prefix,
                    "--config",
                    str(config_path),
                    "--output-dir",
                    str(output_dir),
                    "--checkpoint-file",
                    str(checkpoint_path),
                ]
                if args.full_scan:
                    cmd.append("--full-scan")
                scan = subprocess.run(cmd, check=False)
                if scan.returncode != 0:
                    print(f"LEDGER_SCAN_FAILED: build_ledger.py exited {scan.returncode}", file=sys.stderr)
                    return scan.returncode or 2
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
                include_jev=bool(args.jev),
                jev_force_live=bool(args.live),
                jev_allow_vault=bool(args.live),
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
                if args.jev:
                    for ov in payload["untriaged_weak_overlaps"]:
                        adv = ov.get("jev_advisory") or {}
                        print(
                            f"  [jev:{adv.get('provider')}] {ov['left_observation_id']} <-> "
                            f"{ov['right_observation_id']} -> {adv.get('effective_output')} "
                            f"(conf={adv.get('confidence', 0.0):.2f})"
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
                gbrain_bin=str(args.gbrain_bin),
            )
            if args.json:
                _print_json(payload)
            else:
                mode_tag = " (dry-run)" if payload["dry_run"] else ""
                print(
                    f"sync-gbrain{mode_tag}: targets={payload['target_project_count']} "
                    f"created={payload['created_count']} updated={payload['updated_count']} "
                    f"unchanged={payload['unchanged_count']} ingest={payload['ingest']['status']}"
                )
            return 0

        if args.command == "lineage":
            payload = lineage_status_payload(
                state_dir=Path(args.state_dir),
                lineage_path=Path(args.project_lineage),
                sync=bool(args.sync),
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

        if args.command == "jev":
            if args.jev_action == "heads":
                heads = jev_list_heads()
                payload = {"count": len(heads), "heads": heads}
                if args.json:
                    _print_json(payload)
                else:
                    for h in heads:
                        print(
                            f"{h['id']}\ttype={h['type']}\tauthority={h['authority']}\t"
                            f"ceiling={h['ceiling']}\tmodel={h['provider']['model']}"
                        )
                return 0

            if args.jev_action == "decide":
                raw_state = str(args.state)
                if raw_state.startswith("@"):
                    state_text = Path(raw_state[1:]).read_text(encoding="utf-8")
                else:
                    state_text = raw_state
                parsed_state = json.loads(state_text)
                if not isinstance(parsed_state, dict):
                    raise ValueError("--state must decode to a JSON object.")
                eff_prov = args.provider or ("jev" if args.live else None)
                payload = jev_decide(
                    list(args.head_ids),
                    parsed_state,
                    trace_dir=Path(args.state_dir) if args.trace else None,
                    provider=eff_prov,
                    allow_vault=bool(args.live),
                    force_live=bool(args.live),
                    record_trace=bool(args.trace),
                )
                if args.json:
                    _print_json(payload)
                else:
                    for d in payload["decisions"]:
                        print(
                            f"{d['head_id']}\toutput={d['effective_output']}\t"
                            f"provider={d['provider']}\tconf={d['confidence']:.3f}\t"
                            f"ceiling={d['ceiling']}"
                        )
                return 0

            if args.jev_action == "project":
                eff_prov = args.provider or ("jev" if args.live else None)
                payload = project_advisory_for_referent(
                    Path(args.state_dir),
                    args.referent,
                    provider=eff_prov,
                    force_live=bool(args.live),
                    allow_vault=bool(args.live),
                    record_trace=False,
                )
                if args.json:
                    _print_json(payload)
                elif payload.get("status") == "resolved":
                    adv = payload["jev_advisory"]
                    print(f"project: {payload['canonical_project_id']} ({payload['project_key']})")
                    print(f"lifecycle posture (advisory): {adv['lifecycle_posture']}")
                    print(f"attention urgency (advisory): {adv['attention_urgency']}/3")
                else:
                    print(f"{payload.get('status', 'unresolved')} {args.referent!r}")
                return {"resolved": 0, "ambiguous": 3, "unresolved": 4}.get(
                    str(payload.get("status")),
                    2,
                )

            if args.jev_action == "eval":
                eff_prov = "jev" if (args.live and args.provider == "code") else args.provider
                payload = jev_evaluate_heads(
                    provider=eff_prov,
                    head_filter=args.head_filter,
                    allow_vault=bool(args.live),
                    force_live=bool(args.live or eff_prov == "jev"),
                    skip_pre_rule=bool(args.skip_pre_rule),
                )
                if args.json:
                    _print_json(payload)
                else:
                    print(
                        f"jev eval ({eff_prov}, skip_pre_rule={payload['skip_pre_rule']}): "
                        f"{payload['correct']}/{payload['total']} ({payload['accuracy'] * 100:.1f}%) "
                        f"pre_rule={payload['pre_rule_hits']} live_calls={payload['live_model_calls']} "
                        f"tokens={payload['total_input_tokens']}"
                    )
                return 0 if (payload["abstained"] == 0 and payload["accuracy"] >= 0.85) else 2

        if args.command == "mcp":
            return run_mcp_from_args(args)

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
    except (OSError, ValueError) as exc:
        print(f"LEDGER_ERROR: {exc}", file=sys.stderr)
        return 2
    return 2
