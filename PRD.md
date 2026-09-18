# PRD

## Product

Project Ledger — project-reality compiler and agent control substrate.

## Product thesis

Projects exist as distributed evidence across repos, working directories, mirrors, backups, inventories, clouds, project declarations, and work history. Humans can often reconstruct which things are the same project and which copy is current by intuition; agents pay that reconstruction cost repeatedly and are more vulnerable to stale, duplicated, or ambiguous state.

Project Ledger should compile that distributed evidence into a **small, trustworthy, explainable project model** so an agent can orient accurately, choose the right working surface, understand uncertainty, act cheaply, and leave structured continuity for the next agent.

The success criterion is not “we indexed everything.” It is **routine project control with bounded reads, explicit uncertainty, and minimal repeated reasoning.**

## Current status

The September 2026 implementation has crossed two boundaries:

1. estate observation/orientation;
2. a conservative first canonical-identity/exact-resolution slice.

Implemented now:

- multi-source compatibility scanner with filesystem/git/inventory-policy ingestion and sidecar overlays;
- stable source, compatibility-snapshot, and manifestation-oriented observation identities;
- typed observation materialization;
- source probes/health with degraded-source containment;
- canonical projects compiled from exact normalized repository remotes plus explicit durable identity decisions;
- identity review queue for blocked/conflicting/ambiguous exact referents;
- versioned `state/system-manifest.json`, `observations.json`, `canonical-projects.json`, and `review-queue.json`;
- `ledger validate`, `refresh`, `compile`, `orient`, `sources`, and exact `resolve`;
- a homelab deployment seam that can run non-mutating provider refreshes.

Not implemented yet:

- preferred-location resolver and project capsules;
- `show/locate/explain`;
- structured session receipts/current-state resolver;
- Project Ledger semantic change feed and incremental compilation.

Automatic identity is intentionally narrow: names and compatibility project keys never auto-merge projects. They remain resolution/ambiguity surfaces until stronger typed declaration provenance exists.

The committed human-facing ledger is historical evidence; current production freshness and identity behavior require a live provider run on a node with the intended source access.

## Primary users

### Agent operator

An autonomous or supervised agent that needs to understand project reality accurately enough to act without broad rediscovery.

### Human operator

The owner who needs a comprehensible project estate, explicit review points, and confidence that agents are acting on the right project/copy/state.

### Downstream systems

Task/execution control planes, homelab orchestration, reporting/analytics, and knowledge systems that need project identity/state without implementing their own deduplication logic.

## Jobs to be done

### Orientation

Given a cold start, tell an agent what system state is trustworthy, how fresh it is, which sources are degraded, and what capabilities are available.

### Resolution

Given a messy referent such as a name, path, URL, repo, or old key, resolve it to one canonical project or return explicit ambiguity.

### Location

For a canonical project, rank known observations/working locations and explain which is safest/useful to operate on from the current context.

### State continuity

Expose the freshest trustworthy project lifecycle/session/continuation state without forcing the agent to reconstruct chronology from commits and prose.

### Explainability

For identity, preferred location, or resolved state, show why the system believes what it believes and what conflicts remain.

### Change/review

Show material deltas and concentrate uncertainty into a bounded review queue.

### Accretion

After material work or review, preserve structured receipts/decisions so the same reconstruction or ambiguity is not paid for again.

## Product goals

### G1. Agent-legible reality

A normal agent should orient from one system manifest and one project capsule, following deeper pointers only when necessary.

### G2. Durable identity

Represent conceptual projects independently of path, name, machine, current remote, or individual copy.

### G3. Provenance without cognitive overload

Keep full traceability underneath compact agent views. Compression must not destroy explainability.

### G4. Explicit epistemics

Distinguish observed, declared, inferred, decided, unknown, stale, unavailable, absent, and conflicted states.

### G5. Incremental economy

Avoid rescanning/reasoning globally when source fingerprints or deltas can limit work to affected entities.

### G6. Ambiguity containment

Stop safely when identity/state cannot be resolved confidently and create a small actionable review item rather than a plausible guess.

### G7. Knowledge ratchet

Every durable identity/review/session resolution should make future operation cheaper and more reliable.

### G8. Clean system boundaries

Own project topology/current-state compilation; integrate with but do not duplicate task lifecycle, deep knowledge memory, source-control history, or raw inventories.

## Non-goals

Project Ledger should not:

- become a general task/project management system;
- become a document/semantic-memory store;
- copy entire source repos/files/inventories into its own canonical store;
- mutate target projects while sensing them;
- silently solve ambiguous identity for aesthetic cleanliness;
- require frontier-model reasoning for ordinary metadata extraction;
- provide real-time distributed services before local/versioned contracts are stable;
- build a heavy UI before the agent/query model works;
- treat every source as always online;
- equate “not observed right now” with “does not exist.”

## Functional requirements

### FR1. Source registry and snapshots

The system accepts configured live and inventory-backed sources with stable source identity, source class, freshness policy, adapter metadata, and bounded source health/errors.

Each sensing pass produces a snapshot identity/fingerprint sufficient for change detection and provenance.

### FR2. Normalized observations

