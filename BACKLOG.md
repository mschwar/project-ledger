# Backlog

This backlog is ordered by dependency, not novelty. Work should land as bounded packages with tests/evidence and should not outrun the abstraction layer beneath it.

See `SYSTEM.md` for invariants and `ROADMAP.md` for gates.

## P0 — Wave 0 operational closeout

### P0.1 Real-source refresh

On a node with the intended source access:

- pull current `main`;
- run tests;
- run the ledger against current configured roots;
- inspect source gaps/coverage;
- capture representative duplicate/identity anomalies as fixtures/input for Wave 1/2;
- commit an intentional refreshed human-facing snapshot only if it is trustworthy.

### P0.2 Branch/recovery hygiene

- inspect remaining rescue/merged feature branches for unique recovery value;
- archive/delete obsolete refs when safe;
- ensure no operational process still treats them as authority.

---

# P1 — Agent substrate and contracts

## P1.1 Freeze compatibility observation contract

Deliver:

- explicit current output version (compat/v0);
- documented JSON envelope semantics;
- golden fixture for current flat output;
- compatibility test preventing accidental field-meaning drift.

Exit evidence: old scanner consumers can identify/version what they are reading.

## P1.2 Introduce stable source and run identities

Deliver:

- `source_id` contract/registry;
- source class (`live`, `mirror`, `backup`, `inventory-only`, etc.);
- `snapshot_id` / compiler run ID;
- source adapter/version metadata;
- source/config fingerprints;
- freshness/health result model.

Fixtures:

- healthy live source;
- missing/unavailable source;
- partial inventory source;
- unchanged source fingerprint.

## P1.3 Introduce stable observation identity

Deliver:

- `observation_id` strategy;
- source-native-ID preference where available;
- deterministic fallback documented/tested;
- path migration/alias behavior;
- observation identity separate from canonical project identity.

## P1.4 Epistemic claim model

Deliver typed contracts for:

- observed claim;
- declaration;
- inference + confidence;
- evidence references;
- selected/resolved claim references.

Do not implement general semantic matching yet.

## P1.5 Explicit null/freshness/error semantics

Deliver:

- `unknown`, `unavailable`, `stale`, `absent`, `conflicted`, `not_applicable`, `known` convention;
- machine-readable error/review codes;
- migration away from ambiguous empty strings in new schemas;
- tests for source unavailable vs empty/absent.

## P1.6 Full config validation

Deliver one preflight validation layer covering:

- root object shape;
- required paths/fields;
- discovery mode;
- numeric thresholds;
- booleans/lists;
- inventory-policy artifacts;
- duplicate source IDs/labels;
- actionable errors.

## P1.7 Sidecar as versioned declaration source

Deliver:

- sidecar `schema_version` plan/validation;
- accepted field/type validation;
- malformed/unsupported-version review/error behavior;
- explicit rule that sidecar values become declarations rather than unconditional canonical truth.

Do not add `canonical_project_id` to sidecars until the canonical registry can assign/validate it safely.

## P1.8 Minimal system manifest

Deliver first `state/system-manifest.json` containing:

- compiler/schema versions;
- run ID/timestamp;
- config digest;
- source health/freshness;
- observation counts;
- known capability flags;
- pointers to generated outputs;
- explicit `canonicalization_available: false` until Wave 2.

Add a compact human `ledger orient` rendering only if it can consume the same manifest contract.

---

# P2 — Canonical identity compiler

## P2.1 Extract source adapters and normalization

Refactor under frozen compatibility tests:

- config/validation;
- source abstraction;
- filesystem scanner;
- git extraction;
- inventory-policy adapter;
- normalization/models.

Keep `build_ledger.py` as compatibility entrypoint.

## P2.2 Multi-source estate fixture

Build a durable fixture containing:

- one project present as live repo + mirror + backup;
- same-name unrelated project;
- renamed/moved project;
- missing remote;
- divergent sidecars;
- inventory-only observation;
- inaccessible source;
- strong and weak identity evidence.

This fixture should become the primary canonicalization acceptance environment.

## P2.3 Identity evidence model

Implement records/scoring for:

- explicit project key/declaration;
- normalized remote;
- source-native identity;
- aliases/path migrations;
- README/repo compound evidence;
- negative/conflict evidence;
- weak name/semantic similarity clearly marked as weak.

## P2.4 Decision registry

Implement durable inputs for:

- merge;
- split;
- reject-match;
- alias;
- canonical human key assignment;
- supersession.

Every decision carries rationale/evidence and stable ID.

## P2.5 Canonical project IDs and compiler

Deliver:

- immutable `canonical_project_id` assignment;
- observation membership graph;
- project-key aliases;
- resolved fields with claim provenance;
- reproducible compile from observations + decisions.

## P2.6 Identity review queue

