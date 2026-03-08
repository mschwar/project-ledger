# AGENTS.md

## Mission

Build `project-ledger` into a durable inventory system for projects, repositories, and idea vaults spread across multiple computers and storage roots.

The repo currently contains a working prototype scanner. Your job is to harden it into a maintainable toolchain that supports:

- repeated ingestion across multiple machines
- stable project identity across runs and devices
- manual curation where heuristics are insufficient
- mergeable machine-specific outputs
- human-readable and agent-readable artifacts

## Start Here

Read these files in this order before making non-trivial changes:

1. `README.md`
2. `PRD.md`
3. `ARCHITECTURE.md`
4. `SCHEMA.md`
5. `ROADMAP.md`
6. `BACKLOG.md`
7. `RUNBOOK.md`

## Current State

As of March 8, 2026:

- `build_ledger.py` is the working prototype CLI
- `ledger_config.json` defines the current scan roots
- `templates/project-ledger.sidecar.example.json` defines the sidecar pattern
- `prompts/session_end_prompt.md` defines the session-end ledger update prompt
- the prototype emits CSV, JSON, and Markdown outputs
- unit tests exist but coverage is intentionally small

## Operating Rules

1. Do not break the existing `python build_ledger.py` flow while refactoring.
2. Preserve deterministic outputs unless the schema/version explicitly changes.
3. When changing schema, update:
   - `SCHEMA.md`
   - sidecar example template
   - tests
   - README and runbook if operator behavior changes
4. When adding a new capability, add at least one automated test.
5. Prefer standard library solutions unless a dependency is clearly justified.
6. Generated artifacts in `output/` are not canonical source files.
7. Keep sidecar files human-editable and conservative. Do not force complex manual workflows.
8. Any work that changes session-end metadata expectations must also update `prompts/session_end_prompt.md`.

## Expected Commands

```powershell
python build_ledger.py
python -m unittest tests\test_build_ledger.py
```

If you introduce new commands, document them in `README.md` and `RUNBOOK.md`.

## Definition Of Done

A change is not done unless:

- the code path works locally
- tests pass
- docs reflect the new behavior
- schema changes are documented
- the handoff path for future agents remains clear

## Preferred Execution Order

Parallel work is fine, but sequence dependencies matter:

1. Schema hardening
2. Package refactor and config validation
3. Merge and multi-machine support
4. Reporting and operator UX improvements
5. Automation and optional UI layers

## Session-End Requirement

When you finish work in this repo, update relevant docs and, if appropriate, refresh any sample sidecar or output examples. Use `prompts/session_end_prompt.md` as the pattern for how downstream repos should be updated.
