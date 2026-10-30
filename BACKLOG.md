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

# P3 — Agent read plane and Reality Provider

## P3.1 Canonical system manifest

**Complete.** `state/system-manifest.json` includes `counts` (`sources`, `observations`, `canonical_projects`, `project_capsules`, `identity_evidence`, `review_items`), `health`, `capabilities`, `artifacts` (including `canonical_markdown` and `project_capsules`), and per-source health/freshness.

## P3.2 Project capsule contract

**Complete (2026-10-29).** Implemented in `ledger/capsules.py` (`PROJECT_CAPSULE_SCHEMA_VERSION = "1.0.0"`), materialized at `state/projects/<canonical_project_id>.json` for every canonical project during `compile_state` / `materialize_identity`, and registered as an `available` capability in `state/system-manifest.json` (`counts.project_capsules`, `artifacts.project_capsules`). Each capsule includes:

- canonical ID, `project_key`, `display_name`, sorted `aliases`, bounded `purpose` (`<= 240` chars), `canonical_url`, `normalized_remotes`, `identity_anchor`, and bounded `resolved_fields` provenance;
- `gbrain_source_slug` and `gbrain` cross-link (`source_slug`, `project_slug`, `page_key`, `match_basis`);
- explicit `as_of` and `freshness` block (`state`, `input_observed_at`, `content_as_of`, `content_as_of_basis`, available/unavailable observation counts);
- `preferred_location` (`state`, `observation_id`, `source_id`, `source_class`, `location`, `confidence`, `reason_codes`, `git_drift`, `warning_codes`, `warnings`, `explanation`);
- `git_facts` (including `upstream_ref`, `ahead_count`, `behind_count`, `dirty`, `drift_state`, `warnings`) and bounded `current_state`;
- ranked `observations[]` summaries with explicit `reason_codes`, `score_components`, and `git_drift`;
- open `review` summaries and progressive-disclosure `pointers` (including `machina` `task_system` and `gbrain` `knowledge_system` links);
- enforced `16 KiB` (`CAPSULE_MAX_BYTES = 16384`, `CAPSULE_MAX_OBSERVATION_SUMMARIES = 25`) context budget recorded in `budget`.

## P3.3 Resolve command

**Complete (2026-10-29).** `ledger resolve <referent> [--state-dir DIR] [--json]` resolves canonical ID, `project_key`, alias, remote URL, or known path (`0` resolved, `3` ambiguous, `4` unresolved), and returns `canonical_project_id`, `project_key`, `gbrain_source_slug`, `gbrain`, `preferred_location`, and git upstream drift `warnings` for Reality Provider consumers (`machina`, `Hermes`).

## P3.4 Show command

**Complete (2026-10-29).** `ledger show <project> [--state-dir DIR] [--json]` resolves any project referent via `resolve_payload` and returns the materialized project capsule with explicit `as_of`, `gbrain` cross-link, `preferred_location`, and `warnings` (`0` resolved, `3` ambiguous without guessing, `4` unresolved, `2` operational error).

## P3.5 Preferred-location resolver + locate command

**Complete (2026-10-29).** `rank_project_observations` and `ledger locate <project> [--state-dir DIR] [--json]` rank observations deterministically using accessibility (`ACCESSIBLE_SOURCE` vs `INVENTORY_ONLY_LOCATOR` / `SOURCE_UNAVAILABLE`), source class (`live` > `mirror` > `backup` > `inventory`), canonical remote match (`CANONICAL_REMOTE_MATCH`), recent session continuity (`RECENT_SESSION_EVIDENCE`), git commit/branch state (`GIT_WORKTREE`, `PRIMARY_BRANCH`), primary active root (`PRIMARY_ACTIVE_ROOT`), and upstream content freshness (`FRESH_REMOTE_REF`, `FRESH_GIT_COMMIT`, `FRESH_FILESYSTEM_TOUCH`), and surface live git upstream drift warnings (`GIT_BEHIND_UPSTREAM`, `GIT_MAIN_BEHIND_ORIGIN`, `GIT_AHEAD_OF_UPSTREAM`, `GIT_DIRTY_WORKTREE`).

## P3.6 Explain command

**Complete (2026-10-29).** Implemented in `ledger/explain.py` and exposed as `ledger explain <referent> [--state-dir DIR] [--identity-decisions PATH] [--json]`. Explains from compiled artifacts and durable decisions — without fresh model improvisation:

- canonical projects (`subject_kind = "canonical_project"`): identity unification (`identity_anchor`, `unifying_authority`, `strong_reason_code`, `merge_decision_ids`, `cross_project_weak_overlaps`), `resolved_fields` provenance (`project_key`, `display_name`, `gbrain_source_slug`), `preferred_location` and git drift `warnings`, `gbrain` cross-link, applied `decisions`, and open `review` items;
- observations (`subject_kind = "observation"`): canonical project membership, location ranking, and identity anchor;
- review items (`subject_kind = "review_item"`): `code`, `severity`, `reason_automation_stopped`, `resolution_actions`, and `evidence`;
- identity decisions (`subject_kind = "identity_decision"`): active vs superseded state, `superseded_by`, affected observations, and affected canonical projects.

