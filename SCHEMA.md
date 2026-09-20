# Schema

## Purpose

This document is the schema authority for Project Ledger. It distinguishes:

1. the legacy flat compatibility record;
2. the implemented typed source/observation substrate;
3. the implemented first canonical-identity/review slice;
4. the broader current-state/capsule/change schema families that remain design targets.

Do not treat a target field as implemented merely because it appears in this document. The runtime capability map in `state/system-manifest.json` is the executable boundary.

---

# 0. Compatibility scanner schema

`build_ledger.py` still emits one flat project-like record. Preserve this while migration proceeds.

## Identity compatibility fields

- `project_hash` — deterministic hash of current `project_key` basis; **not** a canonical project ID.
- `project_key` — best current key from sidecar, remote URL, inventory key, or fallback slug.

## Naming/classification

- `name`
- `project_type`
- `repo_name`
- `tags`
- `status`
- `description`

## Provenance/location

- `source_label`
- `machine_name`
- `storage_scope`
- `shared`
- `path`
- `path_from_root`
- `canonical_url`
- `remote_url`
- `sidecar_path`

## Navigation/activity

- `readme_path`
- `readme_link_md`
- `path_link_md`
- `last_touch_at`
- `head_branch`
- `head_commit`
- `head_commit_at`
- `last_remote_ref_at`
- `last_push_at`
- `last_session_at`
- `last_session_summary`
- `next_step`

## Scan/classification details

- `git`
- `obsidian`
- `markdown_file_count`
- `obsidian_note_count`
- `tree_scan_truncated`
- `readme_sha256`
- `include_reason`

The compatibility record mixes observed facts, declarations, inference, and project-level hints. That is compatibility debt, not a model to extend indefinitely.

---

# 1. Executable Wave 1 substrate

Current constants from `ledger/__init__.py`:

```text
COMPILER_VERSION                    = 0.2.0
MANIFEST_SCHEMA_VERSION             = 1.1.0
OBSERVATION_SCHEMA_VERSION          = 1.0.0
COMPAT_FLAT_SCHEMA_VERSION          = 0.1.0
CANONICAL_PROJECT_SCHEMA_VERSION    = 1.0.0
IDENTITY_DECISION_SCHEMA_VERSION    = 1.0.0
IDENTITY_EVIDENCE_SCHEMA_VERSION    = 1.0.0
REVIEW_QUEUE_SCHEMA_VERSION         = 1.0.0
```

Full compatibility/migration policy for these versions is still Gate B work. Unsupported future major versions must eventually fail/degrade explicitly rather than being guessed through.

## 1.1 Source registration

Each configured root has an effective `source_id`.

Production config uses explicit human-readable IDs. A deterministic derived ID exists only as a compatibility fallback.

Properties:

- source identity is independent of `roots[]` ordering;
- normal locator/path changes should preserve an intentionally assigned source ID;
- duplicate effective source IDs are invalid;
- `source_id` is not a project ID.

Current source record fields in the manifest:

```text
source_id
label
source_class          live | mirror | backup | inventory
discovery
machine_name          nullable
storage_scope         nullable
path
path_state            known | unavailable
status                available | unavailable
status_reason
freshness_state       currently conservative; usually unknown
probe_as_of           nullable current probe timestamp evidence
probe_fingerprint
artifacts[]
compat_snapshot_id
compat_snapshot_as_of
compat_snapshot_basis
```

### Current probe semantics

`probe_fingerprint` is a cheap fingerprint of the current source probe inputs available to Wave 1. For inventory-policy sources it includes inventory/policy artifact hashes. For live filesystems it currently includes locator/access/root-stat evidence and is **not a recursive content digest**.

It must not be interpreted as proof that all descendant content is unchanged.

## 1.2 Compatibility source/run snapshot

Compatibility scanner v0 emits one overall `generated_at` and does not preserve native per-source scan snapshots.

Wave 1 therefore creates:

