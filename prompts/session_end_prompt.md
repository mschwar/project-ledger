# Session-End Agent Prompt

Paste this at the end of a working session inside a project/repo/vault:

```text
Before you end this session, update the project ledger metadata for the current project root.

Required:
1. Create or update `.project-ledger.json` in the project root.
2. Refresh these fields only with information you can justify from the current session:
   - `project_key` (only if missing; choose a stable slug)
   - `display_name`
   - `status`
   - `tags`
   - `canonical_url`
   - `repo_name`
   - `shared`
   - `last_session_at` (ISO 8601 with timezone)
   - `last_session_summary` (2-5 sentences, concrete, no fluff)
   - `next_step` (one clear next action)
   - `last_push_at` (ONLY if you actually pushed during this session; otherwise leave as-is or blank)
3. If `README.md` exists and its status/next-steps section is stale, update it to match the sidecar.
4. Return:
   - the exact file(s) changed
   - the final JSON fields written
   - whether a push happened in this session

Rules:
- Do not invent a push timestamp.
- Do not replace stable fields with guesses.
- Keep the summary factual and implementation-specific.
- If the project is not git-backed, leave git-specific values blank.
```

Use the sidecar schema from `templates/project-ledger.sidecar.example.json`.