Deliver stable review items for:

- ambiguous match;
- probable duplicate;
- conflicting strong identity evidence;
- split suspicion;
- divergent declarations affecting identity.

Each item includes evidence, reason automation stopped, severity, and resolution actions.

## P2.7 Resolution ratchet tests

For each review resolution type:

- resolve once;
- persist decision;
- rerun identical inputs;
- assert review does not recur;
- assert explain path identifies decision.

---

# P3 — Agent read plane

## P3.1 Canonical system manifest

Upgrade manifest to include:

- canonical project count vs observation count;
- review burden;
- material change counts;
- canonical/query artifact pointers;
- source health summary.

## P3.2 Project capsule contract

Define and materialize bounded capsules containing:

- ID/key/aliases;
- purpose/current state placeholder;
- canonical URL/repo;
- ranked observation summaries;
- freshness;
- conflicts/review;
- evidence/task/knowledge pointers.

Set and test a deliberate size/context budget.

## P3.3 Resolve command

`ledger resolve <referent>` supports canonical ID, key/alias, URL, known path, source-native identifier, and fuzzy name only when ambiguity is surfaced.

JSON output first.

## P3.4 Show command

`ledger show <project>` returns project capsule with explicit `as_of` and schema version.

## P3.5 Preferred-location resolver + locate command

Implement explainable ranking from:

- source class;
- accessibility;
- canonical remote match;
- git freshness/divergence;
- recent trusted-session location;
- operator/node policy.

Output ranked observations + reason codes/confidence.

## P3.6 Explain command

Support explanation for:

- why observations merged/separated;
- why a canonical field was selected;
- why a location was preferred;
- why review was opened.

Must use stored evidence/policy, not fresh model improvisation.

## P3.7 Human canonical ledger view

Rebuild Markdown around canonical projects with expandable/linkable observation provenance. Never label raw observation count as project count.

---

# P4 — Accretive state and receipts

## P4.1 Session receipt schema

Implement compact factual receipts with:

- canonical project ID/reference;
- timestamp/actor/node;
- base/result refs;
- landed state;
- work summary/material areas;
- verification;
- unresolved discoveries;
- continuation point;
- PR/commit/task/evidence links.

## P4.2 Record-session command/integration

Add `ledger record-session` or equivalent ingestion path. It should be safe/idempotent and should not require agents to rewrite canonical views.

## P4.3 Current-state resolution policies

Resolve:

- lifecycle;
- latest trusted session;
- next step;
- preferred working observation;
- state freshness;
- sidecar/receipt conflicts.

## P4.4 Divergent sidecar handling

Fixture and implement:

- two copies of one canonical project with different sidecars;
- claims remain visible;
- field-specific policy resolves or opens review;
- no last-write-wins.

## P4.5 Semantic change feed

Deliver `state/changes.json` and query command for material changes between trusted runs.

## P4.6 Session-end prompt migration

Update prompts so agents prefer structured receipt + compile when available, with sidecar fallback during compatibility period.

---

# P5 — Incremental resource economy

## P5.1 Source fingerprint/checkpoint cache

Skip unchanged source extraction safely.

## P5.2 Dependency invalidation graph

Track affected:

```text
source -> observation -> identity component -> canonical project -> signal/view
```

Recompile only affected scope.

## P5.3 Derived-view digest cache

Cache capsules/summaries keyed by evidence/state digests.

## P5.4 Semantic escalation adapter

Only after deterministic canonicalization is mature:

- optional cheap/local model for ambiguous semantic evidence;
- structured prompt/input from existing review evidence;
- confidence threshold;
- no automatic consequential merge without policy;
- record model/version/result as inference evidence.

## P5.5 Cost instrumentation

Measure per run:

- sources rescanned/skipped;
- observations changed;
- projects recompiled;
- reviews opened/resolved;
- semantic/local/frontier calls;
- cache hits.

The goal is to make repeated estate operation visibly cheaper.

---

# P6 — Integration and portfolio intelligence

Only after Gates C/D/E are satisfied:

- task-system canonical project ID linking;
- work-candidate export without task ownership creep;
- GBrain/knowledge pointers by canonical project ID;
- cross-node preferred-location/capability resolution;
- Mission Control/UI over the same manifest/capsule/review contracts;
- canonical-project lifecycle/freshness analytics;
- scheduled source health/compile;
- optional SQLite/query materialization if justified.

---

# Explicitly deferred / anti-backlog

Do **not** prioritize these before their prerequisites:

- heavy local/web UI;
- model-first semantic dedupe;
- broad new ingestion adapter spree;
- real-time watchers/distributed service;
- complex task fields/workflows inside Project Ledger;
- deep document/knowledge ingestion;
- optimization that bypasses correctness fixtures;
- direct hand-editing of canonical compiled project files.
