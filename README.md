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

Wave 1 now has a real first vertical slice while preserving the existing scanner contract.

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
state/observations.json
state/canonical-projects.json
state/review-queue.json
```

`state/` is intentionally gitignored: it is rebuildable compiled state, not canon.

### Agent-facing commands now implemented

```bash
python -m ledger validate
python -m ledger refresh
python -m ledger compile
python -m ledger orient
python -m ledger sources
python -m ledger resolve <referent>
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
 -> current source probes + compatibility source/run snapshots
 -> compatibility observations
 -> typed observations
 -> exact-remote identity evidence + explicit identity decisions
 -> canonical projects + identity review
 -> system manifest / orient / exact resolve
```

Automatic identity is intentionally conservative:

- exact normalized repository remote is the only automatic merge authority;
- compatibility `project_key` values and names are referents, not automatic merge authority;
- explicit committed decisions can merge, split, reject a match, or add an alias;
- ambiguous exact referents remain explicit.

Still unavailable:

```text
project capsules / show / locate / explain
 -> structured session receipts / current-state resolver
 -> Project Ledger semantic change feed
 -> incremental compilation
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

## Tests

```bash
python -m unittest discover -s tests
```

CI compiles both the legacy scanner and the `ledger` package and runs the complete unittest suite on pull requests.

## Next build gate

The identity walking skeleton is implemented. The next software wave is deliberately **not auto-authorized**.

Before expanding location/current-state features, run the live homelab provider proof in `CURRENT.md` / `docs/work/REALITY-TO-IDENTITY.md` and inspect how the conservative identity rules behave against the real estate. Choose the next constraint from that evidence.

Remaining contract hardening—compatibility policy, stronger source freshness, and typed declaration provenance—should advance when it blocks demonstrated identity/location use, not as detached infrastructure work.

See `CURRENT.md` first, then `ROADMAP.md` / `BACKLOG.md`.
