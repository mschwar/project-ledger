"""Zero-dependency documentation hygiene gate and adversarial regression tests.

Enforces UNSLOP documentation guardrails across active human-facing guides and AGENTS.md:
- DOC-001:TLDR: README.md has no UTF-8 BOM and opens with a 1..60 word '## TL;DR' section.
- DOC-002:RUNON: No prose sentence in scoped guides exceeds 45 words.
- LAN-009:JARGON: Zero banned AI/corporate filler or ambiguous modals.
- LAN-003:ACRONYM: Zero unexpanded unfamiliar acronyms in scoped guides.
- DOC-003:LINK: Zero file:// links and all relative Markdown links + #heading anchors resolve.
"""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

MAX_REPO_DOC_SENTENCE_WORDS = 45
MAX_README_TLDR_WORDS = 60

HUMAN_FACING_DOC_GLOBS: tuple[str, ...] = (
    "README.md",
    "AGENT_PROTOCOL.md",
    "ARCHITECTURE.md",
    "CURRENT.md",
    "PRD.md",
    "ROADMAP.md",
    "RUNBOOK.md",
    "SCHEMA.md",
    "SYSTEM.md",
    "docs/COMPATIBILITY.md",
    "docs/WAVE1-AGENT-SUBSTRATE.md",
    "docs/decisions/README.md",
)

OPERATIONAL_DOC_GLOBS: tuple[str, ...] = (
    "AGENTS.md",
)

BANNED_JARGON_AND_FILLER: dict[str, str] = {
    "and/or": "state 'A or B' or 'A, B, or both'",
    "as appropriate": "state the exact condition",
    "if applicable": "state the exact condition",
    "etc.": "list all required items or state the exact category",
    "leverage": "use 'use'",
    "utilize": "use 'use'",
    "synergy": "state the specific interaction",
    "hopefully": "state the expected result or verification check",
    "more or less": "give the exact range or tolerance",
    "could possibly": "use 'can' or state the condition",
    "delve": "state the specific analysis action",
    "delve into": "use 'examine' or 'analyze'",
    "tapestry": "use literal domain nouns",
    "testament to": "state the concrete evidence directly",
    "it is important to note": "delete throat-clearing filler and state the point",
    "in today's fast-paced": "delete generic AI preamble",
    "paradigm shift": "describe the specific architectural or methodological change",
    "holistic approach": "name the concrete dimensions covered",
    "seamless integration": "specify the interface contract",
    "game-changer": "state the measurable effect",
    "cutting-edge": "state the specific technical capability",
}

COMMON_ACRONYMS_ALLOWLIST: frozenset[str] = frozenset(
    {
        "API",
        "ASCII",
        "CLI",
        "CMS",
        "CPU",
        "CSS",
        "CSV",
        "DAG",
        "DNS",
        "DOCX",
        "DOM",
        "DPI",
        "FAQ",
        "GIT",
        "GPU",
        "GUI",
        "HTML",
        "HTTP",
        "HTTPS",
        "IDE",
        "IEC",
        "IETF",
        "ISBN",
        "ISO",
        "JSON",
        "JUNIT",
        "JWT",
        "LLM",
        "MAC",
        "MIT",
        "NIST",
        "OCR",
        "PDF",
        "PNG",
        "RAM",
        "REST",
        "RFC",
        "RGB",
        "SDK",
        "SHA",
        "SLO",
        "SOP",
        "SQL",
        "SSH",
        "SSL",
        "SVG",
        "TCP",
        "TLS",
        "TOML",
        "TSV",
        "UDP",
        "URI",
        "URL",
        "USA",
        "USB",
        "USD",
        "UTC",
        "UTF",
        "UUID",
        "WCAG",
        "WWW",
        "XML",
        "XSS",
        "YAML",
        "ZIP",
        "AGENTS",
        "BLOCKER",
        "DEMO",
        "DOCTYPE",
        "ERROR",
        "EST",
        "FAIL",
        "FALSE",
        "III",
        "INFO",
        "LICENSE",
        "MAJOR",
        "MAY",
        "MINOR",
        "MST",
        "MUST",
        "NEVER",
        "NONE",
        "NOT",
        "NOTE",
        "ONLY",
        "PASS",
        "PATCH",
        "PDT",
        "PST",
        "README",
        "REQUIRED",
        "SHALL",
        "SHOULD",
        "TBD",
        "TLDR",
        "TODO",
        "TRUE",
        "VII",
        "VIII",
        "WARN",
        "AAAS",
        "ADR",
        "APA",
        "ARES",
        "ASD",
        "ASES",
        "BMJ",
        "CDC",
        "DTCG",
        "FKGL",
        "GAO",
        "HAS",
        "JAMA",
        "JARS",
        "NAS",
        "NIH",
        "NNG",
        "PNAS",
        "SGCS",
        "STE",
        "SWCS",
        "UNSLOP",
    }
)

