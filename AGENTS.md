# AGENTS.md

## Mission

Build and operate `project-ledger` as the durable **project-reality compiler and control substrate** for projects spread across machines and storage surfaces.

The system senses distributed source evidence, normalizes it into observations, reconciles durable project identity without hiding uncertainty, compiles project/current-state views, and exposes a cheap trustworthy read path for agents.

The design goal is not maximum metadata. It is **minimum uncertainty per unit of agent attention and compute**.

## Start here

Use progressive disclosure rather than reading the entire repo by default.

For any substantive task:

1. Read [CURRENT.md](CURRENT.md) for the executable frontier and current capability boundary.
2. Read [SYSTEM.md](SYSTEM.md) for the conceptual model and invariants.
3. Read [AGENT_PROTOCOL.md](AGENT_PROTOCOL.md) for the operating loop.
4. Read only the task-relevant contract:
   - [ARCHITECTURE.md](ARCHITECTURE.md) for module and dataflow changes
   - [SCHEMA.md](SCHEMA.md) for entity, identity, and state changes
   - [RUNBOOK.md](RUNBOOK.md) for operation and recovery
   - [ROADMAP.md](ROADMAP.md) / [BACKLOG.md](BACKLOG.md) for sequencing and execution
   - [PRD.md](PRD.md) for product outcomes and acceptance criteria
5. Inspect current ledger and source artifacts only when the task depends on estate state.

Target-state agents should normally orient through `ledger orient` / `state/system-manifest.json` and load a project capsule instead of reconstructing the estate from prose.

## Current state

As of November 11, 2026:

