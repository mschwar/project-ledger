# Create Full Handoff Packet

## Purpose

Use this prompt when you want an agent or LLM to turn an existing repo, folder, prototype, or idea workspace into a self-contained handoff packet for a team of agents.

The output should let future agents take over without needing extra verbal context from the human.

## Copy/Paste Prompt

```text
You are taking over an existing project, repository, folder, or idea workspace.

Your task is to create a complete handoff packet so that a team of agents can continue the build-out without needing extra verbal explanation from the user.

Operate directly in the repo/workspace. Do not stop at a proposal. Read the existing files, infer the project state, and create or update the needed markdown/docs so the workspace becomes self-describing.

Primary objective:
Create a comprehensive, practical, agent-ready handoff packet grounded in the actual repository state.

The packet must be optimized for:
- future agents joining cold
- parallel work across multiple agents
- minimal ambiguity about product intent, architecture, schema, priorities, and operating rules

Process:
1. Inspect the repo/workspace structure, current docs, code, scripts, configs, tests, and generated artifacts.
2. Determine what already exists, what the project currently does, what is missing, and what future agents would need to know to continue.
3. Create or update the handoff docs listed below.
4. Keep the docs grounded in the real current state of the repo. Do not write generic filler.
5. If the project is early-stage or idea-only, say so explicitly and document assumptions and target direction.
6. If there are generated artifacts, caches, scratch dirs, or outputs that should not be treated as source, add or update `.gitignore` if appropriate.
7. Verify consistency across the docs and run relevant tests or validation commands if available.

Required deliverables:

1. `AGENTS.md`
   Include:
   - project mission
   - what future agents should read first
   - current state summary
   - operating rules for editing, schema changes, tests, and docs
   - expected commands
   - definition of done
   - preferred execution order or workstream order
   - session-end expectations if relevant

2. `PRD.md`
   Include:
   - product name
   - current status
   - problem statement
   - users and stakeholders
   - jobs to be done
   - goals
   - non-goals
   - current scope
   - functional requirements
   - non-functional requirements
   - success metrics
   - risks
   - milestone framing

3. `ARCHITECTURE.md`
   Include:
   - current architecture
   - target architecture
   - major components/modules
   - data flow
   - key abstractions
   - testing strategy
   - operational constraints
   - design principles

4. `SCHEMA.md`
   Include if the project has structured data, metadata, records, APIs, configs, sidecars, manifests, or output artifacts.
   Cover:
   - current fields/entities
   - field meanings
   - precedence or merge rules if applicable
   - known gaps
   - target schema direction
   - change-management guidance

5. `ROADMAP.md`
   Include:
   - current completed phase
   - next phases
   - deliverables per phase
   - exit criteria
   - parallelizable workstreams where relevant

6. `BACKLOG.md`
   Include:
   - prioritized tasks grouped into `P0`, `P1`, `P2`
   - short rationale for each major task
   - clear next implementation moves

7. `RUNBOOK.md`
   Include:
   - how to run the project
   - how to test/validate it
   - how to add new inputs/config/roots/modules as relevant
   - how to respond to common failure modes
   - what to update when schema or workflow changes
   - handoff checklist for end of session

8. `README.md`
   Update if needed so it points clearly to the handoff packet and reflects the current entrypoint/use case.

9. `.gitignore`
   Update if needed to exclude generated outputs, caches, scratch data, or test artifacts.

Quality bar:
- Be comprehensive, but not bloated.
- Every doc should help a future agent act correctly.
- Distinguish clearly between current state and target state.
- Prefer concrete statements over vague aspirations.
- Use the real repo structure and actual commands where possible.
- Avoid inventing capabilities that do not exist.
- If something is missing or uncertain, state that explicitly and convert it into backlog or roadmap items.

Output expectations:
- Create the files directly in the workspace.
- Keep filenames conventional and root-level unless there is a strong reason otherwise.
- If the repo already has some of these files, update them instead of duplicating intent.
- Ensure the handoff packet is internally consistent.

Before finishing:
1. Re-read the new docs for consistency.
2. Run available tests or validation commands if applicable.
3. Summarize:
   - files created/updated
   - what the packet now enables
   - any gaps that remain
   - recommended next steps for the next agent team
```

## Notes

- This prompt works for both code-first repos and idea-first folders.
- For greenfield concepts, the agent should still produce the same packet, but explicitly mark the repo as pre-implementation.
- For mature repos, the packet should focus on accurate operational continuity rather than aspirational rewrite plans.
