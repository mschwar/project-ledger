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

Goal: make evidence/state machine-readable and unambiguous before canonicalization grows.

Deliverables:

### Contract/version foundation

- freeze/version current flat observation JSON as compatibility output;
- introduce explicit schema/compiler version fields;
- define compatibility/migration policy;
- introduce typed error/state/null semantics.

### Stable IDs and source model

- `source_id` registry/derivation;
- `snapshot_id` / run identity;
- stable `observation_id` strategy;
- source class: live, mirror, backup, inventory-only, etc.;
- source health/freshness/fingerprint contract.

### Epistemic model

- observed/declaration/inference/decision claim types;
- evidence references;
- confidence semantics;
- field-resolution policy interface.

### Validation

- full config validation layer;
- sidecar/declaration schema validation;
- version validation;
- stable machine-readable error codes.

### Minimal orientation manifest

Materialize the first `state/system-manifest.json` even before full canonical projects exist. It should expose run/version/source health/observation counts/review debt and clearly label unavailable future capabilities.

Gate B exit:

- an agent can inspect one manifest and know what ran, against which sources, how fresh/healthy those sources are, and what schema it is reading;
- compatibility observation records have stable IDs and explicit semantics;
- unsupported/malformed state fails explicitly rather than by ambient interpretation.

---

# Wave 2 — Canonical identity compiler

Goal: compile durable project identity while preserving all observations and uncertainty.

Deliverables:

### Modular extraction under frozen contracts

- extract source adapters/normalization/models from `build_ledger.py` without changing compatibility behavior;
- fixture-based multi-source estate tests.

### Identity graph

- identity evidence records;
- aliases;
- strong deterministic matching rules;
- scored weak evidence separated from decisions;
- negative/split evidence.

### Decision registry

- merge/split/reject-match/alias/canonical-key decisions;
- supersession semantics;
- operator/review rationale/evidence references.

### Canonical projects

- immutable `canonical_project_id`;
- human-readable stable `project_key` aliases;
- observation links retained;
- resolved canonical fields with provenance;
- duplicate/ambiguity groups.

### Review queue

- first-class identity review items with stable IDs, evidence, severity, resolution actions;
- resolved review items ratchet into durable decisions/tests.

Gate C exit:

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

- `ledger orient`;
- `ledger resolve`;
- `ledger show`;
- `ledger locate`;
- `ledger explain`;
- `ledger sources`;
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

- source fingerprints/checkpoints;
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

Deliverables may include:

- stable plugin/query contract for homelab agents across nodes;
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
- do not add many ingestion sources faster than identity/review capacity can absorb them.
