# Future Spec — Surface divergent sidecar declarations as a review

- Status: **landed** (2026-09-19, programme P1.4 — see
  `docs/work/P1.4-EPISTEMIC-CLAIM-CONTRACT-20260919.md`)
- Date: 2026-09-18 (UTC)
- Node: Matthews-MacBook-Air-3 (Hermes child node)
- Repo: `github.com/mschwar/project-ledger`
- Found while: completing the P2.2 multi-source estate fixture (estate-5)

## Problem

When two observations of the **same project** (same exact normalized remote) carry
**conflicting sidecar `project_key` declarations**, the current identity compiler merges
them on remote authority, keeps the remote as the stable key, preserves both conflicting
hints in `project_key_hints`, and opens **no review**.

This is pinned by `tests/fixtures/multi-source-estate.json` case `estate-5`:
observations `m5-north` (`project_key="north"`) and `m5-south` (`project_key="south"`)
both carry remote `github.com/mschwar/north`; the merged project's key is
`github.com/mschwar/north` with hints `["north","south"]` and `review_count == 0`.

Per `SCHEMA.md` §6: "A sidecar is a declaration source attached to an observation. If
multiple observations contain conflicting sidecars, emit claims/conflict/review; do not
use last-write-wins." The compiler currently avoids last-write-wins (it does not pick
one sidecar as truth), but it also does **not** surface the conflict as a review item.
An agent reading the canonical project sees both hints but no explicit signal that two
declarations disagree — the ambiguity is silently absorbed by remote authority.

## Current behavior (intended for the first identity slice)

- exact remote is the only automatic merge authority; it is also treated as authoritative
  enough to select the stable project key, so a conflicting sidecar pair does not block
  the merge;
- no review is opened for the divergent sidecar claims.

## Proposed change

After the identity compiler is stable and the minimal epistemic claim/evidence contract
(P1.4) lands, emit a bounded review item (e.g. `DIVERGENT_SIDECAR_DECLARATIONS`) when
two observations that merged (by remote or explicit decision) carry conflicting sidecar
fields that affect identity (`project_key` today; potentially display-name/sitecar
conflicts later). The review must:

- reference both conflicting observation IDs;
- carry the differing claims as evidence;
- leave the remote-based merge intact (the merge stands; the review is advisory
  unless a field-resolution policy says otherwise);
- resolve into a durable identity/field decision via the review ratchet (P2.7).

This is deliberately queued behind P1.4 (epistemic claim/evidence contract) and P2.7
(review ratchet), so it does not introduce ad hoc conflict semantics into the first
conservative slice.

## Acceptance criteria (landed with P1.4, 2026-09-19)

- [x] A merge of two same-remote observations with conflicting `project_key` sidecars
      opens a `DIVERGENT_SIDECAR_DECLARATIONS` review referencing both observations
      (`ledger/identity.py`; coverage in `tests/test_p14_epistemic_claims.py`).
- [x] The existing `estate-5` fixture is updated to reflect the new expected review
      (its semantics otherwise unchanged — remote key, both hints, merge intact).
- [x] A field-resolution policy (a `canonical_key` decision choosing the authoritative
      key) resolves the review without last-write-wins, and it does not recur on
      unchanged inputs (the P2.8-style ratchet).
- [x] Full suite still passes after the fixture update (102 tests).

## Explicitly out of scope now

- Adding any new schema field or review code before P1.4/P2.7 dependencies land.
- Changing how remote authority selects the project key.
- Last-write-wins sidecar resolution.