- `main` is sole product authority on `origin` (`P0.2` archived all 9 legacy pre-Wave-2 `origin/*` branches into `/central/archive/project-ledger-p02-branch-hygiene-20261031/`). It contains the converged multi-source compatibility scanner, executable Wave 1–5 agent substrate, 1:1 GitHub sync (`ledger sync-github`), and Reality Provider seam (`machina`, `homelab`, `gbrain`).
- Strict 1:1 git-repository correspondence is active (`defaults.require_git = true` and `defaults.exclude_autosnapshot_repos = true` in `ledger_config.json`, retiring `macbook-air-2-higgsfield-agent-project` and `matty-pc-inventory` and excluding GBrain auto-snapshot pseudo-repos). All `109` canonical projects across `12` registered git sources are real git repositories with network remotes (`105` owned/collaborator GitHub repos, `1` archived `homelab-reboot`, `3` upstream clones, `0` non-git folders, `0` unpushed local repos).
- `ledger sync-github` (`ledger/github_sync.py`, `state/github-sync.json`, `schemas/github-sync.schema.json`) and `ops/project-ledger-discovery-watchdog.sh` close the remote-to-local-to-GBrain loop every 15 minutes. They discover accessible GitHub repos via stdlib `/user/repos` (`0600` runtime token cache + `gh auth token` + `INV-FLEET-GAME-01` SSH fallback) and auto-clone missing non-archived repos over SSH (`git@github.com:<owner>/<repo>.git`) into `/central/repos/active`. They also normalize HTTPS GitHub remotes to SSH, incrementally fetch changed repos (`pushed_at` cache), and fast-forward (`git merge --ff-only`) clean default-branch checkouts without clobbering dirty, work-in-progress (WIP), or diverged trees. Finally, they pin new `canonical_project_id` values via `ledger lineage --sync` and trigger `ledger sync-gbrain --ingest` on semantic deltas while keeping `compile` and `refresh` strictly read-only under Law 10 and containing offline failures under Law 11.
- Preferred refresh is `ledger refresh` (`python -m ledger refresh`); automation can use `--no-markdown-mirror`, reconciliation can force uncached extraction via `--full-scan`, and upstream remote drift sensing (`git ls-remote origin`, `GIT_REMOTE_AHEAD`, `remote_ahead`) can be enabled via `--check-remote [--remote-timeout 3.0]`.
- Implemented agent commands are `validate`, `refresh`, `compile`, `sync-github`, `orient`, `sources`, `resolve`, `show`, `locate`, `explain`, `review`, `changes`, `lineage`, `sync-gbrain`, and `mcp` (plus the standalone `ledger-mcp` stdio JSON-RPC 2.0 Model Context Protocol (MCP) server at `/home/prime/.local/bin/ledger-mcp`, registered across Claude Code, Codex, Hermes, and Antigravity).
- Generated Wave 1–5 state is `state/system-manifest.json`, `state/source-checkpoints.json`, `state/github-sync.json`, `state/observations.json`, `state/canonical-projects.json`, `state/canonical-projects.md`, `state/identity-evidence.json`, `state/review-queue.json`, `state/changes.json`, `state/runs/<run_id>.json`, and `state/projects/<canonical_project_id>.json`; `state/` is rebuildable and gitignored.
- Stable source IDs, compatibility snapshot IDs, manifestation-oriented observation IDs, source probes/health, explicit capability states, and degraded-source containment are implemented. Versioned static sidecar validation (`P1.7`: `schemas/project-ledger-sidecar.schema.json`, `ledger/sidecar.py`, `SIDECAR_DECLARATION_INVALID` review) ignores mutable `commit`, `head_commit`, and `next_step` fields so static manifests stay fresh across commits, emitting `sidecar_declaration` claims only when `sidecar.present == True`. All 14 compiled `state/` and committed `registry/` or sidecar JSON contracts enforce Draft 2020-12 JSON Schemas (`schemas/*.schema.json`), alongside `pyproject.toml` packaging (`ledger`, `ledger-mcp`, and `ruff check .`).
- Canonical identity is implemented: exact normalized repository remotes auto-merge, while secondary non-`upstream` remotes (such as `fork`) surface on `observation["git_remotes"]`, `normalized_remotes`, and `git_remote:<name>` referents without altering primary `origin` merge rules. `ledger resolve` supports case-insensitive `<owner>/<repo>` shorthand, split same-remote projects auto-disambiguate `project_key` (`DISAMBIGUATED_SPLIT_PROJECT_KEY`), and durable merge/split/reject/alias/canonical_key/supersede decisions act as compiler inputs. `registry/project-lineage.json` (`ledger/lineage.py`, `ledger lineage [--sync|--check] [--merge-from PATH]`, `U3`) pins all `179` canonical and historical project IDs immutably across remote additions, GitHub renames, merge absorptions, and `state/` wipes while demoting superseded anchors to `historical_canonical_id` referents. Shadowed backwards-compatibility symlinks across all roots are suppressed natively (`is_shadowed_symlink`), and canonical projects and identity review are materialized.
- The full Wave 3 read plane (`P3.1`–`P3.7`) and Strategic Unlocks (`U1`, `U2`, `U5`, `#39`) are implemented with `16 KiB` bounded project capsules (`state/projects/<canonical_project_id>.json`). Capsules combine read-only `.ctx/` ingestion and pure-Python `compiled_structural_scan` synthesis (zero target writes under Law 10, always running `extract_structural_ctx()` when `.ctx/` is absent regardless of root `CONTEXT.md`). That synthesis exposes bounded `operational_surfaces` (`<= 12`, CLI/HTTP/MCP/env), `operational_surface_count`, `dependency_summary` (`internal_edge_count`, iterative Tarjan `cycle_count`), and subsystem `arch_role`. Capsules also project read-only `.machina/state.json` continuity across `current_state.machina_continuity`, GBrain Reality Provider blocks, `ledger explain`, and `state/canonical-projects.md` (`session_receipts_reason_code = "OWNED_BY_MACHINA"`). Explainable preferred-location resolution (`live > mirror > backup > inventory` with reason codes, confidence, git drift warnings, and `stale_sidecar` warnings) pairs with deterministic posture rules (`ledger/rules.py`, `show_payload()["posture"]`). Collision-free GBrain cross-linking (`gbrain_source_slug`, `gbrain.page_key`), idempotent `projects/<slug>.md` projection with stale-stub pruning (`ledger sync-gbrain`), `ledger show`, `ledger locate`, `ledger explain`, `ledger review [--approve-candidates]`, `state/canonical-projects.md`, and the stdlib MCP server (`ledger/mcp_server.py`, 9 read-only tools) are active.
- Frontier zero-shot weak-overlap triage (`registry/weak-overlap-triage-candidates.json` + `ledger review --approve-candidates`) replaces the legacy Wave 5 custom local-LLM matcher plan. Both Batch 1 (21 pairs / 33 decisions) and Batch 2 (19 post-`CONV-001` pairs / 29 decisions) are approved in `registry/identity-decisions.json`. Seven redundant checkouts (`steam-db-mattified`, `gbrain-candidate`, `gbrain-master`, `/central/projects/song-clean`, `/central/3813-patio`, `/central/proxy-lead`, `/central/white-rabbit`) are reconciled, archived, and superseded cleanly (`14` active sources, `62` approved triage candidates, `82` total / `67` active decisions, `0` open reviews, `0` untriaged overlaps, `0` `stale_sidecar` warnings, `0` git-behind warnings). `machina` owns the Work Unit / Attempt / Receipt kernel, `homelab` owns worktree leases and the deployed reality-provider seam, and `gbrain` owns semantic and entity search.
- Wave 4's semantic change feed (`P4.5` + `U4`: `state/changes.json`, bounded header-indexed run history `state/runs/<run_id>.json`, and `ledger changes [--since <run_id>] [--project <referent>] [--entity-kind <kind>] [--change-type <code>]`) is implemented, reporting zero-churn estate deltas across sources, canonical project lineage (`PROJECT_MERGED`, `PROJECT_SPLIT`, `PROJECT_REANCHORED`, `PROJECT_UNAVAILABLE` vs `PROJECT_REMOVED`), observations, git state/drift, reviews, and negative decisions.
- Single-pass `os.scandir` source/candidate extraction and compile-stage (`sidecar_envelope` + `git_drift` + `ctx_raw_skeleton`) checkpointing (`P5.1` + `PL-GAP-CLOSEOUT-02` + `U4`: `ledger/checkpoints.py`, `state/source-checkpoints.json`, `ledger refresh [--full-scan]`) are implemented. Warm refreshes reuse unchanged candidate extractions, fast `inventory_policy` stat signatures, and compile probes across staging `--output-dir` paths (`0` subprocesses and `~1.98s` CPU time on 14-source / 224-candidate refreshes). Read-only git calls use `--no-optional-locks`, `build_identity_evidence()` uses bucket indexing, and outputs emit portable Portable Operating System Interface (POSIX) paths while preserving byte-identical compatibility entries.
- The committed human ledger is historical evidence, not proof of current production freshness. A live provider proof on the intended homelab node has now been completed (2026-09-19, see `docs/work/R0-LIVE-PROVIDER-PROOF-20260919.md`), and post-`CONV-001` roots + approved weak-overlap triage decisions are active on `ai-server`.

