# Bootstrap Greenfield Idea Repo

## Purpose

Use this prompt when the starting point is mostly an idea, concept folder, note collection, or rough prototype, and you want an agent to turn it into a structured repo/workspace that future agents can build from.

This is the right choice for:

- greenfield ideas
- idea folders with notes but little code
- concept-first projects that need repo structure and operating docs

## Copy/Paste Prompt

```text
You are taking over a greenfield or idea-first project.

The current workspace may contain notes, loose markdown files, fragments, prompts, sketches, or partial code, but it does not yet have a fully operational repo structure.

Your task is to convert it into a coherent starter repository/workspace that a team of agents can continue building without extra verbal context from the user.

Operate directly in the workspace. Do not stop at planning only. Inspect what exists, infer the intent, and create the initial repo scaffolding and documentation needed for execution.

Primary objective:
Turn a concept or loose project folder into a structured, implementation-ready starting point with explicit goals, constraints, architecture direction, and execution plan.

Process:
1. Inspect all existing notes, markdown files, prompts, code fragments, and configs.
2. Infer the actual project idea, intended users, likely outputs, constraints, and unknowns.
3. Separate:
   - what is already known
   - what is assumed
   - what is undecided
4. Create a repo structure and handoff packet that make the project buildable by future agents.
5. If code scaffolding is appropriate, create only the minimal coherent scaffold. Do not overbuild speculative implementation.
6. If there is no existing README or repo entrypoint, create one.
7. Add `.gitignore` if needed.
8. Run any validation that is actually possible.

Required deliverables:

1. `README.md`
   Include:
   - project overview
   - current status as greenfield or pre-implementation
   - intended use case
   - repo structure overview
   - how to continue from here

2. `AGENTS.md`
   Include:
   - mission
   - what future agents should read first
   - operating rules
   - what is real vs assumed
   - definition of done for early-stage work

3. `PRD.md`
   Include:
   - problem
   - users
   - jobs to be done
   - goals
   - non-goals
   - assumptions
   - open questions
   - initial milestones

4. `ARCHITECTURE.md`
   Include:
   - current state
   - proposed target architecture
   - candidate components
   - data flow or workflow
   - tradeoffs
   - what should not be built yet

5. `ROADMAP.md`
   Include:
   - phase 0: clarify and stabilize concept
   - phase 1: minimum viable implementation
   - later phases
   - exit criteria per phase

6. `BACKLOG.md`
   Include:
   - `P0`, `P1`, `P2`
   - the first concrete implementation moves
   - research/clarification tasks
   - tasks that should wait

7. `RUNBOOK.md`
   Include:
   - how to orient inside the repo
   - what commands exist, if any
   - how to validate progress
   - how to add structure without drifting from the concept

8. `SCHEMA.md`
   Include if the project has any data model, content model, artifact model, ledger, manifest, API surface, config shape, or output contract.

9. `.gitignore`
   Add or update if needed.

Optional but encouraged:

- create starter directories if the repo is currently shapeless
- add placeholder files where they reduce ambiguity
- add sample config/template files if they clarify intended usage

Quality bar:
- Be explicit that this is a greenfield bootstrap.
- Document assumptions instead of hiding them.
- Avoid fake maturity.
- Prefer a coherent foundation over large speculative code dumps.
- Make the next 1-3 implementation moves obvious.

Before finishing:
1. Re-read the docs for internal consistency.
2. Ensure assumptions and open questions are clearly marked.
3. Summarize:
   - files created/updated
   - what the repo is now ready for
   - biggest remaining unknowns
   - recommended next implementation step
```

## Notes

- This is for converting ideas into an executable starting point.
- If the repo is already mature, use the full handoff packet or legacy takeover variant instead.
