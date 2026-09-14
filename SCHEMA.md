# Schema

## Purpose

This document defines the current compatibility fields and the target typed data model implementing `SYSTEM.md`.

The target schema is intentionally layered. Raw observations, declarations, inferences, decisions, canonical projects, current state, and agent views are different entity classes and must not collapse into one flat record.

## Current compatibility schema

The existing scanner emits one flat project-like record. Keep this stable while migration begins.

### Current identity

- `project_hash` — deterministic hash of current `project_key` basis; **not** a future canonical project ID.
- `project_key` — best current human/stable key from sidecar, remote URL, inventory key, or fallback slug.

### Current naming/classification

- `name`
- `project_type`
- `repo_name`
- `tags`
- `status`
- `description`

### Current provenance/location

- `source_label`
- `machine_name`
- `storage_scope`
- `shared`
- `path`
- `path_from_root`
- `canonical_url`
- `remote_url`
- `sidecar_path`

### Current navigation/activity

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

### Current scan/classification details

- `git`
- `obsidian`
- `markdown_file_count`
- `obsidian_note_count`
- `tree_scan_truncated`
- `readme_sha256`
- `include_reason`

The flat compatibility record currently mixes observation facts, declarations, inference, and project-level state. That is the primary schema debt to remove.

---

# Target schema families

## Common envelope

Every durable/materialized entity should carry enough metadata to be interpreted without ambient context.

Recommended common fields where applicable:

- `schema_version`
- stable entity ID
- `created_at` or `observed_at`
- `source_as_of` where different from ingestion time
- compiler/run ID for generated entities
- provenance/evidence references
- status/freshness/error metadata

Timestamps should be ISO 8601 with timezone. IDs should never depend on display formatting.

## 1. Source

A registered sensing surface.

Suggested fields:

- `source_id` — immutable stable ID
- `source_type` — filesystem, git-root, inventory-policy, mirror, backup, cloud-inventory, etc.
- `label`
- `machine_id` / node identity when relevant
- locator/config reference
- `storage_scope`
- source class: live, mirror, backup, inventory-only, other
- adapter/version
- freshness policy
- capability/access metadata
- enabled/disabled state

Source configuration is an input to compilation.

## 2. Snapshot / run

One bounded sensing result.

Suggested fields:

- `snapshot_id`
- `source_id`
- `observed_at`
- `source_as_of`
- source fingerprint/digest
- config digest
- adapter version
- result: success, partial, unavailable, failed
- warnings/errors
- object/observation counts
- previous snapshot ID

A source being unavailable is not equivalent to an empty successful snapshot.

## 3. Observation

A normalized manifestation of a project-like entity within a source.

Suggested fields:

- `observation_id`
- `snapshot_id`
- `source_id`
- source-native ID if available
- observed locator/path
- normalized location
- project type signals
- git/vault flags
- repo/remote facts
- README/hash/title facts
- timestamps/counts
- access state
- include evidence/reason
- references to claims/declarations discovered with this observation

`observation_id` should be stable across snapshots where the source exposes a stable native ID; otherwise use a documented deterministic basis and retain aliases when paths migrate.

## 4. Claim

A typed statement about an observation or canonical project.

Suggested fields:

- `claim_id`
- `subject_type` / `subject_id`
- `field` / predicate
- typed value
- `claim_kind`: `observed`, `declared`, `inferred`
- source/evidence references
- `observed_at` / `as_of`
- authority class
- confidence for inference
- supersedes/invalidates references when appropriate
- optional freshness/expiry policy

Claims let canonical resolution preserve why a value exists.

## 5. Identity evidence

Evidence that observations are the same or different conceptual project.

Suggested fields:

- `identity_evidence_id`
- observation IDs involved
- evidence type: explicit canonical ID, explicit project key, normalized remote, source-native relation, README hash, alias, name similarity, path migration, semantic similarity, negative/conflict evidence
- score/confidence where applicable
- evidence source
- created/observed time
- explanation/reason code

Evidence is not itself a merge decision.

## 6. Identity decision

A durable explicit resolution.

Suggested fields:

- `decision_id`
- decision type: merge, split, alias, reject-match, canonical-key assignment, supersede
- affected observation/canonical IDs
- resulting canonical ID if applicable
- rationale/evidence refs
- authority: operator, trusted automation, reviewed agent
- `decided_at`
- superseded-by reference

Decisions are compiler inputs and should be append/supersede oriented rather than silently rewritten.

## 7. Canonical project

The durable compiled conceptual entity.

Suggested fields:

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

## 8. Current state

Mutable continuity data resolved for a canonical project.

Suggested fields:

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

## 9. Session receipt

A cross-system continuity record after material work.

Suggested fields:

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

## 10. Review item

A first-class ambiguity/error requiring bounded resolution.

Suggested fields:

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

## 11. Change event

A derived semantic change between trusted compiled runs.

Suggested fields:

- `change_id`
- previous/current run IDs
- entity ID
- change type
- before/after references or compact values
- materiality/severity
- provenance

This powers delta-first agent operation.

---

# Agent materialized views

## System manifest

`state/system-manifest.json`

Suggested fields:

- schema/compiler versions
- `run_id`
- generated/as-of timestamps
- config digest
- source health/freshness summaries
- canonical project count
- observation count
- review counts by type/severity
- material change count
- pointers to canonical datasets/views
- supported query/control capabilities

## Project capsule

`state/projects/<canonical_project_id>.json`

Suggested fields:

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

The capsule should intentionally omit deep evidence payloads unless needed for normal orientation.

## Review queue / change feed

`state/review-queue.json` and `state/changes.json` are indexed collections of the entities above, with run/schema metadata.

---

# Null and state semantics

Avoid using `""` as a universal missing value in the new schema.

For material fields distinguish:

- unknown — no reliable evidence exists;
- unavailable — source cannot currently be read;
- stale — value exists but exceeds freshness policy;
- absent — source was successfully checked and value/object was absent;
- conflicted — credible candidate claims disagree;
- not_applicable — field has no meaning for this entity;
- known — value is resolved within policy.

Representation may use explicit status envelopes or companion fields; choose one consistent versioned convention.

---

# Resolution policy

There is no global precedence order. Resolution is field-specific.

Each canonical resolved field should have a named/testable policy defining:

- candidate claim types allowed;
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

# Identity rules

1. `canonical_project_id` is immutable and not derived from path/name/display formatting.
2. `project_key` is a human-readable alias and should remain stable once intentionally assigned, but can be superseded with alias preservation.
3. observation IDs and canonical IDs are different namespaces.
4. remotes/URLs/paths/names are identity evidence and aliases, not sufficient universal identity.
5. ambiguous matches create review items.
6. split decisions are as important as merge decisions.
7. identity decisions must be explainable and regression-testable.

---

# Sidecar evolution

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

# Compatibility/versioning policy direction

Before executable target schema lands:

1. version the existing observation JSON envelope;
2. preserve current flat export as `v0`/compatibility output;
3. introduce new entity collections beside it rather than silently changing meanings;
4. validate schemas at compile boundaries;
5. include migration notes for breaking changes;
6. keep generated view versions independent enough to evolve without changing immutable IDs.

Agents should reject or explicitly degrade on unsupported major schema versions rather than guessing.

## Recommendations for agents

- Never infer canonical identity from `project_hash`.
- Preserve stable keys/IDs and aliases.
- Keep epistemic type/provenance when transforming data.
- Do not overwrite conflicts to produce a cleaner record.
- Prefer explicit unknown/stale/unavailable states to blanks.
- Add a regression fixture whenever a real ambiguity or schema failure required substantial reasoning to resolve.
