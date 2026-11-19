# Proposal — Education and Learning Portfolio Structure (`WQ-7`)

- Status: **proposed (2026-11-19)** — ideas and architecture proposal only; repository retirements, content fold-ins, and cross-repository module extractions remain gated on explicit operator sign-off.
- Date: 2026-11-19 (UTC)
- Repo: `github.com/mschwar/project-ledger`
- Estate baseline: `110` canonical projects (`109` remote-backed Git repositories + `1` passive peer-census local repository) and `5` archived GitHub repositories (`state/portfolio-index.json`, `state/github-sync.json`).
- Recommended option: **Option A — Four-Lane Federated Spine, Targeted Fold-Ins (`18 -> 11` live repositories), and Two Shared Modules (`unslop` + `praxis-kit`)**.

## TL;DR

Sweep of all `115` live and archived repositories identified `18` live education, pedagogy, and learning repositories plus `1` retired prototype (`class-sync`). We propose retiring `7` redundant or dormant repositories into `/central/archive/`, keeping `4` boundary-distinct institutional and research repositories standalone, and sharing reusable logic through `unslop` and `praxis-kit`.

## 1. Context and Architectural Constraints

Consolidating every education-adjacent repository into one monorepo fails for concrete legal, scientific, and operational reasons. Leaving all `18` live repositories scattered preserves duplicate trees, zombie checkouts, and hidden cross-repository file-path coupling.

Four hard boundaries govern repository boundaries across this domain:

1. **Privacy and Operational Isolation:** Family Educational Rights and Privacy Act (FERPA) rules and Albuquerque Public Schools (APS) operational policies (`OPS-20` and `Tier R2` release holds) govern `LPES-CS`. Live campus encounters, restricted district archives, and daily Beginning-of-Day (BOD) and End-of-Day (EOD) operations cannot share Git history with public journal replication packages.
2. **Authorship and Pre-Registration Commit Locks:** `two-track-education` carries Los Padillas Elementary School (LPES) Leadership Council group authorship over masked public aggregates (`n >= 10`). `chreod` pins pre-analysis prediction registries by immutable commit hash (`7f9a57a`, `c92d321`) and is undergoing manuscript revision. Merging their histories invalidates commit-anchored provenance and muddies group authorship.
3. **Product Loop vs. Falsification Lab Separation:** `lighthouse` runs a lean single-learner mastery loop alongside its school governance charter. `learning-substrate-programme` holds `153` commits of psychometric falsification pipelines (`ACT-R` vs. bounded multi-timescale state) and is paused at a human synthesis gate (`SG-02R2`). Merging them before Wave 5 closes would bury the product loop under research artifacts.
4. **Runtime and Hosting Coupling:** Live system daemons (`janus.service` on port `8765`, `lpes-workbench.service` on port `4174`), native Swift/iOS Xcode targets (`toned`), and repository-bound GitHub Pages domains (`mschwar.github.io/common-project/`) justify small standalone repositories where runtime toolchains do not overlap.

## 2. Full Sweep Inventory and Proposed Dispositions (`18` Live + `1` Archived)

### Lane A — Campus Operations and Community School Control Plane

| Repository | Current State | Proposed Disposition | Rationale |
| --- | --- | --- | --- |
| `mschwar/LPES-CS` (`/central/repos/active/LPES-CS`) | `2,514` commits (`2026-11-18`), `3,070` files; live `lpes-workbench.service` and macOS ingestion daemon | **Leave standalone (sole LPES hub)** | Owns daily campus operations, restricted intake routing, bilingual briefings, Coordinator Workbench (`ops/workbench-ui/`), and the LPES Graduate Profile (`records/days/2026-10-13/graduate-profile/`). |
| `mschwar/init-mockup` (`/central/repos/active/init-mockup`) | `26` commits (`2026-11-12`); archived on GitHub | **Finish retiring -> archive (`Pass 1`)** | Already folded into `LPES-CS` (`PR #433`), and `lpes-workbench.service` runs from `/central/repos/active/LPES-CS`. Left in `/central/repos/active/` on November 12 only because a live shell held that working directory. Move checkout to `/central/archive/lpes-retired-ui-prototypes-2026-11-12/` and record a `supersede` decision in [registry/identity-decisions.json](../../registry/identity-decisions.json). |
| `mschwar/class-sync` (`/central/archive/lpes-retired-ui-prototypes-2026-11-12/class-sync`) | Archived `2026-11-12` (`PR #433`) | **Leave archived** | Already folded into `LPES-CS` with a verified bundle and manifest. |

