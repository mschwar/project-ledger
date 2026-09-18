from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .compiler import compile_state, load_manifest, orient_payload
from .config import LedgerConfigError, describe_sources, read_config, validate_config
from .identity import resolve_payload


def _print_json(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _add_compile_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="ledger_config.json")
    parser.add_argument("--state-dir", default="state")
    parser.add_argument("--identity-decisions", default="registry/identity-decisions.json")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ledger", description="Project Ledger agent-facing substrate CLI.")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="Validate config and required source artifacts.")
    validate.add_argument("--config", default="ledger_config.json")
    validate.add_argument("--json", action="store_true")

    refresh = sub.add_parser("refresh", help="Run the compatibility scanner, then compile agent-facing state.")
    _add_compile_args(refresh)
    refresh.add_argument("--output-dir", default="output")
    refresh.add_argument(
        "--no-markdown-mirror",
        action="store_true",
        help="Do not update docs/ledgers/projects-ledger.md; intended for external provider automation.",
    )
    refresh.add_argument("--json", action="store_true")

    compile_cmd = sub.add_parser("compile", help="Compile typed observations and the system manifest.")
    _add_compile_args(compile_cmd)
    compile_cmd.add_argument("--input-json", default="output/projects.json")
    compile_cmd.add_argument("--json", action="store_true")

    orient = sub.add_parser("orient", help="Read the compact system orientation surface.")
    orient.add_argument("--state-dir", default="state")
    orient.add_argument("--json", action="store_true")

    sources = sub.add_parser("sources", help="Show source health from the latest manifest.")
    sources.add_argument("--state-dir", default="state")
    sources.add_argument("--json", action="store_true")

    resolve = sub.add_parser("resolve", help="Resolve an exact project referent to canonical identity.")
    resolve.add_argument("referent")
    resolve.add_argument("--state-dir", default="state")
    resolve.add_argument("--json", action="store_true")
    return parser


def _compile_from_paths(args: argparse.Namespace, *, input_json: Path) -> dict:
    return compile_state(
        config_path=Path(args.config),
        compat_output_path=input_json,
        state_dir=Path(args.state_dir),
        identity_decisions_path=Path(args.identity_decisions),
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
            if args.no_markdown_mirror:
                from .compat import scan_without_markdown_mirror

                scan_without_markdown_mirror(config_path=config_path, output_dir=output_dir)
            else:
                scan = subprocess.run(
                    [
                        sys.executable,
                        str(repo_root / "build_ledger.py"),
                        "--config",
                        str(config_path),
                        "--output-dir",
                        str(output_dir),
                    ],
                    check=False,
                )
                if scan.returncode != 0:
                    print(f"LEDGER_SCAN_FAILED: build_ledger.py exited {scan.returncode}", file=sys.stderr)
                    return scan.returncode or 2
            manifest = _compile_from_paths(args, input_json=output_dir / "projects.json")
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
                if payload["counts"].get("review_items") is not None:
                    print(f"identity review: {payload['counts']['review_items']}")
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
                    print(
                        f"{source['source_id']}\t{source['status']}\t{source['source_class']}\t"
                        f"{source['label']}\t{source['path']}"
                    )
            return 0

    except LedgerConfigError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"LEDGER_ERROR: {exc}", file=sys.stderr)
        return 2
    return 2
