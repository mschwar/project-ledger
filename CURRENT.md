# Current

This is the canonical executable frontier for Project Ledger. Read it before roadmap/backlog archaeology.

The first canonical-identity walking skeleton is implemented on `main`.

## Product objective

A cold agent should be able to take a messy project referent, determine which conceptual project the operator means, identify trustworthy manifestations and current state, understand uncertainty, and continue work without replaying filesystem/repository archaeology.

## Current walking skeleton

Implemented:

```text
registered sources
 -> compatibility scan
 -> frozen v0 compatibility observation contract (P1.1)
 -> typed manifestation observations
 -> exact-remote identity evidence + explicit decisions
 -> identity evidence model (records + scoring, P2.3)
 -> canonical project IDs + resolved-field provenance (P2.5)
 -> first-class identity review queue (P2.6)
 -> resolved identity questions do not recur under identical inputs (P2.7 ratchet)
 -> PROJECT_KEY_AMBIGUOUS resolves via canonical_key + honest resolution_actions (P2.8)
  -> versioned sidecar declaration validation (`schemas/project-ledger-sidecar.schema.json`, `ledger/sidecar.py`, `load_sidecar()` `HEAD`-drift `stale_sidecar` warnings, v1 `schema_version`, forbidden `canonical_project_id`, typed `observation["sidecar"]["declarations"]`, bounded `SIDECAR_DECLARATION_INVALID` review, P1.7)
  -> divergent sidecar declarations surface as a bounded review (P1.4)
  -> explicit source result/freshness semantics: unavailable vs observed_empty vs observed; content_as_of only from authoritative upstream evidence (P1.5)
  -> canonical projects + identity review
  -> preferred-location resolver + ranked observations with reason codes/confidence + git working-tree drift & `stale_sidecar` sensing (P3.5 / Reality Provider)
  -> gbrain source cross-linking (`gbrain_source_slug` + collision-free `gbrain.page_key` metadata on canonical projects and capsules) + idempotent GBrain `projects/<slug>.md` projector (`ledger sync-gbrain`)
  -> bounded project capsules at state/projects/<canonical_project_id>.json with read-only `capsule["ctx"]` fleet/skeleton/Tier-2/Tier-1 ingestion (P3.2)
  -> human canonical ledger view at state/canonical-projects.md (P3.7)
  -> frontier zero-shot weak-overlap triage candidates (`registry/weak-overlap-triage-candidates.json` + `ledger review --approve-candidates`, all 40 weak overlaps / 62 candidate decisions across Batch 1 + Batch 2 approved into `registry/identity-decisions.json`, 7 redundant secondary/top-level checkouts reconciled, archived, and superseded -> 14 active sources, 80 total / 65 active decisions, 176 canonical projects across 207 observations, 0 open identity reviews, 0 untriaged weak overlaps)
  -> Wave 4 semantic change feed (`state/changes.json` + bounded `state/runs/<run_id>.json` + `ledger changes`, P4.5)
  -> system manifest
  -> ledger orient / ledger resolve / ledger show / ledger locate / ledger explain / ledger review / ledger changes / ledger sync-gbrain / ledger sources
```

This crosses the **estate observation/orientation** boundary, the **conceptual project identity** boundary, the full **Wave 3 agent read-plane** boundary (`P3.1`–`P3.7`), the **Wave 4 semantic change-feed** boundary (`P4.5`), and the **Reality Provider seam** for `machina`, `homelab`, and `gbrain`.

Identity is deliberately conservative:

- exact normalized repository remote is the only automatic merge authority;
- names and compatibility `project_key` values are exact referents, not automatic merge authority;
- non-remote duplicate manifestations remain separate until an explicit decision exists;
- split/reject decisions can block an otherwise exact automatic match;
- ambiguous exact referents remain explicit rather than being guessed.

The **identity evidence model (P2.3)** now records and scores why observations unify
(or do not): every evidence kind carries a strength (`strong`/`weak`/`negative`), weak
name/semantic similarity is explicitly marked weak (never auto-merging), negative
split/reject evidence is first-class, and the materialized `identity-evidence.json`
exposes per-project unifying evidence + summaries. This is descriptive scoring — the
merge authority is unchanged.

