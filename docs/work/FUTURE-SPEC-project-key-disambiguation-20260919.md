# Future Spec — Disambiguate auto-derived `project_key` for split same-remote projects

- Status: **proposed / not landed**
- Date: 2026-09-19 (UTC)
- Node: Matthews-MacBook-Air-3 (Hermes child node)
- Repo: `github.com/mschwar/project-ledger`
- Found while: reviewing the P2.5 canonical-project-ID compiler (`PROJECT_KEY_AMBIGUOUS`)
- Tracks: kanban future spec `t_aea60dab`

## Problem

P2.5 (canonical project IDs, PR #16) fixed the colliding `canonical_project_id`
defect: a split/reject that keeps two observations sharing an exact remote in separate
projects now gives each a distinct, stable canonical ID (each falls back to a
membership-scoped anchor). That fix is in place and pinned.

But the **auto-derived human-readable `project_key` is not disambiguated** for the
same case. When a split/reject keeps two observations that share BOTH an exact remote
AND the same compatibility `project_key`, the two distinct canonical projects each
auto-derive the **same** `project_key` value (e.g. both `"dupe"`). The compilation is
not silent — it surfaces a `PROJECT_KEY_AMBIGUOUS` review — and `project_key` is
explicitly a human-readable alias, not identity (SCHEMA.md invariant #2), so this is
**not** a correctness defect. But because the auto-derived key is not disambiguated,
`ledger resolve <project_key>` returns **ambiguity** for what an operator may consider
two distinct, known projects: both candidates are shown with the identical key
`dupe`, so resolve cannot narrate "this one" vs "that one" by key alone.

## Reproduction (post-P2.5, on `main` 14c8a0e)

Two observations `obs-a` / `obs-b` with remote `github.com/mschwar/dupe` and
compatibility `project_key` `dupe`, split by an explicit decision, compile to **two
distinct** canonical IDs, each with `project_key="dupe"`:

```text
canonical_project_count: 2
  id=prj_75405645689f5e9d0e587193 project_key='dupe' anchor=observation hints=['dupe']
  id=prj_de25e485081d9212c0426c2f project_key='dupe' anchor=observation hints=['dupe']
review_count: 2
  AUTO_MATCH_BLOCKED_BY_DECISION rev_e25485326c42a122d10e797e | Exact remote 'github.com/mschwar/dupe' matched observations that an explicit split/reject decision keeps separate.
  PROJECT_KEY_AMBIGUOUS rev_ed2a0648f3886110973d7321 | Compatibility project_key 'dupe' resolves to 2 canonical projects; no automatic merge was performed.

-- ledger resolve 'dupe' --
status: ambiguous  candidate_count: 2
  prj_75405645689f5e9d0e587193	dupe	dupe
  prj_de25e485081d9212c0426c2f	dupe	dupe
```

The `canonical_project_id`s are correctly distinct and stable (P2.5 intact). The
residual gap is purely in the human-readable alias: both projects claim the key `dupe`,
and both `project_key_hints` arrays are `['dupe']`.

## Current behavior (intended for the first identity slice)

- In `ledger/identity.py`, the auto-derived key is:
  `key_hints[0] if len(key_hints) == 1 else (remotes[0] if len(remotes) == 1 else canonical_project_id)`.
  For a split-same-remote project, `len(key_hints) == 1` (its one observation declares
  a single key), so each project independently picks `key_hints[0]` → both derive
  `"dupe"`. Each keeps the raw hint in its own `project_key_hints` and referents.
- `PROJECT_KEY_AMBIGUOUS` is emitted exactly when one compatibility `project_key`
  (casefolded) maps to >1 canonical project — here it fires with both observations
  as `affected_observation_ids`, which is the correct, non-silent signal.
- `ledger resolve <key>` matches referents by kind priority; `project_key` is priority 2
  and `project_key_hint` priority 3. `dupe` survives as a `project_key` referent on
  **both** projects, so resolve reports `status: ambiguous` with two identical-looking
  candidates.

## Proposed change

When a split/reject keeps observations that share a remote AND a `project_key`,
**disambiguate the auto-derived `project_key` for each project** while keeping the raw
key hint as a referent. Recommendation: append a **stable suffix derived from the
membership-scoped observation anchor** — the same anchor already used for the distinct
canonical ID — so the suffix is stable across recompiles and traceable.

Concretely (option A, minimal surface — recommended):

- In the `else` branch of the auto-derive line, when `len(key_hints) == 1` AND that key
  is shared by another project in the same compile (i.e. the `PROJECT_KEY_AMBIGUOUS`
  condition holds), render the project key as `<key>-<anchor-short>` where
  `<anchor-short>` is a short, stable digest of the membership-scoped anchor
  (e.g. the first ~8 chars of `stable_id("prj", anchor_kind, anchor_value)`, or the
  leaf observation id). For the reproduction that yields e.g. `dupe-75405645` and
  `dupe-de25e485` — distinct, stable, and resolvable.
- Keep the raw key hint (`dupe`) as a `project_key_hint` referent on each project so the
  original operator-facing key is not lost, and keep `PROJECT_KEY_AMBIGUOUS` (or
  downgrade it — see below).
- A `canonical_key` decision still overrides the auto-derived key unconditionally; when
  an explicit key exists there is no need to suffix (the operator supplied the identity).

Option B (alternative, larger surface): generalize the auto-derive to always prefer a
remote-unique key first, and only fall back to a suffixed hint-derived key when the raw
hint collides. This changes more resolution behavior and is not recommended for the
first landing.

## Acceptance criteria (when it lands)

- [ ] Two split same-remote projects with a shared key get **distinct, stable**
      auto-derived `project_key` values (e.g. `dupe-<suffix>` each), stable across
      repeated compiles.
- [ ] `ledger resolve <shared-key>` resolves each project individually (returns one
      candidate per distinct suffixed key) — **or**, if the raw hinted key still matches
      both, the resulting ambiguity is explicitly explained (e.g. both candidates show
      their distinct suffixed keys and anchors) rather than showing two identical `dupe`
      entries.
- [ ] The raw key hint remains reachable as a `project_key_hint` referent on both
      projects.
- [ ] Existing pinned fixture IDs and the P2.5 distinct-canonical-ID behavior are
      unchanged (the reproduction's two `canonical_project_id`s must not change).
- [ ] The `PROJECT_KEY_AMBIGUOUS` review is preserved (or explicitly downgraded in the
      same change, with rationale), and a regression fixture covers the
      split-same-remote-same-key case.
- [ ] Full suite still passes (`python3 -m unittest discover -s tests`).

## Explicitly out of scope now

- Changing `canonical_project_id` anchoring or stability (P2.5 behavior is correct and
  pinned).
- Using the disambiguated key as merge authority — the suffixed key is still a
  human-readable alias, never identity (SCHEMA.md invariant #2).
- Resolving the `PROJECT_KEY_AMBIGUOUS` review automatically (that is the P2.7 review-
  ratchet concern).
- Redesigning `ledger resolve` priority semantics beyond what is needed to make the
  split-pair case non-misleading.