# Work — Multi-source estate fixture (P2.2)

- Status: **landed**
- Date: 2026-09-18 (UTC)
- Node: Matthews-MacBook-Air-3 (Hermes child node)
- Repo: `github.com/mschwar/project-ledger`
- Extends: `docs/work/R0-LIVE-PROVIDER-PROOF-20260919.md` and the R2 identity walking skeleton

## Summary

Completed the **P2.2 multi-source estate fixture** as the primary canonicalization
acceptance environment. The R0 reality cases (`tests/fixtures/r0-reality-cases.json`)
pinned the production proof; this unit adds the broader synthetic estate matrix that
covers every remaining estate column the identity compiler must handle deterministically.

## What landed

- `tests/fixtures/multi-source-estate.json` — one self-contained case per estate column,
  each with observations, optional decisions, and an `expected` block pinning the
  compiler's exact deterministic output (canonical IDs, observation memberships,
  normalized remotes, key hints, review IDs).
- `tests/test_multi_source_estate.py` — 8 tests asserting every case reproduces its
  pinned canonical IDs, observation membership, review queue, and determinism, plus
  focused assertions on the merge anchor, divergent-sidecar key handling, renamed-path
  identity, and bounded unavailable-source review.

## Estate columns covered

| Column | Behavior pinned |
| --- | --- |
| live + mirror + backup | exact remote merges all three into one canonical project, no review |
| renamed / moved project | same remote across a moved path keeps one canonical project |
| same-name unrelated project | stays separate; `PROJECT_KEY_AMBIGUOUS` review |
| missing remote | no strong evidence -> separate by default, ambiguity surfaced |
| divergent sidecars | same remote, conflicting `project_key` claims -> remote stays the stable key, both hints preserved, no last-write-wins, no review |
| inventory-only observation | valid singleton anchored by observation |
| inaccessible source | decision referencing an unavailable observation -> bounded `DECISION_REFERENCE_UNAVAILABLE` review; present observations still compile |
| strong vs weak evidence | exact remote is strong; names/keys are weak |

## Verification

- `python3 -m unittest tests.test_multi_source_estate -v` — 8 tests OK.
- `python3 -m unittest discover -s tests` — 40 tests OK (was 32; +8 new).

## Docs updated

- `BACKLOG.md` — P2.2 marked complete; remaining-column list replaced with the landed matrix.
- `CURRENT.md` — R2 status finalized; estate fixture noted as the acceptance environment.
- `ROADMAP.md` — Wave 2 estate fixture noted as complete.

## Continuation point

The identity compiler now has a deterministic acceptance matrix covering the full estate.
The next constraint should be synthesized from demonstrated use per the Reality-to-Identity
programme (R3 preferred-location resolution and R4 session receipts remain HOLD FOR
SYNTHESIS). P2.1 (extract source adapters under frozen compatibility tests) and P2.3
(identity evidence model) are the next dependency-ordered identity-zone items.