### Lane B — Empirical Education Econometrics and Policy Publications

| Repository | Current State | Proposed Disposition | Rationale |
| --- | --- | --- | --- |
| `mschwar/two-track-education` (`/central/repos/active/two-track-education`) | `110` commits (`2026-11-13`), `378` files (`P1-P6`: Papers A/B/C + LPES Governance Brief) | **Leave standalone** | Preserves LPES Leadership Council authorship, masked public-records replication (`DATA_RIGHTS.md`), and pending district `Tier R2` review. |
| `mschwar/chreod` (`/central/repos/active/chreod`) | `32` commits (`2026-11-18`), `153` files (`C1-C8`, `braid.py` feeder solver, bilingual policy briefs) | **Leave standalone + package `chreod.braid` (`Pass 3`)** | Preserves pre-registered commit locks and ongoing journal revision. Decouple `chreod/Makefile` from its hardcoded `/Users/mschwar/Developer/two-track-education/requirements-review.txt` path, declare self-contained `pyproject.toml` dependencies, and expose `chreod.braid` cleanly. |

### Lane C — Mastery Learning Engine, Pedagogy Primitives, and School Institution

| Repository | Current State | Proposed Disposition | Rationale |
| --- | --- | --- | --- |
| `mschwar/lighthouse` (`/central/repos/research-ml/lighthouse`) | `11` commits (`2026-11-17`), `67` files (calculus learner loop + Tri-Chamber / 4-Horizon capital charter) | **Leave standalone flagship (absorb `baha` + `edifice` seeds)** | Primary home for adaptive mastery workflows and new-school institutional governance (`INSTITUTIONAL_CAPITAL_AND_GOVERNANCE_BLUEPRINT.md`). |
| `mschwar/learning-substrate-programme` (`/central/repos/research-ml/learning-substrate-programme`) | `153` commits (`2026-11-13`), `103` files; paused at `SG-02R2` | **Leave standalone upstream lab (freeze post-Wave 5)** | Keeps psychometric falsification artifacts separate from `lighthouse` until Wave 5 derivation settles, promoting only validated `MODEL_LEDGER.md` constraints. |
| `mschwar/praxis-kit` (`/central/repos/active/praxis-kit`) | `176` commits (`2026-11-13`), `216` files (`kernel/*.md`, `packs/*.toml`, `scripts/pk.py`) | **Treat as shared cross-domain module (`Pass 3`)** | Already compiles deterministic facilitation packs consumed by `LPES-CS` (`./bin/lpes intake praxis`). Migrate `lighthouse/PEDAGOGY_CARDS/` onto `praxis-kit` kernels rather than maintaining two printable-card schemas. |
| `mschwar/baha` (`/central/repos/research-ml/baha`) | `4` commits, dormant since `2026-03-09`, `193` files (`chemistry-mastery/`) | **Fold seeds into `lighthouse` -> archive (`Pass 2`)** | Early chemistry-mastery workspace superseded by `lighthouse` and `learning-substrate-programme`. Preserve durable domain notes, bundle, and archive. |
| `mschwar/edifice` (`/central/repos/research-ml/edifice`) | `2` commits, dormant since `2026-09-16`, `37` files (Obsidian curriculum + textbook index) | **Fold Open Educational Resources (OER) catalog into `lighthouse` -> archive (`Pass 2`)** | Superseded personal curriculum vault. Import `git-dump/SOURCES.md` (10 verified OER repositories) and `pedagogy/` notes into `lighthouse`, then bundle and archive. |

