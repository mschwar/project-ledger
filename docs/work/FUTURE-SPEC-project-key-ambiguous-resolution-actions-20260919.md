# Future Spec — `PROJECT_KEY_AMBIGUOUS` resolution actions are misleading

- Status: **proposed / not landed**
- Date: 2026-09-19 (UTC)
- Node: Matthews-MacBook-Air-3 (Hermes child node)
- Repo: `github.com/mschwar/project-ledger`
- Found while: authoring the P2.7 resolution-ratchet tests (each review type's
  durable-decision resolution was probed against the compiler before pinning it)

## Problem

The `PROJECT_KEY_AMBIGUOUS` review is emitted when one compatibility `project_key`
(casefolded) maps to more than one canonical project — i.e. two distinct-remote
projects both declare the same key hint. Its `resolution_actions` advertise three
operator choices:

```text
Add a merge decision if the observations are the same conceptual project.
Add a canonical_key decision to assign a stable operator-approved key.
Add a reject_match decision if they are genuinely distinct projects.
```

Of these three, **only the merge decision actually closes the review.** Both a
`canonical_key` decision and a `reject_match` decision leave the review open, because
the ambiguity is computed from each observation's *raw* `project_key` hint
(`facts[obs].project_key`), not from the *resolved* canonical `project_key` of the
project each observation belongs to. So two of the three documented resolution paths
do not resolve the issue they claim to.

This was surfaced while authoring `tests/test_p27_resolution_ratchet.py`: the P2.7
ratchet requires each review type to converge under a durable decision, and probing
the compiler showed `canonical_key` does not converge for `PROJECT_KEY_AMBIGUOUS`
(only `merge` does). It is a **documentation/semantics inconsistency, not a data-loss
or correctness bug** — the ambiguity is correctly surfaced, just not clearable by the
two actions that claim to clear it.

## Reproduction (post-P2.6, on `main` e231a12)

Two observations `obs-a` / `obs-b` with distinct remotes (`github.com/x/a.git`,
`github.com/x/b.git`) and the same compatibility `project_key` `garden`:

- With NO decisions: `review_count == 1`, `PROJECT_KEY_AMBIGUOUS`.
- Add a `merge` decision (`observation_ids: [obs-a, obs-b]`):
  `review_count == 0` — review cleared. (This is the P2.7 resolution path.)
- Add only a `canonical_key` decision (`observation_id: obs-a, key: garden-a`):
  `review_count` stays `1` — `PROJECT_KEY_AMBIGUOUS` still fires, because the
  *resolved* `project_key` of project `a` is now `garden-a` but its raw hint is still
  `garden`, and the ambiguity is keyed on raw hints:
  `keys: ['garden-a', 'garden']`, review still `['PROJECT_KEY_AMBIGUOUS']`.
- Add only a `reject_match` decision (`obs-a` vs `obs-b`): the two projects remain
  separate AND both still expose the `garden` hint, so `PROJECT_KEY_AMBIGUOUS` still
  fires.

## Current behavior (intended demarcation)

- In `ledger/identity.py`, the `PROJECT_KEY_AMBIGUOUS` grouping uses
  `facts[obs_id]["project_key"]` — the observation's compatibility hint — not the
  canonical project's resolved `project_key`. A `canonical_key` decision overrides the
  resolved key only (and never changes membership), so it has no effect on this
  check. A `reject_match` cannot reduce the hint-to-project cardinality to 1.
- Non-silent surfacing is correct; the defect is confined to the advertised
  `resolution_actions` not matching what the compiler accepts.

## Proposed direction (pick one on a future work unit)

**Option A — compute the ambiguity on resolved keys.** Change the `_review` trigger
to group by the canonical project's *resolved* `project_key` (with the
`canonical_key` override applied) rather than the raw hint. Then an operator who
assigns a distinct `canonical_key` to one of two same-key projects successfully
disambiguates and clears the review — matching the advertised `resolution_actions`.
Keep the raw hint as a `project_key_hint` referent so the original operator-facing
key is not lost. This changes review output for the disambiguated case only; the
`PROJECT_KEY_AMBIGUOUS` review for genuinely-unresolved same-key distinct projects
is preserved.

**Option B — correct the `resolution_actions` instead.** If ambiguity-on-raw-hint is
intended (the review is a *warning that a human key is shared*, not a fixable-by-key
condition), then trim the advertised actions to the one that works (`merge`) and
clarify in `detail`/`reason_automation_stopped` that a `canonical_key` override does
not close the warning because the raw hint remains shared.

**Option C — record the ineffective-action attempt as a durable decision.** When an
operator issues a `canonical_key` / `reject_match` while a `PROJECT_KEY_AMBIGUOUS`
review is open on those observations, keep the review but record a resolution-attempt
decision so an operator UI can show "an attempt was made; it does not clear the
warning because the raw hint is shared." (Largest surface; only if A/B are rejected.)

## Acceptance criteria (when it lands)

- [ ] The P2.7 ratchet is not regressed: whatever is chosen, the `merge` resolution
      for `PROJECT_KEY_AMBIGUOUS` still clears the review on identical inputs.
- [ ] The advertised `resolution_actions` on `PROJECT_KEY_AMBIGUOUS` are consistent
      with actual compiler behavior (either the review clears after `canonical_key`
      in option A, or the actions are corrected in option B).
- [ ] The raw `project_key` hint remains reachable as a `project_key_hint` referent.
- [ ] Pinned review-ID fixtures and existing estate/reality fixtures are unchanged
      except where the chosen option deliberately changes this review's semantics.
- [ ] Full suite still passes (`python3 -m unittest discover -s tests`).

## Explicitly out of scope now

- Changing identity authority (keys remain referents, never merge authority —
  SCHEMA.md invariant #2).
- Auto-resolving the review (that is the P2.7 review-ratchet concern; the `merge`
  ratchet is already pinned).
- Redesigning `ledger resolve` priority semantics.
- Any change to `canonical_project_id` anchoring/stability (P2.5 behavior is correct
  and pinned).
