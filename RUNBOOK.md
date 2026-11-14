# Runbook

## Purpose

Operate and extend Project Ledger safely while preserving its role as a project-reality compiler. `SYSTEM.md` defines the invariants; `AGENT_PROTOCOL.md` defines the agent loop.

## Commands available today

```bash
python -m ledger validate
python -m ledger sync-github [--check | --dry-run] [--clone-root /central/repos/active] [--force-fetch] [--json]
python -m ledger refresh [--full-scan] [--check-remote]
python -m ledger refresh --no-markdown-mirror [--full-scan] [--check-remote]
python -m ledger compile [--check-remote]
python -m ledger orient
python -m ledger sources
python -m ledger portfolio [--urgent] [--dirty] [--behind] [--remote-ahead] [--cycles] [--lifecycle STATE] [--depends-on PROJECT] [--depended-on-by PROJECT] [--limit N] [--json]
python -m ledger resolve <referent>
python -m ledger show <project>
python -m ledger locate <project>
python -m ledger explain <referent>
python -m ledger review [--approve-candidates] [--decision-id ID]
python -m ledger changes [--since RUN_ID] [--project REFERENT] [--entity-kind KIND] [--change-type CODE]
python -m ledger lineage [--check | --sync] [--merge-from PATH] [--json]
python -m ledger sync-gbrain [--brain-dir /home/prime/brain] [--project REFERENT] [--ingest] [--push] [--prune-shadow-source ID] [--dry-run] [--json]
python -m ledger mcp [--transport {stdio,http}] [--host 127.0.0.1] [--port 3132]
/usr/bin/python3 -m unittest tests/test_doc_hygiene.py -v
python -m unittest discover -s tests
```

The compatibility scanner remains directly available as `python build_ledger.py`.

Implemented today: `validate`, `sync-github`, `refresh`, `compile`, `orient`, `sources`, `portfolio`, `resolve`, `show`, `locate`, `explain`, `review`, `changes`, `lineage`, `sync-gbrain`, and `mcp` (`ledger-mcp`, plus `ops/project-ledger-mcp-tailnet.sh` for tailnet-only Streamable `HTTP` service).

Session/attempt receipts are owned by `machina` (Project Ledger does not expose a competing `record-session` store). The manifest capability map is the executable source of truth.

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

For routine estate orientation after a successful compile/provider refresh:

1. `python -m ledger orient --json` or read `state/system-manifest.json`;
2. inspect `python -m ledger sources --json` only when source health matters;
3. load `state/observations.json` only when observation-level evidence is required;
4. read design documents only when changing Project Ledger itself.

For system-development work:

1. `CURRENT.md`;
2. `SYSTEM.md`;
3. `AGENT_PROTOCOL.md`;
4. only the task-relevant architecture/schema/runbook/roadmap sections.

The project-level read path is:

```text
ledger orient
 -> ledger resolve <referent>
 -> ledger show <canonical project>
 -> ledger locate <canonical project>
 -> ledger changes [--since <run_id>] [--project <canonical project>]
```

All five agent read commands (`orient`, `resolve`, `show`, `locate`, and `changes`), plus `sources`, `explain`, and `review`, exist today.

A real-source refresh must always be qualified by the sources actually accessible in that run. Code freshness and ledger-data freshness are separate facts.

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
- explicit `source_id`, `machine_name`, and `storage_scope` metadata when the source spans remote or shared storage.

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

Current first slice: exact normalized repository remotes auto-merge; committed merge/split/reject/alias decisions are compiler inputs; ambiguity is materialized in `state/review-queue.json`. Compatibility names/project keys never auto-merge by themselves.

## Identity resolution target rule

Never merge based only on same display name. Prefer strong deterministic evidence; preserve ambiguity when evidence remains weak.

When an ambiguity is explicitly resolved, encode it in `registry/identity-decisions.json` so it does not recur.

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

`.project-ledger.json` is a static versioned repository identity declaration (`schemas/project-ledger-sidecar.schema.json`).

Rules:

- only declare durable identity/classification metadata (`schema_version`, `project_key`, `display_name`, `description`, `status`, `tags`, `canonical_url`, `repo_name`, `gbrain_source_slug`, `shared`, `storage_scope`);
- never write mutable per-commit or per-session fields (`commit`, `head_commit`, `next_step`, `last_session_at`, `last_session_summary`, `last_push_at`) into `.project-ledger.json`;
- do not casually replace stable keys or declare `canonical_project_id` (assigned by compilation);
- recognize that copied projects can have divergent sidecars (`DIVERGENT_SIDECAR_DECLARATIONS`).

Conflicting sidecars create competing claims/review rather than last-write-wins canonical state.

---

# Session-end operations

For material downstream project work:

1. keep `.project-ledger.json` static (update only when durable identity/classification changed);
2. record what landed, verification evidence, commit/PR refs, and one continuation point in PR/commit/handoff artifacts (`prompts/session_end_prompt.md`);
3. leave session/attempt receipts to `machina` and worktree leases to `homelab`.

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
