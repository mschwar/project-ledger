# Wave 1 Agent Substrate — Executable Contract

This document describes the first executable Wave 1 contract currently implemented by the `ledger` package. It is intentionally narrower than the full target model in `SYSTEM.md` and `SCHEMA.md`.

## Purpose

Give a cold agent one cheap, versioned orientation surface over the existing scanner without pretending canonical project identity exists yet.

The compatibility engine remains `build_ledger.py`. The Wave 1 substrate wraps its output with explicit source/snapshot/observation identity, source health, capability boundaries, and a compact system manifest.

## Preferred operator flow

```bash
python -m ledger refresh
python -m ledger orient
python -m ledger sources
```

`refresh` runs the legacy scanner and then compiles generated state.

For separate stages:

```bash
python build_ledger.py
python -m ledger compile
```

Strict validation:

```bash
python -m ledger validate
```

## Generated artifacts

```text
state/system-manifest.json
state/observations.json
```

These are materialized projections and are gitignored. They must be reproducible from source config plus compatibility output.

## Version fields

Current constants:

```text
compiler_version              0.2.0
manifest schema_version       1.1.0
typed observation version     1.0.0
compat flat schema version    0.1.0
```

These values establish an executable version boundary; full compatibility/migration policy remains Tranche 1B work.

## Stable IDs

### source_id

Production roots carry explicit human-readable stable `source_id` values in `ledger_config.json`.

Rules:

- `source_id` is source identity, not source location;
- reordering `roots[]` must not change it;
- moving a source should normally preserve it;
- duplicate explicit/effective IDs are invalid;
- deterministic derived IDs exist only as a compatibility fallback.

### Compatibility snapshot_id

Compatibility scanner v0 emits one overall `generated_at`, but does not retain native per-source snapshot metadata. Wave 1 therefore creates a bounded compatibility snapshot identity:

```text
compat_snapshot_id = hash(source_id, compat_output.generated_at)
```

Every resolved typed observation points to that source/run snapshot. This is deliberately separate from the source's **current health probe** so compiling an older compatibility output while a source is now unavailable does not rewrite history.

Future source adapters may emit stronger native snapshot/checkpoint identities without changing `source_id`.

### observation_id

An observation identifies a manifestation, not a conceptual project.

Current deterministic basis prioritizes:

1. `path_from_root`;
2. observed `path`;
3. canonical/remote locator;
4. project key only as a fallback;
5. display name only as final fallback.

The ID is scoped by `source_id`.

Changing a project-key claim must therefore not rename an otherwise unchanged manifestation.

## Source probe model

Current source classes:

- `live`
- `mirror`
- `backup`
- `inventory`

Each source record in the manifest contains:

- `source_id`;
- `compat_snapshot_id`, `compat_snapshot_as_of`, and compatibility snapshot basis;
- label/class/discovery mode;
- machine/storage hints;
- path and path state;
- current source status and reason;
- `freshness_state`;
- current probe timestamp evidence where available;
- current `probe_fingerprint`;
- required inventory/policy artifact states and hashes where applicable.

`probe_fingerprint` is a cheap current probe/checkpoint hint, **not a cryptographic digest of every live filesystem descendant**. Inventory-policy fingerprints are stronger because they include durable inventory/policy artifact hashes. Wave 5 may use stronger adapter-specific checkpoints for incremental invalidation.

`freshness_state` is currently conservative and normally `unknown`; stronger freshness policy is Tranche 1B.

## Failure containment

There are two intentionally different behaviors.

### `ledger validate`

Strict operator validation. Missing required inventory/policy artifacts are errors.

### `ledger compile`

Orientation-preserving compilation. Structurally valid but unavailable source artifacts are represented as unavailable and degrade the manifest rather than preventing the rest of the estate from being represented.

This implements the system invariant that one degraded source should not poison the whole estate.

A full `ledger refresh` still depends on the underlying compatibility scanner being able to complete its ingestion step.

## Typed observation wrapper

`state/observations.json` preserves every compatibility entry verbatim under `compat_entry` while adding an explicit envelope:

```text
schema_version
observation_id
source_id
snapshot_id
source_resolution
observed_at
project_key      compatibility claim/hint; not canonical identity
location
compat_entry
```

`source_resolution` is `resolved` when `source_label` maps to a registered source and `unresolved` otherwise. Unresolved observations receive deterministic provisional source/snapshot IDs and degrade manifest health rather than being silently attached to a guessed source.

## System manifest

`state/system-manifest.json` currently exposes:

- manifest/compiler versions;
- run ID and compile/input-as-of times;
- config path/digest;
- aggregate health;
- source-unavailable and unresolved-source counts;
- source and observation counts;
- current source probes plus compatibility snapshot linkage;
- capability states;
- artifact pointers;
- explicit limitations.

Canonical project and review counts are `null`, not zero, because those capabilities do not exist yet.

## Capability contract

Implemented capabilities are reported `available`:

- compatibility observations
- typed observations
- source health

The Wave 1 document originally ended before canonical identity. The current compiler now extends this substrate with a conservative R2 identity slice:

- canonical projects — available;
- identity review queue — available;
- exact `ledger resolve` — available;
- project capsules — still unavailable (`WAVE3_NOT_IMPLEMENTED`);
- semantic change feed — still unavailable (`WAVE4_NOT_IMPLEMENTED`).

Automatic identity is limited to exact normalized repository remote plus explicit durable decisions; this document remains the source/snapshot/observation contract, while `SCHEMA.md §1.7` defines the identity extension.

An agent should consume this capability map rather than infer support from design prose.

## Null/state vocabulary

The new contracts reserve explicit semantic states:

```text
known
unknown
unavailable
stale
absent
conflicted
not_applicable
```

This tranche uses them selectively; later schema work should replace compatibility blank-string ambiguity with these semantics field by field.

## Cross-repo boundary

This subsystem stays in `project-ledger`.

- AGENT05 owns reusable execution/control grammar and may later consume the manifest/capsules as REALITY inputs.
- homelab owns real-machine deployment/orchestration and may later schedule refreshes or expose the query interface across nodes.
- neither should duplicate Project Ledger's source/identity schema.

Integration should consume stable outputs rather than copy implementation code or create another project registry.

## Gate B work still open

Before Wave 2 canonical identity begins in earnest:

- executable schema validation for generated artifacts;
- explicit major/minor compatibility/migration rules;
- stronger freshness/as-of contracts where evidence permits;
- adapter-native source snapshot/checkpoint metadata where justified;
- sidecar/declaration validation;
- typed claim/evidence envelopes;
- field-resolution policy interface;
- regression fixtures for real production ambiguities discovered during the live refresh.
