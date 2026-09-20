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

**Complete (2026-09-20).** The flat compatibility observation contract is now
explicit and version-pinned: `docs/COMPATIBILITY.md` documents the envelope
(`generated_at` / `config_path` / `entry_count` / `entries[]`), the exact ordered
36-field entry list, and a semver major/minor policy; the golden fixture
`tests/fixtures/compat-contract.json` + `tests/test_compat_contract.py` assert
`ledger.models.CSV_FIELDS == fixture entry_fields` exactly, so a field rename,
reorder, removal, or unapproved addition fails the drift test; and the compile
boundary now rejects an unsupported `format` (`COMPAT_FORMAT_UNSUPPORTED`) or
schema major (`COMPAT_MAJOR_UNSUPPORTED`) explicitly via
`ledger/compat_contract.py` rather than guessing through (CLI exit 2, stable
code). A same-major newer minor is tolerated; a missing version marker defaults to
supported v0 (unchanged `build_ledger.py` producer output). Suite 102 -> 112 tests.

### P1.4 Minimal epistemic claim/evidence contract

**Complete (2026-09-19).** A minimal typed claim surface — `ledger/claims.py`
(`declared`/`observed`/`inferred` claim kinds; only `declared` emitted today) — plus a
`DIVERGENT_SIDECAR_DECLARATIONS` review that surfaces conflicting sidecar `project_key`
declarations on a merged project instead of silently absorbing them (SCHEMA.md §6).
Resolvable by a `canonical_key` decision without last-write-wins, with no recurrence on
unchanged inputs (P2.8-style ratchet). See
`docs/work/P1.4-EPISTEMIC-CLAIM-CONTRACT-20260919.md` and
`tests/test_p14_epistemic_claims.py`. Deliberately only what canonical identity needs
immediately — not a generalized knowledge framework. `confidence` for inference remains
reserved vocabulary (no inferred claim is emitted yet). Suite 94 -> 102 tests.

Original deliverable shape (delivered in minimal scope only):

- observed/declaration/inference envelopes;
- evidence references;
- confidence for inference (reserved);
- a small field-resolution policy interface (the `canonical_key` field-resolution rule).

Do not build a generalized knowledge framework.

### P1.5 Tighten freshness/result semantics

**Complete (2026-09-20).** Each source in the manifest now carries a materialized
`result_state` (`observed` / `observed_empty` / `unavailable`) and an
`observation_count`, distinguishing a source that was probed successfully but yielded
zero observations (healthy-but-empty) from a genuinely unavailable source. `freshness_state`
is honest: `known` only where an upstream source supplied authoritative timestamp evidence
(exposed as `content_as_of` + `content_as_of_basis`; authoritative priority git remote-ref >
HEAD commit > filesystem last-touch), `unknown` for an observed source with no such evidence
(never invented from compile time or path mtime), and `unavailable` for an unprobeable
source. Manifest schema 1.1.0 -> 1.2.0 adds these fields plus a `health.observed_empty_source_count`.
See `docs/work/P1.5-FRESHNESS-20260920.md` and `tests/test_p15_freshness.py`. Suite 112 -> 122 tests.

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

**Complete.** The source adapters and normalization that lived in the
`build_ledger.py` monolith have been extracted into the `ledger/` package under the
frozen compatibility tests, which still pass unchanged (40 tests):

- config/validation — already in `ledger/config.py`; `ledger/sources/common.py`
  now carries the scanner's shared path/exclude/readme helpers and constants;
- source abstraction — `ledger/sources/base.py` (`SourceAdapter` ABC + adapter
  registry keyed by discovery mode + `collect_entries` orchestration);
- filesystem scanner — `ledger/sources/filesystem.py` (discover
  children/git_repos/self, candidate inspection, tree summary, entry building);
- git extraction — `ledger/sources/git.py` (`git_output`, `gather_git_metadata`);
- inventory-policy adapter — `ledger/sources/inventory_policy.py`;
- normalization/models — `ledger/models.py` (URL/timestamp/boolean/tag
  normalization, markdown rendering primitives, project-type classification,
  `CSV_FIELDS`).

