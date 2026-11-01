# AGENTS.md

## Mission

Build and operate `project-ledger` as the durable **project-reality compiler and control substrate** for projects spread across machines and storage surfaces.

The system senses distributed source evidence, normalizes it into observations, reconciles durable project identity without hiding uncertainty, compiles project/current-state views, and exposes a cheap trustworthy read path for agents.

The design goal is not maximum metadata. It is **minimum uncertainty per unit of agent attention and compute**.

## Start here

Use progressive disclosure rather than reading the entire repo by default.

For any substantive task:

1. Read `CURRENT.md` for the executable frontier and current capability boundary.
2. Read `SYSTEM.md` for the conceptual model/invariants.
3. Read `AGENT_PROTOCOL.md` for the operating loop.
4. Read only the task-relevant contract:
   - `ARCHITECTURE.md` for module/dataflow changes
   - `SCHEMA.md` for entity/identity/state changes
   - `RUNBOOK.md` for operation/recovery
   - `ROADMAP.md` / `BACKLOG.md` for sequencing/execution
   - `PRD.md` for product outcomes and acceptance criteria
4. Inspect current ledger/source artifacts only when the task depends on estate state.

Target-state agents should normally orient through `ledger orient` / `state/system-manifest.json` and load a project capsule instead of reconstructing the estate from prose.

## Current state

As of November 1, 2026:

