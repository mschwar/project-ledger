# Future Spec — Give `DECISION_SUPERSEDE_UNKNOWN` review a decision-scoped referent instead of empty `observation_ids`

- Status: **landed (P2.6, 2026-09-19)** — implemented in `ledger/identity.py` `_review`
  and covered by `tests/test_p26_review_queue.py`. The `affected_decision_ids` referent
  and the review_id-collision fix are now on `main`.
- Date: 2026-09-19 (UTC)
- Node: Matthews-MacBook-Air-3 (Hermes child node)
- Repo: `github.com/mschwar/project-ledger`
- Found while: reviewing the P2.4 decision-registry review envelope (`DECISION_SUPERSEDE_UNKNOWN`)
- Tracks: kanban future spec `t_bfe5448f`

## Problem

P2.4 (decision registry) added the `supersede` decision type. When a `supersede`
decision references an unknown `supersedes_decision_id`, the identity compiler emits a
bounded `DECISION_SUPERSEDE_UNKNOWN` review. Because a supersede decision references
another *decision* (not observations), the review is created with
`affected_observation_ids: []`.

Reproduced on `main` (`67acc56`) with a `supersede` decision referencing
`dec-does-not-exist`:

```json
{
  "review_id": "rev_75600c35ef0691c435da6d12",
  "code": "DECISION_SUPERSEDE_UNKNOWN",
  "state": "open",
  "affected_observation_ids": [],
  "detail": "Supersede decision dec-sup references unknown decision 'dec-does-not-exist'.",
  "evidence": [{"kind": "decision", "decision_id": "dec-sup", "type": "supersede"}]
}
```

This is semantically odd: the review is about a *decision reference*, not observations.
Downstream consumers that key review items by `affected_observation_ids` (or assume it
is non-empty) will mis-handle this item. The `detail` and `evidence` carry the real
signal, but the envelope is inconsistent with every other review code.

## Second, more serious defect: review_id collision

Because `_review` derives `review_id` from `stable_id("rev", code, ids)` and `ids` is
the **empty** observation list, **two distinct supersede-unknown reviews collapse to
the same `review_id`**. `compile_identity` stores reviews in `review_by_id` (a dict
keyed by `review_id`), so the first review is silently dropped.

Reproduced on `main` (`67acc56`) with two supersede decisions referencing two
*different* unknown decisions (`dec-ghost-a`, `dec-ghost-b`):

```text
review_count: 1
rev_75600c35ef0691c435da6d12 DECISION_SUPERSEDE_UNKNOWN Supersede decision dec-sup2 references unknown decision 'dec-ghost-b'.
distinct review_ids: 1
```

Only one review survives; the `dec-sup1 → dec-ghost-a` review is lost. This is a real
data-loss bug in the review queue, not just an envelope inconsistency. Any fix that
adds a decision-scoped referent must also feed that referent into the `stable_id`
material so each distinct unknown-target review gets a distinct `review_id`.

## Current behavior (intended for the first identity slice)

- `_review(code, observation_ids, detail, evidence=...)` in `ledger/identity.py` builds
  the envelope with `affected_observation_ids: sorted(set(observation_ids))` and
  `review_id = stable_id("rev", code, ids)`.
- `_decision_observation_ids` returns `[]` for `supersede` (correctly — it references a
  decision, not observations), so the `DECISION_SUPERSEDE_UNKNOWN` review is emitted
  with an empty observation list and a review_id hashed over that empty list.
- `review_by_id` is a dict keyed by `review_id`; duplicate review_ids silently drop
  earlier items.

## Proposed change

Add a decision-scoped referent to the review envelope so a review can reference a
decision (or other non-observation entity) without an empty observation list, and feed
that referent into the `review_id` material so distinct unknown-target reviews do not
collide.

Two options (the spec recommends option 1 for minimal surface, with option 2 as the
generalization):

1. **Add an optional `affected_decision_ids` field** to the review item, populated for
   `DECISION_SUPERSEDE_UNKNOWN` (and any future decision-scoped review). Keep
   `affected_observation_ids` present (possibly empty) for backward compatibility.
2. **Generalize the review envelope with a `subject` field**
   (`{kind: "decision", id: "dec-sup"}`) alongside `affected_observation_ids`.

Concretely (option 1):

- Extend `_review` to accept an optional `decision_ids: list[str]` keyword, defaulting to
  `[]`, and include it in the envelope as `affected_decision_ids` and in the
  `stable_id` material: `stable_id("rev", code, ids, decision_ids)`.
- In the `DECISION_SUPERSEDE_UNKNOWN` emission, pass
  `decision_ids=[decision["decision_id"]]` (the *superseding* decision id — the one that
  exists and is the review's subject). Optionally also surface the unknown target in
  `evidence` (already present in `detail`).
- Update `SCHEMA.md` §1.7 review-queue envelope and §2.8 review item to document
  `affected_decision_ids`.
- Add a regression test asserting:
  - a `DECISION_SUPERSEDE_UNKNOWN` review carries a non-empty `affected_decision_ids`
    equal to the superseding decision id;
  - two supersede decisions referencing two different unknown decisions produce **two**
    distinct review items with distinct `review_id`s (no collision).

## Acceptance criteria (when it lands)

- [ ] A `DECISION_SUPERSEDE_UNKNOWN` review carries an explicit, non-empty
      `affected_decision_ids` referent.
- [ ] Two supersede decisions referencing two different unknown decisions produce two
      distinct review items with distinct `review_id`s (collision fixed).
- [ ] Existing review codes keep their current `affected_observation_ids` behavior
      (field remains present, possibly empty, for decision-scoped reviews).
- [ ] `SCHEMA.md` documents the new field; a regression test covers both the referent
      and the collision.
- [ ] Full suite still passes after the change.

## Explicitly out of scope now

- Changing the `supersede` decision schema or `_decision_observation_ids` semantics.
- Resolving the review automatically (that is the P2.7 review-ratchet concern).
- Adding decision-scoped referents to review codes that already reference observations
  (`DECISION_REFERENCE_UNAVAILABLE`, `DECISION_CONFLICT`, etc.) — those keep their
  observation-based envelope.
