# Backlog

## P0

### P0.1 Refactor into package modules

- extract config loading
- extract discovery
- extract filesystem metadata
- extract git metadata
- extract exporters

Why:

The current single-file implementation is the biggest maintainability bottleneck.

### P0.2 Add config validation

- validate root entries
- validate discovery mode names
- validate integer thresholds
- fail clearly on missing paths and malformed config

### P0.3 Add sidecar validation

- validate expected field names and types
- report malformed sidecars in output
- add tests for malformed JSON and wrong types

### P0.4 Introduce schema versioning

- define schema version field for JSON output
- document compatibility expectations
- decide migration behavior for sidecars

### P0.5 Expand test fixtures

- create stable fixture directories under `tests/fixtures`
- stop relying only on inline scratch data
- add regression coverage for discovery and metadata extraction

## P1

### P1.1 Implement merge command

- accept multiple JSON ledger files
- preserve machine observations
- produce canonical project list
- produce ambiguity report

### P1.2 Add identity matching rules

- explicit `project_key`
- normalized remote URL
- path aliases
- README hash and repo-name heuristics
- manual override table for conflicts

### P1.3 Add change reports

- new projects
- missing projects
- metadata deltas
- stale active projects

### P1.4 Add confidence and review state

- low-confidence candidate detection
- unresolved identity conflicts
- manual review required flags

## P2

### P2.1 Improve operator outputs

- richer Markdown sections
- grouped reports by root/type/tag
- sidecar coverage report

### P2.2 Optional structured storage

- SQLite export
- canonical merged database

### P2.3 Optional UI

- local-only review UI
- search and filter
- click-through to path/README/sidecar

## Nice To Have

- import metadata from README frontmatter
- import metadata from Obsidian vault config or vault notes
- scheduled scans
- host-specific profiles
- push/pull helper docs for machine synchronization
