#!/usr/bin/env python3
"""Verify current PR checks against merge-policy.json, then merge the exact checked revision."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

FIELDS = "headRefOid,baseRefName,isDraft,state,statusCheckRollup,url,mergeCommit,commits,files"
UUID7_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

#: Recorded on the merge commit when a denylisted path was authorized, so the
#: second authorization is evidence in `main`'s history rather than a claim in a
#: chat log.
DENYLIST_AUTHORIZATION_TRAILER = "Operator-Authorization"
DENYLISTED_PATHS_TRAILER = "Denylisted-Paths-Authorized"
#: A reflexive `-y` is not a second authorization. The words are recorded
#: verbatim, so they have to be words.
MIN_AUTHORIZATION_CHARS = 8
#: What an operator types at a terminal to confirm a denylisted merge.
AUTHORIZATION_CONFIRMATION_PREFIX = "authorize"


class Refusal(ValueError):
    """Fail-closed merge gate refusal."""


def require(condition: Any, message: str) -> None:
    if not condition:
        raise Refusal(message)


def _default_root() -> Path:
    here = Path(__file__).resolve()
    for idx in (1, 2):
        if len(here.parents) > idx and (here.parents[idx] / "ops/merge-policy.json").is_file():
            return here.parents[idx]
    return here.parents[1]


ROOT = _default_root()


def gh(root: Path, *args: Any) -> str:
    gh_bin = os.environ.get("MERGE_GATE_GH") or os.environ.get("LPES_GH") or "gh"
    result = subprocess.run(
        [gh_bin, *map(str, args)],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    require(result.returncode == 0, "merge_github_failed: " + result.stderr.strip())
    return result.stdout.strip()


def policy(root: Path, policy_path: Path | None = None) -> dict[str, Any]:
    target = policy_path if policy_path is not None else (root / "ops/merge-policy.json")
    data = json.loads(target.read_text(encoding="utf-8"))
    names = data.get("required_checks")
    retired = data.get("retired_checks", [])
    required_trailers = data.get("required_trailers", [])
    base_branch = data.get("base_branch")
    denylisted = data.get("denylisted_path_globs", [])
    require(
        data.get("schema_version") == 1
        and isinstance(base_branch, str)
        and bool(base_branch.strip())
        and isinstance(names, list)
        and bool(names)
        and len(names) == len(set(names))
        and all(isinstance(n, str) and bool(n.strip()) for n in names)
        and isinstance(retired, list)
        and len(retired) == len(set(retired))
        and all(isinstance(n, str) and bool(n.strip()) for n in retired)
        and isinstance(required_trailers, list)
        and len(required_trailers) == len(set(required_trailers))
        and all(
            isinstance(t, str) and bool(t.strip()) and ":" not in t
            for t in required_trailers
        )
        # Absent/empty means "this repository declares no denylist" (the portable
        # template is copied into repos that may not want one). Present but
        # malformed fails closed.
        and isinstance(denylisted, list)
        and len(denylisted) == len(set(denylisted))
        and all(isinstance(g, str) and bool(g.strip()) for g in denylisted)
        and not (set(names) & set(retired)),
        "merge_policy_invalid",
    )
    return data


def _extract_trailer_values(text: str, trailer: str) -> list[str]:
    prefix = f"{trailer}:"
    values: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            values.append(stripped[len(prefix) :].strip())
    return values


def _validate_trailer_value(trailer: str, val: str) -> None:
    require(bool(val), f"merge_invalid_trailer_value: {trailer}={val}")
    if trailer == "Homelab-Spec-Id":
        require(
            bool(UUID7_RE.fullmatch(val)),
            f"merge_invalid_trailer_value: {trailer}={val}",
        )


def verify_required_trailers(
    rules: dict[str, Any],
    snap: dict[str, Any],
    *,
    merge_method: str,
    body_file: Path | None,
    allow_missing_trailers: bool = False,
) -> None:
    if body_file is not None:
        require(body_file.is_file(), "merge_body_file_missing")

    required_trailers = rules.get("required_trailers") or []
    if not required_trailers or allow_missing_trailers:
        return

    commit_texts: list[str] = []
    for commit in snap.get("commits") or []:
        if isinstance(commit, dict):
            headline = str(commit.get("messageHeadline") or "")
            body = str(commit.get("messageBody") or "")
            commit_texts.append(f"{headline}\n{body}")

    if merge_method == "squash":
        require(
            body_file is not None and body_file.is_file(),
            "merge_squash_body_file_required_for_trailers",
        )
        assert body_file is not None
        body_text = body_file.read_text(encoding="utf-8")
        for trailer in required_trailers:
            body_vals = _extract_trailer_values(body_text, trailer)
            commit_vals = [v for t in commit_texts for v in _extract_trailer_values(t, trailer)]
            require(bool(body_vals), f"merge_required_trailer_missing: {trailer}")
            for val in body_vals:
                _validate_trailer_value(trailer, val)
            for val in commit_vals:
                _validate_trailer_value(trailer, val)
            require(
                set(commit_vals).issubset(set(body_vals)),
                f"merge_squash_body_missing_commit_trailer: {trailer}",
            )
        return

    texts: list[str] = []
    if body_file is not None:
        texts.append(body_file.read_text(encoding="utf-8"))
    texts.extend(commit_texts)

    for trailer in required_trailers:
        values: list[str] = []
        for text in texts:
            values.extend(_extract_trailer_values(text, trailer))
        require(bool(values), f"merge_required_trailer_missing: {trailer}")
        for val in values:
            _validate_trailer_value(trailer, val)


def _repo_args(repo: str | None) -> tuple[str, ...]:
    return ("--repo", repo) if repo else ()


def _repo_from_pr_url(url: Any) -> str | None:
    match = re.match(r"^https?://([^/]+)/([^/]+/[^/]+)/pull/\d+$", str(url or ""))
    if not match:
        return None
    host, slug = match.group(1), match.group(2)
    return slug if host == "github.com" else f"{host}/{slug}"


def matches_denylist(rel_path: str, patterns: Sequence[str]) -> bool:
    """Return True if a repository-relative path matches any denylisted glob.

    Deliberately a local mirror of `scripts/workspec_supervisor.py::matches_denylist`
    (plain fnmatch, plus `dir/**` also matching `dir` itself) rather than an import:
    this gate is copied verbatim into
    `lab/docs/templates/mschwar-agent-os/merge_gate.py` for other repositories to
    adopt, so it must stay stdlib-only, and importing the supervisor would pull the
    whole homelab kernel into it. A parity test pins the two implementations to the
    same behaviour, so they cannot drift apart silently.
    """
    norm = rel_path.strip().lstrip("/")
    for pat in patterns:
        if fnmatch.fnmatch(norm, pat):
            return True
        if pat.endswith("/**") and (norm == pat[:-3] or norm.startswith(pat[:-2])):
            return True
    return False


def changed_paths(snap: dict[str, Any]) -> list[str]:
    """Repository-relative paths the PR touches, from its `files` field."""
    paths: list[str] = []
    for entry in snap.get("files") or []:
        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
            paths.append(entry["path"])
    return paths


def denylisted_changes(snap: dict[str, Any], rules: dict[str, Any]) -> list[str]:
    """Paths in *snap* that match the policy's denylisted globs."""
    patterns = rules.get("denylisted_path_globs") or []
    if not patterns:
        return []
    return sorted({p for p in changed_paths(snap) if matches_denylist(p, patterns)})


