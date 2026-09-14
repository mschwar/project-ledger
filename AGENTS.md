# AGENTS.md

## Mission

Build and operate `project-ledger` as the durable **project-reality compiler and control substrate** for projects spread across machines and storage surfaces.

The system senses distributed source evidence, normalizes it into observations, reconciles durable project identity without hiding uncertainty, compiles project/current-state views, and exposes a cheap trustworthy read path for agents.

The design goal is not maximum metadata. It is **minimum uncertainty per unit of agent attention and compute**.

## Start here

Use progressive disclosure rather than reading the entire repo by default.

For any substantive task:

1. Read `SYSTEM.md` for the conceptual model/invariants.
2. Read `AGENT_PROTOCOL.md` for the operating loop.
3. Read only the task-relevant contract:
   - `ARCHITECTURE.md` for module/dataflow changes
   - `SCHEMA.md` for entity/identity/state changes
   - `RUNBOOK.md` for operation/recovery
   - `ROADMAP.md` / `BACKLOG.md` for sequencing/execution
   - `PRD.md` for product outcomes and acceptance criteria
4. Inspect current ledger/source artifacts only when the task depends on estate state.

Target-state agents should normally orient through `ledger orient` / `state/system-manifest.json` and load a project capsule instead of reconstructing the estate from prose.

## Current state

As of September 14, 2026:

- `main` contains the converged multi-source scanner and is product authority.
- `build_ledger.py` remains the working monolithic CLI.
- discovery supports `children`, `git_repos`, `self`, and `inventory_policy`.
- configured sources include `/central` roots, selected Mac mirrors, Matty-PC inventory, Google Drive inventory/policy, and backup surfaces.
- `.project-ledger.json` provides a thin project-local declaration/current-state overlay.
- outputs include CSV, JSON, Markdown, and the committed human-facing ledger mirror.
- CI compiles the scanner and runs unit tests.
- observation and canonical-project entities are not yet separated in executable schema.
- canonical multi-observation merge, system manifest/project capsules, session receipts, change feed, and review queue are target architecture, not yet implemented.

The next milestone is the agent-native substrate: versioned schemas/IDs/claim semantics and the observation-vs-canonical boundary that all later control/query surfaces depend on.

## Architectural laws

These summarize `SYSTEM.md`; the full document is authoritative for rationale.

1. Canonical state is compiled, not directly hand-authored.
2. Preserve provenance; never silently collapse ambiguous observations.
3. Separate observed facts, declarations, inferences, and explicit decisions.
4. Identity is not a path/name/URL; those are evidence and aliases.
5. Freshness and uncertainty are first-class state.
6. Agent-facing reads should be compact materialized views with pointers to deeper evidence.
7. Deterministic/incremental computation precedes semantic/expensive reasoning.
8. Resolved ambiguity becomes a durable decision/test so it is not paid for twice.
9. Project Ledger owns project topology/current-state resolution, not general task lifecycle or deep knowledge storage.
10. Sensing is read-only with respect to target projects.
11. Fail locally and explicitly; degraded sources/ambiguous projects do not poison healthy state.
12. `main` remains authoritative; shadow product branches are defects.

## Development operating rules

1. Use bounded work units: one coherent task -> focused branch/workspace -> tests/docs -> review/QA -> resolve -> merge.
2. Preserve the existing `python build_ledger.py` entrypoint while refactoring unless an explicitly versioned migration changes it.
3. Preserve deterministic output semantics unless the schema/version explicitly changes.
4. Schema/protocol changes must update documentation, validation/tests, migration expectations, and agent/operator behavior in the same work unit.
5. New capabilities require automated coverage appropriate to their risk.
6. Prefer standard-library/deterministic solutions unless a dependency or model materially improves correctness or cost.
7. Generated artifacts are views, not identity truth.
8. Keep project-local declarations human-editable and conservative.
9. Do not overload sidecars into a task manager.
10. Do not use expensive models where exact source evidence can answer the question.

## Expected commands today

```powershell
python build_ledger.py
python -m unittest tests.test_build_ledger
```

CI runs compile + tests on pushes and pull requests.

Target commands are defined in `SYSTEM.md`/`ARCHITECTURE.md` and should be introduced behind stable machine-readable contracts.

## Definition of done

A substantive change is not done unless:

- behavior/contracts are implemented coherently;
- tests/validation pass;
- documentation reflects actual behavior;
- schema compatibility/migration is addressed where relevant;
- uncertainty/error paths are explicit;
- the agent/operator read path remains clear;
- material decisions or new failure knowledge are made durable;
- completed work is merged so `main` remains authoritative.

## Session-end requirement

For material work, leave a bounded evidence-bearing handoff: what landed, verification, unresolved issues, exact continuation point, and relevant refs. Today use the project sidecar/session-end prompt where appropriate. The target architecture upgrades this into structured session receipts linked to canonical projects.
