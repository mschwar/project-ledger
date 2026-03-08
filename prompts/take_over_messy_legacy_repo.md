# Take Over Messy Legacy Repo

## Purpose

Use this prompt when a repo already exists but is messy, inconsistent, poorly documented, partially broken, or hard for a new agent to understand.

This is the right choice for:

- legacy repos with unclear structure
- repos with partial docs and unclear ownership
- stale codebases with drift between docs and implementation
- handoffs where the main problem is operational confusion

## Copy/Paste Prompt

```text
You are taking over a messy legacy repository or workspace.

The project already exists, but it is not handoff-ready. The structure, docs, naming, or operating assumptions may be inconsistent or stale.

Your task is to create a takeover packet that makes this legacy repo understandable and maintainable for a team of agents without needing extra verbal context from the user.

Operate directly in the workspace. Do not stop at analysis only. Inspect the real repo state, identify what is true now, and create/update the docs needed for safe continuation.

Primary objective:
Convert a confusing legacy repo into a self-describing workspace with clear current-state documentation, risks, operating rules, and a prioritized recovery plan.

Process:
1. Inspect the repo structure, code layout, docs, configs, scripts, tests, generated outputs, and obvious dead files.
2. Determine:
   - what the repo appears to do
   - what currently works
   - what is stale or contradictory
   - what future agents would likely misunderstand
3. Create or update the docs below so the repo can be operated safely.
4. Do not rewrite the whole repo unless explicitly necessary. Document and prioritize first.
5. If docs conflict with code, prefer current code reality and note the mismatch.
6. If caches, build outputs, logs, or scratch files should be ignored, update `.gitignore` if appropriate.
7. Run the relevant tests or validation commands if available and record what actually passed or failed.

Required deliverables:

1. `README.md`
   Update so it reflects:
   - what the repo actually does now
   - current status
   - known caveats
   - actual entrypoints and commands

2. `AGENTS.md`
   Include:
   - mission
   - what to read first
   - repo-specific safety rules
   - dangerous or fragile areas
   - definition of done for legacy cleanup work

3. `CURRENT_STATE.md`
   Include:
   - what is confirmed working
   - what is unverified
   - what appears broken
   - stale or conflicting docs/code areas
   - immediate repo risks

4. `PRD.md`
   Reconstruct the practical product intent from the existing repo.
   Include:
   - current implied product/problem
   - users
   - goals
   - non-goals
   - mismatches between intended and actual implementation

5. `ARCHITECTURE.md`
   Include:
   - current architecture as actually found
   - pain points and inconsistencies
   - target cleanup direction
   - boundaries future agents should preserve

6. `ROADMAP.md`
   Include:
   - stabilization phase
   - cleanup/refactor phase
   - feature continuation phase
   - exit criteria

7. `BACKLOG.md`
   Include:
   - `P0` stabilization tasks
   - `P1` structural cleanup tasks
   - `P2` future improvements
   - rationale for major items

8. `RUNBOOK.md`
   Include:
   - actual run/test commands
   - common failure modes
   - what to inspect first when things break
   - what files must stay in sync

9. `SCHEMA.md`
   Include if the repo has structured data, APIs, manifests, content models, config formats, or durable artifacts.

10. `.gitignore`
   Update if needed.

Quality bar:
- Be honest about uncertainty.
- Distinguish confirmed facts from inferred intent.
- Prefer recoverability and continuity over cosmetic cleanup.
- Make the first stabilization moves obvious.
- Do not bury major risks.

Before finishing:
1. Re-read the docs for consistency.
2. Run available validation commands if relevant.
3. Summarize:
   - files created/updated
   - what is now clear that was previously unclear
   - top repo risks still remaining
   - recommended next stabilization step
```

## Notes

- This variant is for repos where the main need is clarity, triage, and safe continuation.
- If the repo is small and not especially messy, the lightweight handoff packet is usually enough.
