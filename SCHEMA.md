# Schema

## Purpose

This document defines the current ledger fields and the target schema direction.

## Current Output Fields

### Identity

- `project_hash`
  - current meaning: deterministic hash of `project_key` fallback basis
  - type: string
- `project_key`
  - current meaning: best available stable key
  - source priority: sidecar, normalized remote URL, slugified display name
  - type: string

### Naming and classification

- `name`
- `project_type`
- `repo_name`
- `tags`
- `status`
- `description`

### Location and provenance

- `source_label`
- `machine_name`
- `storage_scope`
- `shared`
- `path`
- `path_from_root`
- `canonical_url`
- `remote_url`
- `sidecar_path`

### Navigation

- `readme_path`
- `readme_link_md`
- `path_link_md`

### Activity

- `last_touch_at`
- `head_branch`
- `head_commit`
- `head_commit_at`
- `last_remote_ref_at`
- `last_push_at`
- `last_session_at`
- `last_session_summary`
- `next_step`

### Classification and scan details

- `git`
- `obsidian`
- `markdown_file_count`
- `obsidian_note_count`
- `tree_scan_truncated`
- `readme_sha256`
- `include_reason`

## Current Sidecar Fields

Expected sidecar fields today:

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

## Precedence Rules

1. Sidecar wins for explicit operator-maintained fields.
2. Extractors fill in missing inferred metadata.
3. Derived fields such as `project_hash` are computed after precedence resolution.

## Known Schema Gaps

The current schema does not yet separate:

- machine-local observation records
- canonical merged project records
- identity aliases
- confidence scores
- review status

## Target Additions

### Observation-level fields

- `inventory_run_id`
- `observed_at`
- `root_path`
- `root_label`
- `host_fingerprint`
- `scan_errors`
- `access_warnings`

### Canonical-level fields

- `canonical_project_id`
- `aliases`
- `machine_observations`
- `identity_confidence`
- `review_state`
- `duplicate_group`

### Workflow fields

- `priority`
- `owner`
- `visibility`
- `archive_state`
- `last_human_review_at`

## Recommendations For Agents

1. Do not overload `project_hash` with semantic meaning beyond current implementation.
2. Preserve `project_key` stability once a human or sidecar sets it.
3. Add versioning before making breaking schema changes.
4. When merged canonical data is introduced, keep observation records intact rather than flattening away source context.
