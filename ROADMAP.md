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

- freeze/version current flat observation JSON as compatibility output beyond the current compatibility-version declaration — **done (P1.1)**;
- define explicit major/minor compatibility and migration policy — **done (P1.1)**: `docs/COMPATIBILITY.md` §3 (semver major/minor); unsupported major/format rejected explicitly at the compile boundary (`COMPAT_MAJOR_UNSUPPORTED` / `COMPAT_FORMAT_UNSUPPORTED`) via `ledger/compat_contract.py`;
- add executable schema validation for generated manifest/observation artifacts.

### Source/freshness model

- strengthen source `as_of` / freshness semantics where upstream sources provide authoritative timestamps — **done (P1.5)**: `content_as_of` / `content_as_of_basis` derived only from genuine upstream evidence (git remote-ref > HEAD commit > filesystem last-touch), never invented from compile time or path mtime;
- distinguish unavailable vs stale vs successfully-observed-empty source outcomes in the materialized contracts — **done (P1.5)**: per-source `result_state` (`observed` / `observed_empty` / `unavailable`) + `observation_count` and manifest-health `observed_empty_source_count`; `freshness_state` is `known`/`unknown`/`unavailable` and honest about what can be known;
- add previous-snapshot/checkpoint links needed for later incremental operation.

### Epistemic model

- formalize observed/declaration/inference/decision claim envelopes — **minimal slice done (P1.4)**:
  `ledger/claims.py` provides `declared`/`observed`/`inferred` claim kinds (only `declared`
  emitted today) and divergence detection;
- evidence references — **done (P2.3 / P1.4)**;
- confidence semantics (reserved);
- field-resolution policy interface — **minimal rule done (P1.4)**: `canonical_key` resolves
  divergent sidecar declarations without last-write-wins.

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

# Wave 3 — Agent read plane and Canonical Reality Provider

Status: **complete (2026-10-29)**.

Goal: make correct project orientation/resolution cheap enough to be the default agent behavior, and expose the authoritative Reality Provider contract for `machina`, `homelab`, and `gbrain`.

Deliverables:

### Project capsules (`P3.2` — done)

- `state/projects/<canonical_project_id>.json`;
- bounded identity/purpose/current topology summary (`16 KiB` size budget);
- ranked observation/location summaries with explicit reason codes and confidence;
- GBrain source and project-page cross-links (`gbrain_source_slug`, `gbrain.page_key`);
- git upstream ahead/behind and dirty working-tree drift warnings (`git_drift`, `warnings`);
- freshness/conflict/review pointers;
- deeper evidence pointers rather than payload duplication.

### Query CLI/API (`P3.3`–`P3.6` — done)

Already available from Wave 1: `ledger orient`, `ledger sources`.

Wave 3 additions (all implemented):

- `ledger resolve <referent>` — returns `canonical_project_id`, `gbrain_source_slug`, `preferred_location`, and git drift `warnings`;
- `ledger show <project>`;
- `ledger locate <project>`;
- `ledger explain <referent>`;
- `ledger review [--approve-candidates]`.

### Preferred-location resolver (`P3.5` — done)

- source class/accessibility/git freshness/operator policy inputs;
- ranked locations with reason codes/confidence and live git upstream drift warnings (`GIT_BEHIND_UPSTREAM`, `GIT_MAIN_BEHIND_ORIGIN`, `GIT_AHEAD_OF_UPSTREAM`, `GIT_DIRTY_WORKTREE`);
- never hidden heuristics.

### Human views (`P3.7` — done)

- `state/canonical-projects.md` rendered around canonical projects with preferred locations, GBrain sources, warnings, and expandable `<details>` observation provenance rather than presenting raw observations as project counts.

Gate D exit (satisfied):

- a cold agent or worker spawner (`machina`, `Hermes`) can orient and resolve/locate a project with a single call;
- it receives `canonical_project_id`, the authoritative checkout path on `ai-server`, GBrain source/page cross-links, and explicit git drift warnings;
- identity/location/field decisions are deterministically explainable via `ledger explain`.

---

# Waves 4–6 — Realigned Ecosystem Division of Labor (2026-10-29)