`build_ledger.py` remains the compatibility entrypoint: it now re-exports the
extracted names unchanged and keeps only argument parsing, the CSV/JSON/Markdown
writers, and the markdown-mirror step, so output is byte-identical. Direct adapter
coverage was added in `tests/test_source_adapters.py` (8 tests; total suite 48).

Keep `build_ledger.py` as compatibility entrypoint.

## P2.2 Multi-source estate fixture

**Complete — the full multi-source estate fixture is now the primary canonicalization
acceptance environment.** `tests/fixtures/multi-source-estate.json` +
`tests/test_multi_source_estate.py` pin the compiler's exact deterministic output for
every estate column:

- one project present as live repo + mirror + backup (exact remote merges all three);
- renamed/moved project (same remote across a moved path keeps one canonical project);
- same-name unrelated project (stays separate; `PROJECT_KEY_AMBIGUOUS` review);
- missing remote (no strong evidence -> separate by default, ambiguity surfaced);
- divergent sidecars (same remote, conflicting `project_key` claims -> remote stays the
  stable key, both hints preserved, no last-write-wins; a bounded
  `DIVERGENT_SIDECAR_DECLARATIONS` review opens per P1.4/P2.6);
- inventory-only observation (valid singleton anchored by observation);
- inaccessible source (a decision referencing an unavailable observation becomes a
  bounded `DECISION_REFERENCE_UNAVAILABLE` review; present observations still compile);
- strong and weak identity evidence (exact remote is strong; names/keys are weak).

The R0 reality cases (`tests/fixtures/r0-reality-cases.json`) remain as the production
proof pin; the estate fixture is the broader synthetic acceptance matrix.

## P2.3 Identity evidence model

**Complete — records/scoring implemented as `ledger/evidence.py` (SCHEMA.md §1.8).**
Models every evidence kind with a strength class (`strong` / `weak` / `negative`) and
materializes `state/identity-evidence.json`:

- normalized remote (strong, the lone auto-merge authority);
- source-native identity (strong provenance: source + snapshot anchoring);
- explicit merge decision (strong);
- explicit project key / declaration (weak, declared);
- path locator + path migration alias (weak);
- raw URL (weak);
- display name (weak — collides across unrelated projects);
- README/repo compound evidence (weak);
- name / semantic similarity (explicitly weak, referent-only — never merges);
- negative / conflict evidence from split/reject decisions (negative).

Per canonical project, `identity_evidence[]` + `identity_evidence_summary` expose the
unifying authority (exact remote vs explicit decision vs singleton) and weak-overlap
counts. `cross_project_weak_overlaps` records weak hits and negative decisions between
distinct projects so unused evidence is visible and clearly weak. Scoring is purely
descriptive: merge authority is unchanged (exact normalized remote + durable decisions).
Coverage: `tests/test_identity_evidence.py` (13 tests); total suite 64.

## P2.4 Decision registry

**Complete.** Durable inputs now cover the full decision-type set, implemented in
`ledger/identity.py` (validation + compiler) and `ledger/evidence.py` (supersession-aware
negative evidence):

- merge;
- split;
- reject-match;
- alias;
- canonical human key assignment (`canonical_key` — operator-approved stable
  `project_key` override; never changes membership);
- supersession (`supersede` — marks a prior decision inactive for the compile;
  append/supersede oriented, never silently rewritten).

Every decision carries a stable `decision_id` and may carry `rationale`, `evidence`
refs, `authority` (`operator` | `trusted_automation` | `reviewed_agent`), and
`decided_at`. A `supersede` referencing an unknown decision becomes a bounded
`DECISION_SUPERSEDE_UNKNOWN` review. Superseded negative decisions no longer constrain
compilation or evidence. Coverage: `tests/test_decision_registry.py` (10 tests); total
suite 74.

## P2.5 Canonical project IDs and compiler