Source-native facts become typed project observations with stable observation identity where possible, provenance, timestamps, source-native identifiers, and access state.

### FR3. Claims and declarations

Observed facts, project-local/operator declarations, and inferred claims retain epistemic type, provenance, time, and confidence where relevant.

### FR4. Canonical identity

The system compiles observations into immutable canonical project identities using explicit evidence/decision rules. Ambiguous matches become review items.

### FR5. Field-specific resolution

Canonical/current-state fields are resolved through named testable policies rather than one global precedence rule. Selected values retain evidence references and conflicts.

### FR6. Current state

The system resolves lifecycle/session/continuation state and preferred working observation while preserving freshness and conflict semantics.

### FR7. Agent views

Materialize at minimum:

- system manifest;
- project capsules;
- review queue;
- change feed.

Views must carry run/schema/as-of metadata and pointers to deeper evidence.

### FR8. Query/control contract

Provide stable machine-readable operations conceptually equivalent to:

- orient;
- resolve;
- show;
- locate;
- explain;
- changes;
- review;
- sources;
- compile/validate;
- record-session.

### FR9. Durable decisions

Identity merge/split/alias and review resolutions are durable compiler inputs, not transient chat conclusions.

### FR10. Session receipts

Material project work can emit a compact factual receipt linked to the project and relevant git/task/evidence references.

### FR11. Incremental compilation

Unchanged source fingerprints should avoid unnecessary extraction/reasoning. Source deltas should invalidate only affected observations, identity components, canonical projects, signals, and views where practical.

### FR12. Compatibility/migration

The current flat observation output remains available through a documented compatibility period while versioned target entities are introduced beside it.

## Non-functional requirements

### NFR1. Determinism

Unchanged deterministic inputs/decisions produce semantically stable compiled outputs except explicit run metadata.

### NFR2. Explainability

Important identity/state/location resolutions can be explained from retained evidence and policy without rerunning expensive reasoning.

### NFR3. Failure containment

One source failure, malformed declaration, or ambiguous identity should degrade only affected scope.

### NFR4. Minimal expensive inference

The default path uses exact/deterministic logic; semantic models are reserved for unresolved ambiguity.

### NFR5. Bounded context

Normal agent orientation should not require loading the complete observation/evidence store or all design docs.

### NFR6. Versionability

Schema and query contracts carry explicit versions and incompatible major versions fail/degrade explicitly rather than being guessed through.

### NFR7. Idempotence

Compile/materialization/review resolution operations should be safely repeatable and avoid semantic churn.

### NFR8. Authority

`main` is product authority; generated views are not hand-maintained canonical truth.

## Agent-ergonomic acceptance tests

The architecture should eventually pass scenarios like:

1. A cold agent can determine source health, run freshness, project counts, and open review burden with one bounded orientation read.
2. `homelab`, a path to a homelab copy, and its canonical URL resolve to one project when evidence/decisions support that, with all observations retained.
3. Two same-name unrelated projects return ambiguity/separate canonical IDs rather than being merged.
4. A backup remains visible as a stale observation but is not selected as the preferred working location over a healthy live checkout.
5. An unavailable machine does not cause its projects to be marked deleted.
6. A conflicting sidecar becomes explicit claims/review instead of last-write-wins state.
7. After an identity review is resolved, the same inputs no longer trigger the same review.
8. A material agent session leaves enough receipt/current-state data that the next agent can continue without replaying the previous conversation.
9. Changing one source causes bounded recomputation rather than mandatory semantic re-analysis of the entire estate.
10. Any recommended identity/location/state value can be explained with evidence and freshness.

## Success metrics / design targets

Prefer metrics that measure agent control quality rather than raw inventory size:

- orientation requires one manifest plus at most one project capsule for routine project work;
- percentage of project resolutions satisfied deterministically without semantic model calls;
- number/rate of recurring review items after prior resolution — target near zero;
- stale/unavailable/conflicted state is explicitly classified rather than blank;
- canonical-project counts are distinct from observation counts;
- source deltas trigger bounded affected-project recomputation;
- project capsule size remains intentionally bounded while preserving evidence pointers;
- downstream systems consume canonical IDs instead of reimplementing identity matching.

## Major risks

- over-modeling before real canonicalization fixtures exist;
- allowing convenience heuristics to masquerade as identity certainty;
- duplicating state already owned by tasks/git/knowledge systems;
- letting sidecars become divergent mini-databases;
- building views/UI before epistemic/freshness contracts stabilize;
- creating an event/receipt firehose with low-value detail;
- optimizing incremental performance before correctness fixtures exist;
- documentation drifting ahead of executable capability.

## Release direction

The immediate sequence is not “add more sources.” It is:

1. freeze/version the current observation contract;
2. establish stable IDs, epistemic types, source/snapshot contracts, and validation;
3. build canonical identity/decision/review compilation beside compatibility output;
4. materialize manifest/capsules and agent query surfaces;
5. add receipts/current-state resolution and delta-first operation;
6. then broaden analytics/integration/UI on top of trusted abstractions.

See `ROADMAP.md` and `BACKLOG.md` for gated execution.
