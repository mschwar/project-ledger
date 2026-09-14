# Architecture Decision Records

Use this directory for durable system decisions whose rationale future agents should not have to reconstruct.

Create an ADR when a decision:

- establishes or changes a system invariant;
- chooses ownership/boundaries between subsystems;
- changes identity/schema/version semantics;
- introduces a difficult-to-reverse storage/protocol choice;
- resolves a recurring architectural ambiguity;
- rejects a plausible alternative future agents are likely to reconsider.

Do not create ADRs for ordinary implementation details that are obvious from code/tests.

## Minimal ADR structure

```text
# ADR-NNNN: Decision title

- Status: proposed | accepted | superseded
- Date: YYYY-MM-DD

## Context
What recurring problem/constraint required a decision?

## Decision
What is now true?

## Consequences
What does this enable/cost/constrain?

## Alternatives rejected
What plausible alternatives were considered and why rejected?

## Follow-up
What contracts/tests/work must reflect the decision?
```

When superseding an ADR, preserve the old record and link both directions. The goal is an architectural knowledge ratchet, not a mutable wiki that erases why the system became what it is.
