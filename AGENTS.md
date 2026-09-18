# AGENTS.md

## Mission

Build and operate `project-ledger` as the durable **project-reality compiler and control substrate** for projects spread across machines and storage surfaces.

The system senses distributed source evidence, normalizes it into observations, reconciles durable project identity without hiding uncertainty, compiles project/current-state views, and exposes a cheap trustworthy read path for agents.

The design goal is not maximum metadata. It is **minimum uncertainty per unit of agent attention and compute**.

## Start here

Use progressive disclosure rather than reading the entire repo by default.

For any substantive task:

1. Read `CURRENT.md` for the executable frontier and current capability boundary.
2. Read `SYSTEM.md` for the conceptual model/invariants.
3. Read `AGENT_PROTOCOL.md` for the operating loop.
4. Read only the task-relevant contract:
   - `ARCHITECTURE.md` for module/dataflow changes
   - `SCHEMA.md` for entity/identity/state changes
   - `RUNBOOK.md` for operation/recovery
   - `ROADMAP.md` / `BACKLOG.md` for sequencing/execution
   - `PRD.md` for product outcomes and acceptance criteria
4. Inspect current ledger/source artifacts only when the task depends on estate state.

Target-state agents should normally orient through `ledger orient` / `state/system-manifest.json` and load a project capsule instead of reconstructing the estate from prose.

## Current state

As of September 18, 2026:

- `main` is product authority and contains the converged multi-source compatibility scanner plus the executable Wave 1 agent substrate.
- preferred refresh is `python -m ledger refresh`; automation can use `--no-markdown-mirror`.
- implemented agent commands are `validate`, `refresh`, `compile`, `orient`, and `sources`.
- generated Wave 1 state is `state/system-manifest.json` plus `state/observations.json`; `state/` is rebuildable and gitignored.
- stable source IDs, compatibility snapshot IDs, manifestation-oriented observation IDs, source probes/health, explicit capability states, and degraded-source containment are implemented.
- the first canonical identity slice is implemented: exact normalized repository remotes auto-merge; durable merge/split/reject/alias decisions are compiler inputs; canonical projects and identity review are materialized; `ledger resolve` is available.
- project capsules, preferred-location resolution, `show/locate/explain`, structured session receipts/current-state resolution, and Project Ledger's semantic change feed are not implemented yet.
- homelab owns the deployed reality-provider seam and consumes the non-mutating refresh contract; Project Ledger continues to own source/project schema and identity semantics.
- the committed human ledger is historical evidence, not proof of current production freshness. A live provider proof on the intended homelab node remains an operational closeout item.

The observation -> canonical identity boundary has now been crossed conservatively. Do not broaden automatic matching beyond exact remote identity without evidence/decision contracts. `CURRENT.md` is the canonical frontier pointer; the next software constraint is chosen only after live provider proof/synthesis.

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

```bash
python -m ledger validate
python -m ledger refresh
python -m ledger refresh --no-markdown-mirror
python -m ledger compile
python -m ledger orient
python -m ledger sources
python -m ledger resolve <referent>
python -m unittest discover -s tests
```

The compatibility entrypoint `python build_ledger.py` remains supported during migration.

Commands such as `show`, `locate`, `explain`, `review`, `changes`, and `record-session` remain unavailable until their stated gate lands. Consume the manifest capability map rather than inferring capability from prose.

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
