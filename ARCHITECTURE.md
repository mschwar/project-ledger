# Architecture

`SYSTEM.md` defines the conceptual model. This document maps that model onto concrete software boundaries and artifacts.

## Current implementation

The runtime is a deliberate layered migration architecture.

`build_ledger.py` remains the compatibility scanner:

```text
config
 -> discover candidates
 -> score/include
 -> extract filesystem/git/inventory metadata
 -> overlay sidecar
 -> emit CSV/JSON/Markdown compatibility observations
```

The `ledger/` package now adds:

```text
compatibility observations
 -> stable source/snapshot/observation identity
 -> source probes/health
 -> typed observations
 -> exact normalized remote identity + durable identity decisions
 -> canonical projects + review queue
 -> system manifest
 -> ledger orient / ledger sources / ledger resolve
```

The identity layer is intentionally conservative. Exact normalized repository remote is the only automatic merge rule. Names/project-key hints are referents, not identity authority. Explicit committed decisions can merge/split/reject/alias.

Still absent: project capsules, preferred-location/current-state resolution, receipts, semantic change feed, and incremental compilation.

The next architecture constraint must be selected after the live provider proof rather than inferred from roadmap order.

## Target architecture

The target system is an incremental compiler with a query/materialization layer:

```text
SOURCE ADAPTERS
  -> SNAPSHOTS
  -> NORMALIZED OBSERVATIONS + CLAIMS
  -> IDENTITY RESOLUTION
  -> CANONICAL PROJECTS
  -> CURRENT-STATE RESOLUTION
  -> DERIVED SIGNALS / REVIEW
  -> MATERIALIZED AGENT VIEWS
  -> QUERY / CONTROL SURFACE

                         ^
                         |
              decisions / receipts
                         |
                    agent work
```

Canonical views are rebuilt from evidence/declarations/decisions rather than directly edited.

## Module boundaries

Suggested package structure:

```text
project-ledger/
  ledger/
    cli.py
    config.py
    ids.py
    models.py
    validation.py

    sources/
      base.py
      filesystem.py
      git.py
      inventory_policy.py

    normalize/
      observations.py
      claims.py

    identity/
      evidence.py
      resolver.py
      decisions.py
      aliases.py

    state/
      declarations.py
      receipts.py
      resolver.py

    compile/
      compiler.py
      incremental.py
      changes.py
      review.py

    views/
      manifest.py
      capsules.py
      markdown.py
      csv.py
      json.py

    query/
      resolve.py
      locate.py
      explain.py
      orient.py

  state/                  # generated/materialized views; source-of-truth status varies by artifact
  registry/               # explicit durable decisions/declarations owned by Project Ledger
  tests/
  docs/decisions/
  build_ledger.py         # compatibility entrypoint while migration proceeds
```

Module names may change, but the responsibility boundaries should not collapse back together.

## Layer contracts

### 1. Source adapter

A source adapter senses one class of source and emits a source snapshot plus source-native facts. It must not decide canonical identity.

Adapter contract should include:

- stable `source_id`;
- adapter/type/version;
- source locator/config digest;
- `observed_at` / `source_as_of`;
- source health/result state;
- source-native object IDs where available;
- warnings/errors scoped to the source;
- deterministic fingerprint allowing unchanged work to be skipped.

Adapters are read-only toward source projects during sensing.

### 2. Normalization

Normalization converts source-native facts into typed observations/claims without losing source pointers.

It may normalize URLs, paths, timestamps, booleans, tags, and git metadata. It must not hide conflicts or make irreversible identity decisions.

### 3. Identity resolution

Identity consumes observation evidence and durable decisions and produces relationships to canonical projects.

Resolution ladder:

1. explicit immutable canonical ID/declaration when trusted;
2. explicit identity decisions/aliases;
3. normalized remote/source-native strong keys;
4. deterministic compound evidence;
5. scored heuristic/semantic evidence;
6. unresolved review item.

Low-confidence matches never silently merge.

Identity outputs should carry explanation/evidence references, not only a project ID.

### 4. Canonical project compilation

A canonical project is a compiled durable entity containing links to all observations and selected resolved fields. It does not copy full source content.

The canonicalizer applies field-specific resolution policies. It records selected claim provenance and unresolved conflicts.

### 5. Current-state resolution

Current state resolves mutable project-level continuity information from declarations/receipts/observations using freshness and authority policy.

Examples:

- lifecycle state;
- last trusted session;
- continuation/next-step pointer;
- preferred working observation/location;
- freshness state;
- known blockers/review references.

This layer should not ingest arbitrary task trees.

### 6. Derived signals/review

Derived calculations include:

- source freshness/health;
- new/missing observations;
- canonical-project changes;
- identity conflicts/probable duplicates;
- stale projects;
- no-current-state/continuation signals;
- source divergence;
- preferred-location ranking.

