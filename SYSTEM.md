# Project Ledger System Model

## Definition

Project Ledger is a **project-reality compiler and control substrate**. It converts distributed, uneven, and sometimes contradictory evidence about projects into a compact, typed, provenance-preserving model that an agent can cheaply orient against and act from without rediscovering the world every session.

It is not primarily a scanner, database, dashboard, or task manager. Those are surfaces around one responsibility: **compile project reality into a trustworthy agent-readable abstraction.**

## Design target

A cold agent should be able to answer with bounded reads:

1. What is authoritative and how fresh is it?
2. What durable projects exist rather than just duplicate copies?
3. Where are their observations, and which are live, mirrored, backed up, stale, or unavailable?
4. What is known, declared, inferred, conflicted, or unresolved?
5. What is the freshest trustworthy project state and continuation point?
6. What changed since the last trusted run?
7. What requires review, and why did automation stop?
8. What is the cheapest safe action path?
9. What durable record should this session leave so the next agent starts smarter?

The optimization target is **minimum uncertainty per unit of agent attention and compute**.

## The abstraction tower

Higher layers compile lower layers but never erase their provenance.

```text
L0 source surfaces     filesystems, git, inventories, mirrors, backups, cloud metadata
L1 source snapshots    bounded statements of what a source looked like at a time
L2 observations        normalized project-like manifestations
L3 claims/evidence     observed facts, declarations, inferences
L4 identity graph      evidence and decisions linking observations
L5 canonical projects  durable project entities retaining all observations
L6 current state       lifecycle/session/continuation state
L7 derived signals     freshness, conflicts, changes, review/work candidates
L8 agent views         system manifest, project capsules, review queue, change feed
L9 receipts            durable records of material agent work feeding the next compile
```

The control loop is:

```text
SENSE -> NORMALIZE -> RECONCILE -> MATERIALIZE -> ORIENT -> ACT -> RECORD -> SENSE
```

## Ownership boundaries

Source systems own raw reality. Repositories own code/history; filesystems own paths; inventory systems own inventory; knowledge systems own deep memory.

Project Ledger owns project topology: normalized observations, provenance, identity evidence and decisions, canonical project identity, current-state resolution, freshness/conflict semantics, and compiled agent views.

Task systems own task lifecycle. Project Ledger may expose a next step, blockers, readiness, or work candidates, but should not become a competing task manager.

The rule is:

```text
Project Ledger: what project is this, where is it, what state is it in, what is uncertain?
Task system:     what work should execute, by whom/what, and through what workflow?
```

## Canonical state is compiled

Canonical project outputs are materialized views, not hand-authored truth. Agents/operators may change compilation inputs such as:

- source configuration;
- project declarations/sidecars;
- identity merge/split/alias decisions;
- review resolutions;
- session receipts;
- explicit lifecycle/current-state declarations.

The compiler rebuilds canonical state from those inputs. This makes state explainable, rebuildable, reversible, diffable, and safe for agents to extend.

## Epistemic types

Do not flatten different kinds of knowledge into one field.

- **Observation** — deterministic source fact: path existed, git HEAD was X, inventory contained ID Y.
- **Declaration** — explicit maintained assertion: project key, display name, lifecycle state.
- **Inference** — machine conclusion with confidence: probable duplicate, likely preferred copy.
- **Decision** — durable ambiguity resolution: merge/split observations, accept alias, prefer a claim.

Every important canonical value should be explainable through these layers.

## Authority is field-specific

There is no universal rule like “sidecar always wins.” Resolution policy depends on the field. A human merge/split decision outranks identity heuristics; observed git HEAD outranks stale prose; a fresh trusted session receipt may outrank an older continuation pointer.

An agent should be able to ask of any resolved value: where did it come from, how fresh is it, what conflicts existed, and why was it selected?

## Stable identity

Names and locations are evidence, not identity. The target model distinguishes:

- `source_id`
- `snapshot_id`
- `observation_id`
- immutable `canonical_project_id`
- human-readable `project_key`
- aliases such as historical keys, names, paths, URLs, and source-native IDs.

A canonical project must survive moves, renames, remote changes, machine migrations, archive/restore, and display-name changes.

## Freshness semantics

Stale is not false, missing is not absent, and blank is not unknown. The model must distinguish at least:

- `unknown`
- `unavailable`
- `stale`
- `absent`
- `conflicted`
- known/current

Snapshots and mutable claims carry explicit `observed_at`/`as_of` semantics.

## Cheap agent read path

The storage/provenance model can be rich; the normal read path should be tiny.

### System manifest

Target: `state/system-manifest.json`