_FENCED_CODE_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_HTML_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->", re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_LINK_TARGET_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_URL_RE = re.compile(r"https?://\S+")
_FILE_URI_RE = re.compile(r"\bfile://\S*", re.IGNORECASE)
_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_VOWEL_GROUP_RE = re.compile(r"[aeiouy]+", re.IGNORECASE)
_ACRONYM_TOKEN_RE = re.compile(r"\b([A-Z]{3,10})s?\b")
_RULE_ID_RE = re.compile(r"\b[A-Z]{2,12}-[A-Z0-9][A-Z0-9._-]*\b")
_FILENAME_TOKEN_RE = re.compile(
    r"\b[A-Za-z0-9_.-]+\.(?:md|markdown|txt|json|jsonl|yml|yaml|toml|rst|svg|html|css|py|sh|xml|pdf)\b"
)
_DOMAIN_BRAND_RE = re.compile(r"\bGOV\.UK\b")
_README_TLDR_HEADING_RE = re.compile(r"^(##)\s+TL;DR\s*$", re.MULTILINE)


def count_syllables(word: str) -> int:
    cleaned = re.sub(r"[^a-z]", "", word.lower())
    if not cleaned:
        return 0
    if len(cleaned) <= 3:
        return 1
    if cleaned.endswith("e"):
        if not cleaned.endswith(("le", "ee", "oe", "ye")) or (
            cleaned.endswith("le") and len(cleaned) > 2 and cleaned[-3] in "aeiouy"
        ):
            cleaned = cleaned[:-1]
    elif cleaned.endswith("ed") and len(cleaned) > 4 and cleaned[-3] not in "td":
        cleaned = cleaned[:-2]
    elif cleaned.endswith("es") and len(cleaned) > 4 and not cleaned.endswith(("ses", "zes", "xes", "ches", "shes")):
        cleaned = cleaned[:-2]
    groups = _VOWEL_GROUP_RE.findall(cleaned)
    return max(1, len(groups))


def strip_code_and_links(text: str, *, code_placeholder: str = "code") -> str:
    s = _FENCED_CODE_RE.sub("\n", text)
    s = _HTML_COMMENT_RE.sub("\n", s)
    s = _MD_LINK_RE.sub(r"\1", s)
    s = _URL_RE.sub("", s)
    return _INLINE_CODE_RE.sub(code_placeholder, s)


def normalize_markdown_to_sentences(text: str) -> list[str]:
    s = strip_code_and_links(text, code_placeholder="code")
    sentences: list[str] = []
    for raw_line in s.splitlines():
        line = raw_line.strip()
        if not line or re.fullmatch(r"[\|\s:\-]+", line):
            continue
        line = re.sub(r"^(?:#{1,6}|>|\*|-|\+|\d+\.)\s+", "", line)
        line = line.replace("**", "").replace("*", "").replace("|", ". ")
        for chunk in re.split(r"[.!?]+(?:\s+|$)", line):
            words = _WORD_RE.findall(chunk)
            if words:
                sentences.append(" ".join(words))
    return sentences