```text
compat_snapshot_id = stable_id("snap", source_id, compat_output.generated_at)
```

with:

```text
compat_snapshot_as_of    = compat output generated_at
compat_snapshot_basis    = "compat_output.generated_at"
```

This is a bounded migration identity: it says which registered source + compatibility scan time an observation is associated with. It is deliberately independent of the source's *current* health probe.

Later source adapters may introduce stronger native snapshot/checkpoint identities.

## 1.3 Typed observation collection

Generated file:

```text
state/observations.json
```

Envelope:

```text
schema_version
compat_schema_version
compiler_version
run_id
compiled_at
observed_at
observation_count
observations[]
```

Each current typed observation contains:

```text
schema_version
observation_id
source_id
snapshot_id
source_resolution      resolved | unresolved
observed_at
project_key            nullable compatibility hint/claim
location               nullable
compat_entry           complete legacy record
```

### observation_id semantics

An observation is a **manifestation/location**, not a conceptual project.

The deterministic fallback basis is scoped by `source_id` and currently prioritizes:

1. `path_from_root`;
2. `path`;
3. `canonical_url`;
4. `remote_url`;
5. `project_key` only as fallback;
6. display `name` only as final fallback.

Changing a project-key claim does not rename an otherwise unchanged manifestation when a stronger location key exists.

If a compatibility `source_label` cannot be mapped to a registered source, compilation does not guess. It assigns deterministic provisional source/snapshot IDs, marks `source_resolution=unresolved`, and degrades manifest health.

## 1.4 System manifest

Generated file:

```text
state/system-manifest.json
```

Current top-level fields:

```text
schema_version
compiler_version
run_id
compiled_at
input_observed_at
config
health
counts
sources
capabilities
artifacts
limitations
```

### config

```text
path
digest
```

### health

```text
state                                  ok | degraded
source_unavailable_count
unresolved_observation_source_count
identity_review_count
```

### counts

```text
sources
observations
canonical_projects
review_items
```

Canonical project and identity-review counts are now concrete integers. Unimplemented capability counts should continue to use explicit null/absence rather than being silently coerced to zero.

### capabilities

Each capability has a state envelope:

```text
state          available | unavailable | degraded
reason_code    optional
detail         optional
```

Current available capabilities:

- `compat_observations`
- `typed_observations`
- `source_health`
- `canonical_projects`
- `review_queue`

Current explicit unavailable capabilities:

- `project_capsules` — `WAVE3_NOT_IMPLEMENTED`
- `change_feed` — `WAVE4_NOT_IMPLEMENTED`

Agents must prefer this executable capability map over inference from design prose.

## 1.5 New semantic state vocabulary

The new contracts reserve:

```text
known
unknown
unavailable
stale
absent
conflicted
not_applicable
```

Wave 1 uses this vocabulary selectively. The compatibility record still contains blank-string ambiguity. Later migration should replace that ambiguity field-by-field rather than reinterpret old blanks silently.

## 1.6 Validation/error behavior

`ledger validate` is strict and uses stable `LedgerConfigError.code` values for structural/config/artifact errors.

`ledger compile` validates structure but intentionally does not require every source artifact to be currently accessible. Unavailable sources are materialized as degraded source health so one broken source cannot erase estate orientation.

This difference is contractual, not accidental.

For exact current operational details see `docs/WAVE1-AGENT-SUBSTRATE.md`.

## 1.7 Executable canonical identity slice

Generated files:

```text
state/canonical-projects.json
state/review-queue.json
```

Durable identity decisions are committed compiler inputs:

```text
registry/identity-decisions.json
```

### Automatic identity authority

The first slice deliberately has one automatic merge rule:

```text
exact normalized repository remote
```

Examples such as HTTPS and SSH forms of the same GitHub remote normalize to one identity key.

The following **do not** automatically merge projects:

- display name;
- compatibility `project_key`;
- path similarity;
- README similarity;
- semantic/fuzzy similarity.

