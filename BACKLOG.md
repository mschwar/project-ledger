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

## Landed in Wave 1 Tranche 1A

The following are implemented on `main` and must not be re-created as new work:

- compiler/schema version constants;
- explicit stable production `source_id` values and deterministic fallback;
- source classes, source probes/health, probe fingerprints, and compatibility snapshot IDs;
- stable manifestation-oriented `observation_id`;
- strict structural config validation with stable error codes;
- explicit semantic-state vocabulary for new contracts;
- typed observation wrappers;
- `state/system-manifest.json` and `state/observations.json`;
- explicit unavailable capability reason codes;
- degraded-source failure containment during compile;
- `ledger validate`, `refresh`, `compile`, `orient`, and `sources`;
- automation-safe `ledger refresh --no-markdown-mirror`.

## Remaining Gate B work

### P1.1 Freeze compatibility observation contract

Deliver:

- documented compatibility envelope and major/minor policy;
- a golden compatibility fixture preventing accidental field-meaning drift;
- explicit unsupported-major behavior.

### P1.4 Minimal epistemic claim/evidence contract

Deliver only what canonical identity needs immediately:

- observed/declaration/inference envelopes;
- evidence references;
- confidence for inference;
- a small field-resolution policy interface.

Do not build a generalized knowledge framework.

### P1.5 Tighten freshness/result semantics

Deliver:

- unavailable vs successfully-observed-empty distinction;
- stronger `as_of` semantics where an upstream source actually supplies evidence;
- no invented freshness when it cannot be known.

Defer elaborate checkpoint/history machinery until incremental compilation needs it.

### P1.7 Sidecar as versioned declaration source

Deliver:

- sidecar `schema_version` plan/validation;
- accepted field/type validation;
- malformed/unsupported-version error or review behavior;
- explicit conversion of sidecar values into declarations rather than unconditional canonical truth.

Do not add `canonical_project_id` to sidecars until the registry can assign/validate it safely.

### P1.9 Production reality proof

Status: COMPLETE (2026-09-19). Evidence: `docs/work/R0-LIVE-PROVIDER-PROOF-20260919.md`.

Ran current `main` (`bf04d7b`) through the deployed non-mutating provider seam on
ai-server. Retained commit/run ID (`run_4c8e9084d4ca23637eac9267`), source health
(all 14 available), counts (132 observations → 129 canonical projects, 1 review item),
and representative duplicate/same-name manifestations as canonicalization fixtures
(homelab/white-rabbit/reality-ledger multi-manifestation merges; `the-garden` ambiguity).

Exit evidence satisfied: one current provider run demonstrated the exact Wave 1 contract
against the intended estate. A deployment defect (three dead roots in the committed
config) was surfaced and fixed as a host-specific uncommitted seam edit; the committed
template has now been reconciled with the live host (2026-09-19) — the three dead roots
are removed from `ledger_config.json` and marked `[Needs validation]` in a provenance
note (see `docs/work/FUTURE-SPEC-reconcile-ledger-config-template-20260919.md`).

---

# P2 — Canonical identity compiler

## Landed — R2 identity walking skeleton

Implemented:

- conservative canonical identity compilation from exact normalized repository remotes;
- durable `registry/identity-decisions.json` input supporting merge/split/reject/alias;
- `state/canonical-projects.json`;
- `state/review-queue.json`;
- canonical-project/review counts and capabilities in the system manifest;
- exact `ledger resolve` with explicit resolved/ambiguous/unresolved outcomes;
- regression coverage for same-name unrelated projects, duplicate manifestations, negative decisions, explicit merge/alias, and stable canonical IDs.

Do not broaden automatic matching to names/project-key hints merely to increase merge rate.

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

**Partial — R0 reality cases landed.** `tests/fixtures/r0-reality-cases.json` +
`tests/test_r0_reality_regressions.py` reproduce the exact canonical IDs from the
R0 live provider proof (homelab/white-rabbit/reality-ledger multi-manifestation
merges; `the-garden` `PROJECT_KEY_AMBIGUOUS` split + review). This covers the
"one project present as live repo + mirror/backup" and "same-name unrelated
project" columns of the full fixture.

Still open to reach the full multi-source estate fixture:

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