The **decision registry (P2.4)** now covers the full durable decision-type set: merge,
split, reject-match, alias, canonical human key assignment (`canonical_key`), and
supersession (`supersede`). Every decision carries a stable `decision_id` and may carry
`rationale`, `evidence` refs, `authority`, and `decided_at`. Supersession is
append/supersede oriented (never silently rewritten): a superseded decision is inactive
for the compile, and a `supersede` referencing an unknown decision becomes a bounded
`DECISION_SUPERSEDE_UNKNOWN` review.

The **canonical-ID compiler (P2.5)** now guarantees a distinct `canonical_project_id`
per conceptual project: a normalized remote is only used as the anchor when it is
unique to one project across the compile, so a split/reject that keeps two
observations sharing an exact remote in separate projects no longer collides. Each
canonical project also carries a `resolved_fields` claim-provenance map explaining
which observation/decision/evidence produced its `project_key` and `display_name`.

The **identity review queue (P2.6)** is now first-class: every review item carries
`severity`, `reason_automation_stopped`, `resolution_actions`, and `evidence`, and
decision-scoped reviews (`DECISION_SUPERSEDE_UNKNOWN`) carry a non-empty
`affected_decision_ids` referent so two distinct unknown-target reviews no longer
collapse to one `review_id` (a data-loss bug fixed in P2.6). Observation-scoped
review IDs are unchanged.

The **resolution ratchet (P2.7)** now pins, for every first-class review type that a
durable decision can resolve, the full review-resolution loop: resolve once, persist
the decision, rerun with identical observations, assert the review does not recur,
and assert the resolving decision is discoverable in the compiled state a future
`ledger explain` will read. This closes the Gate C guarantee that a resolved identity
question does not recur on unchanged inputs. Covered resolutions: `merge` closes
`PROJECT_KEY_AMBIGUOUS`; superseding the conflicting negative decision closes
`DECISION_CONFLICT` and `AUTO_MATCH_BLOCKED_BY_DECISION`; superseding the stale
decision closes `DECISION_REFERENCE_UNAVAILABLE`; adding the referenced decision closes
`DECISION_SUPERSEDE_UNKNOWN`.

The **`PROJECT_KEY_AMBIGUOUS` resolution-results gap (P2.8)** is now closed: the review
is computed on each canonical project's *resolved* `project_key` (canonical_key
override applied) rather than the raw compatibility hint, so a `canonical_key` decision
that assigns a distinct key to one of two same-key projects now disambiguates and
clears the review — matching the advertised `resolution_actions`. The actions were
corrected to the two paths that actually clear (`merge` and `canonical_key`); a
`reject_match` confirms distinctness but, with the human key still shared, does not
clear the warning (now stated in `reason_automation_stopped`). Genuinely-unresolved
same-key review IDs (r0 `the-garden`, estate-3/4) are unchanged; raw hints remain
reachable as `project_key_hint` referents. This previously surfaced as a
documentation/semantics inconsistency recorded as a future spec (t_92cca8a6), not a
data-loss or correctness bug.