> **Architectural update (2026-10-29):** The September 2026 roadmap originally planned for Project Ledger to build its own session-receipt engine (Wave 4) and its own local-LLM semantic matcher (Wave 5). Since September:
>
> - **`machina`** owns the **Work Unit / Attempt / Receipt kernel** (`work/units`, `work/receipts`, `work/programmes`);
> - **`homelab`** owns **worktree leases** and scheduled provider execution (`project-ledger-discovery-watchdog.sh`);
> - **`gbrain`** owns **semantic/entity search and cross-session memory** (`gbrain` sources + `projects/<slug>` pages).
>
> Building a competing receipt store or a custom local-LLM deduplication pipeline inside Project Ledger is explicitly retired to prevent duplication.

## Realigned Reality Provider Responsibilities (Implemented)

1. **Authoritative Reality Provider for `machina` & `homelab`**:
   - `ledger resolve <referent>` and `ledger locate <project>` tell `machina` and `Hermes` before spawning a worker: the `canonical_project_id`, the authoritative checkout path on `ai-server`, and explicit git working-tree drift warnings (e.g., `"warning: local main is 20 commits behind origin/main"`).
   - `machina` owns work-unit and attempt receipts; Project Ledger capsules point `pointers.task_system` to `machina`.
2. **Cross-Linking `canonical_project_id` to `gbrain` Sources**:
   - Every canonical project in `state/canonical-projects.json` and `state/projects/<id>.json` maps to its registered `gbrain` source slug (`active-repos`, `the-garden`, `central-notes`, `central-knowledge`, `central-catalog`, `atlas-registry`, or `default`) and `projects/<project_slug>` page key (`ledger/gbrain.py`), so resolving a project yields both deterministic git/filesystem truth (from Project Ledger) and semantic history/decisions (from GBrain).
3. **Frontier Zero-Shot Triage on `cross_project_weak_overlaps`**:
   - Replaces Wave 5's custom local-model deduplication pipeline with a single frontier agent pass over `state/identity-evidence.json` + repo READMEs, emitting a candidate decision batch (`registry/weak-overlap-triage-candidates.json`) inspectable via `ledger review` and promotable via one-click human approval (`ledger review --approve-candidates`). The initial 21 weak-overlap candidate pairs (33 durable decisions) were approved into `registry/identity-decisions.json` on 2026-10-30.
4. **Semantic Change Feed (`P4.5`)**:
   - Materializes `state/changes.json` and bounded semantic run snapshots `state/runs/<run_id>.json` (`ledger changes [--since <run_id>] [--project <referent>]` and `ledger explain <chg_...>`) for estate-level delta reporting across refresh/compile runs without raw timestamp churn, preserving offline-source vs removed-project distinction.

## Remaining Optional Future Slices

- **Incremental source checkpointing (`P5.1`)**: skip unchanged source extraction when scan duration warrants it.

---

# Cross-cutting programmes

## Programme A — Contracts and invariants

Leads every wave. Schema, IDs, epistemic types, authority/freshness, compatibility, tests.

## Programme B — Source sensing

Adapters, inventory policy, source health, fingerprints, git working-tree drift sensing. Expand only when coverage value exceeds added identity/review burden.

## Programme C — Identity and decisions

Canonicalization, aliases, merge/split, evidence, frontier zero-shot weak-overlap triage, review ratchet.

## Programme D — Agent experience

Manifest, capsules, semantic change feed, query commands (`orient`, `sources`, `resolve`, `show`, `locate`, `explain`, `review`, `changes`), bounded context.

## Programme E — Ecosystem integration

`machina` (Work Unit / Attempt / Receipt kernel), `homelab` (worktree leases & watchdog seam), and `gbrain` (`gbrain_source_slug` & `projects/<slug>` cross-links) without ownership creep.

## Sequencing rule

Do not let higher layers compensate for missing lower-layer contracts. In particular:

- do not build rich analytics on raw observation counts before canonical identity;
- do not build a heavy UI before the agent query/read model;
- do not build a competing receipt/task store against `machina` or a competing semantic search/LLM matcher against `gbrain`;
- do not add many ingestion sources faster than identity/review capacity can absorb them;
- do not duplicate AGENT05/`machina` execution grammar or `homelab` worktree leases inside Project Ledger.
