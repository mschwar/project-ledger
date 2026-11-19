# Identity & Lineage Registries

This directory contains durable **compiler inputs**, not generated canonical state:

- `identity-decisions.json` (`schemas/identity-decisions.schema.json`) — operator- and reviewed-agent-approved identity resolutions (`merge`, `split`, `reject_match`, `alias`, `canonical_key`, `supersede`).
- `project-lineage.json` (`schemas/project-lineage.schema.json`) — durable immutable `canonical_project_id` lineage registry (`ledger lineage --check` / `ledger lineage --sync`).
- `project-catalog.json` (`schemas/project-catalog.schema.json`) — curated repository profile catalog declaring `display_name`, `description`, `goal` (why it exists), `what_it_does`, and `primary_language` per `canonical_project_id` without mutating target checkouts.
- `weak-overlap-triage-candidates.json` (`schemas/weak-overlap-triage-candidates.schema.json`) — reviewed frontier triage candidate batches (`ledger review --approve-candidates`).

Decisions use stable `decision_id` values and include rationale/authority/timestamp metadata. Missing referenced observations degrade into bounded `DECISION_REFERENCE_UNAVAILABLE` review items during compilation (resolved via append-only `supersede` decisions) rather than erasing the rest of the estate.

Generated `state/canonical-projects.json` and `state/review-queue.json` are rebuildable views and must not be edited as decision sources.