The **Wave 3 agent read plane (`P3.1`–`P3.7`) and Reality Provider seam (`P4.1`–`P4.4`, `P5.1`–`P5.3`)** are now implemented:
- `ledger resolve <referent>`, `ledger locate <project>`, and `ledger show <project>` return the authoritative checkout path (`preferred_location.path`), `gbrain_source_slug` / `gbrain` cross-links (`active-repos`, `the-garden`, `central-notes`, `central-knowledge`, `central-catalog`, `atlas-registry`, `default`, with `/central/registry/mirrors/` excluded), and explicit git working-tree drift warnings (`GIT_BEHIND_UPSTREAM`, `GIT_MAIN_BEHIND_ORIGIN`, `GIT_AHEAD_OF_UPSTREAM`, `GIT_DETACHED_HEAD`, `GIT_DIRTY_WORKTREE`).
- `ledger explain <referent>` (`P3.6`) returns full provenance across canonical projects, observations, review items, and identity decisions.
- `state/canonical-projects.md` (`P3.7`) provides a human-auditable canonical ledger view with preferred locations, GBrain sources, git drift warnings, and expandable multi-manifestation provenance.
- `ledger review` and `registry/weak-overlap-triage-candidates.json` (`P5.1`–`P5.3`) replace Wave 5's legacy custom local-LLM deduplication pipeline with frontier zero-shot triage batches: both Batch 1 (21 weak-overlap pairs / 33 durable decisions) and Batch 2 (19 post-`CONV-001` weak-overlap pairs / 29 durable decisions) are approved in `registry/identity-decisions.json`, and all 7 redundant secondary/top-level checkouts (`steam-db-mattified`, `gbrain-candidate`, `gbrain-master`, `/central/projects/song-clean` under `/central/archive/batch2-retired-checkouts-20261030/`, plus `/central/3813-patio`, `/central/proxy-lead`, and `/central/white-rabbit` under `/central/archive/retired-top-level-checkouts-20261031/`) are reconciled, archived, and superseded cleanly (`14` active registered sources, `62` approved triage candidates, `80` total / `65` active decisions, `176` canonical projects across `207` observations, `0` open identity reviews, and `0` untriaged weak overlaps).
- `state/changes.json`, bounded run snapshots at `state/runs/<run_id>.json` (`RUN_HISTORY_MAX_RUNS = 50`), and `ledger changes [--since <run_id>] [--project <referent>] [--entity-kind <kind>] [--change-type <code>]` (`P4.5`) provide zero-churn semantic estate deltas across runs (exposing top-level `change_count`, `review_count`, and `untriaged_weak_overlap_count` alongside lineage events `PROJECT_MERGED`, `PROJECT_SPLIT`, `PROJECT_REANCHORED`, and `PROJECT_UNAVAILABLE`), and `ledger explain <change_id>` explains individual change events.
- `state/source-checkpoints.json` and `ledger/checkpoints.py` (`P5.1`) provide deterministic pure-Python source and per-candidate extraction checkpointing during `ledger refresh` (with `--full-scan` to force full reconciliation), skipping `git` subprocess bursts and redundant metadata extraction on unchanged candidates across ephemeral staging `--output-dir` tempdirs while emitting portable output-root-relative POSIX artifact paths and preserving byte-identical compatibility outputs.
- `schemas/project-ledger-sidecar.schema.json`, `ledger/sidecar.py`, and `load_sidecar()` (`P1.7`) validate `.project-ledger.json` sidecar `schema_version` (`1` / `1.0` / `1.0.0` + legacy unversioned), forbid `canonical_project_id` in sidecars, validate field types across all v1 declaration fields and nested `git` sub-objects without altering frozen `CSV_FIELDS`, emit `stale_sidecar` warnings when declared commit/branch/timestamp lags live `git rev-parse HEAD`, attach `observation["sidecar"]`, and surface malformed/invalid sidecars as bounded `SIDECAR_DECLARATION_INVALID` review items.
- `ledger/capsules.py` (`P3.2`) additionally ingests read-only `ctx` structural capsules (`.ctx/skeleton.json`, `.ctx/fleet-capsule.json` / `.ctx/capsule.json`, Tier-2 `.ctx/CONTEXT-0.md` + `metadata.json`, and Tier-1 `ctx` `CONTEXT.md`) into `capsule["ctx"]` with progressive subsystem trimming inside the `16 KiB` capsule budget.
- `ledger/gbrain_pages.py` and `ledger sync-gbrain --brain-dir /home/prime/brain [--project REFERENT] [--ingest] [--dry-run] [--json]` idempotently project timestamp-free Reality Provider fenced blocks (`<!-- BEGIN PROJECT-LEDGER REALITY --> ... <!-- END PROJECT-LEDGER REALITY -->`) across all 176 collision-free `projects/<slug>.md` pages in GBrain while preserving existing YAML frontmatter, prose, and `<!-- timeline -->` sections.
- Competing session-receipt storage (originally Wave 4) is retired in favor of `machina` (which owns Work Unit / Attempt / Receipt kernel state) and `homelab` (which owns worktree leases).

All planned Wave 0–5 Project Ledger capabilities are now implemented (`unavailable_capabilities == []` in `state/system-manifest.json`), and all 9 legacy pre-Wave-2 `origin/*` branches have been archived into a verified git bundle (`/central/archive/project-ledger-p02-branch-hygiene-20261031/`) and pruned so `main` is sole authority (`P0.2`).

## Current commands

