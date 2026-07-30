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
python -m unittest tests\test_build_ledger.py
```

## Before Making Changes

1. Read `AGENTS.md`, `PRD.md`, `ARCHITECTURE.md`, and `SCHEMA.md`.
2. Decide whether the change is:
   - implementation only
   - schema affecting
   - workflow affecting
3. If schema or workflow changes, update the relevant docs in the same task.

## When Adding A New Scan Root

1. Edit `ledger_config.json`.
2. Set a useful `label`.
3. Choose the correct `discovery` mode.
4. Add `exclude_names` for obvious noise.
5. Add `force_include_names` if important directories are low-signal.
6. Run the scanner and inspect the Markdown output before trusting the results.

For inventory-backed cloud roots:
1. Keep `path` pointed at the mounted folder only for operator navigation.
2. Set `discovery` to `inventory_policy`.
3. Point `inventory_jsonl` at the durable inventory artifact.
4. Point `policy_path` at the matching root-policy file.
5. Restrict `policy_crawl_treatments` to the intended promotion set, usually `project_discovery`.
6. Verify the resulting entries carry `google-drive:` style project keys and a source label that preserves the policy root.

## When A Project Is Missing

Check in this order:

1. Was the parent root included in `ledger_config.json`?
2. Was the directory excluded accidentally?
3. Does it have enough signals to pass the current score threshold?
4. Should it be added to `force_include_names`?
5. Should a sidecar be created to make it explicit?

## When A Project Identity Is Wrong

Short-term fix:

1. create or update `.project-ledger.json`
2. set stable `project_key`
3. set `display_name` and `canonical_url` if useful
4. rerun the scanner

Long-term fix:

- improve identity logic
- add regression tests
- document the new rule in `SCHEMA.md` or `ARCHITECTURE.md`

## When Changing The Schema

Required updates:

1. code
2. `SCHEMA.md`
3. sidecar example template if relevant
4. tests
5. README or runbook if operator-visible behavior changed

## Session-End Practice

For downstream project repos, the expected closing move is:

1. update `.project-ledger.json`
2. write factual `last_session_summary`
3. write one clear `next_step`
4. only set `last_push_at` if a push actually happened

Reference: `prompts/session_end_prompt.md`

## Known Limitations

- actual remote push time is not inferable from local git alone
- merge logic across machines is not implemented yet
- current discovery heuristics are intentionally simple
- unreadable filesystem entries are skipped and surfaced only through scan-gap reporting
- current tests are still prototype-grade

## Handoff Checklist

Before ending a work session in this repo:

1. run tests
2. run the scanner if behavior changed
3. confirm docs are still accurate
4. leave the next step obvious in docs or backlog
