# Identity Decision Registry

This directory contains durable **compiler inputs**, not generated canonical state.

`identity-decisions.json` is intentionally empty until an ambiguity is explicitly resolved.

Supported first-slice decision types:

- `merge` — authoritative union of two or more observation IDs;
- `split` — authoritative negative relationship across the listed observation IDs;
- `reject_match` — authoritative negative relationship for one observation pair;
- `alias` — attach an exact operator-approved referent to the canonical project containing an observation.

Decisions use stable `decision_id` values and should include rationale/evidence metadata when humans or reviewed agents add them. Missing referenced observations degrade into bounded review items during compilation rather than erasing the rest of the estate.

Generated `state/canonical-projects.json` and `state/review-queue.json` are rebuildable views and must not be edited as decision sources.
