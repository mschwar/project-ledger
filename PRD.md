# PRD

## Product

Project Ledger

## Status

Prototype exists as of March 8, 2026.

## Problem

The owner has projects, repos, idea folders, and Obsidian vaults spread across multiple computers and storage roots. They need one reliable ledger that answers:

- what exists
- where it lives
- whether it is git-backed
- whether it is an Obsidian vault
- when it was last touched
- where its README lives
- what its canonical repo or URL is
- what happened in the last working session
- what the next concrete action is

The current reality is fragmented. Some projects are local-only. Some are synced. Some are nested in imported machine backups. Some are repos without useful README metadata. Some are ideas rather than codebases.

## Users

### Primary user

The owner who needs a complete, durable inventory of work across machines.

### Secondary users

- agents operating on the owner's projects
- future maintainers of the ledger tool
- reporting and cleanup workflows that depend on project metadata

## Jobs To Be Done

1. Ingest candidate project roots from one or more configured locations.
2. Classify each root as directory, git repo, Obsidian vault, or mixed.
3. Generate stable enough identifiers that the same project can be matched across machines.
4. Overlay machine-inferred metadata with manual sidecar metadata.
5. Export artifacts that are useful in spreadsheets, scripts, and Markdown reviews.
6. Support session-end updates so metadata stays current after actual work.
7. Eventually merge per-machine ledgers into one canonical master ledger.

## Product Goals

### G1. Reliable discovery

Identify project-like roots from configured paths with good recall and acceptable precision.

### G2. Stable identity

Represent the same project consistently across runs and across machines.

### G3. Mixed metadata model

Combine inferred metadata with operator-maintained metadata without making the manual workflow painful.

### G4. Operator clarity

Provide artifacts that are easy to review and fix by hand.

### G5. Agent readiness

Make it easy for agents to update the ledger at the end of a session and easy for future agents to continue building the tool.

## Non-Goals

For the current phase, the product does not need to:

- become a cloud service
- provide real-time file watching
- infer true remote push timestamps from hosting APIs
- fully replace manual curation
- solve semantic deduplication of unrelated projects with similar names
- scan every file on disk without scoped configuration

## Current Scope

The current prototype supports:

- config-driven root discovery
- shallow heuristics for project-like directories
- git metadata extraction from local repositories
- Obsidian detection via `.obsidian`
- README detection and summary extraction
- sidecar overlay via `.project-ledger.json`
- CSV, JSON, and Markdown outputs

## Functional Requirements

### FR1. Configured root ingestion

The system must accept one or more scan roots from a config file.

Supported discovery modes:

- `children`
- `git_repos`
- `self`

### FR2. Candidate evaluation

The system must score likely projects based on observable signals such as:

- `.git`
- `.obsidian`
- README presence
- code file presence
- markdown/document presence
- special project files
- explicit sidecar presence

### FR3. Metadata extraction

For each project entry, the system must attempt to collect:

- name
- path
- source label
- machine name
- storage scope
- git flag
- obsidian flag
- repo name
- remote URL
- README location
- last touch timestamp
- commit metadata
- local remote-ref timestamp
- inferred markdown note counts

### FR4. Sidecar overlay

The system must support a per-project sidecar file that can override or enrich inferred metadata.

Sidecar use cases:

- stable `project_key`
- canonical URL
- project status
- tags
- last session summary
- next step
- manually recorded `last_push_at`

### FR5. Artifact export

The system must export:

- CSV for spreadsheet workflows
- JSON for scripts/agents
- Markdown for review and navigation

### FR6. Session-end workflow

The repo must define a standard prompt/pattern that agents can use to update sidecar metadata before ending a work session.

### FR7. Multi-machine merge

The target product must support merging ledgers from multiple machines into a canonical dataset while retaining machine-specific observations.

This is not fully implemented yet and is a required next-phase deliverable.

### FR8. Change review

The target product should produce reports showing:

- newly discovered projects
- missing or dropped projects
- changed identity candidates
- missing sidecars for important projects
- low-confidence entries needing review

Not yet implemented.

## Non-Functional Requirements

### NFR1. Determinism

Repeated runs against unchanged input should produce stable artifacts.

### NFR2. Safety

Scanning must be read-only with respect to target projects.

### NFR3. Minimal setup

The tool should work with Python 3.12+ and standard library only unless a clear need justifies dependencies.

### NFR4. Explainability

The system should expose why a directory was included via fields like `include_reason`.

### NFR5. Maintainability

The codebase should be refactored from monolithic script form into clear modules without changing observable behavior unintentionally.

## Success Metrics

### Initial success

- owner can generate a useful ledger for major known roots
- active projects can be enriched with sidecars
- downstream agents can follow the repo docs without additional human explanation

### Later success

- per-machine ledgers merge with low manual conflict resolution
- low-confidence review queue is small and actionable
- active project metadata stays current because session-end updates are routine

## Major Risks

- project identity across machines is harder than simple path hashing
- README heuristics are noisy
- local git metadata cannot prove actual push time
- imported machine backups may create duplicates or stale shadows
- owners may not consistently maintain sidecars unless the workflow stays light

## Release Milestones

See `ROADMAP.md` for milestone breakdown and `BACKLOG.md` for concrete tasks.