## P3.7 Human canonical ledger view

**Complete (2026-10-29).** Implemented in `ledger/human_view.py` and materialized at `state/canonical-projects.md` (`manifest["artifacts"]["canonical_markdown"]`). Presents canonical project count distinctly from raw observation count, with preferred locations, GBrain sources, git drift warnings, and expandable `<details>` multi-manifestation provenance.

---

# P4–P6 — Realigned Ecosystem Integration (2026-10-29)

> **Realigned (2026-10-29):** Since September 2026, `machina` owns the Work Unit / Attempt / Receipt kernel, `homelab` owns worktree leases, and `gbrain` owns semantic/entity search. Project Ledger does **not** build a competing session-receipt store (`P4.1`–`P4.2` retired in favor of `machina`) or a custom local-LLM deduplication pipeline (`P5.4` replaced by frontier zero-shot triage).

## Reality Provider for `machina` & `homelab` (Complete — 2026-10-29)

- `ledger resolve <referent>` and `ledger locate <project>` act as the authoritative Reality Provider before spawning a worker: returning `canonical_project_id`, authoritative checkout path (`preferred_location.location`), and live git drift warnings (`warning: local main is N commits behind origin/main`, `warning: local working tree at ... has uncommitted changes`).
- Capsules link `pointers.task_system` to `provider: "machina"`.

## GBrain Source Cross-Linking (Complete — 2026-10-29)

- `ledger/gbrain.py` maps each canonical project in `state/canonical-projects.json` and `state/projects/<id>.json` to its registered `ai-server` GBrain source slug (`active-repos`, `the-garden`, `central-notes`, `central-knowledge`, `central-catalog`, `atlas-registry`, or `default`) and `projects/<project_slug>` page key.

## Frontier Zero-Shot Triage on `cross_project_weak_overlaps` (Complete — 2026-10-30)

- `ledger/triage.py` and `ledger review [--approve-candidates]` replace Wave 5's local-LLM matcher.
- Batch 1 (`registry/weak-overlap-triage-candidates.json`): 33 frontier-triaged candidate decisions covering the initial 21 `cross_project_weak_overlaps` were approved into `registry/identity-decisions.json` on 2026-10-30 (`207 -> 190` canonical projects).
- Batch 2 (`registry/weak-overlap-triage-candidates.json`): 29 frontier-triaged candidate decisions (`merge`, `reject_match`, and companion `canonical_key` decisions) covering all 19 remaining post-`CONV-001` weak overlaps were approved into `registry/identity-decisions.json` on 2026-10-30 (`190 -> 176` canonical projects, `65` total active decisions, `0` open identity reviews, and `0` untriaged weak overlaps).

## P4.5 Semantic change feed (Complete — 2026-10-30)

- Implemented in `ledger/changes.py` and wired into `compile_state`, `orient`, `explain`, and `ledger changes`.
- Materializes `state/changes.json` (`CHANGE_FEED_SCHEMA_VERSION = "1.0.0"`) plus a bounded compact semantic run-history ring buffer at `state/runs/<run_id>.json` (`RUN_HISTORY_MAX_RUNS = 50`), automatically backfilling pre-Wave-4 `state/` artifacts prior to overwrite.
- Supports `ledger changes [--since <run_id>] [--project <referent>] [--entity-kind <kind>] [--change-type <code>] [--json]` and `ledger explain <chg_...>`, suppressing raw `scan_time`/mtime churn while distinguishing `PROJECT_UNAVAILABLE`/`OBSERVATION_UNAVAILABLE` (offline source) from `PROJECT_REMOVED`/`OBSERVATION_REMOVED`.

## P5.1 Source fingerprint/checkpoint cache (Complete — 2026-10-30)

- Implemented in `ledger/checkpoints.py` (`SOURCE_CHECKPOINT_SCHEMA_VERSION = "1.0.0"`) after live `ai-server` benchmarking measured ~`32.3s` uncached full-scan time across the 17 sources (234 candidates).
- Persists `state/source-checkpoints.json` and exposes `capabilities.source_checkpoints = "available"`, `manifest.checkpoints`, and per-source `checkpoint_status` (`hit | partial | miss | unavailable | bypassed | not_applicable`), `checkpoint_fingerprint`, and `last_extracted_at`.
- Skips `git` subprocess bursts and redundant metadata extraction on unchanged candidates while preserving byte-identical compatibility entries; supports `--full-scan` on `ledger refresh` to force full reconciliation.

---

# Explicitly deferred / anti-backlog

Do **not** prioritize these:

- competing session-receipt store or task/work-unit kernel (owned by `machina`);
- competing worktree lease manager (owned by `homelab`);
- custom local-LLM semantic deduplication pipeline or entity search engine (owned by `gbrain` + frontier zero-shot triage);
- heavy local/web UI;
- broad new ingestion adapter spree;
- direct hand-editing of canonical compiled project files.
