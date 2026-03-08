# Architecture

## Current Architecture

The current implementation is a single script:

- `build_ledger.py`

Its pipeline is:

1. load config
2. discover candidate roots
3. score/include candidates
4. extract filesystem and git metadata
5. merge sidecar metadata
6. emit CSV, JSON, and Markdown artifacts

This is correct enough for a prototype but too monolithic for long-term extension.

## Target Architecture

Refactor toward a small package with explicit boundaries.

### Proposed layout

```text
project-ledger/
  ledger/
    __init__.py
    cli.py
    config.py
    models.py
    discovery.py
    identity.py
    sidecar.py
    merge.py
    reporters.py
    exporters/
      csv_exporter.py
      json_exporter.py
      markdown_exporter.py
    extractors/
      filesystem.py
      git.py
      obsidian.py
  tests/
  build_ledger.py
```

`build_ledger.py` should become a thin entrypoint over package code.

## Core Concepts

### Scan root

A configured filesystem path plus a discovery mode and exclusions.

### Candidate project

A directory that might represent a meaningful project, repo, vault, or idea container.

### Observed record

Metadata produced from one machine's scan of one project root.

### Canonical project

A merged identity representing the same project across one or more observed records.

### Sidecar

A human-maintained metadata overlay stored inside the project root.

## Data Flow

```text
config
  -> discovery
  -> candidate scoring
  -> metadata extractors
  -> sidecar overlay
  -> identity resolution
  -> outputs
```

Future merge mode:

```text
machine ledgers
  -> identity matching
  -> conflict resolution
  -> canonical master ledger
  -> review report
```

## Identity Strategy

The prototype currently uses:

- sidecar `project_key` when available
- normalized remote URL when available
- fallback slug/name-derived key otherwise

This is not sufficient for long-term cross-machine matching by itself.

### Target identity model

Use layered identity evidence:

1. explicit sidecar `project_key`
2. normalized remote URL
3. repo root name plus README hash plus title similarity
4. path aliases and machine-specific aliases
5. manual override table for ambiguous cases

The system should distinguish:

- stable canonical identity
- machine-local observation identity
- candidate duplicate relationships

## Schema Ownership

`SCHEMA.md` is the source of truth for field definitions.

If code and schema diverge, the agent making the change must reconcile them in the same task.

## Exports

### CSV

Human-friendly and spreadsheet-friendly.

### JSON

Canonical machine-readable artifact for scripts and merge flows.

### Markdown

Quick review table with direct links to paths and READMEs.

### Future exports

- SQLite
- change reports
- issue queue / review queue files

## Testing Strategy

### Current

Small `unittest` coverage around candidate detection, links, and git/sidecar extraction.

### Required next step

Introduce fixture-based tests for:

- nested git repos
- obsidian-only vaults
- low-signal docs-only projects
- malformed sidecars
- multiple root configurations
- merge behavior once implemented

## Operational Constraints

- target roots may be large
- some directories may be inaccessible
- some roots may be imported backups rather than live projects
- git remote information may be missing or stale
- Windows path behavior must remain a first-class concern

## Design Principles

1. Read-only scanning
2. Deterministic outputs
3. Human-overridable identity
4. Small, explicit modules
5. Clear separation between observed machine data and canonical merged data
