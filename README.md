# Project Ledger

Project Ledger is a **project-reality compiler and control substrate** for a distributed project estate.

It ingests project-like observations from directories, git repos, Obsidian vaults, mirrors, backups, and inventory/policy-backed sources, preserves provenance and uncertainty, and is being evolved into the shared project-identity/current-state layer for agents and operator tooling.

The optimization target is simple: **a cold agent should be able to understand what project the user means, where trustworthy manifestations exist, how fresh the evidence is, what is uncertain, and what capability is actually available without replaying repo/filesystem archaeology.**

## Start here

- `CURRENT.md` — canonical executable frontier and next bounded work
- `SYSTEM.md` — canonical system model and invariants
- `AGENT_PROTOCOL.md` — agent orientation/action/handoff protocol
- `SCHEMA.md` — compatibility schema and typed target contracts
- `docs/WAVE1-AGENT-SUBSTRATE.md` — exact executable Wave 1 contract
- `ARCHITECTURE.md` — implementation/dataflow architecture
- `ROADMAP.md` / `BACKLOG.md` — gated evolution and executable work
- `RUNBOOK.md` — operations/recovery
- `docs/decisions/` — durable architectural decisions

## Current executable boundary

Waves 0–5 and the Reality Provider seam (`machina`, `homelab`, `gbrain`) are implemented on `main` while preserving the frozen v0 compatibility scanner contract.

### Preferred refresh

```bash
python -m ledger refresh
```

That runs the compatibility scanner and then compiles the agent-facing state plane.

Compatibility outputs remain:

```text
output/projects.csv
output/projects.json
output/projects.md
docs/ledgers/projects-ledger.md
```

Generated agent projections are:

```text
state/system-manifest.json
state/source-checkpoints.json
state/observations.json
state/canonical-projects.json
state/canonical-projects.md
state/identity-evidence.json
state/review-queue.json
state/changes.json
state/runs/<run_id>.json
state/projects/<canonical_project_id>.json
```

`state/` is intentionally gitignored: it is rebuildable compiled state, not canon.

### Agent-facing commands now implemented

```bash
python -m ledger validate
python -m ledger refresh [--full-scan]
python -m ledger refresh --no-markdown-mirror [--full-scan]
python -m ledger compile
python -m ledger orient
python -m ledger sources
python -m ledger resolve <referent>
python -m ledger show <project>
python -m ledger locate <project>
python -m ledger explain <referent>
python -m ledger review [--approve-candidates] [--decision-id ID]
python -m ledger changes [--since RUN_ID] [--project REFERENT] [--entity-kind KIND] [--change-type CODE]
python -m ledger sync-gbrain [--brain-dir /home/prime/brain] [--project REFERENT] [--ingest] [--dry-run] [--json]
```

Use `--json` on the agent-facing commands when machine-readable output is preferred.

`orient` is the cheapest current entrypoint after a compile. It reports run/as-of information, source and observation counts, estate health, and the capability boundary so agents do not mistake planned features for implemented ones.

## What Wave 1 now makes explicit

The first typed substrate includes:

- compiler/schema versions;
- explicit stable `source_id` values in the production config;
- source classes (`live`, `mirror`, `backup`, `inventory`);
- current source health and artifact availability;
- deterministic current source probe fingerprints;
- compatibility `snapshot_id` values scoped by source + compatibility scan time;
- stable manifestation-oriented `observation_id` values;
- typed observation wrappers around the existing flat compatibility records;
- explicit capability states and reason codes for unavailable future layers;
- stable validation error codes;
- explicit `known` / `unknown` / `unavailable` / `stale` / `absent` / `conflicted` / `not_applicable` vocabulary for the new contracts;
- failure containment: a temporarily unavailable source degrades the compiled manifest instead of erasing the rest of the estate from orientation.

The legacy flat record is still a compatibility observation, **not a canonical project entity**.

## Current capability boundary

Implemented now:

```text
registered sources
 -> current source probes + incremental source/candidate extraction checkpoints (P5.1) + compatibility source/run snapshots
 -> compatibility observations
 -> typed observations
 -> exact-remote identity evidence + explicit identity decisions + frontier zero-shot triage
 -> canonical projects + identity review + GBrain cross-links
 -> bounded project capsules + preferred-location resolution + human canonical view
 -> semantic run history + semantic change feed (state/changes.json)
 -> system manifest / orient / sources / resolve / show / locate / explain / review / changes
```

