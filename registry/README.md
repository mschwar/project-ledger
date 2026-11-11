# Identity & Lineage Registries

This directory contains durable **compiler inputs**, not generated canonical state:

- `identity-decisions.json` (`schemas/identity-decisions.schema.json`) — operator- and reviewed-agent-approved identity resolutions (`merge`, `split`, `reject_match`, `alias`, `canonical_key`, `supersede`).
- `project-lineage.json` (`schemas/project-lineage.schema.json`) — durable immutable `canonical_project_id` lineage registry (`ledger lineage --check` / `ledger lineage --sync`).
- `weak-overlap-triage-candidates.json` (`schemas/weak-overlap-triage-candidates.schema.json`) — reviewed frontier triage candidate batches (`ledger review --approve-candidates`).

Decisions use stable `decision_id` values and include rationale/authority/timestamp metadata. Missing referenced observations degrade into bounded `DECISION_REFERENCE_UNAVAILABLE` review items during compilation (resolved via append-only `supersede` decisions) rather than erasing the rest of the estate.

Generated `state/canonical-projects.json` and `state/review-queue.json` are rebuildable views and must not be edited as decision sources.
