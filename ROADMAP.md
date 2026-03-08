# Roadmap

## Phase 0

Status: completed on March 8, 2026

Delivered:

- prototype scanner
- config-driven root discovery
- sidecar overlay pattern
- CSV, JSON, Markdown exports
- initial tests
- session-end prompt
- handoff documentation

## Phase 1

Goal: harden the prototype into a maintainable core

Deliverables:

- refactor `build_ledger.py` into package modules
- config validation with clear errors
- richer fixture-based test coverage
- sidecar validation and better malformed-sidecar reporting
- schema versioning plan
- cleaner repo hygiene and sample fixture strategy

Exit criteria:

- no major feature lives only in monolithic script code
- tests cover the major discovery modes and extractor branches
- schema and code are in sync

## Phase 2

Goal: support multi-machine ingestion and canonical merge

Deliverables:

- machine/run metadata in JSON outputs
- merge command for multiple ledger JSON files
- alias and identity resolution rules
- duplicate/ambiguous match review report
- canonical master ledger artifact

Exit criteria:

- two machine snapshots can be merged reproducibly
- ambiguous matches are surfaced instead of silently collapsed

## Phase 3

Goal: make the ledger operationally useful day to day

Deliverables:

- change reports between runs
- missing-sidecar report for important projects
- stale-project report based on activity thresholds
- optional status summaries by tag/type/root
- better README/path navigation output

Exit criteria:

- owner can review what changed since the last ingest without manual diffing

## Phase 4

Goal: reduce maintenance friction

Deliverables:

- optional SQLite export
- optional lightweight local UI
- optional automation hooks for scheduled scans
- improved session-end integration patterns

Exit criteria:

- routine maintenance does not require manual CSV inspection for common tasks

## Parallelizable Workstreams

### Workstream A

Schema and identity

### Workstream B

Package refactor and tests

### Workstream C

Merge engine and review reports

### Workstream D

Operator UX and runbook improvements

Workstream A should lead. B and C can proceed after A stabilizes core assumptions.
