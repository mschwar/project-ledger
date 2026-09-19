# Future Spec — Reconcile committed `ledger_config.json` template with live ai-server roots

- Status: **landed** (2026-09-19, commit on `main`; see `git log` for the reconcile commit)
- Date: 2026-09-19 (UTC)
- Node: ai-server (`100.127.139.63`), the homelab node that owns the Project Ledger provider seam
- Author: Hermes child node (Matthews-MacBook-Air-3), driving ai-server over Tailscale SSH
- Extends: `docs/work/R0-LIVE-PROVIDER-PROOF-20260919.md` (the R0 live-provider proof that surfaced this defect)

## Summary

The **committed** `ledger_config.json` on `main` still lists three roots that abort the deployed
homelab watchdog (`project-ledger-discovery-watchdog.sh`) under `set -euo pipefail`. During R0
these were removed only as a host-specific **uncommitted** seam edit on ai-server
(`/home/prime/project-ledger/ledger_config.json`); the committed upstream template still lists
them. This spec proposes reconciling the committed template with the live host, marking the
removals `[Needs validation]` rather than inventing replacements.

## The three dead roots (verified live 2026-09-19)

| `source_id` | `path` | Why it aborts the watchdog |
|---|---|---|
| `project-ledger-live` | `/home/matt/project-ledger` | mode 750, owned `matt`, not readable by `prime` (`ls` → `Permission denied`) |
| `google-drive-inventory` | `/home/matt/GoogleDrive` | same — mode 750, owned `matt`, not readable by `prime` |
| `opt-homelab` | `/opt/homelab` | does not exist (`ls` → `No such file or directory`) |

Live verification (as `prime` on ai-server, 2026-09-19):

```text
$ ls -ld /home/matt/project-ledger   -> Permission denied
$ ls -ld /home/matt/GoogleDrive      -> Permission denied
$ ls -ld /opt/homelab                -> No such file or directory
```

Historical root cause (brain page `reports/project-ledger/access-and-rescan-20260818`): on
2026-08-15 both `/home/prime` and `/home/matt` were rebuilt (fresh homes, mode 750). `/home/matt`
holds only default dotfiles; neither `project-ledger` nor `GoogleDrive` exists there. `/opt/homelab`
has never been a live root on this host. These are off-host / mirror / dead paths that belong in
the `[Needs validation]` class, exactly as the 2026-08-18 rescan already documented for the
`matty-pc:*` and Mac live-scan roots.

## Current state (ground truth)

- **Committed template** (`main`, `ledger_config.json`): still lists all three roots. Confirmed via
  `git show HEAD:ledger_config.json` on the canonical checkout.
- **Live seam config** (`/home/prime/project-ledger/ledger_config.json`): the three roots are
  removed as a host-specific **uncommitted** edit (`git status` shows ` M ledger_config.json`).
  Live root set (14 sources): `central-apps`, `central-infra`, `central-services`, `central-git`,
  `central-projects`, `central-active`, `proxy-lead`, `white-rabbit`,
  `macbook-air-2-hermes-paperclip-adapter`, `macbook-air-2-higgsfield-agent-project`,
  `macbook-air-2-homelab`, `macbook-air-2-local-deep-research`, `matty-pc-inventory`,
  `backup-20260416-repos`.
- **Watchdog** (`scripts/project-ledger-discovery-watchdog.sh`): `set -euo pipefail`; runs
  `build_ledger.py --config $PROJECT_LEDGER_CONFIG`. A dead root makes `build_ledger.py` exit
  non-zero, which aborts the watchdog before it can refresh the snapshot. The R0 proof reached
  `health=ok` only because the seam config had the roots removed.

## Ownership boundary (why this is a spec, not a direct edit)

- **ai-server owns the deployed reality-provider seam** (the host-specific path set). Per
  `AGENTS.md`: "homelab owns the deployed reality-provider seam and consumes the non-mutating
  refresh contract; Project Ledger continues to own source/project schema and identity semantics."
- **Project Ledger owns the schema** (`ledger_config.json` shape, `source_id` semantics,
  discovery modes) — not the host-specific path set.
- Therefore the *decision* to remove these three roots from the committed template is a
  **homelab-deployment decision owned by ai-server (the seam owner)**, and must be coordinated
  with the homelab fleet before landing. This spec records the proposal and the evidence; it does
  not itself land the change.

## Proposed change

Reconcile the committed `ledger_config.json` template with the live host by **removing** the three
dead roots and marking the removal `[Needs validation]` — do **not** invent replacement paths.

Concretely, in the committed template:

1. Remove the `project-ledger-live` root (`/home/matt/project-ledger`, `discovery: self`).
2. Remove the `google-drive-inventory` root (`/home/matt/GoogleDrive`,
   `discovery: inventory_policy`).
3. Remove the `opt-homelab` root (`/opt/homelab`, `discovery: children`).
4. Record the removal in the config's provenance/comment surface as `[Needs validation]` — i.e.
   these roots are absent on the live host and should be re-added only with live evidence that the
   path exists and is readable by `prime`. Do not silently drop them without a marker, so a future
   operator knows they were deliberately removed, not forgotten.

The live seam config already reflects exactly this state (the three roots removed); the committed
template should be brought into agreement with it.

## Acceptance criteria

- [x] `git show HEAD:ledger_config.json` no longer lists `project-ledger-live`,
      `google-drive-inventory`, or `opt-homelab`.
- [x] The removal is marked `[Needs validation]` (a comment/provenance note), not silently dropped.
- [x] The committed template's remaining roots match the live seam root set (14 sources) exactly.
- [ ] `bash scripts/project-ledger-discovery-watchdog.sh` (with `PROJECT_LEDGER_REPO` pointing at a
      checkout using the reconciled config) completes without aborting under `set -euo pipefail`.
      (Verified on ai-server during R0 with the seam config; re-verify on the next live watchdog run.)
- [x] A fresh `build_ledger.py --config ledger_config.json --output-dir output` run succeeds and
      produces a valid `projects.json`. (Config validation passes; the only `validate` failure on a
      non-ai-server host is the expected `matty-pc-inventory` artifact path check, which is host-specific.)

## Coordination / landing requirements

- This is a **homelab-deployment decision owned by ai-server (the seam owner)**. Coordinate with
  the homelab fleet before landing.
- The canonical homelab checkout is `/central/repos/infra/homelab` (H08 sole writer). Its pre-commit
  guard refuses direct commits to the primary checkout — land via an isolated worktree + lease
  (see `homelab-project-ledger` skill, "Editing homelab consumer defaults" card protocol).
- The `ledger_config.json` lives in the **project-ledger** repo (`github.com/mschwar/project-ledger`),
  not homelab. The seam edit on ai-server is intentionally uncommitted (repo stays on `main`;
  `output/` and `state/` are gitignored). Reconcile the committed template via a normal
  project-ledger PR/branch, coordinated with the fleet.
- Do **not** `git reset --hard` in the canonical checkout — the live seam config is an uncommitted
  host-specific edit that must be preserved.

## Out of scope

- Re-adding any of the three roots with invented paths.
- Changing the `ledger_config.json` schema, `source_id` semantics, or discovery modes (Project
  Ledger owns the schema; this spec only reconciles the host-specific path set).
- Any change to the homelab watchdog script itself.
