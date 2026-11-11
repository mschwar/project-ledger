# Session-End Agent Prompt

```text
Before ending this material work session, leave an evidence-bearing project handoff that makes the next agent cheaper and more accurate.

1. Establish what actually landed.
   - Record the relevant branch/commit/PR/task refs if available.
   - Distinguish completed-and-landed work from attempted/uncommitted work.
   - Record the verification actually performed (tests/checks/scan), not what was intended.

2. Keep `.project-ledger.json` a static repository identity manifest:
   - Only update durable identity/classification fields when justified:
     `schema_version`, `project_key`, `display_name`, `description`, `status`,
     `tags`, `canonical_url`, `repo_name`, `gbrain_source_slug`, `shared`, `storage_scope`.
   - NEVER write mutable per-commit or per-session fields (`commit`, `head_commit`,
     `next_step`, `last_session_at`, `last_session_summary`, `last_push_at`) into
     `.project-ledger.json`, so static repo identity manifests do not become stale
     after every `git commit`.

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
- Keep the sidecar a static project identity declaration, not a session log or task manager.
- If the same ambiguity was conclusively resolved during this session, flag it for a durable identity/review decision or regression fixture so future agents do not pay for it again.
```
