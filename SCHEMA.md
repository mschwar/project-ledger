# Schema

## Purpose

This document is the schema authority for Project Ledger. It distinguishes:

1. the legacy flat compatibility record;
2. the **currently implemented Wave 1 typed substrate**;
3. the later canonical-project/current-state schema families that remain design targets.

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
COMPILER_VERSION            = 0.1.0
MANIFEST_SCHEMA_VERSION     = 1.0.0
OBSERVATION_SCHEMA_VERSION  = 1.0.0
COMPAT_FLAT_SCHEMA_VERSION  = 0.1.0
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
```

### counts

```text
sources
observations
canonical_projects     null until Wave 2
review_items           null until Wave 2
```

`null` means capability not implemented/known. It must not be converted to `0`.

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

Current explicit unavailable capabilities:

- `canonical_projects` — `WAVE2_NOT_IMPLEMENTED`
- `review_queue` — `WAVE2_NOT_IMPLEMENTED`
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

---

# 2. Target epistemic and canonical schema families

The following layers are **not yet executable capabilities** unless explicitly stated otherwise.

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

Evidence that observations are the same or different conceptual project:

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

Durable explicit resolution:

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

Durable compiled conceptual entity:

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
- affected entity IDs
- evidence refs/summary
- exact reason automated resolution stopped
- candidate resolution actions
- confidence
- opened/updated timestamps
- state: open, resolved, superseded
- resolution decision ID when closed

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

Planned:

```text
state/review-queue.json
state/changes.json
```

They are not implemented in Wave 1.

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

Gate B must finish these rules before Wave 2 depends on them:

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