def resolve_authorization(
    snap: dict[str, Any],
    rules: dict[str, Any],
    pr: str | int,
    expected_head: str,
    *,
    authorization: str | None = None,
    provider: Callable[[list[str]], str] | None = None,
) -> tuple[list[str], str]:
    """Decide whether this merge may proceed, and with whose authorization.

    Three outcomes, in this order:
      * no denylisted path touched -> `([], "")`, nothing to authorize;
      * a denylisted path touched, and either an explicit `authorization` or a
        `provider` (the terminal confirmation) supplies the operator's words ->
        `(matched, words)`;
      * a denylisted path touched with neither -> Refusal naming the paths.

    This is the "second human auth check": it cannot be satisfied by re-running the
    merge, by `--dangerously-skip-permissions`-style bypass, or by the agent
    deciding on its own, because the words are recorded verbatim in the merge
    commit and a short/absent authorization refuses.
    """
    matched = denylisted_changes(snap, rules)
    if not matched:
        return [], ""
    text = (authorization or "").strip()
    if not text and provider is not None:
        text = str(provider(matched) or "").strip()
    require(
        len(text) >= MIN_AUTHORIZATION_CHARS,
        "merge_denylisted_paths_require_operator_authorization: "
        + ", ".join(matched)
        + " -- pass --denylisted-authorization with the operator's own words, or "
        "confirm interactively at a terminal (an agent may not self-authorize)",
    )
    return matched, text


def authorization_block(
    pr: str | int,
    expected_head: str,
    matched: Sequence[str],
    authorization: str,
) -> str:
    """The evidence appended to the merge commit body."""
    return (
        f"\n{DENYLISTED_PATHS_TRAILER}: {', '.join(matched)}\n"
        f"{DENYLIST_AUTHORIZATION_TRAILER}: {authorization.strip()}\n"
        f"Authorized-Head: {expected_head}\n"
        f"Authorized-PR: {pr}\n"
    )


