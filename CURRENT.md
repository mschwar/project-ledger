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
  -> divergent sidecar declarations surface as a bounded review (P1.4)
  -> canonical projects + identity review
  -> system manifest
  -> ledger orient / ledger resolve / ledger sources
```

This crosses both the **estate observation/orientation** boundary and the first **conceptual project identity** boundary.

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

Still not implemented:

```text
preferred working location
 -> project capsules
 -> structured current state / session receipts
 -> semantic change feed
 -> incremental compilation
```

The next software constraint is intentionally **not preselected**. R0 live provider proof must now test the identity slice against the real estate before R3 is authorized.

## Current commands

```bash
python -m ledger validate
python -m ledger refresh
python -m ledger refresh --no-markdown-mirror
python -m ledger compile
python -m ledger orient
python -m ledger sources
python -m ledger resolve <referent>
python -m unittest discover -s tests
```

`python build_ledger.py` remains the compatibility entrypoint.

## Reality / proof boundary

- Latest code authority: `main`.
- Generated `state/` is rebuildable projection, not canon.
- The committed Markdown ledger is historical evidence, not proof of current source freshness.
- Homelab owns the deployed provider seam; Project Ledger owns source/project schema and identity semantics.
- One live provider run on the intended homelab node is still required as production proof after the September Wave 1 changes.

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