Automatic identity is intentionally conservative:

- exact normalized repository remote is the only automatic merge authority;
- compatibility `project_key` values and names are referents, not automatic merge authority;
- explicit committed decisions can merge, split, reject a match, assign a canonical key, supersede, or add an alias;
- ambiguous exact referents remain explicit.

Owned externally in the fleet:

```text
work-unit / attempt / session receipts (owned by machina)
worktree leases & scheduled provider execution (owned by homelab)
semantic / entity search & cross-session memory (owned by gbrain)
```

Canonical project count is now distinct from observation count and is reported in the manifest.

## Source, snapshot, and observation identity

Every committed operator root now has an explicit `source_id`. This identity is independent of `roots[]` ordering and should survive normal path/config refactors.

For additional sources, prefer an intentional stable `source_id` rather than relying on the deterministic compatibility fallback.

Compatibility scanner v0 provides one overall `generated_at`, not a native per-source snapshot record. Wave 1 therefore uses:

```text
source_id + compatibility generated_at -> compat_snapshot_id
```

Separately, the manifest records a **current source probe** with health/access metadata and a `probe_fingerprint`. The probe is not rewritten into historical snapshot identity. This lets an older observation remain tied to the run that produced it even when its source is unavailable now.

An observation is a manifestation inside a source and is keyed primarily by source + observed location, not by a conceptual project key.

The first canonical identity slice now compiles those observations into conceptual projects using only exact normalized repository remotes plus explicit durable decisions. This avoids treating names or compatibility keys as stronger evidence than they are.

See `docs/WAVE1-AGENT-SUBSTRATE.md` for exact semantics and limitations.

## Degraded-source semantics

`python -m ledger validate` is strict: malformed config or missing required inventory/policy artifacts fail validation.

`python -m ledger compile` is orientation-preserving: structurally valid but temporarily unavailable sources are represented as unavailable/degraded in `system-manifest.json` so one broken source does not prevent an agent from understanding the rest of the estate.

A full `ledger refresh` still depends on the compatibility scanner's ability to read its required ingestion artifacts.

## Discovery/compatibility engine

The existing `build_ledger.py` remains the compatibility scanner during migration. It supports:

- `children`
- `git_repos`
- `self`
- `inventory_policy`

and continues to provide filesystem/git/Obsidian/README extraction, project-local sidecar overlay, inventory/policy promotion, Markdown/CSV/JSON exports, and safe handling of missing ordinary filesystem roots.

The new `ledger/` package compiles around this stable output rather than rewriting the monolith before the contracts are proven.

## Sidecars

`.project-ledger.json` remains a thin declaration/current-session compatibility source. It is not canonical truth and should not become a task manager.

Target architecture will turn declarations into typed claims and expose conflicting copies rather than using implicit last-write-wins behavior.

See `templates/project-ledger.sidecar.example.json` and `prompts/session_end_prompt.md`.

## System boundary across repos

Project Ledger owns **project topology and compiled project reality**.

- **AGENT05** owns the reusable execution/control grammar: Work Objects, Assertions, Receipts, evidence, control packets, and disposable-agent continuity.
- **homelab** owns deployment/orchestration against the real machine/service estate and its existing WorkSpec execution plane.
- **Project Ledger** owns source/project topology, provenance, identity/current-state compilation, and the compact reality surfaces those systems can consume.

Do not duplicate Project Ledger's schema/identity engine into AGENT05 or homelab. Integrate through explicit stable outputs once the relevant contract is implemented.

## Tests & verification

```bash
ruff check .
python -m ledger validate
python -m unittest discover -s tests
```

Verification runs locally on the host per fleet proportional-verification policy (`ops/merge-policy.json` requires `GitGuardian Security Checks` on PR heads; `.github/workflows/ci.yml` is `workflow_dispatch:` only and runs `ruff check .`, `py_compile`, and `unittest`).

## Current frontier

Waves 0–5, live `ai-server` provider verification, frontier weak-overlap triage, GBrain reality-block projection, and executable Draft 2020-12 JSON Schemas (`schemas/*.schema.json`) are all landed on `main`.

See `CURRENT.md` first, then `ROADMAP.md` / `BACKLOG.md` (including the Cross-Repo / Out-of-Scope Work Queue).