### Lane D — Artificial Intelligence (AI) Curricula, Skill Micro-Apps, and Public Explainers

| Repository | Current State | Proposed Disposition | Rationale |
| --- | --- | --- | --- |
| `mschwar/ai-onboard` (`/central/repos/agent-tooling/ai-onboard`) | `6` commits (`2026-09-16`), `93` files | **Surviving unified AI curriculum repo (`Pass 2`)** | Organize beginner and collaborator AI workshops as clean tracks (`tracks/friends-macos/`, `tracks/lee-agentic-ai/`, `tracks/0-to-1-agent-lab/`, `manifests/`). |
| `mschwar/ai-onboarding-for-friends` (`/central/repos/agent-tooling/ai-onboarding-for-friends`) | `1` commit (`2026-06-02`), `81` files | **Immediate archive (`Pass 1`)** | Verified `diff -rq == 0` byte-for-byte identical to `ai-onboard/ai-onboarding-for-friends/`. Zero content merge needed. |
| `mschwar/ai-class` (`/central/repos/research-ml/ai-class`) | `2` commits (`2026-06-05`), `40` files (`0->1` Agent Lab deck + `LeeClassDesign`) | **Fold into `ai-onboard` -> archive (`Pass 2`)** | Static workshop curriculum and slide decks serving the same onboarding mission. |
| `mschwar/lee-ai-learning` (`/central/repos/research-ml/lee-ai-learning`) | `1` commit (`2026-09-16`), `93` files (`00-plain-english`..`11-next-steps`) | **Fold into `ai-onboard` -> archive (`Pass 2`)** | Single-commit static course for Lee; folding into `ai-onboard` also repairs hardcoded Windows local links. |
| `mschwar/toned` (`/central/repos/apps/toned`) | `6` commits (`2026-10-30`), `40` files (`EarScaffold` Swift/Xcode) | **Leave standalone micro-app** | Native iOS auditory-perception trainer with an isolated Xcode toolchain. |
| `mschwar/pitch-perfect` (`/central/repos/apps/pitch-perfect`) | `2` commits (`2026-02-25`), `51` files (web pitch-quiz prototype) | **Fold Product Requirements Document (PRD) notes into `toned` -> archive (`Pass 2`)** | Early two-commit prototype addressing the same musical ear-training problem as `toned`. |
| `mschwar/janus` (`/central/repos/agent-tooling/janus`) | `4` commits (`2026-10-30`), live `janus.service` (`:8765`) | **Leave standalone micro-app** | Live Glaswegian pronunciation lab serving private copyright-restricted reference audio from `/home/prime/apps/janus`. |
| `mschwar/major-finder` (`/central/repos/apps/major-finder`) | `2` commits (`2026-08-25`), `41` files | **Leave standalone (parked in `apps/`)** | Self-contained college-major exploration web app. Keep parked until `lighthouse` reaches secondary advising scope. |
| `mschwar/common-project` (`/central/repos/active/common-project`) | `19` commits (`2026-11-12`), live `mschwar.github.io/common-project/` (`5/50` entries) | **Leave standalone public site** | GitHub Pages URL binds directly to the `mschwar/common-project` repository name (`plots` and `scrollytelling` stay standalone under the same rule). |

### Explicit Boundary Exclusions

The sweep inspected and excluded `7` adjacent surfaces from consolidation into the education lanes:

- **Shared Epistemic and Memory Engines:** `mschwar/unslop` (`/central/repos/active/unslop`) acts as a cross-estate verification module (`ares`), and `mschwar/initiate` (`/central/repos/agent-tooling/initiate`, `234` commits) owns general encounter capture, consolidation, and resurfacing across all domains.
- **Commercial Student Printables:** `mschwar/monroehaus` (`/central/repos/apps/monroehaus`) stays parked standalone as a commercial Etsy product bundle under portfolio disposition `D-P14`.
- **Devotional and Ruhi Study Tools:** `mschwar/bahai-quote-ledger`, `mschwar/bahai-consultation-guide`, `mschwar/aunt-natalia-daily-study-tool`, `mschwar/joy`, and `mschwar/kapital-redux` (`firesides/`) belong to the devotional study domain rather than secular school or academic research lanes.
- **Keyword False Positive:** `mschwar/proxy-lead` (`/central/repos/apps/proxy-lead`) is a frozen business-to-business (B2B) telecom lead-generation demo (`D-P06`) that happens to mention K-12 information technology (IT) directors.
- **Non-Git Data Directories:** `/central/projects/lpes-cs`, `/central/projects/constructive-understanding`, and `/central/projects/isabel` are plain reference or prompt folders excluded under `defaults.require_git = true` in [ledger_config.json](../../ledger_config.json).

## 3. Target Topology and Shared Module Seams (`18 -> 11` Live Repositories)

```text
SHARED MODULES (Imported or CLI-Invoked Across Lanes — Never Copy-Pasted)
├── mschwar/unslop (ares)      -> Epistemic claims, RFC-6901 bindings, PDF/UA-2 & WCAG HTML5 gates
└── mschwar/praxis-kit (pk)    -> Deterministic TOML->Markdown printable facilitation & pedagogy packs
         │                                       │
         ▼                                       ▼
┌─────────────────────────────────┐    ┌──────────────────────────────────────────┐
│ LANE A: CAMPUS OPS (FERPA/Mac)  │    │ LANE C: MASTERY & NEW INSTITUTION        │
│ • LPES-CS (sole LPES repo)      │    │ • lighthouse (learner loop + charter)    │
│   ├─ absorbed init-mockup       │    │   ├─ absorbs baha + edifice OER seeds    │
│   ├─ absorbed class-sync        │    │   └─ consumes praxis-kit pedagogy cards  │
│   ├─ houses Graduate Profile    │    │ • learning-substrate-programme (R&D lab; │
│   └─ consumes praxis-kit packs  │    │   promotes MODEL_LEDGER -> lighthouse)   │
└────────────────┬────────────────┘    └────────────────────┬─────────────────────┘
                 │ (masked public aggregates)               │ (profiles/lighthouse_specimen.json)
                 ▼                                          ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ LANE B: EMPIRICAL ECONOMETRICS & POLICY (Public Data, Pre-Registered Commits)   │
│ • two-track-education (P1-P6; LPES Leadership Council authorship; standalone)   │
│ • chreod (C1-C8 + chreod.braid 0-1 feeder/grant solver; standalone + package)   │
└─────────────────────────────────────────────────────────────────────────────────┘

LANE D: SELF-CONTAINED CURRICULA & SKILL MICRO-APPS (Standalone by Runtime/URL)
• ai-onboard (folds in ai-onboarding-for-friends + ai-class + lee-ai-learning: 4 -> 1)
• toned (absorbs pitch-perfect PRD: 2 -> 1)   • janus (:8765 daemon)
• major-finder (parked student web app)       • common-project (GitHub Pages)
```

### Module Extraction Rules (Where to Modularize vs. Leave Alone)

1. **Extend `unslop` (`ares`) for Research Manuscript Compilation:** Both `two-track-education` and `chreod` invoke `python -m ares` and implement near-identical LuaLaTeX Portable Document Format Universal Accessibility (`PDF/UA-2`), Semantic HTML5, and RFC-6901 JSON-pointer verification harnesses. Promote those shared manuscript/pointer helpers into `unslop` rather than coupling `chreod` to `two-track-education` filesystem paths.
2. **Standardize Printable Pedagogy Cards on `praxis-kit`:** `LPES-CS` already consumes `praxis-kit` session packages (`packs/lpes-cs-parent-teacher-pilot` via `./bin/lpes intake praxis`). Authoring `lighthouse` printable pedagogy primitives as `praxis-kit` kernels (`kernel/*.md` + `MANIFEST.toml` SHA-256 provenance) eliminates parallel card tooling.
3. **Package `chreod.braid` Cleanly; Defer `edu-panels` Until a Third Study Exists:** Give `chreod` a standalone `pyproject.toml` so downstream profiles (`profiles/lpes_south_valley.json`, `profiles/lighthouse_specimen.json`) invoke `chreod braid` cleanly. Do **not** extract a separate `edu-panels` Stanford Education Data Archive (SEDA) and New Mexico Public Education Department (NMPED) loader package unless a third empirical study launches.

