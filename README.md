# Project Ledger

Project Ledger is evolving into a **project-reality compiler and control substrate** for a distributed project estate.

Today it scans/ingests project-like observations from directories, git repos, Obsidian vaults, mirrors, backups, and inventory/policy-backed storage surfaces. The target system compiles those observations into durable canonical projects, explicit current/freshness state, reviewable uncertainty, and compact agent-facing views.

The goal is simple to state: **an agent should be able to understand what project the user means, where the trustworthy working copies are, what state the project is in, what is uncertain, and what to do next without repeating repo/filesystem archaeology.**

## Start here

- `SYSTEM.md` — canonical conceptual model and invariants
- `AGENT_PROTOCOL.md` — how agents should orient, reason, act, and hand off
- `ARCHITECTURE.md` — implementation/dataflow architecture
- `SCHEMA.md` — current compatibility fields and target typed entity model
- `PRD.md` — product requirements/acceptance scenarios
- `ROADMAP.md` — gated evolution
- `BACKLOG.md` — executable work packages
- `RUNBOOK.md` — current operations/recovery
- `docs/decisions/` — durable architecture decisions

Agents should use progressive disclosure rather than loading all docs by default.

## What exists today

Current `main` provides:

- config-driven source discovery;
- `children`, `git_repos`, `self`, and `inventory_policy` modes;
- filesystem/git/Obsidian/README metadata extraction;
- inventory + root-policy promotion for remote/cloud inventories;
- machine/source/storage provenance;
- project-local `.project-ledger.json` overlay;
- safe handling/reporting of missing ordinary roots;
- validation for required inventory-policy artifacts;
- CSV/JSON/Markdown exports;
- committed human-facing Markdown ledger mirror;
- unit tests + CI.

Current outputs:

```text
output/projects.csv
output/projects.json
output/projects.md
docs/ledgers/projects-ledger.md
```

The current flat record is best understood as an **observation/compatibility record**, not yet a canonical project entity.

## Run today

```powershell
python build_ledger.py
python -m unittest tests.test_build_ledger
```

Optional scanner arguments:

```powershell
python build_ledger.py --config ledger_config.json --output-dir output
```

CI compiles the scanner and runs tests on pushes/pull requests.

## The system it is becoming

The architecture is a linked abstraction tower:

```text
sources
 -> snapshots
 -> observations + claims
 -> identity evidence + decisions
 -> canonical projects
 -> current state
 -> derived changes/review/freshness
 -> compact agent views
 -> agent work + receipts
 -> next incremental compile
```

Three properties matter more than feature count:

### Epistemic clarity

Observed facts, explicit declarations, machine inferences, and reviewed decisions are different things. Stale/unavailable/absent/conflicted are different states. The system should never force an agent to guess which one a value represents.

### Cheap read path

The target normal path is:

```text
ledger orient
 -> ledger resolve <referent>
 -> ledger show <project>
 -> ledger locate/explain only if needed
```

Planned agent-facing materialized views:

```text
state/system-manifest.json
state/projects/<canonical_project_id>.json
state/review-queue.json
state/changes.json
```

These are compiled semantic caches with evidence pointers, not hand-maintained truth.

### Accretion

Identity resolutions, aliases, review decisions, session receipts, and regression fixtures should become durable. A future agent should not have to solve the same ambiguity twice.

## System boundary

Project Ledger owns **project topology and compiled project/current-state reality**.

It does not own:

- full task lifecycle;
- deep semantic/document memory;
- raw source-control history;
- source inventory payloads.

It links to those systems through stable project IDs/pointers.

## Discovery today

`ledger_config.json` defines source roots.

- `children` — direct child directories scored for project signals
- `git_repos` — recursively find git repositories
- `self` — treat the configured root as one observation
- `inventory_policy` — consume durable inventory + policy and promote roots intended for project discovery

The committed operator config is environment-specific and includes `/central` surfaces, selected Mac mirrors, Matty-PC inventory data, Google Drive inventory/policy data, standalone roots, and backups.

Missing/unreadable ordinary roots are reported as gaps. Required inventory/policy metadata must exist and validate.

## Inventory-policy rule

```text
inventory discovers source reality
 -> policy decides project relevance
 -> Project Ledger emits observations
 -> canonical identity compiler decides project membership (target)
```

Policy promotion must not be mistaken for canonical project identity.

## Sidecars today and tomorrow

A live project may contain `.project-ledger.json` with stable key/display/current-session hints.

Today the scanner overlays those fields directly into its flat output. Target architecture treats the sidecar as a **versioned declaration source** attached to an observation. If several copies of one canonical project contain divergent sidecars, the system should expose competing claims/conflict rather than silently using last-write-wins.

See `templates/project-ledger.sidecar.example.json` and `prompts/session_end_prompt.md`.

## Identity direction

Current keys use explicit sidecar key, normalized git remote, source-specific inventory identity, or weaker fallback.

Target identity separates:

- source identity;
- snapshot/run identity;
- observation identity;
- immutable canonical project identity;
- human-readable project key/aliases.

Paths, names, and URLs remain valuable evidence but are not durable identity by themselves.

## Resource-economy direction

Project Ledger should use the cheapest trustworthy mechanism first:

1. deterministic source evidence;
2. normalization/hashes/stable aliases;
3. explicit heuristics with confidence;
4. cheap/local semantic comparison only when needed;
5. frontier reasoning for consequential ambiguity;
6. human review for high-impact unresolved cases.

Later incremental compilation should fingerprint sources, skip unchanged work, and recompile only affected projects/views.

## Current next milestone

Do not prioritize more UI or broad source expansion yet. The immediate build sequence is:

1. version/freeze the current observation contract;
2. introduce source/snapshot/observation IDs, freshness/error/null semantics, and validation;
3. implement canonical identity evidence/decisions/review;
4. materialize the agent read plane (manifest/capsules/query commands);
5. add structured session receipts/current-state resolution;
6. optimize incremental/cost-aware operation;
7. integrate task/knowledge/control-plane systems through canonical project IDs.

See `ROADMAP.md` and `BACKLOG.md` for gates and executable packages.
