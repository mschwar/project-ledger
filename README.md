# Project Ledger

This project builds a ledger for mixed project roots: plain directories, git repos, and Obsidian vaults.

The scanner is config-driven and emits:

- `output/projects.csv`
- `output/projects.json`
- `output/projects.md`

## Handoff Docs

These files are the operating packet for future agents:

- `AGENTS.md`
- `PRD.md`
- `ARCHITECTURE.md`
- `SCHEMA.md`
- `ROADMAP.md`
- `BACKLOG.md`
- `RUNBOOK.md`

## Why this exists

Your source material is not one neat repo. It is spread across working folders, imported machine backups, vaults, and nested repos. The ledger gives you one place to answer:

- What exists?
- Is it local or shared?
- Is it a git repo?
- Is it an Obsidian vault?
- When was it last touched?
- Where is the README?
- What is the remote URL or canonical location?
- What was the last session summary / next step?

## Run it

```powershell
python build_ledger.py
```

Optionally:

```powershell
python build_ledger.py --config ledger_config.json --output-dir output
```

## How discovery works

`ledger_config.json` defines scan roots.

- `children`: inspect each direct child directory and keep the ones that look project-like
- `git_repos`: recursively find directories that contain `.git`
- `self`: treat the root itself as one ledger entry
- `inventory_policy`: read a durable inventory artifact plus a policy file, then promote only the roots marked for ledger ingestion

The default config scans:

- the top-level children of `Documents`
- nested git repos under `Documents/repos`

Adjust the roots and excludes as needed for other machines, synced folders, backup dumps, or inventory-backed cloud surfaces.

For Google Drive or similar cloud-drive inventories, prefer a policy-backed root instead of recursive mount discovery. Point one root at the mounted folder for operator navigation, but drive candidate selection from:
- `inventory_jsonl`: durable metadata inventory
- `policy_path`: root classification policy
- `policy_crawl_treatments`: usually `["project_discovery"]`

That keeps project-ledger ingestion aligned with the homelab control-plane rule: inventory first, policy second, project promotion third.

If a directory matters but does not have enough obvious signals, add it under `force_include_names` for that root.

## Important field notes

- `project_hash`: deterministic hash used as a ledger identifier
- `project_key`: the best stable key the scanner can find
- `last_touch_at`: newest filesystem timestamp seen under the project tree
- `last_push_at`: not knowable reliably from local git alone; this is best filled manually in the sidecar when a session actually pushes
- `last_remote_ref_at`: fallback based on local remote-tracking refs

If you want the same project to resolve cleanly across multiple computers, set a stable `project_key` once in the sidecar file.

## Per-project sidecar

If a project root contains `.project-ledger.json`, the scanner merges it into the auto-discovered metadata.

Use this for fields that the scanner cannot infer well:

- stable `project_key`
- `display_name`
- `status`
- `tags`
- `canonical_url`
- `shared`
- `last_session_at`
- `last_session_summary`
- `next_step`
- `last_push_at`

See [`templates/project-ledger.sidecar.example.json`](templates/project-ledger.sidecar.example.json).

## Session-end workflow

Use the copy/paste prompt in [`prompts/session_end_prompt.md`](prompts/session_end_prompt.md) as the last instruction to an agent before it ends work in a repo/vault/project.

That prompt is designed to keep the ledger useful over time by forcing a final sidecar refresh with:

- current status
- exact next step
- session summary
- push metadata if relevant

## Adding other machines

You have two straightforward options:

1. Run this project separately on each machine and merge the outputs later.
2. Point `ledger_config.json` at imported backups or synced roots from other machines.

If you use option 2, set a root label like `macbook-air-backup` so the ledger keeps source context.

Example:

```json
{
  "path": "../Mac",
  "label": "macbook-air-backup",
  "discovery": "children",
  "force_include_names": ["Notes", "Ideas", "Research Vault"]
}
```