```bash
python -m ledger validate
python -m ledger refresh [--full-scan]
python -m ledger refresh --no-markdown-mirror [--full-scan]
python -m ledger compile
python -m ledger orient
python -m ledger sources
python -m ledger resolve <referent>
python -m ledger show <project>
python -m ledger locate <project>
python -m ledger explain <referent>
python -m ledger review [--approve-candidates] [--decision-id ID]
python -m ledger changes [--since RUN_ID] [--project REFERENT] [--entity-kind KIND] [--change-type CODE]
python -m ledger sync-gbrain [--brain-dir /home/prime/brain] [--project REFERENT] [--ingest] [--dry-run] [--json]
python -m unittest discover -s tests
```

`python build_ledger.py` remains the compatibility entrypoint.

## Reality / proof boundary

- Latest code authority: `main` (sole branch on `origin`; legacy pre-Wave-2 branches archived under `/central/archive/project-ledger-p02-branch-hygiene-20261031/`).
- Generated `state/` is rebuildable projection, not canon.
- The committed Markdown ledger is historical evidence, not proof of current source freshness.
- Homelab owns the deployed provider seam (`project-ledger-watchdog.timer` 15m incremental + `project-ledger-watchdog-fullscan.timer` daily 03:30 UTC); Project Ledger owns source/project schema, identity semantics, and GBrain reality-block projection.
- Live provider proofs on `ai-server` are complete (`docs/work/R0-LIVE-PROVIDER-PROOF-20260919.md` plus October 2026 post-`CONV-001` Wave 1–5 live timers, pre-spawn `machina`/`homelab` preflight gates, and 176-project GBrain page projection).

## Active bounded programme — reality to identity

### R1 — Canonicality + assurance repair

Status: COMPLETE.

Evidence:

- canonical frontier and execution programme landed;
- operating docs agree with the executable substrate;
- configured CI discovers the provider regression test.

### R2 — Identity walking skeleton

Status: COMPLETE.

New observable capability:

> Given an exact canonical ID, known path, exact normalized repository remote, operator-approved alias, project-key hint, or display name, return one canonical project, explicit ambiguity, or unresolved.

Automatic identity remains narrower than resolution: only exact normalized repository remotes auto-merge. Explicit identity decisions are durable compiler inputs.

The full multi-source estate fixture (`tests/fixtures/multi-source-estate.json` +
`tests/test_multi_source_estate.py`) is now the primary canonicalization acceptance
environment. It pins the compiler's exact deterministic output for every estate column
(live+mirror+backup, renamed/moved, same-name unrelated, missing remote, divergent
sidecars, inventory-only, inaccessible source, strong vs weak evidence), so canonical
identity cannot silently drift from demonstrated estate behavior.

### R0 — Live provider proof

Status: COMPLETE (2026-09-19). Evidence: `docs/work/R0-LIVE-PROVIDER-PROOF-20260919.md`.

The demonstrated reality has been ratcheted into a durable regression fixture:
`tests/fixtures/r0-reality-cases.json` + `tests/test_r0_reality_regressions.py`
reproduce the exact canonical project IDs (homelab/white-rabbit/reality-ledger
multi-manifestation merges) and the `the-garden` `PROJECT_KEY_AMBIGUOUS` review ID
from the production proof, so the identity compiler cannot silently drift from
demonstrated behavior.

Ran the deployed homelab Project Ledger watchdog against current `main` (`bf04d7b`)
on ai-server. Result: `observations=132 sources=14 health=ok changed=yes`, run
`run_4c8e9084d4ca23637eac9267`. All 14 sources available; 129 canonical projects from
132 observations; 1 open review item (`the-garden` PROJECT_KEY_AMBIGUOUS). Representative
multi-manifestation merges (homelab, white-rabbit, reality-ledger) and the `the-garden`
ambiguity are captured as candidate fixtures.

Deployment defect surfaced and fixed on the seam: the committed config still listed
three dead roots (`/home/matt/project-ledger`, `/home/matt/GoogleDrive`, `/opt/homelab`)
that aborted the watchdog; removed as a host-specific uncommitted seam edit. The
committed upstream template has now been reconciled with the live host (2026-09-19):
the three dead roots are removed from `ledger_config.json` and marked `[Needs validation]`
in a provenance note (see `docs/work/FUTURE-SPEC-reconcile-ledger-config-template-20260919.md`).

## Stop condition

Do not add broad ingestion adapters, UI, model-first dedupe, portfolio analytics, workflow engines, or heavy incremental machinery while exact canonical identity is still unavailable.

After R0 produces current production evidence, synthesize the next constraint from demonstrated use. Do not automatically start R3 merely because it is next in the design sequence.