def compute_flesch_kincaid(text: str) -> dict[str, float | int]:
    sentences = normalize_markdown_to_sentences(text)
    all_words: list[str] = []
    for sent in sentences:
        all_words.extend(_WORD_RE.findall(sent))
    if not all_words or not sentences:
        return {
            "words": 0,
            "sentences": 0,
            "syllables": 0,
            "words_per_sentence": 0.0,
            "syllables_per_word": 0.0,
            "grade_level": 0.0,
        }
    total_words = len(all_words)
    total_sentences = len(sentences)
    total_syllables = sum(count_syllables(w) for w in all_words)
    wps = total_words / total_sentences
    spw = total_syllables / total_words
    raw_grade = 0.39 * wps + 11.8 * spw - 15.59
    return {
        "words": total_words,
        "sentences": total_sentences,
        "syllables": total_syllables,
        "words_per_sentence": round(wps, 2),
        "syllables_per_word": round(spw, 2),
        "grade_level": round(max(0.0, raw_grade), 2),
    }


def compute_max_sentence_words(text: str) -> int:
    s = strip_code_and_links(text, code_placeholder="item")
    max_words = 0
    for raw_line in s.splitlines():
        line = raw_line.strip()
        if not line or re.fullmatch(r"[\|\s:\-]+", line) or line.startswith("|"):
            continue
        cleaned = re.sub(r"^(?:#{1,6}|>|\*|-|\+|\d+\.)\s+", "", line)
        cleaned = cleaned.replace("**", "").replace("*", "")
        for chunk in re.split(r"[.!?]+(?:\s+|$)", cleaned):
            words = _WORD_RE.findall(chunk)
            if words:
                max_words = max(max_words, len(words))
    return max_words


def check_banned_jargon(text: str, *, context: str = "prose") -> list[str]:
    s = strip_code_and_links(text, code_placeholder="item")
    lowered = s.lower()
    errors: list[str] = []
    for term, guidance in BANNED_JARGON_AND_FILLER.items():
        if term.endswith("."):
            pattern = re.compile(r"(?<!\w)" + re.escape(term) + r"(?=\s|$|[)\],;:])", re.IGNORECASE)
        else:
            pattern = re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.IGNORECASE)
        if pattern.search(lowered):
            errors.append(
                f"[LAN-009:JARGON] {context}: banned filler/ambiguous term {term!r} ({guidance})"
            )
    return errors


def check_undefined_acronyms(text: str, *, context: str = "prose") -> list[str]:
    s = strip_code_and_links(text, code_placeholder=" ")
    s = _FILENAME_TOKEN_RE.sub(" ", s)
    s = _DOMAIN_BRAND_RE.sub(" ", s)
    s = _RULE_ID_RE.sub(" ", s)

    allowed = set(COMMON_ACRONYMS_ALLOWLIST)
    defined: set[str] = set()
    for m in re.finditer(r"[A-Za-z][A-Za-z\s/-]{2,}\(\s*([A-Z]{3,10})s?\s*\)", s):
        defined.add(m.group(1))
    for m in re.finditer(r"\b([A-Z]{3,10})\s*\([A-Za-z][A-Za-z\s/-]{3,}\)", s):
        defined.add(m.group(1))
    for m in re.finditer(r"(?:^|[\*\-\s])([A-Z]{3,10})\s*(?::|—|--)\s+[A-Za-z]", s, re.MULTILINE):
        defined.add(m.group(1))

    undefined_seen: list[str] = []
    for raw_line in s.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        alpha_chars = [ch for ch in stripped if ch.isalpha()]
        if alpha_chars and all(ch.isupper() for ch in alpha_chars) and len(alpha_chars) >= 8:
            continue
        for match in _ACRONYM_TOKEN_RE.finditer(stripped):
            acr = match.group(1)
            if acr in allowed or acr in defined or acr in undefined_seen:
                continue
            undefined_seen.append(acr)

    return [
        f"[LAN-003:ACRONYM] {context}: undefined acronym {acr!r}; expand at first use or replace with plain terms"
        for acr in undefined_seen
    ]


def markdown_heading_slugs(text: str) -> set[str]:
    slugs: set[str] = set()
    no_fences = _FENCED_CODE_RE.sub("\n", text)
    for match in re.finditer(r"^#{1,6}\s+(.+?)\s*$", no_fences, re.MULTILINE):
        raw = re.sub(r"[`*_~]", "", match.group(1)).strip().lower()
        collapsed = re.sub(r"\s+", "-", re.sub(r"[^a-z0-9\s-]", "", raw)).strip("-")
        gfm = re.sub(r"[^a-z0-9 -]", "", raw).replace(" ", "-").strip("-")
        if collapsed:
            slugs.add(collapsed)
        if gfm:
            slugs.add(gfm)
    return slugs