The observation -> canonical identity boundary has now been crossed conservatively. Do not broaden automatic matching beyond exact remote identity without evidence/decision contracts. `CURRENT.md` is the canonical frontier pointer; the next software constraint is chosen only after live provider proof/synthesis.

## Architectural laws

These summarize `SYSTEM.md`; the full document is authoritative for rationale.

1. Canonical state is compiled, not directly hand-authored.
2. Preserve provenance; never silently collapse ambiguous observations.
3. Separate observed facts, declarations, inferences, and explicit decisions.
4. Identity is not a path/name/URL; those are evidence and aliases.
5. Freshness and uncertainty are first-class state.
6. Agent-facing reads should be compact materialized views with pointers to deeper evidence.
7. Deterministic/incremental computation precedes semantic/expensive reasoning.
8. Resolved ambiguity becomes a durable decision/test so it is not paid for twice.
9. Project Ledger owns project topology/current-state resolution, not general task lifecycle or deep knowledge storage.
10. Sensing is read-only with respect to target projects.
11. Fail locally and explicitly; degraded sources/ambiguous projects do not poison healthy state.
12. `main` remains authoritative; shadow product branches are defects.

## Development operating rules

1. Use bounded work units: one coherent task -> focused branch/workspace -> tests/docs -> review/QA -> resolve -> merge.
2. Preserve the existing `python build_ledger.py` entrypoint while refactoring unless an explicitly versioned migration changes it.
3. Preserve deterministic output semantics unless the schema/version explicitly changes.
4. Schema/protocol changes must update documentation, validation/tests, migration expectations, and agent/operator behavior in the same work unit.
5. New capabilities require automated coverage appropriate to their risk.
6. Prefer standard-library/deterministic solutions unless a dependency or model materially improves correctness or cost.
7. Generated artifacts are views, not identity truth.
8. Keep project-local declarations human-editable and conservative.
9. Do not overload sidecars into a task manager.
10. Do not use expensive models where exact source evidence can answer the question.
11. Keep [README.md](README.md#tldr) and human-facing guides concrete: state inputs, checks, and outputs in plain English before internal terminology (`## TL;DR` 1..60 words, no UTF-8 Byte Order Mark). Keep prose sentences at 45 words or fewer, avoid banned filler or ambiguous modals, expand unfamiliar uppercase acronyms at first use without backticks inside parentheses, use valid relative Markdown links with zero `file://` URIs, and run `/usr/bin/python3 -m unittest tests/test_doc_hygiene.py -v`.

## Expected commands today

```bash
ruff check .
ledger validate
ledger sync-github [--check | --dry-run] [--clone-root /central/repos/active] [--force-fetch] [--json]
ledger refresh [--full-scan] [--check-remote]
ledger refresh --no-markdown-mirror [--full-scan] [--check-remote]
ledger compile [--check-remote]
ledger orient
ledger sources
ledger resolve <referent>
ledger show <project>
ledger locate <project>
ledger explain <referent>
ledger review [--approve-candidates] [--decision-id ID]
ledger changes [--since RUN_ID] [--project REFERENT] [--entity-kind KIND] [--change-type CODE]
ledger lineage [--check | --sync] [--merge-from PATH] [--json]
ledger sync-gbrain [--brain-dir /home/prime/brain] [--project REFERENT] [--ingest] [--dry-run] [--json]
ledger mcp
ledger-mcp
/usr/bin/python3 -m unittest tests/test_doc_hygiene.py -v
python -m unittest discover -s tests
```

`python -m ledger <subcommand>` and the compatibility entrypoint `python build_ledger.py` remain supported.

Session/attempt receipt recording (`record-session`) is intentionally owned by `machina` rather than duplicated inside Project Ledger. Consume the manifest capability map rather than inferring capability from prose.

## Definition of done

A substantive change is not done unless:

- behavior/contracts are implemented coherently;
- tests/validation pass;
- documentation reflects actual behavior;
- schema compatibility/migration is addressed where relevant;
- uncertainty/error paths are explicit;
- the agent/operator read path remains clear;
- material decisions or new failure knowledge are made durable;
- completed work is merged so `main` remains authoritative.

## Session-end requirement

For material work, leave a bounded evidence-bearing handoff: what landed, verification, unresolved issues, exact continuation point, and relevant refs. Today use the project sidecar/session-end prompt where appropriate. The target architecture upgrades this into structured session receipts linked to canonical projects.