def body_file_with_authorization(
    body_file: Path | None,
    pr: str | int,
    expected_head: str,
    matched: Sequence[str],
    authorization: str,
) -> Path:
    """Write the merge body that carries the authorization block.

    A temporary file rather than an in-place edit: the operator's own `--body-file`
    (which the trailer rules read) is left exactly as they wrote it. The call site
    removes the temporary copy after the merge.
    """
    text = body_file.read_text(encoding="utf-8") if body_file is not None else ""
    if text and not text.endswith("\n"):
        text += "\n"
    text += authorization_block(pr, expected_head, matched, authorization)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", prefix="merge-gate-authorized-", suffix=".body", delete=False
    ) as handle:
        handle.write(text)
        path = Path(handle.name)
    return path


def terminal_authorization_provider(
    pr: str | int,
    expected_head: str,
    *,
    stdin: Any = None,
    stdout: Any = None,
) -> Callable[[list[str]], str]:
    """The interactive second check: type `authorize <pr> <head[:12]>` to proceed."""
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    expected = f"{AUTHORIZATION_CONFIRMATION_PREFIX} {pr} {expected_head[:12]}"

    def provider(matched: list[str]) -> str:
        stdout.write(
            "This PR touches denylisted paths:\n"
            + "".join(f"  - {path}\n" for path in matched)
            + "Merging is a separate operator decision from approving the work.\n"
            + f"Type exactly  {expected}  to authorize it, or anything else to refuse.\n> "
        )
        stdout.flush()
        answer = stdin.readline().strip()
        require(answer == expected, "merge_denylisted_authorization_not_confirmed")
        return answer

    return provider


def checked_snapshot(
    root: Path,
    pr: str | int,
    expected_head: str,
    *,
    merged: bool = False,
    repo: str | None = None,
    policy_path: Path | None = None,
) -> dict[str, Any]:
    require(bool(re.fullmatch(r"[0-9a-f]{40}", expected_head)), "merge_expected_head_invalid")
    data = json.loads(gh(root, "pr", "view", pr, *_repo_args(repo), "--json", FIELDS))
    rules = policy(root, policy_path=policy_path)
    require(data.get("headRefOid") == expected_head, "merge_head_changed")
    require(data.get("baseRefName") == rules["base_branch"], "merge_wrong_base")
    require(data.get("state") == ("MERGED" if merged else "OPEN"), "merge_wrong_state")
    require(not data.get("isDraft"), "merge_draft")
    checks = data.get("statusCheckRollup") or []
    retired = set(rules.get("retired_checks") or ())
    names: dict[str, bool] = {}
    for check in checks:
        name = check.get("name") or check.get("context")
        if name in retired:
            continue
        require(bool(name) and name not in names, "merge_ambiguous_check")
        if check.get("__typename") == "CheckRun":
            success = check.get("status") == "COMPLETED" and check.get("conclusion") == "SUCCESS"
        else:
            success = check.get("state") == "SUCCESS"
        names[str(name)] = success
        require(success, f"merge_check_not_successful: {name}")
    for name in rules["required_checks"]:
        require(names.get(name) is True, f"merge_required_check_missing: {name}")
    return data