Uncertainty becomes explicit review items rather than prose warnings scattered across outputs.

### 7. Materialized agent views

The normal agent read path should use generated compact views:

```text
state/system-manifest.json
state/projects/<canonical_project_id>.json
state/review-queue.json
state/changes.json
```

These are semantic caches. They are replaceable/rebuildable and must contain run/schema/freshness metadata.

### 8. Query/control surface

The CLI/query layer should operate on system concepts rather than file internals:

```text
ledger orient
ledger resolve <referent>
ledger show <project>
ledger locate <project>
ledger explain <thing>
ledger changes
ledger review
ledger sources
ledger compile
ledger validate
ledger record-session
```

JSON is the stable agent contract; Markdown/table rendering is a view.

## Two paths: read and write

### Agent read path

Optimize aggressively for cheap orientation:

```text
system manifest
 -> resolve referent
 -> project capsule
 -> targeted evidence only if needed
```

A normal agent should not need the full observation store or all doctrine documents.

### Agent write path

Agents write durable inputs, not compiled outputs:

```text
project work
 -> git/task/source changes
 -> session receipt / declaration / explicit review decision
 -> incremental compile
 -> affected canonical project + views refreshed
```

This produces an auditable loop and avoids hidden canonical mutations.

## Incremental compilation

Full rescans may remain useful as reconciliation, but routine operation should be incremental.

Each source should expose a fingerprint/checkpoint. If unchanged, reuse the prior normalized snapshot. Changed observations should invalidate only the affected identity groups/canonical projects and derived views.

Conceptually:

```text
source delta
 -> affected observation IDs
 -> affected identity components
 -> affected canonical IDs
 -> affected signals/views
```

This is the primary compute-saving mechanism.

## Evidence and decision storage

Durable explicit resolutions should live in a Project Ledger-owned registry separate from generated views. Candidate families:

```text
registry/sources.*
registry/identity-decisions.*
registry/aliases.*
registry/project-declarations.*
registry/review-resolutions.*
```

Exact storage format should follow schema/version work. The architectural rule is more important than the initial file choice: **decisions are inputs; canonical projects are outputs.**

## Sidecars in the target system

`.project-ledger.json` remains useful because project-local declarations travel with a live project. However, a sidecar is an input claim source, not automatically canonical truth.

The compiler should record:

- which observation exposed the sidecar;
- sidecar schema/version;
- claim provenance/freshness;
- conflicts with other declarations;
- whether identity/current-state fields were selected and why.

Multiple copies of one project may contain divergent sidecars; this must become a visible conflict, not last-write-wins behavior.

## Preferred location resolver

Location ranking should be a dedicated, explainable resolver rather than ad-hoc sorting.

Inputs may include:

- source class: live/mirror/backup/inventory-only;
- accessibility from current node;
- canonical remote match;
- git divergence/branch/head freshness;
- trusted recent-session location;
- operator source preference;
- node capability constraints.

Output: ranked observations with reason codes and confidence.

## Explainability

Every important resolution should support an `explain` path. Internally this means retaining references from a compiled value/relationship to candidate claims, selected claim, resolution policy, and conflicts.

Debugging should be possible without rerunning semantic reasoning.

## Error model

Prefer typed bounded results:

- source unavailable;
- source stale;
- malformed declaration;
- invalid config;
- identity ambiguous;
- identity conflict;
- schema incompatible;
- compiled view stale.

Errors should identify affected entity IDs, evidence, and recovery action. One error should not invalidate unrelated sources/projects.

## Testing architecture

Tests should progress from unit behavior to contract and system fixtures:

1. adapter/source fixtures;
2. normalization/schema validation;
3. deterministic ID stability;
4. identity match/no-match/ambiguity fixtures;
5. field-resolution precedence/freshness;
6. incremental compile invalidation;
7. project capsule/system-manifest golden contracts;
8. review resolution ratchet tests;
9. end-to-end multi-source estate fixture.

Every real failure that required nontrivial reasoning is a candidate regression fixture.

## Migration strategy

Do not rewrite the monolith and canonicalizer simultaneously.

Preferred sequence:

1. freeze/version current observation output as a compatibility contract;
2. introduce typed IDs/models/validation around existing behavior;
3. extract adapters/normalization without semantic changes;
4. add canonical compilation as a new output beside current observation output;
5. add manifest/capsules/review/change views;
6. add query commands over those views;
7. shift normal agent operation to the new read path;
8. retire compatibility paths only after validation.

## Design principles

1. Compiler architecture over mutable canonical records.
2. Progressive disclosure for agent reads.
3. Evidence/decision separation.
4. Field-specific authority and freshness.
5. Immutable durable IDs plus human aliases.
6. Deterministic/incremental work before semantic reasoning.
7. Local failure containment.
8. Explainable preference/resolution.
9. Project topology boundary: do not absorb task or knowledge systems.
10. Every resolved ambiguity should reduce future cost.
