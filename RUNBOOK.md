# Runbook

## Purpose

This runbook explains how to operate and extend `project-ledger` safely.

## Standard Commands

Run the scanner:

```powershell
python build_ledger.py
```

Run tests:

```powershell
python -m unittest tests.test_build_ledger
```

CI also compiles `build_ledger.py` and runs the unit test suite on pushes and pull requests.

## Authority Rule

`main` is the authoritative product branch. Do not leave substantial working behavior stranded indefinitely on a feature branch.

For normal development:

1. create a focused branch/workspace
2. implement the bounded change
3. add/update tests and docs
4. obtain review/QA
5. resolve findings
6. merge promptly

## Before Making Changes

1. Read `AGENTS.md`, `PRD.md`, `ARCHITECTURE.md`, and `SCHEMA.md`.
2. Decide whether the change affects:
   - implementation only
   - schema/identity
   - source ingestion
   - operator workflow
3. Update tests/docs in the same task when behavior or contracts change.

## When Adding A Live Filesystem Root

1. Edit the appropriate config.
2. Set a useful `label`.
3. Choose `children`, `git_repos`, or `self`.
4. Add machine/storage provenance when it is not inferable.
5. Add `exclude_names` for obvious noise.
6. Add `force_include_names` only for known low-signal projects.
7. Run the scanner and inspect the Markdown output before trusting the results.

Missing/unreadable ordinary filesystem roots are skipped safely and shown as gaps in the Markdown coverage section.

## When Adding An Inventory-Policy Root

Use this for durable inventories such as Google Drive or remote machine inventories where direct recursive scanning is undesirable.

1. Keep `path` pointed at the mount/navigation root when one exists.
2. Set `discovery` to `inventory_policy`.
3. Set `inventory_jsonl` to the durable inventory artifact.
4. Set `policy_path` to the matching root-policy file.
5. Restrict `policy_crawl_treatments` to the intended promotion set, normally `project_discovery`.
6. Normally require `project_ledger_candidate` policy promotion.
7. Set `remote_name`, `machine_name`, and `storage_scope` as appropriate.
8. Verify resulting observations preserve source/policy provenance and stable source-specific keys.

`inventory_jsonl` and `policy_path` are required inputs. Missing fields or missing artifacts fail with an operator-readable `ValueError` before candidate discovery.

## When A Project Is Missing

Check in this order:

1. Was the source/root included in config or upstream policy?
2. Was the directory/root excluded accidentally?
3. For `children`, does it have enough project signals to pass the score threshold?
4. For `inventory_policy`, was the root promoted with the expected crawl treatment/candidate flag?
5. Should a known low-signal live directory be added to `force_include_names`?
6. Should a sidecar make the project explicit?

## When Identity Looks Wrong Or Duplicated

Short-term correction for a live project:

1. create/update `.project-ledger.json`
2. set a stable `project_key`
3. set `display_name` and `canonical_url` if useful
4. rerun the scanner

Do not assume two same-name observations are one project, and do not silently collapse uncertain matches.

Long-term identity work belongs in the canonical merge/identity layer described by `ROADMAP.md` and `SCHEMA.md`.

## When Changing The Schema

Required updates:

1. code
2. `SCHEMA.md`
3. sidecar example template if relevant
4. tests
5. README/runbook if operator-visible behavior changed
6. schema version/migration policy once versioning lands

## Session-End Practice

For downstream project repos:

1. update `.project-ledger.json`
2. write factual `last_session_summary`
3. write one clear `next_step`
4. only set `last_push_at` if a push actually happened

Reference: `prompts/session_end_prompt.md`.

Project Ledger's sidecar is a current-state pointer, not a full task queue. Rich task lifecycle should remain in the execution/task system.

## Refreshing The Canonical Human-Facing Ledger

On a machine that has access to the intended operator roots:

1. pull current `main`
2. run tests
3. run `python build_ledger.py`
4. inspect `docs/ledgers/projects-ledger.md`
5. review configured-root gaps and obvious duplicate observations
6. commit refreshed generated mirror only when it represents an intentional snapshot/update

Do not claim a scan is current merely because code changed; the committed Markdown mirror has its own generation timestamp.

## Known Limitations

- actual remote push time is not inferable from local git alone
- observation records and canonical projects are not yet separate output entities
- canonical multi-source merge is not implemented yet
- current discovery heuristics are intentionally simple
- current operator config contains environment-specific roots
- scan snapshots can become stale if they are not regenerated on the real source surfaces

## Handoff Checklist

Before ending substantive work in this repo:

1. run/confirm CI tests
2. run the scanner if behavior changed and the required source surfaces are available
3. confirm docs are accurate
4. leave the next step obvious in the repo sidecar/backlog
5. merge the completed bounded work so `main` remains authoritative
