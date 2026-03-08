# Create Lightweight Handoff Packet

## Purpose

Use this prompt when a project does not need a full PRD/architecture package, but still needs enough structure for another agent to take over cleanly.

This is the right choice for:

- small utilities
- early prototypes
- personal scripts
- short-lived experiments that still need continuity

## Copy/Paste Prompt

```text
You are taking over an existing repo, folder, prototype, or small project.

Your task is to create a lightweight but reliable handoff packet so another agent can continue work without extra verbal context from the user.

Operate directly in the workspace. Do not stop at a proposal. Read the current repo state, then create or update the minimum set of docs needed for continuity.

Primary objective:
Create a compact handoff packet that is sufficient for a new agent to understand what exists, how to run it, what matters, and what to do next.

Process:
1. Inspect the repo/workspace structure, key files, scripts, configs, tests, and current docs.
2. Determine the real current state of the project.
3. Create or update the lightweight handoff docs listed below.
4. Keep the docs concise, factual, and action-oriented.
5. Do not invent maturity or architecture that does not exist.
6. If generated outputs, caches, or scratch files should be ignored, update `.gitignore` if appropriate.
7. Run relevant tests or validation commands if available.

Required deliverables:

1. `AGENTS.md`
   Include:
   - mission or purpose of the project
   - what to read/run first
   - current state summary
   - rules for safe edits
   - definition of done for future agents

2. `README.md`
   Update so it clearly explains:
   - what this project is
   - current status
   - how to run it
   - how to validate it
   - where the important files are

3. `STATUS.md`
   Include:
   - what currently works
   - what is incomplete or broken
   - known risks or caveats
   - last known verification status

4. `NEXT_STEPS.md`
   Include:
   - immediate next tasks
   - short priority ordering
   - any blocked items or assumptions

5. `RUNBOOK.md`
   Include:
   - key commands
   - common troubleshooting notes
   - what to update when behavior changes

6. `.gitignore`
   Update if needed.

Optional deliverables:

- `ARCHITECTURE.md` if the project has enough structure to justify it
- `BACKLOG.md` if there is already meaningful queued work
- `SCHEMA.md` if structured data or outputs exist

Quality bar:
- Keep it compact.
- Make it easy for a cold-start agent to become productive in minutes.
- Prefer practical continuity over formal completeness.
- Distinguish clearly between current reality and desired future state.

Before finishing:
1. Re-read the new docs for consistency.
2. Run available validation commands if relevant.
3. Summarize:
   - files created/updated
   - what a new agent can now do
   - remaining gaps
```

## Notes

- Use this when the full handoff packet would be overkill.
- If the project grows, a future agent can later upgrade this into the full handoff packet format.
