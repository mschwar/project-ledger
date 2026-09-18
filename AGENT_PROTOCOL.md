# Agent Operating Protocol

## Purpose

This is the normative operating protocol for agents using or changing Project Ledger.

The goal is to minimize reconstruction cost, hidden assumptions, duplicated reasoning, and accidental divergence while maximizing evidence-backed continuity across agents and nodes.

## The default agent loop

```text
ORIENT -> RESOLVE -> INSPECT -> DECIDE -> ACT -> VERIFY -> RECORD -> HAND OFF
```

Do not begin routine work with repo archaeology or a broad filesystem crawl if a compiled view can answer the question.

## 1. ORIENT

Read the cheapest authoritative surface first.

Implemented path:

1. `CURRENT.md` when changing Project Ledger itself;
2. after a compile/provider refresh, `ledger orient` / `state/system-manifest.json`;
3. `ledger sources` when source health is relevant;
4. deeper docs/artifacts only when the bounded read surface is insufficient.

Before acting, know:

- authoritative branch/state;
- schema/compiler version when relevant;
- source freshness/coverage;
- known degraded sources;
- which requested capabilities are actually available in the manifest;
- whether the task is product work, source ingestion, identity work, review resolution, or downstream project work.

Current boundary: `orient` describes the estate and `resolve` can perform exact canonical resolution. Preferred-location/current-state/project-capsule queries remain later work.

## 2. RESOLVE

Never treat a human name, path, or repo folder as sufficient identity when duplicates are possible.

Implemented exact behavior:

```text
ledger resolve <user referent>
```

Resolution returns one of:

- one canonical project with matched-by evidence;
- an explicit ambiguity set; or
- unresolved.

Exact normalized remote/path/canonical ID and operator-approved aliases are higher-priority referents than compatibility project-key hints or display names.

Do not silently choose one of several plausible projects.

## 3. INSPECT

Load the smallest useful project context.

Target behavior:

```text
ledger show <project>
ledger locate <project>
```

The project capsule should normally be enough to determine:

- what the project is;
- lifecycle/current state;
- freshest trustworthy session summary and continuation point;
- canonical remote/URL;
- available observations ranked by utility/freshness;
- preferred working location and why;
- known conflicts/review items;
- deeper pointers if more evidence is required.

Use progressive disclosure. Read source repos/files only when the capsule does not contain enough evidence for the decision.

## 4. DECIDE

Separate four epistemic classes in your reasoning and outputs:

- observed;
- declared;
- inferred;
- explicitly decided.

Never promote inference into durable fact without recording its basis and confidence or an explicit resolution.

Use the cheapest reasoning ladder that can safely answer the question:

1. exact/deterministic evidence;
2. normalization and stable identifiers;
3. explicit heuristics;
4. cheap/local semantic reasoning;
5. frontier reasoning;
6. human review.

Escalate because ambiguity remains, not because expensive reasoning is available.

## 5. ACT

Prefer bounded work units.

For Project Ledger development:

1. one coherent task;
2. one branch/worktree/workspace;
3. implementation + tests + docs together;
4. review/foreign QA;
5. resolve findings;
6. merge promptly;
7. leave `main` authoritative.

For downstream project work, operate on the preferred working observation when available. If location preference is uncertain or consequential, surface the uncertainty instead of guessing.

Do not mutate source projects during discovery/sensing.

## 6. VERIFY

Verification is evidence, not ceremony.

Record what was actually checked:

- tests run and outcome;
- compile/lint/schema validation;
- scan/refresh performed and source coverage;
- git refs before/after when relevant;
- generated artifacts inspected;
- unresolved issues discovered.

Do not report a current scan unless the relevant sources were actually read in that run.

## 7. RECORD

Material work should leave structured durable state.

Current mechanism:

- project sidecar for thin project current-state metadata;
- git commit/PR history for code changes;
- backlog/docs for system work not yet represented structurally.

Target mechanism:

- session receipt linked to canonical project;
- explicit identity/review decisions;
- incremental compile producing refreshed project capsule/change feed.

Never edit a compiled canonical artifact as the primary source of truth.

## 8. HAND OFF

A handoff should let the next agent continue without replaying the session.

Minimum useful handoff:

- what changed and what actually landed;
- verification evidence;
- unresolved issues and their severity;
- one clear continuation point;
- exact project/commit/PR/task references;
- freshness/as-of qualification when state may change.

Avoid narrative diaries. Preserve decisions, evidence, and continuation state.

---

# Agent-facing design rules

## Bounded context first

Default agent-facing artifacts must be intentionally compact. Large provenance stores should be pointer-addressable, not injected wholesale.

## Stable IDs everywhere

Every durable entity that an agent may reference across sessions should have a stable ID. Names are display values; paths are locations; URLs are evidence/aliases.

## Explicit null semantics

Do not force agents to infer meaning from empty strings. Schema should distinguish unknown, unavailable, stale, absent, conflicted, and not-applicable where material.

## Explainable preference

If the system recommends a preferred observation, identity match, or current-state value, it must expose the reason/evidence.

## Idempotent operations

Re-running compile/resolve/materialization on unchanged inputs should not create semantic churn.

## Incremental by default

Process source deltas and affected projects rather than rescanning/reasoning globally when possible.

## Fail locally

One degraded source or ambiguous project should not poison the entire estate.

## Ratchet resolved ambiguity

A review resolution must become a durable decision or fixture so the same question does not recur.

## Preserve uncertainty

A clean-looking wrong answer is worse than a visible ambiguity.

---

# What agents should not do

- Do not use a same-name match as proof of identity.
- Do not collapse backups/mirrors/live checkouts without evidence.
- Do not rewrite stable project keys casually.
- Do not infer push time from local git when it is not knowable.
- Do not treat an inaccessible source as an absent project.
- Do not rebuild a task-management system inside Project Ledger.
- Do not add ingestion adapters faster than identity/review capacity can absorb them without a clear coverage benefit.
- Do not use a frontier model for deterministic metadata extraction.
- Do not leave substantial working behavior stranded on a long-lived branch.
- Do not end material work with only prose if a durable structured decision/receipt is appropriate.

# Definition of agent-ergonomic success

The system is agent-ergonomic when routine project control requires a few stable calls/reads, uncertainty is explicit, expensive reasoning is rare and targeted, and each successful session reduces the work required by the next one.