- `main` is sole product authority on `origin` (`P0.2` archived all 9 legacy pre-Wave-2 `origin/*` branches into `/central/archive/project-ledger-p02-branch-hygiene-20261031/`) and contains the converged multi-source compatibility scanner plus the executable Wave 1–5 agent substrate and Reality Provider seam for `machina`, `homelab`, and `gbrain`.
- preferred refresh is `ledger refresh` (`python -m ledger refresh`); automation can use `--no-markdown-mirror`, and reconciliation can force uncached extraction via `--full-scan`.
- implemented agent commands are `validate`, `refresh`, `compile`, `orient`, `sources`, `resolve`, `show`, `locate`, `explain`, `review`, `changes`, `lineage`, `sync-gbrain`, and `mcp` (plus the standalone `ledger-mcp` stdio JSON-RPC 2.0 server installed at `/home/prime/.local/bin/ledger-mcp` and registered across Claude Code, Codex, Hermes, and Antigravity MCP configs).
- generated Wave 1–5 state is `state/system-manifest.json`, `state/source-checkpoints.json`, `state/observations.json`, `state/canonical-projects.json`, `state/canonical-projects.md`, `state/identity-evidence.json`, `state/review-queue.json`, `state/changes.json`, `state/runs/<run_id>.json`, and `state/projects/<canonical_project_id>.json`; `state/` is rebuildable and gitignored.
- stable source IDs, compatibility snapshot IDs, manifestation-oriented observation IDs, versioned sidecar declaration validation (`P1.7`: `schemas/project-ledger-sidecar.schema.json`, `ledger/sidecar.py`, `load_sidecar()` `HEAD`-drift `stale_sidecar` warnings, `observation["sidecar"]` with `sidecar_declaration` claims emitted only when `sidecar.present == True`, `SIDECAR_DECLARATION_INVALID` review), 100% executable Draft 2020-12 JSON Schemas across all 13 compiled `state/` and committed `registry/` / sidecar JSON contracts (`schemas/*.schema.json`), `pyproject.toml` packaging (`ledger` and `ledger-mcp` console scripts + `ruff check .` lint gate), source probes/health, explicit capability states, and degraded-source containment (including `scripts/machina_adapter.sh map` Law 11 containment) are implemented.
- canonical identity is implemented: exact normalized repository remotes auto-merge; split same-remote projects auto-disambiguate `project_key` (`DISAMBIGUATED_SPLIT_PROJECT_KEY`); durable merge/split/reject/alias/canonical_key/supersede decisions are compiler inputs; `registry/project-lineage.json` (`ledger/lineage.py`, `ledger lineage [--sync|--check]`, `U3`) pins all `172` live `canonical_project_id` values immutably across remote additions, GitHub renames, merge absorptions, and `state/` wipes while demoting superseded anchor IDs to `historical_canonical_id` referents; shadowed backwards-compatibility symlinks across all roots are suppressed natively independent of `roots[]` ordering (`is_shadowed_symlink`); canonical projects and identity review are materialized; `ledger resolve` is available.
- the full Wave 3 read plane (`P3.1`–`P3.7`) + Strategic Unlocks (`U1`, `U2`, `U5`) are implemented: bounded project capsules (`state/projects/<canonical_project_id>.json` with a `16 KiB` size budget, read-only `capsule["ctx"]` explicit `.ctx/` ingestion + pure-Python `compiled_structural_scan` synthesis across `166 / 172` capsules with zero target writes under Law 10, and end-to-end read-only `.machina/state.json` continuity projection across `current_state.machina_continuity`, GBrain Reality Provider blocks, `ledger explain`, and `state/canonical-projects.md` with `session_receipts_reason_code = "OWNED_BY_MACHINA"`), explainable preferred-location resolution (`live > mirror > backup > inventory` with reason codes, confidence, git working-tree drift warnings, and `stale_sidecar` warnings), collision-free GBrain source/page cross-linking (`gbrain_source_slug` and `gbrain.page_key`) plus idempotent `projects/<slug>.md` reality-block projection with safe stale-stub pruning (`ledger sync-gbrain`), `ledger show <project>`, `ledger locate <project>`, `ledger explain <referent>`, `ledger review [--approve-candidates]`, the human canonical ledger view (`state/canonical-projects.md`), and the stdlib read-only MCP stdio server (`ledger/mcp_server.py`).
- frontier zero-shot weak-overlap triage (`registry/weak-overlap-triage-candidates.json` + `ledger review --approve-candidates`) replaces the legacy Wave 5 custom local-LLM matcher plan: both Batch 1 (21 weak-overlap pairs / 33 durable decisions) and Batch 2 (19 post-`CONV-001` weak-overlap pairs / 29 durable decisions) are approved in `registry/identity-decisions.json`, with 7 redundant secondary/top-level checkouts (`steam-db-mattified`, `gbrain-candidate`, `gbrain-master`, `/central/projects/song-clean`, `/central/3813-patio`, `/central/proxy-lead`, `/central/white-rabbit`) reconciled, archived, and superseded cleanly (`14` active registered sources, `62` approved triage candidates, `82` total / `67` active identity decisions, `0` open identity reviews, `0` untriaged weak overlaps, `0` `stale_sidecar` warnings, and `0` git-behind warnings); `machina` owns the Work Unit / Attempt / Receipt kernel (retiring the legacy Wave 4 competing receipt store), `homelab` owns worktree leases and the deployed reality-provider seam, and `gbrain` owns semantic/entity search.
- Wave 4's semantic change feed (`P4.5` + `U4`: `state/changes.json`, bounded header-indexed run history `state/runs/<run_id>.json`, and `ledger changes [--since <run_id>] [--project <referent>] [--entity-kind <kind>] [--change-type <code>]`) is implemented, reporting zero-churn estate deltas across sources, canonical project lineage (`PROJECT_MERGED`, `PROJECT_SPLIT`, `PROJECT_REANCHORED`, `PROJECT_UNAVAILABLE` vs `PROJECT_REMOVED`), observations, git state/drift, reviews, and negative decisions.
- single-pass `os.scandir` source/candidate extraction and compile-stage (`sidecar_envelope` + `git_drift` + `ctx_raw_skeleton`) checkpointing (`P5.1` + `PL-GAP-CLOSEOUT-02` + `U4`: `ledger/checkpoints.py`, `state/source-checkpoints.json`, `ledger refresh [--full-scan]`) is implemented, reusing unchanged candidate extractions, fast `inventory_policy` stat signatures, and compile probes across ephemeral staging `--output-dir` directories (`0` subprocesses and `~1.98s` CPU time on warm 14-source / 224-candidate estate refreshes) while running read-only git calls with `--no-optional-locks`, bucket-indexing `build_identity_evidence()`, emitting portable output-root-relative POSIX artifact paths, and preserving byte-identical compatibility entries.
- the committed human ledger is historical evidence, not proof of current production freshness. A live provider proof on the intended homelab node has now been completed (2026-09-19, see `docs/work/R0-LIVE-PROVIDER-PROOF-20260919.md`), and post-`CONV-001` roots + approved weak-overlap triage decisions are active on `ai-server`.

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

## Expected commands today

```bash
ruff check .
ledger validate
ledger refresh [--full-scan]
ledger refresh --no-markdown-mirror [--full-scan]
ledger compile
ledger orient
ledger sources
ledger resolve <referent>
ledger show <project>
ledger locate <project>
ledger explain <referent>
ledger review [--approve-candidates] [--decision-id ID]
ledger changes [--since RUN_ID] [--project REFERENT] [--entity-kind KIND] [--change-type CODE]
ledger lineage [--check | --sync] [--json]
ledger sync-gbrain [--brain-dir /home/prime/brain] [--project REFERENT] [--ingest] [--dry-run] [--json]
ledger mcp
ledger-mcp
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