def check_markdown_links(markdown_text: str, *, source_file: Path, repo_root: Path) -> list[str]:
    stripped = _FENCED_CODE_RE.sub("\n", markdown_text)
    stripped = _HTML_COMMENT_RE.sub("\n", stripped)
    stripped = _INLINE_CODE_RE.sub("code", stripped)

    errors: list[str] = []
    resolved_root = repo_root.resolve()
    rel_label = (
        str(source_file.resolve().relative_to(resolved_root))
        if source_file.resolve().is_relative_to(resolved_root)
        else str(source_file)
    )

    for _, raw_target in _MD_LINK_TARGET_RE.findall(stripped):
        target = raw_target.strip()
        if not target:
            continue
        if target.lower().startswith("file://"):
            errors.append(f"[DOC-003:LINK] {rel_label}: machine-local file:// link {raw_target!r} is forbidden")
            continue
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        if ' "' in target or " '" in target:
            target = target.split()[0]
        file_part, sep, fragment = target.partition("#")
        dest = source_file.resolve() if not file_part else (source_file.resolve().parent / file_part).resolve()
        if not dest.is_relative_to(resolved_root):
            errors.append(f"[DOC-003:LINK] {rel_label}: link {raw_target!r} escapes repository root")
            continue
        if not dest.exists():
            errors.append(f"[DOC-003:LINK] {rel_label}: broken relative link {raw_target!r}")
            continue
        if sep == "#" and fragment and dest.is_file() and dest.suffix.lower() in (".md", ".markdown"):
            slugs = markdown_heading_slugs(dest.read_text(encoding="utf-8"))
            if fragment.lower() not in slugs:
                errors.append(
                    f"[DOC-003:LINK] {rel_label}: broken Markdown heading anchor #{fragment} in {raw_target!r}"
                )

    prose_without_md_links = _MD_LINK_RE.sub(r"\1", stripped)
    for match in _FILE_URI_RE.finditer(prose_without_md_links):
        errors.append(f"[DOC-003:LINK] {rel_label}: machine-local URI {match.group(0)!r} is forbidden")

    return errors


def extract_readme_tldr_sections(markdown_text: str) -> list[str]:
    matches = list(_README_TLDR_HEADING_RE.finditer(markdown_text))
    sections: list[str] = []
    for m in matches:
        level = len(m.group(1))
        start = m.end()
        next_heading = re.search(rf"^#{{1,{level}}}\s+\S", markdown_text[start:], re.MULTILINE)
        end = start + next_heading.start() if next_heading else len(markdown_text)
        body = markdown_text[start:end].strip()
        sections.append(body)
    return sections


def expand_doc_globs(repo_root: Path, patterns: tuple[str, ...]) -> list[Path]:
    seen: set[Path] = set()
    ordered: list[Path] = []
    for pat in patterns:
        for match in sorted(repo_root.glob(pat)):
            if match.is_file() and match not in seen:
                seen.add(match)
                ordered.append(match)
    return ordered