Those values may still be exact resolve referents. If an exact lower-authority referent maps to more than one canonical project, resolution returns ambiguity instead of guessing.

### Durable decision types

The versioned decision registry supports:

- `merge` — explicitly union two or more observation IDs;
- `split` — explicitly keep listed observation IDs in separate conceptual projects;
- `reject_match` — reject one specific observation pair;
- `alias` — attach an operator-approved exact referent to the project containing an observation;
- `canonical_key` — assign an operator-approved stable human-readable `project_key` to the project containing an observation (overrides the auto-derived key; never changes membership);
- `supersede` — mark a prior decision as inactive for this compile (append/supersede oriented, never silently rewritten).

Every decision carries a stable `decision_id` and may carry `rationale`, `evidence`
refs, `authority` (`operator` | `trusted_automation` | `reviewed_agent`), and
`decided_at`. A `supersede` decision references `supersedes_decision_id`; superseded
decisions no longer constrain compilation or evidence.

Missing observation references become bounded `DECISION_REFERENCE_UNAVAILABLE` review
items rather than failing unrelated identity compilation. Conflicting positive/negative
decisions become `DECISION_CONFLICT`. A `supersede` referencing an unknown decision
becomes a bounded `DECISION_SUPERSEDE_UNKNOWN` review.

### Canonical project artifact

Current per-project fields:

```text
schema_version
canonical_project_id
project_key
display_name
resolved_fields
observation_ids[]
identity_anchor
normalized_remotes[]
project_key_hints[]
referents[]
identity_evidence[]
merge_decision_ids[]
canonical_key_decision_id
```

Current canonical IDs are deterministic under unchanged identity evidence. A single normalized remote is the preferred anchor; explicit merge decisions anchor multi-remote/non-remote merged groups; otherwise a singleton observation anchors the project. A normalized remote is only used as the anchor when it is **unique to one project** across the whole compile — when a split/reject keeps two observations that share an exact remote in separate projects, each falls back to a membership-scoped anchor so the two projects never share a `canonical_project_id` (P2.5).

`resolved_fields` records claim provenance for each resolved canonical field (`project_key`, `display_name`): which observation/decision/evidence produced the value, so a cold agent can see *why* a canonical value exists without re-running archaeology. Provenance kinds are `decision` (canonical_key), `declaration` (project_key sidecar), `observed` (remote/name), or `derived` (fallback).

This first slice does not yet claim the full eventual immutable-ID registry semantics described later in this document.

### Review queue artifact

Current envelope:

```text
schema_version
run_id
compiled_at
review_count
items[]
```

Each review item is first-class (P2.6):

```text
review_id
code
state            (open)
severity         (warning | error)
affected_observation_ids[]
affected_decision_ids[]   (decision-scoped reviews only; empty otherwise)
detail
reason_automation_stopped
resolution_actions[]
evidence[]
```

`affected_observation_ids` stays present (possibly empty) for backward
compatibility. Decision-scoped reviews (e.g. `DECISION_SUPERSEDE_UNKNOWN`) carry a
non-empty `affected_decision_ids` referent so the review is about the decision, not
observations; that referent is fed into the `review_id` material so two distinct
decision-scoped reviews never collapse to one `review_id` (a data-loss bug fixed in
P2.6). Observation-scoped review IDs are unchanged.

Current review codes include:

- `PROJECT_KEY_AMBIGUOUS` (warning) — a project key resolves to more than one
  canonical project; computed on each project's *resolved* key (canonical_key override
  applied), so a distinct `canonical_key` disambiguates and clears it. A `merge` also
  clears it; a `reject_match` confirms distinctness but does not clear a shared human
  key (P2.8);
- `AUTO_MATCH_BLOCKED_BY_DECISION` (warning);
- `DECISION_REFERENCE_UNAVAILABLE` (warning);
- `DECISION_CONFLICT` (error);
- `DECISION_SUPERSEDE_UNKNOWN` (warning).

