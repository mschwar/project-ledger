# Roadmap

## Wave 0 — Convergence

Status: in progress September 14, 2026

Goal: restore one authoritative product line before further expansion.

Delivered / landing:

- reconcile the long-lived evolved feature branch with stale `main`
- review rescue branches as snapshots rather than competing implementation lines
- resolve outstanding PR review findings that affect operator correctness
- update doctrine/PRD/backlog to actual implemented behavior
- add CI for the unit test suite
- merge the evolved implementation to `main`

Remaining operator follow-up after merge:

- run `python build_ledger.py` on the real homelab surface to refresh generated outputs from current roots
- inspect the refreshed Markdown ledger for gaps/duplicates
- update the repo sidecar with the post-refresh state if needed
- retire obsolete rescue/feature branches once their recovery value is no longer needed

Exit criteria:

- `main` is authoritative
- CI passes on the merged implementation
- docs describe the actual discovery/ingestion behavior
- no known review finding blocks routine operation

## Wave 1 — Schema and identity hardening

Goal: formalize the data contracts that later canonicalization depends on.

Deliverables:

- schema versioning
- explicit observation-level schema
- explicit canonical-project schema
- identity evidence/alias model
- review/confidence states
- sidecar validation and compatibility rules
- config validation cleanup beyond current inventory-policy validation

Exit criteria:

- observation and canonical concepts are represented separately in documentation/tests
- breaking schema changes have an explicit version/migration policy
- identity precedence and uncertainty are testable contracts

## Wave 2 — Maintainable core + canonical merge

Goal: turn the monolithic scanner into a maintainable engine and merge observations reproducibly.

Deliverables:

- refactor `build_ledger.py` into package modules without changing observable scan behavior
- fixture-based test coverage for major discovery/extractor branches
- merge command for multiple/source observations
- canonical project IDs
- normalized remote and path aliases
- duplicate/ambiguous match groups
- manual override table
- canonical master artifact while retaining source observations

Exit criteria:

- no major feature lives only in a monolithic script path
- two source snapshots can be merged reproducibly
- uncertain identity matches are surfaced instead of silently collapsed

## Wave 3 — Historical/current-state layer

Goal: know change over time rather than only latest state.

Deliverables:

- run/snapshot metadata
- `inventory_run_id` / `observed_at` semantics
- durable comparison between runs
- lifecycle/current-state normalization
- freshness semantics for active/stale/archived observations

Exit criteria:

- the system can explain what changed between two runs
- current-state reports operate on canonical projects rather than raw duplicates

## Wave 4 — Review and portfolio analytics

Goal: make the ledger operationally useful day to day.

Deliverables:

- new/missing project reports
- duplicate/ambiguity review queue
- missing-sidecar report
- stale-active/no-next-step report
- grouped summaries by root/type/tag/status
- scan health and coverage diagnostics

Exit criteria:

- the owner can review meaningful change and uncertainty without manual CSV diffing
- analytics distinguish observations from canonical projects

## Wave 5 — Agent/control-plane integration

Goal: expose trustworthy project reality to downstream systems.

Deliverables:

- stable machine-readable query/export contract
- project resolution by canonical ID/key/alias
- agent resume-context packet
- session-end automation hooks
- clean handoff of work candidates to task/execution systems without making Project Ledger the task manager

Exit criteria:

- an agent on another node can resolve the same project and its freshest known observation/current state
- downstream control-plane tools do not need to reimplement project identity

## Wave 6 — Optional structured storage and operator UI

Goal: reduce maintenance friction after the contracts stabilize.

Possible deliverables:

- SQLite export/storage
- query CLI
- lightweight local/Mission-Control UI
- scheduled scans

Exit criteria:

- routine use no longer requires manual artifact inspection for common questions

## Parallelizable Programmes

### Programme A — Schema and identity

Leads the sequence. Other work should not outrun its contracts.

### Programme B — Core refactor and tests

Can proceed once Wave 1 contracts are stable enough to preserve behavior.

### Programme C — Canonical merge and review

Depends on explicit observation/canonical separation.

### Programme D — Federated ingestion

Continue adding sources only when they materially improve coverage; avoid multiplying adapters faster than identity resolution can absorb them.

### Programme E — Analytics and operator UX

Build on canonical identity/history, not directly on raw observation counts.

### Programme F — Agent/control-plane integration

Consume Project Ledger as the project-reality layer; keep task lifecycle elsewhere.
