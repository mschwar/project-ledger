# ADR-0001: Treat Project Ledger as an agent-native reality compiler

- Status: accepted design direction
- Date: 2026-09-14

## Context

Project Ledger began as a useful filesystem/project scanner. Multi-source ingestion then expanded the number and kinds of observations: live roots, repos, vaults, mirrors, backups, machine inventories, and policy-backed cloud inventories.

At that scale the limiting problem is no longer discovery. It is repeated cognitive reconstruction: determining which observations represent one conceptual project, which source is fresh/usable, what state is trustworthy, what conflicts exist, and how an agent should continue work without replaying prior archaeology.

A flat ledger row and human Markdown table cannot provide a sufficiently explicit epistemic/control model for autonomous agents.

## Decision

Project Ledger will evolve as a **project-reality compiler and control substrate**.

The architecture will separate:

```text
sources -> snapshots -> observations/claims -> identity evidence/decisions
       -> canonical projects -> current state/signals -> agent views
```

Canonical project state is compiled from evidence/declarations/decisions rather than directly hand-edited.

Normal agent reads will use compact materialized views (system manifest and project capsules) with pointers to deeper evidence. Ambiguity will become first-class review items. Material agent work will eventually emit structured session receipts so continuity accretes across sessions.

Project Ledger will not absorb general task lifecycle, deep knowledge/document storage, or raw source ownership.

## Key consequences

### Positive

- agents can orient with bounded context;
- canonical state becomes explainable/rebuildable;
- uncertainty/freshness are explicit;
- duplicate observations no longer masquerade as duplicate projects;
- identity/review resolutions become durable and stop recurring;
- deterministic/incremental work can handle most cases cheaply;
- downstream systems can share canonical project IDs instead of reimplementing identity.

### Costs

- schema becomes layered rather than one flat row;
- canonicalization requires decision/review machinery;
- current sidecars become claim/declaration sources rather than unquestioned overrides;
- migration must preserve compatibility outputs while new contracts land;
- documentation/tests must track epistemic and version semantics more rigorously.

## Alternatives rejected

### Keep extending the flat scanner

Rejected because source proliferation amplifies duplicate/conflicting state and forces every agent/downstream tool to reconstruct project identity independently.

### Make the sidecar the canonical database

Rejected because one conceptual project may have several divergent copies/sidecars and some observations are inventory-only or inaccessible.

### Put everything into one central mutable project record

Rejected because direct canonical mutation loses provenance and makes rebuild/debug/reversal difficult.

### Solve identity primarily with LLM semantic matching

Rejected as the default because deterministic evidence and explicit decisions are cheaper, more reproducible, and more explainable. Semantic models remain an escalation path for ambiguous evidence.

### Turn Project Ledger into the task/control plane itself

Rejected because project topology and task lifecycle are separate ownership domains. Project Ledger should provide reliable project identity/state to the task system.

## Invariants introduced

- no silent canonicalization;
- canonical views are compiled;
- durable identity is independent of path/name;
- observed/declaration/inference/decision types remain distinguishable;
- freshness and unavailable/stale/absent/conflicted states remain explicit;
- deterministic reasoning precedes expensive semantic reasoning;
- resolved ambiguity becomes durable knowledge/tests;
- `main` remains product authority.

## Follow-up

`SYSTEM.md`, `AGENT_PROTOCOL.md`, `ARCHITECTURE.md`, `SCHEMA.md`, `PRD.md`, `ROADMAP.md`, and `BACKLOG.md` encode the detailed contracts and gated migration path stemming from this decision.
