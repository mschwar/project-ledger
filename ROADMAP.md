# Roadmap

The roadmap follows the abstraction tower in `SYSTEM.md`. Each wave should make the system more legible and useful to agents without forcing later layers to compensate for unstable lower-layer contracts.

## Wave 0 — Authority convergence

Status: product convergence landed on `main`; real-source refresh/hygiene follow-up remains.

Goal: one authoritative product line and trustworthy development loop.

Landed:

- evolved multi-source implementation merged to `main`;
- outstanding correctness review findings addressed;
- docs converged to actual ingestion behavior;
- CI added and passing on merged implementation;
- long-lived shadow product branch eliminated as authority.

Remaining operational closeout:

- refresh from real homelab sources on a node with full configured access;
- inspect refreshed output for gaps/duplicate fixtures worth preserving;
- retire obsolete rescue/feature branches when recovery value is exhausted.

Gate A exit: `main` is authoritative, tests pass, and source refresh can run without a known blocking defect.

---

# Wave 1 — Agent substrate and contracts

Status: **in progress**. The first executable vertical slice is implemented: stable source/snapshot/observation IDs, source health/fingerprints, config validation, typed observation wrappers, `state/system-manifest.json`, and the `validate` / `refresh` / `compile` / `orient` / `sources` CLI surface.

Goal: make evidence/state machine-readable and unambiguous before canonicalization grows.

## Tranche 1A — executable orientation substrate

Implemented:

- compiler/schema version constants;
- explicit stable `source_id` values in the production config;
- source classes and source-health states;
- deterministic input fingerprints and `snapshot_id` values;
- manifestation-oriented stable `observation_id` values;
- strict config validation with stable error codes;
- typed null/state vocabulary for new contracts;
- typed observation wrappers around the flat compatibility output;
- `state/system-manifest.json` with run/config/source/health/count/capability metadata;
- explicit unavailable-capability reason codes for future layers;
- degraded-source failure containment in compiled orientation;
- `python -m ledger refresh` as the one-command scan + compile path;
- `orient` and `sources` as cheap agent read surfaces;
- full legacy + Wave 1 CI discovery.

This tranche deliberately does **not** introduce canonical projects, semantic dedupe, project capsules, receipts, or a new task plane.

## Tranche 1B — remaining Gate B contract work

### Contract/version foundation

- freeze/version current flat observation JSON as compatibility output beyond the current compatibility-version declaration;
- define explicit major/minor compatibility and migration policy;
- add executable schema validation for generated manifest/observation artifacts.

### Source/freshness model

- strengthen source `as_of` / freshness semantics where upstream sources provide authoritative timestamps;
- distinguish unavailable vs stale vs successfully-observed-empty source outcomes in the materialized contracts;
- add previous-snapshot/checkpoint links needed for later incremental operation.

### Epistemic model

- formalize observed/declaration/inference/decision claim envelopes;
- evidence references;
- confidence semantics;
- field-resolution policy interface.

### Validation

- sidecar/declaration schema validation;
- generated schema/version validation;
- stable machine-readable error/review codes across boundaries.

Gate B exit:

- an agent can inspect one manifest and know what ran, against which sources, how fresh/healthy those sources are, and what schema it is reading;
- compatibility observation records have stable IDs and explicit semantics;
- unsupported/malformed state fails explicitly rather than by ambient interpretation;
- claim/evidence and field-resolution contracts are stable enough that Wave 2 identity does not need to invent them ad hoc.

---

# Wave 2 — Canonical identity compiler

Status: **first walking skeleton landed; broader Wave 2 remains open**. The R0 reality
cases are now ratcheted as deterministic regression fixtures
(`tests/fixtures/r0-reality-cases.json` reproduces exact canonical/review IDs from the
2026-09-19 production proof).

Goal: compile durable project identity while preserving all observations and uncertainty.

Landed first slice:

- exact normalized repository remote as the only automatic identity merge rule;
- durable JSON decision registry supporting merge/split/reject/alias inputs;
- canonical project materialization;
- bounded identity review queue;
- distinct canonical-project/review counts in the manifest;
- exact `ledger resolve` with resolved/ambiguous/unresolved outcomes;
- reality-shaped regression fixtures covering multi-manifestation merge, same-name separation, negative decisions, explicit merge/alias, and stable IDs.