def merge(
    root: Path,
    pr: str | int,
    expected_head: str,
    *,
    repo: str | None = None,
    policy_path: Path | None = None,
    merge_method: str = "merge",
    subject: str | None = None,
    body_file: Path | None = None,
    delete_branch: bool = False,
    allow_missing_trailers: bool = False,
    denylisted_authorization: str | None = None,
    authorization_provider: Callable[[list[str]], str] | None = None,
) -> dict[str, Any]:
    require(merge_method in {"merge", "squash"}, "merge_method_invalid")
    rules = policy(root, policy_path=policy_path)
    checked_snapshot(root, pr, expected_head, repo=repo, policy_path=policy_path)
    # Re-read immediately before mutation. GitHub also guards head and base races.
    snap = checked_snapshot(root, pr, expected_head, repo=repo, policy_path=policy_path)
    merge_repo = repo or _repo_from_pr_url(snap.get("url"))
    if delete_branch:
        require(bool(merge_repo), "merge_repo_required_for_delete_branch")

    # The second authorization check, before anything is mutated.
    matched, auth_text = resolve_authorization(
        snap,
        rules,
        pr,
        expected_head,
        authorization=denylisted_authorization,
        provider=authorization_provider,
    )
    effective_body = body_file
    temporary_body: Path | None = None
    if matched:
        # The authorization has to BE the evidence, so it goes into the merge
        # commit rather than a side channel: a denylisted merge without a body has
        # nowhere to record it.
        require(
            body_file is not None,
            "merge_denylisted_authorization_requires_body_file: pass --body-file so "
            "the operator authorization is recorded in the merge commit (squash "
            "already requires one)",
        )
        effective_body = body_file_with_authorization(
            body_file, pr, expected_head, matched, auth_text
        )
        temporary_body = effective_body

    verify_required_trailers(
        rules,
        snap,
        merge_method=merge_method,
        body_file=effective_body,
        allow_missing_trailers=allow_missing_trailers,
    )
    merge_args: list[Any] = [
        "pr",
        "merge",
        pr,
        *_repo_args(merge_repo),
        f"--{merge_method}",
        "--match-head-commit",
        expected_head,
    ]
    if subject:
        merge_args.extend(["--subject", subject])
    if effective_body is not None:
        merge_args.extend(["--body-file", str(effective_body)])
    if delete_branch:
        merge_args.append("--delete-branch")
    try:
        gh(root, *merge_args)
        result = checked_snapshot(
            root,
            pr,
            expected_head,
            merged=True,
            repo=merge_repo,
            policy_path=policy_path,
        )
    finally:
        if temporary_body is not None:
            temporary_body.unlink(missing_ok=True)
    require((result.get("mergeCommit") or {}).get("oid"), "merge_unconfirmed")
    return {**result, "denylisted_paths": matched, "operator_authorization": auth_text}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pr", help="Pull request number or URL")
    parser.add_argument("--expected-head", required=True, help="40-char expected PR head SHA")
    parser.add_argument("--check-only", action="store_true", help="Verify checks without merging")
    parser.add_argument("--repo", help="Optional GitHub OWNER/REPO override")
    parser.add_argument("--root", type=Path, default=ROOT, help="Repository root")
    parser.add_argument("--policy", type=Path, help="Optional path to merge-policy.json")
    parser.add_argument(
        "--merge-method",
        choices=("merge", "squash"),
        default="merge",
        help="GitHub PR merge strategy (default: merge)",
    )
    parser.add_argument("--subject", help="Optional merge/squash commit title")
    parser.add_argument("--body-file", type=Path, help="Optional merge/squash commit body file")
    parser.add_argument("--delete-branch", action="store_true", help="Delete head branch on merge")
    parser.add_argument(
        "--no-spec",
        "--allow-missing-trailers",
        dest="allow_missing_trailers",
        action="store_true",
        help="Explicitly allow merging a housekeeping PR without required_trailers",
    )
    parser.add_argument(
        "--denylisted-authorization",
        metavar="WORDS",
        default=None,
        help=(
            "The operator's own words authorizing a merge that touches denylisted "
            "paths. Required non-interactively; at a terminal the operator is "
            "prompted to type a confirmation instead."
        ),
    )
    parser.add_argument(
        "--no-terminal-confirmation",
        action="store_true",
        help="Never prompt for denylisted-path authorization; refuse instead",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    policy_path = args.policy.resolve() if args.policy is not None else None
    body_file = args.body_file.resolve() if args.body_file is not None else None
    try:
        denylisted_paths: list[str] = []
        authorization_text = ""
        if args.check_only:
            # Check-only reports whether the merge WOULD go through, including the
            # second authorization; it never prompts.
            snap = checked_snapshot(
                root,
                args.pr,
                args.expected_head,
                repo=args.repo,
                policy_path=policy_path,
            )
            denylisted_paths, authorization_text = resolve_authorization(
                snap,
                policy(root, policy_path=policy_path),
                args.pr,
                args.expected_head,
                authorization=args.denylisted_authorization,
            )
            result = snap
        else:
            provider = None
            if not args.no_terminal_confirmation and sys.stdin.isatty():
                provider = terminal_authorization_provider(args.pr, args.expected_head)
            result = merge(
                root,
                args.pr,
                args.expected_head,
                repo=args.repo,
                policy_path=policy_path,
                merge_method=args.merge_method,
                subject=args.subject,
                body_file=body_file,
                delete_branch=args.delete_branch,
                allow_missing_trailers=args.allow_missing_trailers,
                denylisted_authorization=args.denylisted_authorization,
                authorization_provider=provider,
            )
            denylisted_paths = result.get("denylisted_paths") or []
            authorization_text = result.get("operator_authorization") or ""
        print(
            json.dumps(
                {
                    "state": result["state"],
                    "head": result["headRefOid"],
                    "url": result["url"],
                    "denylisted_paths": denylisted_paths,
                    "operator_authorization_recorded": bool(authorization_text),
                }
            )
        )
        return 0
    except (OSError, ValueError) as exc:
        print(f"merge: FAIL {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