def audit_repo_docs(
    repo_root: Path = REPO_ROOT,
    *,
    human_globs: tuple[str, ...] = HUMAN_FACING_DOC_GLOBS,
    operational_globs: tuple[str, ...] = OPERATIONAL_DOC_GLOBS,
) -> list[str]:
    errors: list[str] = []

    readme_path = repo_root / "README.md"
    if not readme_path.is_file():
        errors.append("[DOC-001:TLDR] Missing repository README.md")
    else:
        raw_bytes = readme_path.read_bytes()
        if raw_bytes.startswith(b"\xef\xbb\xbf"):
            errors.append("[DOC-001:TLDR] README.md must not start with a UTF-8 BOM")
        readme_text = raw_bytes.decode("utf-8-sig")
        tldrs = extract_readme_tldr_sections(readme_text)
        if not tldrs:
            errors.append("[DOC-001:TLDR] README.md must include a '## TL;DR' section")
        else:
            tldr_words = int(compute_flesch_kincaid(tldrs[0])["words"])
            if tldr_words < 1 or tldr_words > MAX_README_TLDR_WORDS:
                errors.append(
                    f"[DOC-001:TLDR] README.md TL;DR has {tldr_words} words (must be 1..{MAX_README_TLDR_WORDS})"
                )
            errors.extend(check_banned_jargon(tldrs[0], context="README.md#tl-dr"))
            errors.extend(check_undefined_acronyms(tldrs[0], context="README.md#tl-dr"))

    human_docs = expand_doc_globs(repo_root, human_globs)
    operational_docs = [
        p for p in expand_doc_globs(repo_root, operational_globs) if p not in set(human_docs)
    ]

    for doc_path in human_docs:
        rel = str(doc_path.relative_to(repo_root))
        text = doc_path.read_bytes().decode("utf-8-sig")
        errors.extend(check_banned_jargon(text, context=rel))
        errors.extend(check_undefined_acronyms(text, context=rel))
        max_words = compute_max_sentence_words(text)
        if max_words > MAX_REPO_DOC_SENTENCE_WORDS:
            errors.append(
                f"[DOC-002:RUNON] {rel}: longest sentence has {max_words} words "
                f"(max {MAX_REPO_DOC_SENTENCE_WORDS})"
            )
        errors.extend(check_markdown_links(text, source_file=doc_path, repo_root=repo_root))

    for doc_path in operational_docs:
        rel = str(doc_path.relative_to(repo_root))
        text = doc_path.read_bytes().decode("utf-8-sig")
        errors.extend(check_banned_jargon(text, context=rel))
        errors.extend(check_undefined_acronyms(text, context=rel))
        max_words = compute_max_sentence_words(text)
        if max_words > MAX_REPO_DOC_SENTENCE_WORDS:
            errors.append(
                f"[DOC-002:RUNON] {rel}: longest sentence has {max_words} words "
                f"(max {MAX_REPO_DOC_SENTENCE_WORDS})"
            )
        errors.extend(check_markdown_links(text, source_file=doc_path, repo_root=repo_root))

    return errors


