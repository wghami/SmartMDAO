#!/usr/bin/env python3
"""
The documentation gate: a change to the library must change the docs too, or say
why it does not need to.

Item 1 of the Definition of Done in docs/handoff.md is "docs/ is updated", and it
was the item most often forgotten - the handoff once pointed the next contributor
at a phase that had shipped months earlier, and a planned finding shipped in a
different shape with no document saying so. Tests catch numbers that drift; this
catches the change that never touched the docs at all.

ONE SCRIPT, FOUR CALLERS, so they cannot disagree about what "updated" means:

    --ci             GitHub Actions, on every pull request (PR body from $PR_BODY)
    --pre-push       .githooks/pre-push, before a push leaves the machine
    --claude-stop    Claude Code Stop hook: a reminder at the end of a turn
    --claude-pretool Claude Code PreToolUse hook: blocks `gh pr create`

THE RULE. If anything under smartmdao/ changed, something under docs/ must have
changed as well - or the PR body / a commit message must contain a line

    Docs: not needed — <a reason of at least ten characters>

A pure refactor or a test-only fix is a legitimate reason; the point is that the
decision is written down rather than forgotten. The reason is required because
an empty waiver is the same as no gate.

Standard library only: it has to run in CI before anything is installed, and in
a hook where startup time is paid on every call.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

SOURCE_PREFIXES = ("smartmdao/",)
DOC_PREFIXES = ("docs/",)

#: "Docs: not needed — reason". Accepts an em dash, en dash or hyphens, since
#: people type whichever is nearest. The reason must be at least ten characters.
WAIVER = re.compile(r"^\s*Docs:\s*not needed\s*(?:—|–|-{1,2})\s*(\S.{9,})$", re.I | re.M)

CHECKLIST = """\
Update whichever of these the change affects:
  docs/roadmap.md       tick the box; record what it cost and what it changed
  docs/known-issues.md  a new sharp edge, or one this change resolves
  docs/cookbook.md      new API, finding code or behaviour a user relies on
  docs/architecture.md  internals that moved
  docs/design/NNN-*.md  a decision with alternatives worth remembering
