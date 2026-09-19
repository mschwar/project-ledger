# R0 — Live Provider Proof (evidence receipt)

Date: 2026-09-19 (UTC)
Node: ai-server (`100.127.139.63`), the homelab node that owns the Project Ledger provider seam.
Operator: Hermes child node (Matthews-MacBook-Air-3) driving ai-server over Tailscale SSH.

## What ran

The deployed homelab Project Ledger watchdog against current `main`:

```bash
PROJECT_LEDGER_REPO=/home/prime/project-ledger \
  bash /central/repos/infra/homelab/scripts/project-ledger-discovery-watchdog.sh
```

- Project Ledger commit: `bf04d7b` (`feat: cross the canonical identity walking skeleton (#7)`)
- Watchdog result: `project-ledger watchdog refreshed observations=132 sources=14 health=ok changed=yes`
- Run ID: `run_4c8e9084d4ca23637eac9267`
- Compiled at: `2026-09-19T04:56:00Z`
- Compiler version: `0.2.0`; schema version: `1.1.0`

## Health and counts

| Metric | Value |
|---|---|
| health.state | `ok` |
| sources | 14 (all `available`) |
| observations | 132 |
| canonical_projects | 129 |
| review_items | 1 |
| source_unavailable_count | 0 |
| unresolved_observation_source_count | 0 |

Available capabilities: `canonical_projects`, `compat_observations`, `review_queue`,
`source_health`, `typed_observations`.
Unavailable (expected, not yet implemented): `change_feed` (WAVE4_NOT_IMPLEMENTED),
`project_capsules` (WAVE3_NOT_IMPLEMENTED).

## Source health

All 14 configured sources reported `available` (`root_accessible` / `inventory_artifacts_available`).
No source was unavailable or degraded in this run.

## Deployment defect fixed to reach this proof

The committed `ledger_config.json` on `main` still contained three dead roots that
abort the watchdog under `set -euo pipefail`:

- `project-ledger-live` → `/home/matt/project-ledger` (mode 750, owned `matt`, not readable by `prime`)
- `google-drive-inventory` → `/home/matt/GoogleDrive` (same)
- `opt-homelab` → `/opt/homelab` (does not exist)

Per the ai-server host-config convention, these were removed from the **seam's**
`/home/prime/project-ledger/ledger_config.json` as a host-specific **uncommitted** edit
(repo stays on `main`; `output/` and `state/` are gitignored). The committed upstream
config still lists them — a candidate follow-up to reconcile the template with the
live host (mark `[Needs validation]` rather than inventing them).

## Representative identity cases (candidate fixtures)

### 1. Multi-manifestation → correctly merged by exact remote

- **homelab** → `prj_3988fba6a0c8cf990a577bb8`
  - `obs_cc508b56483db8d8e146107a` — `/central/repos/infra/homelab` (central-infra, `git@github.com:mschwar/homelab.git`)
  - `obs_f0c43e37fa8e90f8dafd9d52` — `/central/registry/mirrors/matthews-macbook-air-2/Documents/homelab` (macbook mirror, `https://github.com/mschwar/homelab.git`)
- **white-rabbit** → `prj_8d14c04e0a62ad084fccf416`
  - `obs_128ca37a8e8f71df0611b79b` — `/central/white-rabbit` (white-rabbit, `https://github.com/mschwar/white-rabbit.git`)
  - `obs_9ddd0040fce7668f9c3aa442` — `/central/repos/active/white-rabbit` (central-active, same remote)
- **reality-ledger** → `prj_981bd44f515a9c435684919c`
  - `obs_d04229543f0f27e9cda304c7` — `/central/projects/reality-ledger` (central-projects)
  - `obs_e78c1dfefc3786c34da9bf7a` — `/central/repos/active/reality-ledger` (central-active)

### 2. Same-name / ambiguous → correctly NOT merged, review opened

- **`the-garden`** → `rev_08eb96a0b8b7722ac70b2a20` (code `PROJECT_KEY_AMBIGUOUS`, state `open`)
  - `obs_ae0625819f15a3d3b080671f` — "The Garden", `/central/git/the_garden` (central-git)
  - `obs_f6554456613bfca1cfa76246` — "AGENTS.md - Your Workspace", `/central/repos/active/the_garden` (central-active)
  - Detail: "Compatibility project_key 'the-garden' resolves to 2 canonical projects; no automatic merge was performed."

### 3. Same-name observations in different canonical projects

None found in this run (no false merges by name).

## Evidence artifacts

- `/central/registry/reports/project-ledger/watchdog/system-manifest.json`
- `/central/registry/reports/project-ledger/watchdog/orient.json`
- `/central/registry/reports/project-ledger/watchdog/sources.json`
- `/central/registry/reports/project-ledger/watchdog/projects.json` / `.md` / `.csv`
- `/central/registry/reports/project-ledger/watchdog/semantic-snapshot.json`
- `/tmp/r0state/` on ai-server: `canonical-projects.json`, `observations.json`, `review-queue.json` (ephemeral; regenerable via `ledger refresh`)

## Continuation point

R0 has produced current production evidence against the intended estate. Per the
Reality-to-Identity programme, the next software constraint should now be **synthesized
from demonstrated use** — do not automatically start R3 merely because it is next in the
design sequence. Candidate fixtures above (multi-manifestation merge, `the-garden`
ambiguity) are ready to become regression fixtures for the identity compiler.
