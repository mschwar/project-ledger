# Backlog

## P0 — Convergence completion

### P0.1 Merge evolved implementation to `main`

- land PR review fixes
- land current doctrine/roadmap updates
- land CI
- merge the long-lived feature branch

### P0.2 Refresh from real operator roots

After merge, on the homelab surface:

- pull current `main`
- run unit tests
- run `python build_ledger.py`
- inspect `docs/ledgers/projects-ledger.md`
- confirm scan gaps/coverage
- record any duplicate/identity anomalies as Wave 1/2 work

### P0.3 Branch hygiene

- preserve rescue branches only as long as they have recovery value
- retire the merged feature branch after convergence
- do not use long-lived branches as the operational source of truth

## P1 — Schema and identity

### P1.1 Introduce schema versioning

- define schema version field for JSON output
- document compatibility expectations
- decide sidecar migration behavior

### P1.2 Separate observation and canonical schemas

Observation-level fields should include:

- source/root provenance
- machine/host identity
- observed path/location
- scan/run metadata
- access/scan warnings
- extracted git/filesystem/vault facts

Canonical-level fields should include:

- stable canonical project ID
- aliases/identity evidence
- linked machine/source observations
- identity confidence
- review state
- duplicate group
- lifecycle/current-state metadata

### P1.3 Formalize identity matching rules

- explicit sidecar `project_key`
- normalized remote URL
- path/machine aliases
- repo/name/README evidence
- manual overrides for conflicts
- never silently collapse ambiguous matches

### P1.4 Add sidecar validation

- validate expected field names and types
- surface malformed sidecars in review output
- add regression coverage

### P1.5 Complete config validation

Current inventory-policy required fields/artifacts are validated. Expand this into a single explicit config validation layer covering:

- root object/type
- required `path`
- discovery modes
- integer thresholds
- treatment lists and booleans
- useful warnings for missing ordinary live roots versus required metadata artifacts

## P2 — Core refactor and canonical merge

### P2.1 Refactor into package modules

Suggested extraction order:

- config/validation
- discovery
- filesystem/git/inventory extractors
- models/schema
- sidecar overlay
- identity
- merge
- reporters/exporters

Keep `build_ledger.py` as a thin compatibility entrypoint.

### P2.2 Expand stable fixtures

- nested git repos
- obsidian-only vaults
- low-signal docs-only projects
- malformed sidecars
- missing/unreadable roots
- inventory-policy roots
- duplicate projects observed from multiple sources
- ambiguous identity groups

### P2.3 Implement merge command

- accept multiple/source observation sets
- preserve observations
- resolve high-confidence identity matches
- produce canonical project list
- produce ambiguity/duplicate review output

### P2.4 Add manual identity overrides

- durable alias/override artifact
- explicit merge/split decisions
- regression tests for resolved conflicts

## P3 — History, change, and review

### P3.1 Add run metadata

- `inventory_run_id`
- `observed_at`
- input/source snapshot metadata
- scan warnings/errors

### P3.2 Add change reports

- new projects/observations
- missing observations
- metadata deltas
- stale active projects
- project lifecycle transitions

### P3.3 Add confidence/review queue

- unresolved identity conflicts
- probable duplicates
- low-confidence observations
- missing sidecars for high-value projects
- canonical projects with no current next step

## P4 — Operator analytics

### P4.1 Improve human-facing outputs

- distinguish observation count from canonical project count
- group by root/type/tag/status
- show duplicate groups and freshest observation
- show scan coverage and source freshness

### P4.2 Structured storage/query

Only after the canonical schema is stable:

- optional SQLite export/store
- query CLI

### P4.3 Optional UI

Only after canonical identity/change reporting works:

- local review UI or Mission-Control integration
- search/filter
- click-through to observation/readme/sidecar

## P5 — Agent/control-plane integration

- resolve project by canonical key/alias
- return freshest/current observation candidates
- generate resume-context packet
- accept session-end current-state updates safely
- expose work candidates to the task/execution layer without owning full task lifecycle

## Nice To Have

- import metadata from README frontmatter
- richer Obsidian metadata
- scheduled scans
- host-specific config profiles
- helper commands for source/machine registration
