# Session-End Agent Prompt

Use this compatibility prompt until structured Project Ledger session receipts are implemented.

```text
Before ending this material work session, leave an evidence-bearing project handoff that makes the next agent cheaper and more accurate.

1. Establish what actually landed.
   - Record the relevant branch/commit/PR/task refs if available.
   - Distinguish completed-and-landed work from attempted/uncommitted work.
   - Record the verification actually performed (tests/checks/scan), not what was intended.

2. If the current live project uses `.project-ledger.json`, update only fields you can justify from this session:
   - `project_key`: only if missing and you have a stable justified key; do not casually rename an existing stable key.
   - `display_name`
   - `status`
   - `tags`
   - `canonical_url`
   - `repo_name`
   - `shared`
   - `last_session_at`: ISO 8601 with timezone.
   - `last_session_summary`: 2-5 concrete sentences describing what actually landed/currently exists.
   - `next_step`: one bounded continuation action, not a task tree.
   - `last_push_at`: ONLY if a push actually occurred and the timestamp is justified; otherwise preserve the prior value/blank.

3. Preserve uncertainty.
   - List unresolved issues discovered during the session.
   - Do not convert guesses into stable metadata.
   - If project identity/location is ambiguous, say so rather than choosing silently.
   - If a source was unavailable or state was not refreshed, qualify the handoff with the actual as-of/coverage limitation.

4. If README/current-state prose is now materially false, update it only where doing so avoids contradiction with landed reality.

5. Return a compact handoff containing:
   - what landed;
   - exact refs/files materially changed;
   - verification evidence;
   - unresolved issues;
   - one continuation point;
   - whether a push/merge occurred;
   - any freshness/coverage caveat.

Rules:
- Do not invent push timestamps, test results, source freshness, or merge state.
- Do not replace stable identity fields with inferred guesses.
- Keep the sidecar a thin project declaration/current-state pointer, not a task manager.
- Prefer references to git/tasks/evidence over copying large histories into the sidecar.
- If the same ambiguity was conclusively resolved during this session, flag it for a durable identity/review decision or regression fixture so future agents do not pay for it again.
```

## Target migration

When the structured receipt system lands, the preferred closeout becomes:

```text
material work
 -> structured session receipt
 -> project/current-state compile
 -> refreshed project capsule/change feed
```

The sidecar remains a project-local declaration source, while the receipt carries session-specific landed work and verification evidence.