class DocHygieneTests(unittest.TestCase):
    def _make_clean_fixture_repo(self, root: Path) -> None:
        (root / "docs").mkdir(parents=True, exist_ok=True)
        (root / "README.md").write_text(
            "# Sample Tool\n\n"
            "## TL;DR\n\n"
            "Sample Tool validates task configuration files in a target folder and writes a summary report.\n\n"
            "## Usage\n\n"
            "Read [Compatibility](docs/COMPATIBILITY.md#inputs-and-outputs) and [AGENTS.md](AGENTS.md) before running.\n",
            encoding="utf-8",
        )
        (root / "docs" / "COMPATIBILITY.md").write_text(
            "# Compatibility\n\n"
            "## Inputs and Outputs\n\n"
            "Pass a JSON file into the command to receive a verified text receipt.\n",
            encoding="utf-8",
        )
        (root / "AGENTS.md").write_text(
            "# AGENTS.md\n\n"
            "Keep [README.md](README.md#tldr) concise and factual.\n",
            encoding="utf-8",
        )

    def test_repository_docs_pass_hygiene_gate(self) -> None:
        errors = audit_repo_docs(REPO_ROOT)
        self.assertEqual(errors, [], "\n".join(errors))

    def test_adversarial_missing_or_invalid_tldr_and_bom_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_clean_fixture_repo(root)
            self.assertEqual(audit_repo_docs(root), [])

            (root / "README.md").write_text("# Sample Tool\n\nPlain intro without summary.\n", encoding="utf-8")
            errors = audit_repo_docs(root)
            self.assertTrue(any("[DOC-001:TLDR]" in err for err in errors), errors)

            too_long = " ".join(["word"] * 61) + "."
            (root / "README.md").write_text(
                f"# Sample Tool\n\n## TL;DR\n\n{too_long}\n",
                encoding="utf-8",
            )
            errors = audit_repo_docs(root)
            self.assertTrue(any("[DOC-001:TLDR]" in err and "61 words" in err for err in errors), errors)

            (root / "README.md").write_bytes(
                b"\xef\xbb\xbf# Sample Tool\n\n## TL;DR\n\nShort factual summary.\n"
            )
            errors = audit_repo_docs(root)
            self.assertTrue(any("[DOC-001:TLDR]" in err and "UTF-8 BOM" in err for err in errors), errors)

    def test_adversarial_runon_sentence_over_45_words_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_clean_fixture_repo(root)
            runon = " ".join(["clear"] * 46) + "."
            (root / "docs" / "COMPATIBILITY.md").write_text(
                f"# Compatibility\n\n## Inputs and Outputs\n\n{runon}\n",
                encoding="utf-8",
            )
            errors = audit_repo_docs(root)
            self.assertTrue(any("[DOC-002:RUNON]" in err and "46 words" in err for err in errors), errors)

    def test_adversarial_banned_jargon_fails_in_guides_and_agents_unless_backticked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_clean_fixture_repo(root)

            (root / "docs" / "COMPATIBILITY.md").write_text(
                "# Compatibility\n\n## Inputs and Outputs\n\nAvoid banned words such as `leverage` and `etc.` in prose.\n",
                encoding="utf-8",
            )
            self.assertEqual(audit_repo_docs(root), [])

            (root / "docs" / "COMPATIBILITY.md").write_text(
                "# Compatibility\n\n## Inputs and Outputs\n\nTeams leverage this workflow and/or extra scripts, etc.\n",
                encoding="utf-8",
            )
            errors = audit_repo_docs(root)
            self.assertTrue(any("[LAN-009:JARGON]" in err and "'leverage'" in err for err in errors), errors)
            self.assertTrue(any("[LAN-009:JARGON]" in err and "'and/or'" in err for err in errors), errors)
            self.assertTrue(any("[LAN-009:JARGON]" in err and "'etc.'" in err for err in errors), errors)

            self._make_clean_fixture_repo(root)
            (root / "AGENTS.md").write_text(
                "# AGENTS.md\n\nUpdate configuration files if applicable.\n",
                encoding="utf-8",
            )
            errors = audit_repo_docs(root)
            self.assertTrue(any("[LAN-009:JARGON]" in err and "AGENTS.md" in err for err in errors), errors)

    def test_adversarial_undefined_acronym_fails_until_expanded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_clean_fixture_repo(root)

            (root / "docs" / "COMPATIBILITY.md").write_text(
                "# Compatibility\n\n## Inputs and Outputs\n\nThe ZXQWV parser validates manifests.\n",
                encoding="utf-8",
            )
            errors = audit_repo_docs(root)
            self.assertTrue(any("[LAN-003:ACRONYM]" in err and "'ZXQWV'" in err for err in errors), errors)

            (root / "docs" / "COMPATIBILITY.md").write_text(
                "# Compatibility\n\n## Inputs and Outputs\n\n"
                "The Zero Xenial Queue Worker Validator (ZXQWV) parser validates manifests.\n",
                encoding="utf-8",
            )
            self.assertEqual(audit_repo_docs(root), [])

    def test_adversarial_broken_links_anchors_and_file_uris_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_clean_fixture_repo(root)

            (root / "docs" / "COMPATIBILITY.md").write_text(
                "# Compatibility\n\n## Inputs and Outputs\n\n"
                "- [Missing file](DOES_NOT_EXIST.md)\n"
                "- [Missing anchor](../README.md#nonexistent-heading)\n"
                "- [Escapes root](../../outside.md)\n"
                "- [Local URI](file:///etc/hosts)\n",
                encoding="utf-8",
            )
            errors = audit_repo_docs(root)
            self.assertTrue(any("broken relative link 'DOES_NOT_EXIST.md'" in err for err in errors), errors)
            self.assertTrue(any("broken Markdown heading anchor #nonexistent-heading" in err for err in errors), errors)
            self.assertTrue(any("escapes repository root" in err for err in errors), errors)
            self.assertTrue(any("file://" in err for err in errors), errors)


if __name__ == "__main__":
    unittest.main()
