# AGENTS.md

## Mission

Build `project-ledger` into the durable project-reality layer for projects, repositories, vaults, and idea containers spread across multiple computers and storage surfaces.

The system discovers machine/source observations, preserves provenance, overlays explicit operator metadata, and is evolving toward canonical project identity across those observations.

It must support:

- repeated ingestion across multiple machines and storage surfaces
- inventory/policy-backed ingestion for cloud or remotely inventoried roots
- stable project identity across runs and devices
- manual curation where heuristics are insufficient
- mergeable machine/source observations
- human-readable and agent-readable artifacts
- a thin current-state pointer (`status`, last session, next step) without becoming a general task manager

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

As of September 14, 2026:

- `build_ledger.py` is the working CLI and remains monolithic
- `ledger_config.json` describes the operator's current live roots
- discovery modes include `children`, `git_repos`, `self`, and `inventory_policy`
- `inventory_policy` consumes durable inventory JSONL plus a root-policy artifact before promoting project candidates
- configured inputs include `/central` project/repo/service surfaces, selected Mac mirrors, Matty-PC inventory data, Google Drive inventory/policy data, and backup roots
- `.project-ledger.json` is the project-local metadata/current-state overlay
- the scanner emits CSV, JSON, Markdown, and the canonical human-facing Markdown mirror under `docs/ledgers/`
- generated snapshots have demonstrated 100+ observations across mixed local/shared sources
- unit tests cover core discovery, sidecars, inventory-policy ingestion, stable inventory identity, path handling, malformed/missing inventory-policy config, and Markdown mirroring
- canonical multi-observation merge is not implemented yet
- observation records and canonical project records are not yet separated in the output schema

The next architectural milestone is schema/identity hardening followed by canonical merge and review reporting.

## Operating Rules

1. `main` is the authoritative product branch. Do not allow long-lived feature branches to become a shadow production branch.
2. Use bounded work units. One substantive change should normally land through one focused branch/PR, receive review/QA, and merge promptly.
3. Do not break the existing `python build_ledger.py` flow while refactoring.
4. Preserve deterministic outputs unless the schema/version explicitly changes.
5. Scanning target projects is read-only.
6. When changing schema, update:
   - `SCHEMA.md`
   - sidecar example template
   - tests
   - README and runbook if operator behavior changes
7. When adding a new capability, add at least one automated test.
8. Prefer standard library solutions unless a dependency is clearly justified.
9. Generated artifacts in `output/` are not canonical source files. `docs/ledgers/projects-ledger.md` is a committed human-facing mirror, not the source of project identity.
10. Keep sidecar files human-editable and conservative. Do not force complex manual workflows.
11. Any work that changes session-end metadata expectations must also update `prompts/session_end_prompt.md`.
12. Preserve provenance and uncertainty. Never silently collapse ambiguous observations into one canonical project.

## Architectural Rule

The intended data flow is:

```text
source inventories / filesystems / git
  -> observations
  -> identity evidence
  -> canonical projects
  -> current state / review state
  -> reports and downstream agent/control-plane consumers
```

Inventory discovers reality. Policy decides which inventory roots are relevant for project discovery. Project Ledger promotes and reconciles project observations.

## Expected Commands

```powershell
python build_ledger.py
python -m unittest tests\test_build_ledger.py
```

CI runs the unit test suite on pushes and pull requests.

If you introduce new commands, document them in `README.md` and `RUNBOOK.md`.

## Definition Of Done

A change is not done unless:

- the code path works locally or in CI
- tests pass
- docs reflect the new behavior
- schema changes are documented
- the handoff path for future agents remains clear
- `main` is left as the intended authoritative state after the work is merged

## Preferred Execution Order

1. Schema and identity hardening
2. Package refactor/config validation cleanup
3. Observation/canonical split and multi-machine merge
4. Review/change reporting and operator analytics
5. Agent/control-plane integration
6. Optional structured storage/UI/automation

## Session-End Requirement

When you finish work in this repo, update relevant docs and the project sidecar when appropriate. Use `prompts/session_end_prompt.md` as the pattern for downstream project metadata updates.