### Exact resolve contract

`ledger resolve <referent>` reads canonical project state and returns:

- `resolved` — one canonical project;
- `ambiguous` — more than one equally authoritative exact candidate;
- `unresolved` — no candidate.

Referent priority is explicit: canonical ID first; exact normalized remote/path/operator alias next; raw URL/project key next; compatibility key hints next; display name last.

CLI exit codes:

```text
0 resolved
3 ambiguous
4 unresolved
2 operational/config error
```

### 1.8 Identity evidence model

Implemented as programme P2.3. The identity evidence model *records and scores* why
observations unify (or do not). Evidence is descriptive — it does **not** change the
merge authority; exactly one automatic merge rule remains (exact normalized remote)
and split/reject decisions remain durable compiler inputs.

Generated file:

```text
state/identity-evidence.json
```

Envelope:

```text
schema_version          = IDENTITY_EVIDENCE_SCHEMA_VERSION
compiler_version
run_id
compiled_at
identity_evidence_count
by_project              keyed by the sorted observation membership (unique per project)
cross_project_weak_overlaps[]
```

Each `by_project` entry:

```text
canonical_project_id
observation_ids[]
summary
evidence[]
```

`summary` scores the project's unification:

```text
unifying_authority       exact_normalized_remote | explicit_decision | singleton | referential
strong_reason_code       EXACT_NORMALIZED_REMOTE | EXPLICIT_MERGE_DECISION | NONE
automatic                bool — merged by exact remote without an operator decision
member_count
weak_overlap_pair_count  intra-project pairs whose only overlap is weak referents
evidence_count
```

Each identity evidence record (also present per canonical project as
`identity_evidence[]` and `identity_evidence_summary`):

```text
identity_evidence_id     stable id (kind, member observations, value)
kind
strength                 strong | weak | negative
authority                observed | declared | decision
reason_code
observation_ids[]
value                    optional
detail                   optional
```

**Evidence kinds and strength** (also queryable via `ledger.evidence.evidence_kinds()`):

```text
strong
  normalized_remote            exact normalized repository remote (auto-merge authority)
  source_native_identity       manifestation anchored to a source + snapshot
  explicit_merge_decision      operator-approved union

weak  (referents only — never identity authority, never auto-merge)
  explicit_project_key         compatibility project_key declaration/hint
  raw_url                      non-normalizable network locator
  path_locator                 filesystem path
  path_migration               renamed/moved path alias
  display_name                 name (may collide across unrelated projects)
  readme_compound              readme/description/repo-nature presence
  name_similarity              same-name overlap
  semantic_similarity          fuzzy/project-key overlap
  cross_project_weak_overlap   weak hit between distinct projects

negative
  negative_decision            explicit split / reject_match
  conflicting_decision         positive decision blocked by a negative one
```

`cross_project_weak_overlaps` records weak hits or negative decisions between
observations in *distinct* projects, so evidence that was considered but deliberately
not used is visible and clearly weak. A negative decision dominates any overlap it
covers and stays categorized `negative`.

The manifest exposes `identity_evidence` as an available capability and an artifact,
and `counts.identity_evidence` as the evidence-record count.

> Note (resolved, P2.5): a split/reject that keeps two observations sharing an exact
> remote in separate projects no longer produces a shared `canonical_project_id` — the
> compiler falls back to a membership-scoped anchor for each, so IDs are distinct and
> stable. The evidence store keys `by_project` by observation membership, which remains
> lossless and deterministic regardless.

---

# 2. Broader target epistemic and canonical schema families

The following sections describe the broader target beyond the implemented first identity slice. Where a concept is already partially executable, the narrower contract in §1.7 is authoritative for current behavior.

## Common envelope direction

Durable/materialized entities should carry enough metadata to be interpreted without ambient context:

