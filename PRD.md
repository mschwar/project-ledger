# PRD

## Product

Project Ledger

## Status

Working prototype in active hardening. The original March 2026 scanner has expanded into a multi-source project-observation registry. Canonical cross-source project merge remains the next major product milestone.

## Problem

The owner has projects, repos, idea folders, and Obsidian vaults spread across multiple computers and storage roots. The same conceptual project may appear as a live repo, machine-local copy, mirrored folder, backup, or cloud/inventory-backed observation.

The system needs to answer two related but distinct questions:

1. What project-like observations exist, and where did each observation come from?
2. Which observations represent the same durable project, and what is the project's current state?

The current implementation answers the first question well enough for routine use and carries partial identity/current-state metadata for the second. It does not yet provide a canonical merged project model.

## Users

### Primary user

The owner who needs a complete, durable inventory and current-state index of work across machines and storage surfaces.

### Secondary users

- agents operating on the owner's projects
- future maintainers of the ledger tool
- reporting, cleanup, task-extraction, and control-plane workflows that depend on project metadata

## Jobs To Be Done

1. Ingest candidate project roots from one or more configured locations or durable inventories.
2. Preserve source, machine, storage, and policy provenance for every observation.
3. Classify each observation as directory, git repo, Obsidian vault, or mixed.
4. Generate stable identity evidence that can match the same project across machines/sources.
5. Overlay inferred metadata with explicit project-local sidecar metadata.
6. Export artifacts useful to spreadsheets, scripts, agents, and Markdown review.
7. Maintain a thin project current-state pointer through session metadata.
8. Merge observations into canonical projects without hiding ambiguous matches.
9. Produce review/change reports so operators can see uncertainty and change rather than manually diffing snapshots.

## Product Goals

### G1. Reliable discovery

Identify project-like observations from configured filesystems and inventory/policy sources with good recall and acceptable precision.

### G2. Stable identity

Represent the same conceptual project consistently across runs, paths, devices, and storage surfaces.

### G3. Provenance preservation

Never flatten away where an observation came from. Canonicalization must retain machine/source observations.

### G4. Mixed metadata model

Combine inferred metadata with operator-maintained metadata without making the manual workflow painful.

### G5. Operator clarity

Provide artifacts and review queues that make uncertainty, duplicates, gaps, and stale state explicit.

### G6. Agent readiness

Make project identity/current state easy for agents to update and consume without turning the ledger into a competing general-purpose task manager.

## Non-Goals

For the current phase, the product does not need to:

- become a cloud service
- provide real-time file watching
- replace a general task/project-management system
- infer true remote push timestamps from local git alone
- fully eliminate manual curation
- silently solve ambiguous semantic deduplication
- scan every file on disk without scoped configuration/policy
- add a heavyweight UI before the canonical data model stabilizes

## Current Scope

The working branch supports:

- config-driven root discovery
- discovery modes: `children`, `git_repos`, `self`, `inventory_policy`
- shallow project-likelihood heuristics
- git metadata extraction from local repositories
- Obsidian detection via `.obsidian`
- README detection and summary extraction
- sidecar overlay via `.project-ledger.json`
- inventory/policy ingestion from durable JSONL inventory plus root policy artifacts
- source/machine/storage provenance
- graceful handling of missing/unreadable ordinary filesystem roots
- validation of required `inventory_policy` artifacts
- CSV, JSON, Markdown outputs
- committed canonical human-facing Markdown mirror
- unit tests and CI

The committed operator config currently covers `/central` project/repo/service roots, selected Mac mirrors, Matty-PC inventory data, Google Drive inventory/policy data, and backup roots. These roots are environment-specific; portability comes from the config contract, not from assuming every machine has the same filesystem layout.

## Functional Requirements

### FR1. Configured root ingestion

The system must accept one or more scan roots from a config file.

Supported discovery modes:

- `children`
- `git_repos`
- `self`
- `inventory_policy`

`inventory_policy` requires both `inventory_jsonl` and `policy_path`. Missing required artifacts must fail with an operator-readable validation error rather than a raw internal exception.

### FR2. Candidate evaluation

The system must score likely projects based on observable signals such as:

- `.git`
- `.obsidian`
- README presence
- code file presence
- markdown/document presence
- special project files
- explicit sidecar presence
- inventory/policy promotion evidence

### FR3. Metadata extraction

For each observation, the system must attempt to collect:

- name
- path or remote/inventory path
- source label
- machine name
- storage scope
- git flag
- obsidian flag
- repo name
- remote/canonical URL
- README location
- last touch timestamp
- commit metadata
- local remote-ref timestamp
- inferred markdown/note counts
- explainable inclusion reason

### FR4. Sidecar overlay

The system must support a per-project sidecar that can override or enrich inferred metadata.

Sidecar use cases include:

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
- Markdown for review/navigation
- a committed human-facing Markdown mirror for repo-native inspection

### FR6. Session-end workflow

The repo must define a standard pattern that agents can use to update sidecar metadata before ending a work session.

This current-state layer should remain intentionally thin: factual status/session summary/next-step metadata, not a full task queue.

### FR7. Multi-source canonical merge

The target product must support merging observations into canonical projects while retaining the underlying observations and provenance.

This is not fully implemented yet and is the central next-phase deliverable.

### FR8. Change and review reporting

The target product should produce reports showing:

- newly discovered observations/projects
- missing/dropped observations
- changed identity candidates
- duplicate/ambiguous identity groups
- missing sidecars for important projects
- stale active projects
- low-confidence entries requiring review
- scan coverage/gaps

Only scan coverage/gap reporting is currently implemented.

## Non-Functional Requirements

### NFR1. Determinism

Repeated runs against unchanged input should produce stable project identity/output content except for explicit run timestamps.

### NFR2. Safety

Scanning must be read-only with respect to target projects.

### NFR3. Minimal setup

The tool should work with Python 3.12+ and standard library only unless a clear need justifies dependencies.

### NFR4. Explainability

The system should expose why a directory/inventory candidate was included.

### NFR5. Maintainability

The codebase should be refactored from monolithic script form into explicit modules without unintentionally changing observable behavior.

### NFR6. Authority

`main` must represent the authoritative product state. Long-lived branches must not become a shadow production line.

## Success Metrics

### Current success

- major configured roots can produce a useful multi-source observation ledger
- policy-backed Google Drive/Matty-PC candidates can be promoted without recursive cloud crawling
- active projects can be enriched with sidecars
- downstream agents can follow repo docs without additional human explanation
- missing/unreadable ordinary roots are reported rather than crashing the run

### Next success

- observation records and canonical project records are explicitly separated
- two or more machine/source observations can be merged reproducibly
- ambiguous matches are surfaced instead of silently collapsed
- project counts represent canonical projects rather than raw observations when requested
- change/review queues are small and actionable

## Major Risks

- project identity across machines is harder than simple path/name hashing
- remote URLs can be absent, stale, or shared by divergent copies
- README/name heuristics are noisy
- imported backups create stale shadow observations
- owners/agents may not consistently maintain sidecars unless the workflow stays light
- adding more ingestion sources before canonical merge can multiply duplicate noise
- UI/analytics built before canonical identity can give false confidence in counts

## Release Direction

See `ROADMAP.md` for waves and `BACKLOG.md` for concrete next work. The immediate sequence is schema/identity hardening, package/config cleanup, observation/canonical split, canonical merge, then review/change analytics.