This slice deliberately does **not** treat names or compatibility `project_key` values as automatic identity authority. Gate-B declaration/freshness hardening remains relevant when it blocks real identity use, but the conservative slice does not depend on untyped declarations for merging.

Deliverables:

### Modular extraction under frozen contracts

- extract source adapters/normalization/models from `build_ledger.py` without changing compatibility behavior;
- fixture-based multi-source estate tests.

The multi-source estate fixture is **complete** and is now the primary canonicalization
acceptance environment: `tests/fixtures/multi-source-estate.json` +
`tests/test_multi_source_estate.py` pin the compiler's exact deterministic output for
every estate column (live+mirror+backup, renamed/moved, same-name unrelated, missing
remote, divergent sidecars, inventory-only, inaccessible source, strong vs weak
evidence). The R0 reality cases remain as the production proof pin.

### Identity graph

- identity evidence records — **done (P2.3)**; `ledger/evidence.py` + `state/identity-evidence.json`;
- aliases;
- strong deterministic matching rules;
- scored weak evidence separated from decisions — **done (P2.3)**: strength-classified
  evidence (strong/weak/negative), weak name/semantic similarity explicitly marked weak;
- negative/split evidence — **done (P2.3)** as first-class negative evidence.

### Decision registry

- merge/split/reject-match/alias/canonical-key decisions — **done (P2.4)**;
- supersession semantics — **done (P2.4)**: `supersede` marks a prior decision inactive
  for the compile (append/supersede oriented, never silently rewritten);
- operator/review rationale/evidence references — **done (P2.4)**: every decision
  carries a stable `decision_id` and may carry `rationale`, `evidence` refs,
  `authority`, and `decided_at`.

### Canonical projects

- immutable `canonical_project_id` — **done (P2.5)**: distinct per conceptual project
  (a shared remote no longer collides when a split/reject keeps observations apart);
- human-readable stable `project_key` aliases;
- observation links retained;
- resolved canonical fields with provenance — **done (P2.5)**: `resolved_fields`
  claim-provenance map on each canonical project;
- duplicate/ambiguity groups.

### Review queue

- first-class identity review items with stable IDs, evidence, severity, reason
  automation stopped, resolution actions — **done (P2.6)**: every review item carries
  `severity`, `reason_automation_stopped`, `resolution_actions`, and `evidence`;
  decision-scoped reviews carry a non-empty `affected_decision_ids` referent (fixing
  a review_id collision that silently dropped distinct supersede-unknown reviews);
- resolved review items ratchet into durable decisions/tests — **done (P2.7)**:
  `tests/test_p27_resolution_ratchet.py` pins, for each review type a durable decision
  can resolve, that resolving once + persisting the decision clears the review on
  re-run with identical observations and that the resolving decision is discoverable
  in the compiled state (the future `ledger explain` read path);

Gate C exit remains broader than the first slice:

- two or more source snapshots compile reproducibly into canonical projects;
- ambiguous same-name/similar projects are not silently merged;
- every merge can be explained;
- canonical project count is distinct from observation count;
- a resolved identity question does not recur on unchanged inputs.

---

# Wave 3 — Agent read plane

Goal: make correct project orientation/resolution cheap enough to be the default agent behavior.

Deliverables:

### Project capsules

- `state/projects/<canonical_project_id>.json`;
- bounded identity/purpose/current topology summary;
- ranked observation/location summaries;
- freshness/conflict/review pointers;
- deeper evidence pointers rather than payload duplication.

### Query CLI/API

Already available from Wave 1: `ledger orient`, `ledger sources`.

Wave 3 additions:

- `ledger resolve`;
- `ledger show`;
- `ledger locate`;
- `ledger explain`;
- `ledger review`.

### Preferred-location resolver

- source class/accessibility/git freshness/operator policy inputs;
- ranked locations with reason codes/confidence;
- never hidden heuristics.

### Human views

Rebuild Markdown/CSV around canonical projects plus expandable observation provenance rather than presenting raw observations as though they were project counts.

Gate D exit:

