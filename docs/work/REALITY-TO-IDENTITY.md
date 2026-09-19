# Reality-to-Identity Programme

Purpose: cross the shortest path from the working observation/orientation substrate to the first genuinely project-level capability.

## Invariant

Every unit must cross or directly enable an observable usability boundary. Infrastructure that does not unblock identity/resolution is deferred.

## Work units

| Unit | State | Boundary / evidence |
| --- | --- | --- |
| R1 | COMPLETE | Canonical docs + CI assurance agree with executable Wave 1 reality |
| R0 | COMPLETE | Live homelab provider proof against current sources; evidence in `docs/work/R0-LIVE-PROVIDER-PROOF-20260919.md` |
| R2 | COMPLETE | Deterministic canonical identity + exact `ledger resolve` |
| R3 | HOLD FOR SYNTHESIS | Explainable preferred-location resolution after R2 is demonstrated |
| R4 | HOLD FOR SYNTHESIS | Structured current-state/session receipt only after identity can anchor it |

## R0 operator/agent prompt

On the homelab node that owns the Project Ledger provider seam:

1. update the Project Ledger checkout to current `main` through the normal clean-repo workflow;
2. run the existing homelab Project Ledger watchdog;
3. capture commit, run ID, input as-of, health/counts, every non-available source, and whether the semantic snapshot changed;
4. inspect the current observations for at least:
   - one project represented in multiple manifestations;
   - one same/similar-name case that must remain separate;
   - one moved/renamed or stale manifestation if available;
5. return only evidence and candidate fixtures. Do not make canonical merge decisions unless the evidence is explicit.

Stop on an actual source/deployment seam; do not substitute the historical committed Markdown ledger for a live run.

## R2 landed contract

The first end-to-end identity path is:

```text
typed observations
 + deterministic strong evidence
 + optional explicit decisions
 -> canonical projects
 + bounded ambiguity
 -> ledger resolve
```

Automatic merge evidence is intentionally narrower than originally proposed: exact normalized repository remote only. Compatibility project keys and names remain referents/ambiguity surfaces until their declaration provenance is typed strongly enough to deserve identity authority. Explicit durable decisions can merge/split/reject/alias.

The implementation remains file/JSON/standard-library based. No DB, workflow engine, semantic model, or UI is justified by the current workload.

## R2 QA contract

Prove with a reality-shaped fixture that:

- live + mirror + backup manifestations of one project compile to one stable canonical ID when strong evidence supports it;
- a same-name unrelated project remains separate;
- a conflicting strong-evidence case becomes explicit ambiguity/review rather than a silent merge;
- an explicit decision ratchets the ambiguity and prevents recurrence;
- exact canonical ID, project key/alias, normalized remote URL, and known path resolve deterministically;
- unchanged inputs are semantically stable;
- `ledger orient` reports canonical identity capability accurately after the boundary lands.

## Synthesis point

R2 is now the software baseline. R0 live provider proof has produced current production
evidence against the intended estate (see `docs/work/R0-LIVE-PROVIDER-PROOF-20260919.md`).
Inspect demonstrated use and choose the next constraint from evidence — do not
automatically start R3 merely because it is next in the design sequence.
