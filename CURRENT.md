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
 -> typed manifestation observations
 -> exact-remote identity evidence + explicit decisions
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

Status: COMPLETE when this PR lands.

New observable capability:

> Given an exact canonical ID, known path, exact normalized repository remote, operator-approved alias, project-key hint, or display name, return one canonical project, explicit ambiguity, or unresolved.

Automatic identity remains narrower than resolution: only exact normalized repository remotes auto-merge. Explicit identity decisions are durable compiler inputs.

### R0 — Live provider proof

Status: READY on a node with the intended source access; can execute independently of R2 implementation.

Run the deployed homelab Project Ledger watchdog against current `main`. Retain:

- Project Ledger commit;
- manifest run ID / input as-of;
- health and counts;
- every unavailable/degraded source;
- representative real duplicate/ambiguity cases worth turning into fixtures.

If live access is unavailable, record the bounded deployment/source defect; do not fake freshness.

## Stop condition

Do not add broad ingestion adapters, UI, model-first dedupe, portfolio analytics, workflow engines, or heavy incremental machinery while exact canonical identity is still unavailable.

After R0 produces current production evidence, synthesize the next constraint from demonstrated use. Do not automatically start R3 merely because it is next in the design sequence.