or, if none applies, add to the PR body (or a commit message):
  Docs: not needed — <why>"""


@dataclass
class Verdict:
    ok: bool
    source: List[str] = field(default_factory=list)
    docs: List[str] = field(default_factory=list)
    waiver: Optional[str] = None

    def explain(self) -> str:
        if not self.source:
            return "No library source changed; nothing to check."
        if self.docs:
            return f"Library changed and docs changed ({', '.join(self.docs[:3])}{'…' if len(self.docs) > 3 else ''})."
        if self.waiver:
            return f"Library changed; docs explicitly not needed: {self.waiver}"
        shown = "\n".join(f"  {path}" for path in self.source[:10])
        more = f"\n  … and {len(self.source) - 10} more" if len(self.source) > 10 else ""
        return (
            f"The library changed but no file under docs/ did:\n{shown}{more}\n\n{CHECKLIST}"
        )


def waiver_in(text: str) -> Optional[str]:
    """The stated reason docs are not needed, if the text contains a valid waiver."""
    match = WAIVER.search(text or "")
    return match.group(1).strip() if match else None


def check(changed: Sequence[str], waiver_text: str = "") -> Verdict:
    """The rule itself: pure, so it can be tested without a repository."""
    source = sorted(p for p in changed if p.startswith(SOURCE_PREFIXES))
    docs = sorted(p for p in changed if p.startswith(DOC_PREFIXES))
    waiver = waiver_in(waiver_text)
    return Verdict(ok=not source or bool(docs) or bool(waiver), source=source, docs=docs, waiver=waiver)


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------

def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout


def default_base() -> str:
    """origin/main when it exists, else main - a fresh clone and CI differ here."""
    for ref in ("origin/main", "main"):
        if subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref],
                          capture_output=True).returncode == 0:
            return ref
    return "HEAD"


def changed_files(base: str, include_worktree: bool) -> List[str]:
    """
    Files changed on this branch since it left `base`.

    With `include_worktree`, uncommitted and untracked files count too - which
    is what the end-of-turn reminder needs, since work in progress is not
    committed yet. A PR and a push only contain commits, so they leave it out.
    """
    merge_base = _git("merge-base", base, "HEAD").strip()
    if include_worktree:
        files = _git("diff", "--name-only", merge_base).splitlines()
        files += _git("ls-files", "--others", "--exclude-standard").splitlines()
    else:
        files = _git("diff", "--name-only", f"{merge_base}...HEAD").splitlines()
    return sorted(set(f for f in files if f))


def commit_messages(base: str) -> str:
    merge_base = _git("merge-base", base, "HEAD").strip()
    return _git("log", "--format=%B", f"{merge_base}..HEAD")


def untagged_release() -> Optional[str]:
    """
    The version on main when it has no `v<version>` tag.

    Only asked on main: a feature branch that bumps the version is expected to
    be untagged until it is merged, because tags go on the merge commit.
    """
    branch = _git("rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch != "main":
        return None
    try:
        with open("pyproject.toml", encoding="utf-8") as handle:
            match = re.search(r'^version\s*=\s*"([^"]+)"', handle.read(), re.M)
    except FileNotFoundError:
        return None
    if not match:
        return None
    version = match.group(1)
    return None if _git("tag", "--list", f"v{version}").strip() else version


# ---------------------------------------------------------------------------
# callers
# ---------------------------------------------------------------------------

def run_ci(base: str) -> int:
    verdict = check(changed_files(base, include_worktree=False), os.environ.get("PR_BODY", ""))
    if verdict.ok:
        print(verdict.explain())
        return 0
    message = verdict.explain()
    print(f"::error title=Docs gate::{message.splitlines()[0]}")
    print(message)
    return 1


def run_pre_push(base: str) -> int:
    verdict = check(changed_files(base, include_worktree=False), commit_messages(base))
    if verdict.ok:
        return 0
    print(f"pre-push: {verdict.explain()}", file=sys.stderr)
    return 1


def run_claude_stop(payload: dict, base: str) -> int:
    """
    A reminder, not a wall: it blocks the end of a turn once, then lets it
    through. `stop_hook_active` is set when the turn is already continuing
    because of this hook, and honouring it is what keeps it from looping.
    """
    if payload.get("stop_hook_active"):
        return 0

    problems = []
    verdict = check(changed_files(base, include_worktree=True))
    if not verdict.ok:
        problems.append("Documentation reminder (docs/handoff.md, Definition of Done item 1). "
                        + verdict.explain())
    version = untagged_release()
    if version:
        problems.append(
            f"Version {version} on main has no v{version} tag. Tag the merge commit and "
            f"push the tag: paper-repro pins by tag, and the docs cite releases by it."
        )
    if not problems:
        return 0
    print("\n\n".join(problems), file=sys.stderr)
    return 2


def run_claude_pretool(payload: dict, base: str) -> int:
    """Blocks `gh pr create` on a branch that changed the library but not the docs."""
    command = (payload.get("tool_input") or {}).get("command", "")
    if "gh pr create" not in command:
        return 0

    body = command
    body_file = re.search(r"--body-file[= ]+(\S+)", command)
    if body_file and os.path.isfile(body_file.group(1).strip("'\"")):
        with open(body_file.group(1).strip("'\""), encoding="utf-8") as handle:
            body += "\n" + handle.read()

    verdict = check(changed_files(base, include_worktree=False), body + "\n" + commit_messages(base))
    if verdict.ok:
        return 0
    print("Blocked: this PR would not pass the CI docs gate.\n\n" + verdict.explain(), file=sys.stderr)
    return 2


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    mode = parser.add_mutually_exclusive_group(required=True)
    for flag in ("--ci", "--pre-push", "--claude-stop", "--claude-pretool"):
        mode.add_argument(flag, action="store_true")
    parser.add_argument("--base", default=None, help="ref the branch is compared with")
    args = parser.parse_args(argv)

    try:
        # Hooks do not promise a working directory; everything below is
        # relative to the repository root.
        os.chdir(_git("rev-parse", "--show-toplevel").strip())
        base = args.base or default_base()

        if args.ci:
            return run_ci(base)
        if args.pre_push:
            return run_pre_push(base)
        payload = json.loads(sys.stdin.read() or "{}")
        if args.claude_stop:
            return run_claude_stop(payload, base)
        return run_claude_pretool(payload, base)
    except subprocess.CalledProcessError as error:
        if args.ci:
            # CI is the layer that enforces. A gate that cannot read the diff
            # there must fail, never wave the PR through.
            print(f"::error title=Docs gate::git failed: {error}")
            return 1
        # Locally, a broken gate should not wedge the session: say so and let
        # CI be the one that enforces.
        print(f"docs_gate: git failed ({error}); not enforcing here.", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
