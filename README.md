# Project Ledger

Project Ledger builds a multi-source ledger of project observations across plain directories, git repos, Obsidian vaults, machine mirrors, backups, and inventory/policy-backed storage surfaces.

It is evolving from a scanner into the project-reality layer for the homelab: discover observations, preserve provenance, resolve durable project identity, maintain a thin current-state pointer, and expose trustworthy project metadata to people and agents.

The scanner emits:

- `output/projects.csv`
- `output/projects.json`
- `output/projects.md`
- `docs/ledgers/projects-ledger.md` (committed human-facing ledger mirror)

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

Project reality is not one neat repo. It is spread across live working roots, mirrors, backups, cloud-drive inventories, git remotes, and project-local metadata.

The observation ledger gives one place to answer:

- What project-like things exist?
- Where and on which source/machine were they observed?
- Is an observation a git repo or Obsidian vault?
- When was it last touched?
- Where is its README or canonical URL?
- What was the last session summary / next step?
- Which observations may represent the same durable project?

Today the scanner is strong at the observation layer. Canonical cross-source project merge is the next major architecture milestone.

## Run it

```powershell
python build_ledger.py
```

Optionally:

```powershell
python build_ledger.py --config ledger_config.json --output-dir output
```

Run tests:

```powershell
python -m unittest tests.test_build_ledger
```

CI runs the unit test suite on pushes and pull requests.

## How discovery works

`ledger_config.json` defines scan roots. Supported discovery modes are:

- `children`: inspect each direct child directory and keep the ones that look project-like
- `git_repos`: recursively find directories that contain `.git`
- `self`: treat the configured root itself as one observation
- `inventory_policy`: read a durable inventory artifact plus a policy file, then promote only roots marked for project discovery

### Current operator config

The committed config is environment-specific and currently references the owner's homelab/project surfaces, including:

- `/central` app/infra/service/git/project/active roots
- selected MacBook mirror roots under the central registry
- Matty-PC inventory/policy data
- Google Drive inventory/policy data
- selected standalone roots and backups

Ordinary missing/unreadable filesystem roots are handled safely and reported in scan coverage. `inventory_policy` roots require readable `inventory_jsonl` and `policy_path` artifacts and fail early with a clear validation error when those required metadata inputs are missing.

If you are operating on another machine, provide an appropriate config rather than assuming these host-specific paths exist there.

## Inventory-policy rule

For Google Drive, Matty-PC inventory, or similar durable inventories, prefer policy-backed promotion instead of indiscriminate recursive scanning.

Configure:

- `path`: mounted/navigation root when available
- `inventory_jsonl`: durable metadata inventory
- `policy_path`: classification/promotion policy
- `policy_crawl_treatments`: normally `["project_discovery"]`
- `require_project_ledger_candidate`: normally `true`

This implements the control-plane rule:

**inventory discovers reality → policy decides relevance → Project Ledger promotes project observations**

The physical mount may be absent while inventory metadata remains useful; the required inventory and policy artifacts themselves must exist.

## Important field notes

- `project_hash`: deterministic hash of the current `project_key`; do not treat it as a future canonical-project ID
- `project_key`: best stable observation/project key the scanner can currently infer or receive from a sidecar
- `source_label`: where/how the observation entered the ledger
- `machine_name`: machine attribution when available
- `last_touch_at`: newest filesystem/inventory timestamp seen under the observed project tree
- `last_push_at`: not reliably inferable from local git; set manually only when justified
- `last_remote_ref_at`: fallback based on local remote-tracking refs
- `include_reason`: explainable evidence for why an observation was included

The current schema does not yet fully separate machine/source observations from canonical merged projects. See `SCHEMA.md` and `ROADMAP.md`.

## Per-project sidecar

If a live project root contains `.project-ledger.json`, the scanner merges it into auto-discovered metadata.

Use this for facts that inference cannot safely own:

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

This is intentionally a thin current-state layer. Project Ledger should expose work context, not become a second general-purpose task manager.

## Session-end workflow

Use [`prompts/session_end_prompt.md`](prompts/session_end_prompt.md) as the last instruction to an agent before ending work in a repo/vault/project.

It keeps project-local state useful by refreshing:

- current status
- exact next step
- factual session summary
- push metadata only when a push actually occurred

## Identity direction

Current identity evidence uses:

1. explicit sidecar `project_key`
2. normalized git remote when available
3. source-specific stable inventory key or name/slug fallback

The target model will separate observation identity from canonical project identity, retain aliases/provenance, assign confidence/review state, and surface ambiguity instead of silently collapsing it.

## Adding another source or machine

Prefer one of these patterns:

1. scan a live/mirrored filesystem root with `children`, `git_repos`, or `self`
2. ingest a durable external inventory with `inventory_policy`

Always give roots useful labels and machine/storage provenance where possible. Avoid adding ingestion surfaces faster than duplicate/canonical identity can be reviewed.
