# Compatibility observation contract (version policy)

This document is the **frozen v0 contract** for the flat compatibility observation
record emitted by `build_ledger.py` and consumed at the compile boundary. It pins
the envelope, the exact ordered entry-field list, and the major/minor version
policy so a future or external producer cannot silently change field meanings and
have the compiler "guess through" an unsupported shape.

Authority: this document plus `ledger/compat_contract.py` (executable policy) and
`tests/fixtures/compat-contract.json` (golden pin). The executable checks are the
runtime truth on top of the prose here.

Source of truth for version constants: `ledger/__init__.py`
(`COMPAT_FLAT_SCHEMA_VERSION`; the compat contract uses `0.1.0`).

---

## 1. What the compat output is

`build_ledger.py` produces one `output/projects.json`. Its top-level envelope is
stable:

```text
generated_at      ISO-8601 local timestamp when the scan wrote the file
config_path       resolved path of the ledger config used
entry_count       number of entries in the array
entries[]         flat, one-object-per-manifestation compatibility records
```

Each `entries[]` object carries exactly the frozen field set in section 2 — no
more, no fewer — emitted in CSV order (`ledger.models.CSV_FIELDS == header order`).

There is also a `projects.csv` whose header must equal the same frozen field list
in the same order, and a `projects.md` display view that is a derived human view
(not a versioned contract of its own).

The flat record is a *legacy compatibility* surface: it mixes observed facts,
declarations, inference, and project-level hints. It is not a model to extend
indefinitely; typed collections (`state/observations.json`,
`state/canonical-projects.json`) are the forward contract. The flat record is
preserved as the explicit v0 contract for compatibility, not grown.

At the compile boundary the flat `compat_entry` is wrapped into each typed
`observation` (`state/observations.json`), which records
`compat_schema_version = COMPAT_FLAT_SCHEMA_VERSION` on the envelope.

---

## 2. Frozen entry field list (v0)

The exact ordered field set (golden pin: `tests/fixtures/compat-contract.json`).
A rename, reorder, removal, or unapproved addition breaks
`tests/test_compat_contract.py::test_csv_fields_frozen_to_golden_fixture`, so
field-meaning drift cannot slip through silently.

| # | field | role |
|---|-------|------|
| 1 | `project_hash` | identity |
| 2 | `project_key` | identity |
| 3 | `name` | naming |
| 4 | `project_type` | naming |
| 5 | `source_label` | provenance |
| 6 | `machine_name` | provenance |
| 7 | `storage_scope` | provenance |
| 8 | `shared` | provenance |
| 9 | `git` | scan |
| 10 | `obsidian` | scan |
| 11 | `repo_name` | naming |
| 12 | `remote_url` | provenance |
| 13 | `canonical_url` | provenance |
| 14 | `path` | provenance |
| 15 | `path_from_root` | provenance |
| 16 | `readme_path` | navigation |
| 17 | `readme_link_md` | navigation |
| 18 | `path_link_md` | navigation |
| 19 | `last_touch_at` | navigation |
| 20 | `head_branch` | navigation |
| 21 | `head_commit` | navigation |
| 22 | `head_commit_at` | navigation |
| 23 | `last_remote_ref_at` | navigation |
| 24 | `last_push_at` | navigation |
| 25 | `markdown_file_count` | scan |
| 26 | `obsidian_note_count` | scan |
| 27 | `tree_scan_truncated` | scan |
| 28 | `readme_sha256` | scan |
| 29 | `include_reason` | scan |
| 30 | `status` | naming |
| 31 | `tags` | naming |
| 32 | `description` | naming |
| 33 | `next_step` | navigation |
| 34 | `last_session_at` | navigation |
| 35 | `last_session_summary` | navigation |
| 36 | `sidecar_path` | provenance |

`role` groups are the classification used in `SCHEMA.md` §0 (identity, naming,
provenance/location, navigation/activity, scan/classification). Field meanings are
described in `SCHEMA.md` §0.

---

## 3. Version policy (major / minor)

Version numbering: semver-style `MAJOR.MINOR.PATCH`.

- **MAJOR** — a change that breaks field meaning or shape. Readers of the older
  major cannot safely interpret the newer data.
- **MINOR** — a backward-compatible additive change. Old readers can still
  interpret the data (extra/optional fields are ignored).
- **PATCH** — a same-shape fix with no meaning change. Transparent to readers.

Rules applied at the compile boundary (`ledger/compat_contract.check_compat_schema`):

1. If the payload declares a `format` other than `project-ledger-compat`, it is
   rejected with `COMPAT_FORMAT_UNSUPPORTED`.
2. If the payload declares a schema with a **different major** than the supported
   `COMPAT_FLAT_SCHEMA_VERSION`, it is **rejected explicitly** with
   `COMPAT_MAJOR_UNSUPPORTED`. The compiler refuses to guess through an
   unsupported major rather than silently reinterpreting fields.
3. A same-major, newer **minor** is **tolerated** (additive, backward compatible).
4. A payload with **no version marker** is treated as the supported v0 contract.
   The current `build_ledger.py` producer emits no marker; v0 is the default.

### Producer guidance

Producers may (but are not required to, today) annotate the envelope with:

```text
format              "project-ledger-compat"
schema_version      COMPAT_FLAT_SCHEMA_VERSION, e.g. "0.1.0"
```

Future producers that change field meaning MUST bump the **major**; consumers that
only read `entries`/`entry_count` and the frozen fields are safe across minors.

### Migration notes

- Adding an optional field is a MINOR change: bump `.MINOR`, add the field to the
  golden fixture's `entry_fields`, and document the meaning here + in `SCHEMA.md` §0.
- Reinterpreting / removing / renaming an existing field is a MAJOR change: bump
  MAJOR, add the new major's golden fixture alongside the old, and keep the
  `COMPAT_MAJOR_UNSUPPORTED` gate so the old contract is not silently misread.
- Never promote `project_hash` into canonical identity (`SCHEMA.md` §5 invariant 8).
- The typed `state/observations.json` `schema_version` and the flat
  `compat_schema_version` are distinct: the former versions the typed envelope,
  the latter names the flat contract it wrapped.

---

## 4. Relationship to other contracts

- The flat contract feeds the typed `observation.compat_entry`; typed
  observations and canonical projects carry their own schema versions
  (`SCHEMA.md` §1).
- Identity/decision/evidence/review schema versions live in `ledger/__init__.py`
  and are separate from the compat contract.
- This document is the compat half of `SCHEMA.md` §7 "Compatibility/versioning
  direction": item 1 (preserve flat v0) and items 4/5 (major/minor policy and
  explicit unsupported-major rejection) are now resolved for the flat contract.