## 4. Alternatives Evaluated

- **Option A — Four-Lane Federated Spine + Targeted Fold-Ins & Shared Modules (Recommended):** Retires `7` redundant or dormant repositories (`18 -> 11`), removes cross-repository `Makefile` path coupling, standardizes shared modules (`unslop`, `praxis-kit`, `chreod.braid`), and preserves every privacy, authorship, and pre-registration boundary.
- **Option B — Aggressive Monorepo Consolidation (Rejected):** Collapsing `two-track-education` + `chreod` or `lighthouse` + `learning-substrate-programme` breaks commit-hash pre-registrations, mixes LPES Leadership Council manuscripts with national/international solo studies, and floods `lighthouse` with `153` commits of falsification runs. Merging anything into `LPES-CS` violates `OPS-20` operational isolation.
- **Option C — Minimalist Duplicate-Only Cleanup (Deferred Fallback):** Retiring only `init-mockup` and `ai-onboarding-for-friends` (`18 -> 16`) takes under ten minutes, but leaves three fragmented single-commit AI onboarding repos, two dormant pre-`lighthouse` vaults, and `chreod`'s hardcoded `two-track-education` path unresolved.

## 5. Staged Execution Plan (When Authorized)

Each pass must follow the house retirement contract: zero live systemd/timer/GBrain source references, clean `git status --porcelain`, verified `git bundle create` + SHA-256 read-back in `ARCHIVE_MANIFEST.json` under `/central/archive/`, GitHub `archived: true` where owned, `supersede` entry in [registry/identity-decisions.json](../../registry/identity-decisions.json), and clean `ledger refresh --no-markdown-mirror` + `ledger lineage --check`.

1. **Pass 1 — Zero-Risk Duplicate and Zombie Retirement (`-2` active repos):**
   - Retire `/central/repos/active/init-mockup` into `/central/archive/lpes-retired-ui-prototypes-2026-11-12/` and record `supersede` -> `LPES-CS`.
   - Bundle and archive `ai-onboarding-for-friends` (verified `100%` byte-identical inside `ai-onboard/ai-onboarding-for-friends/`), archive `mschwar/ai-onboarding-for-friends` on GitHub, and record `supersede` -> `ai-onboard`.
2. **Pass 2 — Content Fold-Ins and Dormant Precursor Retirement (`-5` active repos):**
   - Fold `ai-class` and `lee-ai-learning` into `ai-onboard` (`tracks/0-to-1-agent-lab/` and `tracks/lee-agentic-ai/`), fix broken Windows paths, then bundle and archive both source repos.
   - Fold `edifice/git-dump/SOURCES.md` + `pedagogy/` and salvageable `baha/chemistry-mastery/` specs into `lighthouse`, then bundle and archive `edifice` and `baha`.
   - Copy `pitch-perfect/PRD.md` quiz requirements into `toned/docs/` as an optional roadmap note, then bundle and archive `pitch-perfect`.
3. **Pass 3 — Shared Module Seam Cleanup (`0` repo count change; decouples lanes):**
   - Add standalone `pyproject.toml` / `requirements` to `chreod`, remove the hardcoded `two-track-education` `FIGURES_REQS` path in `chreod/Makefile`, and promote shared `PDF/UA-2` + RFC-6901 manuscript verification helpers into `unslop` (`ares`).
   - Port `lighthouse/PEDAGOGY_CARDS/` to `praxis-kit` kernel packages.
