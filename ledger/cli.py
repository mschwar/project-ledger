from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .compiler import compile_state, load_manifest, orient_payload
from .config import LedgerConfigError, describe_sources, read_config, validate_config


def _print_json(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ledger", description="Project Ledger agent-facing substrate CLI.")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="Validate config and required source artifacts.")
    validate.add_argument("--config", default="ledger_config.json")
    validate.add_argument("--json", action="store_true")

    compile_cmd = sub.add_parser("compile", help="Compile typed observations and the system manifest.")
    compile_cmd.add_argument("--config", default="ledger_config.json")
    compile_cmd.add_argument("--input-json", default="output/projects.json")
    compile_cmd.add_argument("--state-dir", default="state")
    compile_cmd.add_argument("--json", action="store_true")

    orient = sub.add_parser("orient", help="Read the compact system orientation surface.")
    orient.add_argument("--state-dir", default="state")
    orient.add_argument("--json", action="store_true")

    sources = sub.add_parser("sources", help="Show source health from the latest manifest.")
    sources.add_argument("--state-dir", default="state")
    sources.add_argument("--json", action="store_true")
    return parser


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

        if args.command == "compile":
            manifest = compile_state(
                config_path=Path(args.config),
                compat_output_path=Path(args.input_json),
                state_dir=Path(args.state_dir),
            )
            if args.json:
                _print_json(manifest)
            else:
                summary = orient_payload(manifest)
                print(
                    f"compiled {summary['counts']['observations']} observations from "
                    f"{summary['counts']['sources']} sources; health={summary['health']['state']}"
                )
                print(f"manifest: {Path(args.state_dir).resolve() / 'system-manifest.json'}")
            return 0

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
