# Current

This is the canonical executable frontier for Project Ledger. Read it before roadmap/backlog archaeology.

## Product objective

A cold agent should be able to take a messy project referent, determine which conceptual project the operator means, identify trustworthy manifestations and current state, understand uncertainty, and continue work without replaying filesystem/repository archaeology.

## Current walking skeleton

Implemented:

```text
registered sources
 -> compatibility scan
 -> source probes + compatibility snapshots
 -> typed manifestation observations
 -> system manifest
 -> ledger orient / ledger sources
```

This crosses the **estate observation/orientation** boundary.

Not implemented yet:

```text
identity evidence + durable decisions
 -> canonical projects
 -> identity review
 -> exact resolve
 -> preferred working location
 -> project capsules/current state/receipts
```

The active product constraint is **observation -> canonical project identity**.

## Current commands

```bash
python -m ledger validate
python -m ledger refresh
python -m ledger refresh --no-markdown-mirror
python -m ledger compile
python -m ledger orient
python -m ledger sources
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

Status: in progress on the current bounded work unit.

Exit:

- operating docs agree with executable Wave 1 reality;
- already-landed work is not left looking READY in the backlog;
- the non-mutating provider regression test is actually discovered by configured CI;
- this file becomes the single frontier pointer.

### R2 — Identity walking skeleton

Status: next executable software unit.

Cross this observable boundary:

> Given exact strong referents/evidence, compile stable conceptual project identity and resolve to one canonical project or explicit ambiguity.

Minimum acceptance:

- deterministic strong evidence only;
- one canonical project may retain multiple observations;
- same-name unrelated observations are not silently merged;
- explicit durable merge/split/reject/alias decisions are compiler inputs;
- unchanged inputs produce stable canonical IDs/results;
- `ledger resolve <referent>` exists with JSON output;
- every result exposes evidence/reason codes;
- no semantic model required.

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

After R2 lands, reassess the next constraint from demonstrated use rather than automatically executing the rest of the roadmap.