It should expose compiler/schema version, run ID/time, config digest, source health/freshness, canonical-project vs observation counts, review counts, recent material changes, capability/command surface, and pointers to deeper artifacts.

### Project capsule

Target: `state/projects/<canonical_project_id>.json`

One compact semantic cache per project containing identity/aliases, one-line purpose, lifecycle/current state, last trusted session and next step, canonical URL, observations ranked by utility/freshness, preferred working location when resolvable, fresh git facts, open conflicts/review items, `as_of` data, and pointers to deeper evidence/task/knowledge context.

### Review queue

Target: `state/review-queue.json`

Each item should say exactly what is ambiguous, evidence considered, confidence/severity, why automation stopped, available resolutions, and the consequence of a wrong decision.

### Change feed

Target: `state/changes.json`

Agents should consume deltas rather than repeatedly reconstruct the whole estate.

## Agent control surface

The eventual conceptual CLI/API should be small and stable:

```text
ledger orient
ledger resolve <referent>
ledger show <project>
ledger locate <project>
ledger changes [--since RUN]
ledger review
ledger explain <field|relationship|review-id>
ledger sources
ledger compile
ledger validate
ledger record-session <project> ...
```

Stable JSON output is the agent contract; human rendering is another view.

## Resource economy

Use the cheapest trustworthy method first:

```text
1 deterministic extraction/exact keys
2 deterministic normalization/hashes/aliases
3 explicit heuristics with confidence
4 cheap/local semantic model if needed
5 frontier reasoning for consequential ambiguity
6 human review for high-impact unresolved decisions
```

Fingerprint sources, skip unchanged work, recompute only affected projects, cache derived views with source digests, batch ambiguity, and record resolutions so the same uncertainty is never paid for twice.

## Agent accretion: the knowledge ratchet

Every competent session should make future sessions cheaper. Durable ratchets include identity decisions, accepted aliases, source classification, review resolutions, lifecycle transitions, trusted session receipts, known source quirks, and regression fixtures created from failures.

The target session receipt records project identity, time, execution identity when available, base/result refs, bounded work summary, material areas changed, verification, unresolved discoveries, next continuation point, and links to commits/PRs/tasks/evidence.

Receipts supplement git/task history; they exist for cross-system continuity.

## Preferred working location

A project may exist in several places. The system should rank observations using explicit criteria such as live working root over mirror over backup, accessible over unavailable, canonical-remote match over uncertain identity, fresh/clean mainline over divergent stale copy, recent trusted session over untouched copy, and source/node policy.

“Preferred” must always include reasons.

## System invariants

1. No silent canonicalization.
2. No provenance loss.
3. No untraceable canonical edits.
4. No stale-as-current ambiguity.
5. No blank-as-unknown ambiguity.
6. No name/path as durable identity.
7. No expensive reasoning before deterministic evidence is exhausted.
8. No duplicate task system.
9. No material agent session without a bounded handoff/receipt.
10. No repeated ambiguity tax: resolved questions become durable decisions/tests.
11. Sensing never mutates source projects.
12. Contract changes eventually require executable validation/tests.
13. `main` is product authority; shadow implementations are defects.

## Failure containment

Failures should be local and explicit. One broken source marks that source unavailable; one malformed declaration becomes a bounded review/error; one identity ambiguity blocks only that resolution; stale evidence remains visible as stale.

Prefer:

```text
stable error/review code + affected IDs + evidence + recovery action
```

over raw tracebacks or silent omission.

## Documentation control plane

- `README.md` — short orientation/current capability boundary.
- `SYSTEM.md` — canonical conceptual model and invariants.
- `AGENT_PROTOCOL.md` — agent read/act/handoff protocol.
- `ARCHITECTURE.md` — implementation architecture.
- `SCHEMA.md` — typed entities/contracts.
- `PRD.md` — outcomes/acceptance criteria.
- `ROADMAP.md` — gated system evolution.
- `BACKLOG.md` — executable work packages.
- `RUNBOOK.md` — operations/recovery.
- `docs/decisions/` — durable architectural decisions/rationale.

When they disagree: executable schema/tests outrank prose; `SYSTEM.md` invariants and explicit decisions outrank roadmap/backlog/generated views. Conflicts should be repaired, not interpreted indefinitely.

## End state

A cold agent should read/call `orient`, resolve the project, load one capsule, understand freshness/location/state/uncertainty, follow deeper pointers only when needed, do bounded work, emit a receipt, and leave the next agent with a strictly better model.

The emergent property is **epistemic compression without epistemic loss**: a small coherent working model backed by fully traceable evidence.