- `schema_version`
- stable entity ID
- `created_at` or `observed_at`
- `source_as_of` where different from ingestion time
- compiler/run ID for generated entities
- provenance/evidence references
- status/freshness/error metadata

Timestamps should be ISO 8601 with timezone. IDs must not depend on display formatting.

## 2.1 Source snapshot / native run

Target fields may include:

- `snapshot_id`
- `source_id`
- `observed_at`
- `source_as_of`
- adapter-native fingerprint/checkpoint
- config digest
- adapter version
- result: success, partial, unavailable, failed
- warnings/errors
- object/observation counts
- previous snapshot ID

A source being unavailable is not equivalent to an empty successful snapshot.

## 2.2 Claim

A typed statement about an observation or canonical project:

- `claim_id`
- `subject_type` / `subject_id`
- field/predicate
- typed value
- `claim_kind`: `observed`, `declared`, `inferred`
- source/evidence references
- `observed_at` / `as_of`
- authority class
- confidence for inference
- supersedes/invalidates references when appropriate
- optional freshness/expiry policy

Claims preserve why a canonical value exists.

## 2.3 Identity evidence

Evidence that observations are the same or different conceptual project. **Executable**
as §1.8 (`ledger/evidence.py`, `state/identity-evidence.json`); this section is the
broader intent behind the implemented slice.

- `identity_evidence_id`
- observation IDs involved
- evidence type
- score/confidence where applicable
- evidence source
- created/observed time
- explanation/reason code

Evidence types may include explicit canonical ID/key, normalized remote, source-native relation, README/hash facts, aliases, path migration, semantic similarity, and negative/conflict evidence.

Evidence is not itself a merge decision.

## 2.4 Identity decision

Durable explicit resolution (implemented in §1.7):

- `decision_id`
- decision type: merge, split, alias, reject-match, canonical-key assignment, supersede
- affected observation/canonical IDs
- resulting canonical ID if applicable
- rationale/evidence refs
- authority: operator, trusted automation, reviewed agent
- `decided_at`
- superseded-by reference

Decisions are compiler inputs and should be append/supersede oriented rather than silently rewritten.

## 2.5 Canonical project

A narrow canonical project is executable in §1.7. The broader durable entity target is:

- immutable `canonical_project_id`
- human-readable `project_key`
- aliases
- display name
- one-line purpose/description
- linked observation IDs
- resolved canonical repo/URL
- tags/classification
- lifecycle state
- resolved-field provenance map
- open conflict/review IDs
- created/first-seen/last-observed timestamps

A canonical project never discards its observation graph.

## 2.6 Current state

Mutable continuity data resolved for a canonical project:

- `canonical_project_id`
- lifecycle state
- last trusted session receipt ID
- `last_session_at`
- bounded `last_session_summary`
- `next_step`
- blocker/readiness pointers where project-level and not task-tree data
- preferred observation/location + ranking explanation
- state `as_of`
- freshness classification
- conflicting-state claims/review refs

Current state is compiled from declarations, receipts, and source evidence according to field-specific resolution policy.

## 2.7 Session receipt

Cross-system continuity record after material work:

- `receipt_id`
- canonical project ID or unresolved project referent
- timestamp
- actor/agent/execution identity when available
- workspace/node/observation reference
- base/result commit/branch refs when relevant
- bounded completed-work summary
- material files/areas changed
- verification/tests performed
- links to commit/PR/task/artifacts
- unresolved discoveries
- next continuation point
- landed/not-landed state
- confidence/provenance

Receipts should remain compact and factual.

## 2.8 Review item

First-class ambiguity/error requiring bounded resolution:

- `review_id`
- type/code
- severity/impact
- affected entity IDs (`affected_observation_ids[]`, `affected_decision_ids[]`)
- evidence refs/summary
- exact reason automated resolution stopped (`reason_automation_stopped`)
- candidate resolution actions (`resolution_actions[]`)
- confidence
- opened/updated timestamps
- state: open, resolved, superseded
- resolution decision ID when closed

