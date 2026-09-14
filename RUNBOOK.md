# Runbook

## Purpose

Operate and extend Project Ledger safely while preserving its role as a project-reality compiler. `SYSTEM.md` defines the invariants; `AGENT_PROTOCOL.md` defines the agent loop.

## Commands available today

```powershell
python build_ledger.py
python -m unittest tests.test_build_ledger
```

CI compiles `build_ledger.py` and runs the test suite on pushes and pull requests.

The `ledger orient/resolve/show/...` command family described in the design docs is target architecture, not yet implemented.

## Authority rule

`main` is product authority. Generated outputs are views. Target canonical project views will also be compiled outputs rather than directly edited truth.

Normal development loop:

1. bounded task;
2. focused branch/workspace;
3. implementation + tests + docs;
4. review/foreign QA;
5. resolve findings;
6. merge promptly;
7. leave `main` authoritative.

## Agent orientation today

For system-development work:

1. `SYSTEM.md`;
2. `AGENT_PROTOCOL.md`;
3. only the relevant architecture/schema/runbook/roadmap sections;
4. current generated/source artifacts only if the task depends on estate state.

For a real-source refresh, qualify the output by which configured sources were actually accessible. Code freshness and ledger-data freshness are separate facts.

## Target orientation path

When implemented, routine agents should use:

```text
ledger orient
 -> ledger resolve <referent>
 -> ledger show <canonical project>
 -> ledger locate <canonical project> if work location matters
 -> ledger explain ... only when deeper evidence is needed
```

This target read path should replace routine repo archaeology, not add another mandatory layer on top of it.

---

# Source operations

## Adding a live filesystem source

1. Register/configure the source with a useful stable source identity/label.
2. Choose `children`, `git_repos`, or `self` today.
3. Preserve machine/storage/source-class provenance.
4. Set exclusions intentionally.
5. Use forced inclusion only for known low-signal projects.
6. Run tests/validation and scanner.
7. Inspect source coverage/gaps and newly introduced duplicates.
8. Once source registry/fingerprints land, ensure freshness/fingerprint policy is explicit.

Missing/unreadable ordinary roots should degrade as source gaps, not become proof that projects disappeared.

## Adding an inventory-policy source

Use when a durable inventory should be interpreted rather than recursively crawled.

Current required inputs:

- `path` when useful for navigation;
- `discovery: inventory_policy`;
- `inventory_jsonl`;
- `policy_path`;
- intended `policy_crawl_treatments`, normally `project_discovery`;
- normally `require_project_ledger_candidate: true`;
- source/machine/storage metadata as appropriate.

Required metadata artifacts fail early with operator-readable validation errors.

Architectural rule:

```text
inventory discovers source reality
 -> policy decides project relevance
 -> Project Ledger normalizes observations
 -> identity compiler decides canonical project membership
```

Policy promotion is not canonical identity.

---

# Project/identity operations

## Project missing from current observation output

Check in order:

1. source configured/enabled?
2. source actually accessible/current?
3. excluded?
4. candidate threshold/signals sufficient?
5. inventory policy promoted the root?
6. source inventory stale/incomplete?
7. declaration/sidecar malformed?

Do not jump directly to force-inclusion before verifying source health/policy.

## Duplicate-looking projects today

Current short-term correction for live projects:

- use stable sidecar `project_key` and canonical URL only when justified;
- retain both observations in outputs;
- document ambiguous cases rather than collapsing them manually in generated output.

Target state: identity evidence + explicit merge/split/reject decisions + review queue.

## Identity resolution target rule

Never merge based only on same display name. Prefer strong deterministic evidence; preserve ambiguity when evidence remains weak.

After Wave 2, a resolved identity question should produce a durable decision so it does not recur.

---

# Schema/contract changes

Any schema/protocol change must address:

1. current compatibility impact;
2. schema/version changes;
3. code/model/validation;
4. `SCHEMA.md` and relevant design docs;
5. tests/golden fixtures;
6. migration/unsupported-version behavior;
7. agent/operator read/write behavior.

Do not silently change the meaning of an existing field while keeping the same version.

When the target claim model lands, preserve observed/declaration/inference/decision provenance through transformations.

---

# Sidecar/declaration operations

Today `.project-ledger.json` is the thin project-local overlay for stable key/display/current-session hints.

Rules:

- only write facts justified by the project/current session;
- do not invent push timestamps;
- do not casually replace stable keys;
- keep `next_step` bounded;
- do not encode a task tree;
- recognize that copied projects can have divergent sidecars.

Target state: sidecars become versioned declaration sources. Conflicting sidecars create competing claims/review rather than last-write-wins canonical state.

---

# Session-end operations

## Compatibility workflow today

For material downstream project work:

1. update sidecar factual current-state fields when appropriate;
2. factual bounded session summary;
3. one continuation point;
4. `last_push_at` only if actually justified;
5. return/record exact changed files, verification, commit/PR refs, unresolved issues.

Use `prompts/session_end_prompt.md`.

## Target workflow

Prefer a structured session receipt linked to canonical project identity, then compile affected state/views. The receipt should capture landed state and evidence pointers, not narrate the whole session.

A future agent should be able to continue from project capsule + receipt without replaying chat history.

---

# Real-source refresh

On a node with intended source access:

1. pull current `main`;
2. run tests;
3. run scanner/compile;
4. inspect source coverage and warnings;
5. inspect obvious duplicates/identity anomalies;
6. verify output timestamps/source-as-of semantics;
7. commit refreshed human-facing snapshot only when intentional/trustworthy;
8. convert nontrivial anomalies into fixtures/backlog items rather than rediscovering them later.

Do not claim the ledger is current because code changed. Data freshness is source/run-specific.

---

# Review and failure recovery

Prefer local bounded failures.

## Source unavailable

- preserve prior evidence as stale if policy allows;
- mark source unavailable;
- do not infer deletion/absence;
- continue healthy sources.

## Malformed declaration

- scope error to declaration/project;
- retain underlying source observation;
- surface actionable validation/review;
- do not poison unrelated projects.

## Ambiguous identity

- retain separate observations/candidates;
- surface review with evidence;
- do not guess for cleanliness.

## Conflicting current-state claims

- compare authority/freshness according to field-specific policy;
- resolve only when policy permits;
- otherwise open review.

## Unsupported schema version

- fail/degrade explicitly;
- never silently parse incompatible major versions as if compatible.

---

# Resource-economy rules

1. Deterministic source evidence before model reasoning.
2. Fingerprint/skip unchanged sources once supported.
3. Recompute affected projects rather than global semantic reconstruction.
4. Cache derived summaries by evidence digest.
5. Local/cheap semantic model before frontier reasoning where safe.
6. Human review only when consequence/ambiguity justifies it.
7. Record the resolution so the same ambiguity is not paid for twice.

---

# Handoff checklist

Before ending substantive Project Ledger work:

- tests/CI status known;
- behavior/docs/contracts synchronized;
- source refresh performed only if relevant surfaces were available;
- new uncertainty/error paths explicit;
- nontrivial real failure converted into fixture/decision/backlog when useful;
- exact continuation point recorded;
- completed bounded work merged so `main` remains authority.
