"""Human-facing canonical ledger Markdown compiler (`P3.7`).

Renders ``state/canonical-projects.md`` around compiled canonical projects and
their preferred locations, GBrain cross-links, git drift warnings, and
expandable observation provenance — never presenting raw observation count as
canonical project count.
"""

from __future__ import annotations

from pathlib import Path

from .models import atomic_write_text, escape_md_cell


def render_canonical_markdown(
    *,
    capsules: list[dict],
    source_count: int,
    observation_count: int,
    review_count: int,
    run_id: str,
    compiled_at: str,
    input_observed_at: str,
) -> str:
    multi_obs = [c for c in capsules if c.get("observation_count", 0) > 1]
    machina_caps = [
        c
        for c in capsules
        if isinstance((c.get("current_state") or {}).get("machina_continuity"), dict)
        and c["current_state"]["machina_continuity"].get("state") == "available"
    ]
    lines: list[str] = [
        "# Canonical Project Ledger",
        "",
        f"- **Run ID**: `{run_id}`",
        f"- **Compiled At**: `{compiled_at}`",
        f"- **Input Observed As-Of**: `{input_observed_at}`",
        f"- **Canonical Projects**: **{len(capsules)}** (distinct from **{observation_count}** observed manifestations across **{source_count}** sources)",
        f"- **Multi-Manifestation Projects**: {len(multi_obs)}",
        f"- **Machina-Tracked Projects**: {len(machina_caps)}",
        f"- **Open Identity Reviews**: {review_count}",
        "",
        "## Canonical Projects",
        "",
        "| Canonical ID | Project Key | Display Name | Language | Description | Last Commit | Preferred Location | Source Class | GBrain Source | Observations | Warnings |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    sorted_capsules = sorted(
        capsules,
        key=lambda item: (str(item.get("project_key") or "").casefold(), item["canonical_project_id"]),
    )
    for cap in sorted_capsules:
        pref = cap.get("preferred_location") or {}
        gbrain = cap.get("gbrain") or {}
        gbrain_cell = f"`{cap.get('gbrain_source_slug') or 'default'}` (`{gbrain.get('page_key') or ''}`)"
        warnings = cap.get("warnings") or []
        warning_cell = escape_md_cell("; ".join(warnings)) if warnings else ""
        loc = pref.get("location") or ""
        lang = escape_md_cell(str(cap.get("primary_language") or ""))
        desc = escape_md_cell(str(cap.get("description") or cap.get("purpose") or ""))
        lc = cap.get("last_commit") if isinstance(cap.get("last_commit"), dict) else {}
        gf = cap.get("git_facts") if isinstance(cap.get("git_facts"), dict) else {}
        short_sha = str(lc.get("short_sha") or (gf.get("head_commit") or "")[:12]).strip()
        commit_date = str(lc.get("committed_at") or gf.get("head_commit_at") or "")[:10]
        commit_subj = str(lc.get("subject") or gf.get("head_commit_subject") or "").strip()
        if short_sha:
            commit_parts = [f"`{escape_md_cell(short_sha)}`"]
            if commit_date:
                commit_parts.append(f"({escape_md_cell(commit_date)})")
            if commit_subj:
                commit_parts.append(f"— {escape_md_cell(commit_subj[:80])}")
            commit_cell = " ".join(commit_parts)
        else:
            commit_cell = ""
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{cap['canonical_project_id']}`",
                    f"`{escape_md_cell(str(cap.get('project_key') or ''))}`",
                    escape_md_cell(str(cap.get("display_name") or "")),
                    lang,
                    desc,
                    commit_cell,
                    f"`{escape_md_cell(str(loc))}`" if loc else "_unavailable_",
                    f"`{escape_md_cell(str(pref.get('source_class') or ''))}`",
                    gbrain_cell,
                    str(cap.get("observation_count", 0)),
                    warning_cell,
                ]
            )
            + " |"
        )

    if sorted_capsules:
        lines.extend(
            [
                "",
                "## Repository Profiles",
                "",
            ]
        )
        for cap in sorted_capsules:
            pid = cap["canonical_project_id"]
            pkey = str(cap.get("project_key") or "")
            dname = str(cap.get("display_name") or pkey)
            lang = str(cap.get("primary_language") or "unknown")
            desc = str(cap.get("description") or cap.get("purpose") or "").strip()
            goal = str(cap.get("goal") or "").strip()
            what = str(cap.get("what_it_does") or "").strip()
            pref = cap.get("preferred_location") or {}
            loc = str(pref.get("location") or "_unavailable_")
            lc = cap.get("last_commit") if isinstance(cap.get("last_commit"), dict) else {}
            gf = cap.get("git_facts") if isinstance(cap.get("git_facts"), dict) else {}
            short_sha = str(lc.get("short_sha") or (gf.get("head_commit") or "")[:12]).strip()
            branch = str(lc.get("branch") or gf.get("head_branch") or "").strip()
            committed_at = str(lc.get("committed_at") or gf.get("head_commit_at") or "").strip()
            subj = str(lc.get("subject") or gf.get("head_commit_subject") or "").strip()
            author = str(lc.get("author") or gf.get("head_commit_author") or "").strip()

            lines.append(f"### {escape_md_cell(dname)} (`{escape_md_cell(pkey)}`)")
            lines.append("")
            lines.append(f"- **Canonical ID**: `{pid}` | **Primary Language**: `{escape_md_cell(lang)}` | **Location**: `{escape_md_cell(loc)}`")
            if desc:
                lines.append(f"- **Description**: {desc}")
            if goal:
                lines.append(f"- **Goal (Why It Exists)**: {goal}")
            if what:
                lines.append(f"- **What It Does**: {what}")
            if short_sha:
                branch_part = f" on `{branch}`" if branch else ""
                when_part = f" ({committed_at}" + (f" by {author})" if author else ")") if committed_at else ""
                subj_part = f" — {subj}" if subj else ""
                lines.append(f"- **Last Commit**: `{short_sha}`{branch_part}{when_part}{subj_part}")
            lines.append("")

    if machina_caps:
        lines.extend(
            [
                "",
                "## Machina Continuity",
                "",
                "| Canonical ID | Project Key | Active Unit | Last Completed Unit | Next Step |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for cap in sorted(machina_caps, key=lambda item: str(item.get("project_key") or "").casefold()):
            cs = cap.get("current_state") or {}
            mc = cs.get("machina_continuity") or {}
            act_id = str(mc.get("active_unit_id") or "").strip()
            act_title = str(mc.get("active_unit_title") or "").strip()
            act_cell = (
                f"`{escape_md_cell(act_id)}`" + (f" ({escape_md_cell(act_title)})" if act_title else "")
                if act_id
                else "_idle_"
            )
            lc_id = str(mc.get("last_completed_unit_id") or "").strip()
            lc_title = str(mc.get("last_completed_unit_title") or "").strip()
            lc_cell = (
                f"`{escape_md_cell(lc_id)}`" + (f" ({escape_md_cell(lc_title)})" if lc_title else "")
                if lc_id
                else ""
            )
            next_cell = escape_md_cell(str(cs.get("next_step") or "").strip())
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"`{cap['canonical_project_id']}`",
                        f"`{escape_md_cell(str(cap.get('project_key') or ''))}`",
                        act_cell,
                        lc_cell,
                        next_cell,
                    ]
                )
                + " |"
            )

    if multi_obs:
        lines.extend(
            [
                "",
                "## Multi-Manifestation Provenance",
                "",
            ]
        )
        for cap in sorted(multi_obs, key=lambda item: str(item.get("project_key") or "").casefold()):
            pid = cap["canonical_project_id"]
            pkey = cap.get("project_key")
            count = cap.get("observation_count", 0)
            anchor = cap.get("identity_anchor") or {}
            lines.extend(
                [
                    "<details>",
                    f"<summary><strong>{escape_md_cell(str(pkey))}</strong> (<code>{pid}</code>) — {count} observations (anchor: <code>{anchor.get('kind')}={escape_md_cell(str(anchor.get('value') or ''))}</code>)</summary>",
                    "",
                    "| Rank | Observation ID | Source | Class | Status | Location | Branch | Reasons |",
                    "| --- | --- | --- | --- | --- | --- | --- | --- |",
                ]
            )
            for obs in cap.get("observations", []):
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            str(obs.get("rank", "")),
                            f"`{obs.get('observation_id', '')}`",
                            f"`{escape_md_cell(str(obs.get('source_id') or ''))}`",
                            f"`{escape_md_cell(str(obs.get('source_class') or ''))}`",
                            f"`{escape_md_cell(str(obs.get('source_status') or ''))}`",
                            f"`{escape_md_cell(str(obs.get('location') or ''))}`",
                            f"`{escape_md_cell(str(obs.get('head_branch') or ''))}`",
                            escape_md_cell(", ".join(obs.get("reason_codes", []))),
                        ]
                    )
                    + " |"
                )
            lines.extend(["", "</details>", ""])

    return "\n".join(lines).rstrip() + "\n"


def write_canonical_markdown(
    *,
    output_path: Path,
    capsules: list[dict],
    source_count: int,
    observation_count: int,
    review_count: int,
    run_id: str,
    compiled_at: str,
    input_observed_at: str,
) -> Path:
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    text = render_canonical_markdown(
        capsules=capsules,
        source_count=source_count,
        observation_count=observation_count,
        review_count=review_count,
        run_id=run_id,
        compiled_at=compiled_at,
        input_observed_at=input_observed_at,
    )
    atomic_write_text(output_path, text)
    return output_path