**Complete.** The canonical-ID compiler now guarantees a distinct `canonical_project_id`
per conceptual project: a normalized remote is only used as the identity anchor when it
is unique to one project across the whole compile, so a split/reject that keeps two
observations sharing an exact remote in separate projects no longer produces a shared
ID (the legacy colliding-anchor quirk flagged in SCHEMA.md §1.8 is resolved). Each
canonical project also carries a `resolved_fields` claim-provenance map explaining which
observation/decision/evidence produced its `project_key` and `display_name`. Coverage:
`tests/test_p25_canonical_ids.py` (8 tests); total suite 82.

## P2.6 Identity review queue

**Complete.** The review queue is now first-class (`ledger/identity.py` `_review` +
`tests/test_p26_review_queue.py`; total suite 87). Every review item carries
`severity`, `reason_automation_stopped`, `resolution_actions`, and `evidence`, and
the review codes cover the P2.6 categories:

- ambiguous match — `PROJECT_KEY_AMBIGUOUS` (warning);
- conflicting strong identity evidence — `DECISION_CONFLICT` (error);
- split suspicion / blocked auto-match — `AUTO_MATCH_BLOCKED_BY_DECISION` (warning);
- decision reference unavailable — `DECISION_REFERENCE_UNAVAILABLE` (warning);
- decision-scoped supersede-unknown — `DECISION_SUPERSEDE_UNKNOWN` (warning).

This also fixed a genuine data-loss defect: two `DECISION_SUPERSEDE_UNKNOWN` reviews
referencing two different unknown decisions previously collapsed to one `review_id`
(empty `affected_observation_ids` hashed into the ID material), silently dropping the
first review. Decision-scoped reviews now carry a non-empty `affected_decision_ids`
referent that is fed into the `review_id` material, so each distinct unknown-target
review gets a distinct ID. Observation-scoped review IDs are unchanged (pinned
fixtures preserved).

Divergent sidecar declarations affecting identity are now **landed (P1.4)**: merged
projects with conflicting `project_key` declarations open a bounded
`DIVERGENT_SIDECAR_DECLARATIONS` review instead of silently absorbing them (see
`docs/work/P1.4-EPISTEMIC-CLAIM-CONTRACT-20260919.md`).

## P2.7 Resolution ratchet tests

**Complete.** Every first-class review type that a durable decision can resolve is
now pinned by `tests/test_p27_resolution_ratchet.py` (5 tests; total suite 92),
asserting the full review-resolution loop for each:

- resolve once -> persist a durable decision -> rerun with **identical** observations
  -> the review does not recur -> the durable decision that resolved it is
  discoverable in the compiled state a future `ledger explain` (P3.6) will read.

Covered resolutions:

- `PROJECT_KEY_AMBIGUOUS`          -> a merge decision unites the two same-key projects;
- `DECISION_CONFLICT`              -> superseding the conflicting negative decision lets
  the positive merge apply;
- `AUTO_MATCH_BLOCKED_BY_DECISION` -> superseding the blocking negative decision lets the
  exact normalized remote auto-merge;
- `DECISION_REFERENCE_UNAVAILABLE` -> superseding the stale decision stops a missing source
  from blocking present observations;
- `DECISION_SUPERSEDE_UNKNOWN`     -> adding the referenced decision gives an earlier
  supersede a known target.

This closes the Gate C guarantee that "a resolved identity question does not recur on
unchanged inputs" for every resolvable review type.

A review-resolution gap surfaced by the ratchet probe is now **closed as P2.8** (it was
originally recorded as `docs/work/FUTURE-SPEC-project-key-ambiguous-resolution-actions-20260919.md`):
the `PROJECT_KEY_AMBIGUOUS` `resolution_actions` advertised `canonical_key`/`reject_match`
as resolutions, but only `merge` actually cleared the review (the ambiguity is computed
on the raw key hint, not the resolved canonical key). P2.8 computes the ambiguity on the
resolved key so a distinct `canonical_key` clears the review, and corrects the advertised
actions to the two paths that actually clear (`merge`, `canonical_key`). See
`docs/work/P2.8-PROJECT-KEY-AMBIGUOUS-RESOLUTION-20260919.md`.

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