The identity review queue (`state/review-queue.json`) implements the first-class
envelope for its current codes (severity, reason automation stopped, resolution
actions, decision-scoped referents). Confidence, timestamps, and the
resolved/superseded states are the P2.7 review-ratchet concern.

## 2.9 Change event

Derived semantic change between trusted compiled runs:

- `change_id`
- previous/current run IDs
- entity ID
- change type
- before/after references or compact values
- materiality/severity
- provenance

This powers delta-first agent operation.

---

# 3. Target agent materialized views

## Project capsule

Planned:

```text
state/projects/<canonical_project_id>.json
```

Suggested contents:

- canonical ID/key/aliases
- display name/purpose
- lifecycle/current state
- last trusted session + next step
- canonical URL/repo
- ranked observation/location summaries
- preferred location with reason codes
- freshest git facts
- freshness/as-of
- open review/conflict summaries
- task/knowledge pointers
- evidence/query pointers

The capsule should omit deep evidence payloads unless needed for normal orientation.

## Review queue / change feed

`state/review-queue.json` is implemented for the first identity slice.

Still planned:

```text
state/changes.json
```

The current review queue is identity-focused; broader current-state/schema review families remain later work.

---

# 4. Resolution policy

There is no global precedence order. Resolution is field-specific.

Each future canonical field policy should define:

- allowed candidate claim types;
- authority ordering;
- freshness behavior;
- conflict threshold;
- deterministic tie-breaking if safe;
- when to emit review instead of choosing;
- selected claim/evidence references.

Examples:

- identity merge decision outranks heuristic similarity;
- observed current git HEAD outranks stale prose;
- explicit display-name declaration outranks README-title inference;
- fresh trusted receipt may outrank older continuation metadata.

---

# 5. Identity invariants

1. `canonical_project_id` will be immutable and not derived from path/name/display formatting.
2. `project_key` is a human-readable alias, not canonical identity.
3. observation IDs and canonical IDs are different namespaces.
4. remotes/URLs/paths/names are identity evidence and aliases, not sufficient universal identity.
5. ambiguous matches create review items rather than silent merges.
6. split decisions are as important as merge decisions.
7. identity decisions must be explainable and regression-testable.
8. `project_hash` from compatibility output must never be promoted into a canonical project ID.

---

# 6. Sidecar evolution

Current sidecar fields remain compatibility inputs:

- `project_key`
- `display_name`
- `description`
- `status`
- `tags`
- `canonical_url`
- `repo_name`
- `shared`
- `storage_scope`
- `last_session_at`
- `last_session_summary`
- `next_step`
- `last_push_at`

Target changes should add explicit `schema_version` and eventually allow `canonical_project_id` only after the canonical registry can assign/validate it safely.

A sidecar is a declaration source attached to an observation. If multiple observations contain conflicting sidecars, emit claims/conflict/review; do not use last-write-wins.

---

# 7. Compatibility/versioning direction

These compatibility rules remain important hardening work. The first identity slice avoids depending on unfinished declaration semantics by using only exact observed remote identity plus explicit durable decisions:

1. preserve the flat compatibility output as an explicit v0 contract;
2. introduce typed entity collections beside it rather than silently changing field meanings;
3. validate generated schemas at compile boundaries;
4. define major/minor compatibility behavior and migration notes;
5. reject or explicitly degrade on unsupported major versions;
6. let generated view versions evolve without changing immutable entity identity.

## Recommendations for agents

- Never infer canonical identity from `project_hash`.
- Preserve stable source/observation IDs and aliases.
- Keep epistemic type/provenance when transforming data.
- Do not overwrite conflicts to produce a cleaner record.
- Prefer explicit unknown/stale/unavailable states to blanks.
- Use manifest capability states rather than assuming target design is implemented.
- Add a regression fixture whenever a real ambiguity or schema failure required substantial reasoning to resolve.
