# Future Spec — Declare `build_ledger.py` compatibility re-export surface for ruff

- Status: **proposed** (not yet landed)
- Date: 2026-09-21 (UTC)
- Node: Matthews-MacBook-Air-3 (Hermes child node)
- Repo: `github.com/mschwar/project-ledger`
- Found while: reviewing the `fix/remove-unused-imports-tests` work unit (the
  `55dd1c9` commit removed unused imports in `tests/test_source_adapters.py`)
- Tracks: kanban future spec `t_b51a4699`

## Problem

`build_ledger.py` imports ~38 names from `ledger.models` and `ledger.sources.*`
as the **P2.1 compatibility re-export surface**. The module docstring states the
file "re-exports the extracted names unchanged", and `ledger/compat.py` (plus
external homelab consumers) import `build_ledger` and rely on those names being
available unchanged. These imports are deliberate re-exports, **not dead code**,
so deleting them (as ruff's `F401` suggests) would break the documented
compatibility contract.

However, the re-export block is **not lint-declared**: there is no module
`__all__` and no `# noqa`, so a full-tree `ruff check .` fails with **34 F401
errors**, all in the re-export block (`build_ledger.py` lines 20–67):

```text
$ ruff check .
Found 34 errors.
[*] 34 fixable with the `--fix` option.
```

Reproduced with `ruff 0.15.10` on `main` (`55dd1c9`):

- `ruff check ledger tests` -> **All checks passed!** (current branch green)
- `ruff check .` -> **34 F401 errors** (all in `build_ledger.py` re-export block)

The 34 flagged names are the pure re-export set. Six names from the same block
are genuinely used in the file body (`CSV_FIELDS`, `escape_md_cell`,
`markdown_link`, `read_json`, `resolve_path`, `safe_path_exists`) and are not
flagged.

## Root cause: no lint-declared re-export surface

No ruff config file exists in the repo (no `ruff.toml`, `.ruff.toml`,
`pyproject.toml`, `setup.cfg`, or `tox.ini`), and `.github/workflows/ci.yml`
only runs `python -m py_compile ...` plus `python -m unittest discover -s
tests` — **no lint step**. So this does not fail CI today; it is a latent
quality-gate gap. A future developer running `ruff check .` (or `ruff check
--fix .`, which would silently delete the re-export imports) would see 34
errors or, worse, auto-remove the compatibility surface and break
`ledger/compat.py` and external consumers at runtime.

## Proposed change (bounded)

Annotate the re-export import block so a full-tree `ruff check .` passes
**without changing runtime behavior**. Two complementary, minimal options:

1. **Add a module `__all__`** declaring the public re-export names. This is the
   idiomatic ruff-recognized declaration: when a name appears in `__all__`, ruff
   treats it as an intentional re-export and stops flagging `F401` for it. It
   also documents the public surface explicitly, which matches the module's
   stated compatibility role.
2. **Add `# noqa: F401`** to the import block(s). This is the narrower, more
   local annotation and also silences the rule, but it does not document the
   public surface as clearly as `__all__`.

The spec recommends **option 1 (`__all__`)** as the primary fix because it both
clears the lint gate and makes the compatibility contract explicit and
machine-readable, with `# noqa: F401` available as a fallback if a future
change wants per-line suppression instead.

Concretely:

- Add a module-level `__all__` to `build_ledger.py` listing the 38 re-exported
  names (the full set imported in the block, including the six used in the
  body — `__all__` should enumerate the whole public surface, not just the
  currently-unused names).
- Do **not** change any import statement, any function body, or the CLI
  orchestration glue.
- Optionally, once the tree is clean, add a `ruff check .` lint step to
  `.github/workflows/ci.yml` so the quality gate is enforced in CI rather than
  remaining latent.

## Acceptance criteria (when it lands)

- [ ] `ruff check .` exits `0` (34 F401 errors cleared).
- [ ] `ruff check ledger tests` still passes.
- [ ] `python -m unittest discover -s tests` still passes (122 tests green).
- [ ] `build_ledger.py` runtime behavior is byte-identical (no import or body
      change; only the `__all__` declaration added).
- [ ] `ledger/compat.py` and the compatibility tests still import
      `build_ledger` unchanged and pass.

## Explicitly out of scope now

- Deleting the re-export imports (would break the documented compatibility
  contract).
- Refactoring `build_ledger.py` to import from `ledger` instead of re-exporting
  (a larger P2.1+ cleanup, not needed to close the lint gap).
- Adding a broad ruff config / rule set to the repo (only the re-export
  declaration is in scope here; a wider lint policy is a separate decision).