- a cold agent can orient and resolve/load a project with a few stable calls/reads;
- it normally does not need repo archaeology or full observation datasets;
- identity/location recommendations are explainable.

---

# Wave 4 — Accretive current state and receipts

Goal: make every material session improve continuity for future agents.

Deliverables:

### Session receipts

- structured receipt schema;
- project/git/task/evidence pointers;
- landed state and verification;
- unresolved discoveries and continuation point.

### Current-state resolver

- lifecycle state;
- last trusted session;
- next continuation point;
- state freshness/conflict semantics;
- sidecars treated as declaration sources, not last-write-wins truth.

### Change feed

- semantic changes between trusted runs;
- observation and canonical-project deltas;
- materiality classification.

### Review/state ratchet

- review resolution creates durable decision;
- real failures/ambiguities become fixtures when appropriate;
- stale session/declaration conflicts surface explicitly.

Gate E exit:

- after material agent work, the next agent can continue from capsule + receipt without replaying the prior conversation;
- multiple divergent declarations are visible and resolvable;
- change since last trusted run is explicit.

---

# Wave 5 — Incremental compilation and resource economy

Goal: make the system cheap enough to run routinely across the estate.

Deliverables:

- source fingerprints/checkpoints (fingerprints begin in Wave 1; this wave uses them for invalidation);
- skip unchanged extraction;
- delta propagation from source -> observations -> identity components -> canonical projects -> views;
- cached semantic summaries keyed by evidence digests;
- deterministic heuristic confidence thresholds;
- optional cheap/local semantic matcher for unresolved low-stakes identity evidence;
- frontier/human escalation only for consequential ambiguity;
- compile/query performance instrumentation.

Gate F exit:

- routine runs primarily process deltas;
- expensive semantic reasoning is exceptional and measurable;
- unchanged estates produce near-zero semantic churn/reasoning cost.

---

# Wave 6 — Downstream control-plane integration and portfolio intelligence

Goal: make Project Ledger the shared project-identity/state substrate rather than an isolated tool.

Integration ownership:

- Project Ledger publishes stable reality contracts;
- AGENT05 consumes them as reality/evidence inside its execution-control grammar rather than owning a competing ledger;
- homelab deploys/schedules/queries Project Ledger on the real fleet rather than owning a competing identity schema.

Deliverables may include:

- stable plugin/query contract for homelab agents across nodes;
- AGENT05 adapter from manifest/capsules into Situation Model / Control Packet reality inputs;
- task-system project-ID linking/work-candidate handoff;
- GBrain/knowledge pointers keyed to canonical project IDs;
- Mission Control/operator UI consuming manifest/capsules/review/change data;
- portfolio analytics based on canonical projects and trusted history;
- scheduled compiles/source health checks;
- optional SQLite/materialized query store if justified by query volume.

Gate G exit:

- downstream systems consume canonical IDs/views rather than reimplementing identity;
- task and knowledge systems remain separate owners of their domains;
- the UI is a view over the same contracts agents use.

---

# Cross-cutting programmes

## Programme A — Contracts and invariants

Leads every wave. Schema, IDs, epistemic types, authority/freshness, compatibility, tests.

## Programme B — Source sensing

Adapters, inventory policy, source health, fingerprints. Expand only when coverage value exceeds added identity/review burden.

## Programme C — Identity and decisions

Canonicalization, aliases, merge/split, evidence, review ratchet.

## Programme D — Agent experience

Manifest, capsules, query commands, explainability, bounded context.

## Programme E — Accretive state

Receipts, current-state resolution, change feed, durable decisions.

## Programme F — Resource economy

Incremental invalidation, caches, local/cheap reasoning ladder, instrumentation.

## Programme G — Integration

Task/knowledge/control-plane links without ownership creep.

## Sequencing rule

Do not let higher layers compensate for missing lower-layer contracts. In particular:

- do not build rich analytics on raw observation counts before canonical identity;
- do not build a heavy UI before the agent query/read model;
- do not add model-heavy deduplication before deterministic evidence/decision contracts;
- do not add many ingestion sources faster than identity/review capacity can absorb them;
- do not duplicate AGENT05's execution grammar or homelab's WorkSpec/task plane inside Project Ledger.
